"""Jednorazowe, idempotentne naprawy danych produkcyjnych — odkryte przez
audyt E1 / `integrity.py` (docs/ROADMAP_V3.md). Każda naprawa sprawdza swój
efekt (istnienie natural_key) zanim coś zrobi — bezpieczne do wołania przy
KAŻDYM starcie add-onu, nie tylko raz; kolejne naprawy dopisywać tu, nie
usuwać stare (ślad tego, co kiedykolwiek naprawiono)."""
from __future__ import annotations

import logging
import sqlite3

from .tax import lots as taxlots

logger = logging.getLogger(__name__)

_MISSING_MATCH_LOT_2025_08_KEY = "vested_matching:2025-08-28:3.71:24.42"
_MISSING_MATCH_GRANT_KEY = "espp_grant:2024-10-21:24.42"


def fix_missing_espp_match_lot_2025_08(conn: sqlite3.Connection) -> None:
    """Audyt E1 (2026-08-22): dopasowanie ESPP z grantu 2024-10-21 (24.42 szt.,
    vest_date=2025-08-01) nigdy nie dostało lotu — jedyny brak w całej
    historii grantów (`reconcile_vesting()` w tax/grants.py celowo nie zgaduje
    dopasowania bez dokładnego trafienia ilością, więc nie mogła go sama
    naprawić). Rekonstrukcja z sąsiednich lotów tej samej paczki vestingu
    (2025-08-28, 3.71 EUR, NBP 4.2639/2025-08-27 — ta sama cena dla całej
    paczki, wzorzec potwierdzony na WSZYSTKICH innych paczkach w bazie).

    Te akcje uczestniczyły w jedynej zarejestrowanej sprzedaży (sale_id=1,
    2025-10-27, 784 szt.): bez tego lotu FIFO sięgnęło 8.48 szt. za dużo do
    droższego lotu z dnia sprzedaży (5.41 EUR) zamiast do tańszej partii z
    2025-08-28 (3.71 EUR). PIT-38 tej sprzedaży ma nadpisanie
    `reported_cost_pln`/`reported_revenue_pln` (krok 20) — ta naprawa NIE
    zmienia niczego zadeklarowanego, tylko przywraca poprawny ślad audytowy
    w `lots`/`sale_allocations`, na którym opierają się przyszłe analizy
    (koncentracja, plan sprzedaży, stan konta)."""
    existing = conn.execute(
        "SELECT id FROM lots WHERE natural_key = ?",
        (_MISSING_MATCH_LOT_2025_08_KEY,)).fetchone()
    if existing:
        return

    lot_id = taxlots.add_lot(
        conn, "2025-08-28", "matched", 24.42, 3.71, source="manual_reconciliation",
        natural_key=_MISSING_MATCH_LOT_2025_08_KEY,
        notes="Rekonstrukcja E2 (docs/ROADMAP_V3.md) — brakujący lot dopasowania "
              "ESPP z grantu 2024-10-21, znaleziony przez audyt E1 2026-08-22 jako "
              "vest przeterminowany o 386 dni. Cena/kurs z sąsiednich lotów tej "
              "samej paczki (2025-08-28, natural_key vested_matching/vested_release "
              "...:3.71:*).")

    vest = conn.execute(
        "SELECT v.id FROM vests v JOIN grants g ON g.id = v.grant_id "
        "WHERE g.natural_key = ? AND v.vest_date = '2025-08-01' AND v.status = 'pending'",
        (_MISSING_MATCH_GRANT_KEY,)).fetchone()
    if vest:
        conn.execute(
            "UPDATE vests SET status = 'vested', lot_id = ? WHERE id = ?",
            (lot_id, vest["id"]))

    _reallocate_sale(conn, sale_id=1)
    conn.commit()
    logger.warning(
        "Naprawa danych (audyt E1): dodano brakujący lot matched (id=%d, 24.42 szt., "
        "2025-08-28) i przeliczono alokację sprzedaży #1", lot_id)


def _restore_sale_allocations(conn: sqlite3.Connection, sale_id: int) -> None:
    """Przywraca `qty_remaining` lotów tej sprzedaży do stanu SPRZED niej i usuwa
    stare alokacje — połowa `_reallocate_sale` wydzielona osobno, żeby
    `revert_phantom_espp_match_lot_2025_08` mogła usunąć fantomowy lot MIĘDZY
    przywróceniem a ponownym przeliczeniem FIFO (inaczej `open_lots()` w drugiej
    połowie nadal widziałby lot, który mamy właśnie skasować)."""
    old_allocs = conn.execute(
        "SELECT * FROM sale_allocations WHERE sale_id = ?", (sale_id,)).fetchall()
    for a in old_allocs:
        conn.execute(
            "UPDATE lots SET qty_remaining = qty_remaining + ? WHERE id = ?",
            (a["quantity"], a["lot_id"]))
    conn.execute("DELETE FROM sale_allocations WHERE sale_id = ?", (sale_id,))


def _apply_fifo_allocation(conn: sqlite3.Connection, sale_id: int) -> None:
    """Przelicza od zera przez `_plan_fifo` (na aktualnym `open_lots()`) i zapisuje
    nowe alokacje — dokładnie ta sama funkcja i matematyka, której użyłaby realna
    sprzedaż, więc wynik jest identyczny z tym, co silnik zrobiłby, gdyby stan
    lotów w momencie sprzedaży był od razu taki, jak jest teraz."""
    sale = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
    candidates = taxlots.open_lots(conn, as_of=sale["sale_date"])
    allocations = taxlots._plan_fifo(
        candidates, sale["quantity"], sale["price_eur"], sale["fee_eur"], sale["nbp_rate"])

    for alloc in allocations:
        conn.execute(
            "INSERT INTO sale_allocations (sale_id, lot_id, quantity, cost_pln, revenue_pln) "
            "VALUES (?, ?, ?, ?, ?)",
            (sale_id, alloc["lot_id"], alloc["quantity"], alloc["cost_pln"],
             alloc["revenue_pln"]))
        conn.execute(
            "UPDATE lots SET qty_remaining = qty_remaining - ? WHERE id = ?",
            (alloc["quantity"], alloc["lot_id"]))


def _reallocate_sale(conn: sqlite3.Connection, sale_id: int) -> None:
    """Przywraca i od razu przelicza od zera — wrapper zachowany dla
    `fix_missing_espp_match_lot_2025_08` (dodaje lot PRZED wywołaniem, więc obie
    połowy mogą iść jedna po drugiej bez nic pomiędzy)."""
    _restore_sale_allocations(conn, sale_id)
    _apply_fifo_allocation(conn, sale_id)


_PHANTOM_VEST_KEY = "espp_vest:2024-10-21:2025-08-01:24.42"


def revert_phantom_espp_match_lot_2025_08(conn: sqlite3.Connection) -> None:
    """Audyt E1 2026-08-22 (`fix_missing_espp_match_lot_2025_08` powyżej) uznał
    transzę dopasowania ESPP z grantu 2024-10-21 (24.42 szt.) za brakującą i
    dopisał jej osobny lot. **To było błędne.** Te akcje BYŁY już policzone —
    Computershare łączy w JEDEN wiersz Withhold-to-Cover wszystkie transze
    dopasowania ESPP, które stają się dostępne tego samego dnia (wzorzec
    potwierdzony też na 2024-08-29, gdzie cztery transze dzielą jeden wiersz,
    loty #33/#34/#35/#36 — tylko jedna z nich ma osobny wpis w `grants`/`vests`).

    Dowód arytmetyczny (re-import 6 wyciągów, 2026-08-24 — docs/ROADMAP_V3.md,
    sekcja re-importu): suma 50% dopasowania sześciu zakupów ESPP, które razem
    odblokowały się 2025-08-28 (loty `purchase:...` z 2024-10-21 i 2025-02-03 do
    2025-07-28) = 101,396666 — zgadza się z realną wartością Withhold-to-Cover
    tego wyciągu (101,396662 — lot `vested_release:2025-08-28:3.71:101.396662`,
    już poprawnie zaimportowany) co do 0,000004 (zaokrąglenie druku PDF). Grant
    2024-10-21 jest OSTATNIM z tych sześciu zakupów — dokładnie tą samą kwotą,
    którą E1 uznał za brakującą. `reconcile_vesting()` celowo nie potrafi
    dopasować pojedynczej transzy do wspólnej puli (dopasowuje tylko po
    dokładnej równości ilości) — transza `pending` bez lotu jest więc
    POPRAWNYM stanem (potwierdzonym przez `test_reconcile_vesting_resolves_
    exactly_the_provable_tranches`, który dla tej samej transzy 24,42 asercjuje
    dokładnie `pending`), nie luką do naprawienia.

    Skutek błędu: fantomowe 24,42 szt. zawyżały pozycję „Akcje" o tyle samo w
    uzgodnieniu z wyciągiem (E7) — z czego 1,1327 szt. zużyła realokacja FIFO
    sprzedaży #1 z 2025-10-27, reszta (23,2873 szt.) siedziała w bieżącym
    stanie konta jako nieistniejące akcje. Cofa: usuwa fantomowy lot, wraca
    transzę do `pending`/`lot_id=NULL`, przelicza FIFO sprzedaży #1 bez tego
    lotu — suma alokacji zostaje 784,0 szt., zmienia się tylko rozkład na
    loty. `reported_cost_pln`/`reported_revenue_pln` sprzedaży #1 (nadpisania
    PIT-38, krok 20) NIE są tu ruszane — ta naprawa dotyczy wyłącznie śladu
    audytowego w `lots`/`vests`/`sale_allocations`."""
    existing = conn.execute(
        "SELECT id FROM lots WHERE natural_key = ? AND source = 'manual_reconciliation'",
        (_MISSING_MATCH_LOT_2025_08_KEY,)).fetchone()
    if existing is None:
        return

    conn.execute(
        "UPDATE vests SET status = 'pending', lot_id = NULL WHERE natural_key = ?",
        (_PHANTOM_VEST_KEY,))

    _restore_sale_allocations(conn, sale_id=1)
    conn.execute("DELETE FROM lots WHERE id = ?", (existing["id"],))
    _apply_fifo_allocation(conn, sale_id=1)
    conn.commit()
    logger.warning(
        "Naprawa danych (2026-08-24): cofnięto błędną naprawę E1 (audyt "
        "2026-08-22) — usunięto fantomowy lot matched (id=%d, 24.42 szt., "
        "2025-08-28, podwójnie liczony względem lotu 'vested_release:"
        "2025-08-28:3.71:101.396662'), transza grantu 2024-10-21 wróciła do "
        "'pending' i przeliczono alokację sprzedaży #1", existing["id"])


_POOLED_MATCH_VEST_KEY = "espp_vest:2024-10-21:2025-08-01:24.42"
_POOLED_MATCH_LOT_KEY = "vested_release:2025-08-28:3.71:101.396662"


def link_pooled_espp_match_2025_08(conn: sqlite3.Connection) -> None:
    """Krok E9 (docs/PLAN_E9_transza_w_puli.md, 2026-08-24): po
    `revert_phantom_espp_match_lot_2025_08` powyżej transza grantu 2024-10-21
    (24,42 szt.) wraca do `status='pending'`, `lot_id=NULL` — poprawny stan wg
    wyciągu, ale `integrity.py::_stale_pending_vest` nie potrafi go odróżnić od
    realnej luki (przeterminowana transza bez lotu = „prawdopodobnie brakujący
    import") i codziennie zgłasza fałszywy błąd.

    Ta transza nigdy nie dostanie WŁASNEGO lotu — Computershare łączy w jeden
    wiersz Withhold-to-Cover wszystkie transze dopasowania ESPP odblokowane tego
    samego dnia (dowód arytmetyczny w `revert_phantom_espp_match_lot_2025_08`:
    101,396666 ≈ 101,396662, różnica 0,000004 = zaokrąglenie druku PDF; grant
    2024-10-21 jest ostatnim z sześciu zakupów tej paczki). Świadomie NIE
    dokładamy tu żadnego lotu (powrót do fantomu 0.24.1) ani nie rozbijamy lotu
    zbiorczego na kawałki (rozjechałby `natural_key` z ilością i wygenerowałby
    konflikt przy re-imporcie tego samego wyciągu) — zamiast tego `pooled_lot_id`
    wskazuje na lot zbiorczy, którego jest częścią, żeby czytelnicy
    (`integrity.py`, `reconcile.py`, `tax/grants.py::valuation`) mogli odróżnić
    „wydana w puli" od realnej luki.

    Zero zmian w `lots`/`sale_allocations`/`sales` — wartość tej transzy jest
    już policzona w locie zbiorczym, ta naprawa tylko domyka ślad audytowy w
    `vests`."""
    pooled_lot = conn.execute(
        "SELECT id FROM lots WHERE natural_key = ?",
        (_POOLED_MATCH_LOT_KEY,)).fetchone()
    if pooled_lot is None:
        return

    vest = conn.execute(
        "SELECT id FROM vests WHERE natural_key = ? AND pooled_lot_id IS NULL",
        (_POOLED_MATCH_VEST_KEY,)).fetchone()
    if vest is None:
        return

    conn.execute(
        "UPDATE vests SET status = 'vested', pooled_lot_id = ? WHERE id = ?",
        (pooled_lot["id"], vest["id"]))
    conn.commit()
    logger.warning(
        "Naprawa danych (E9, 2026-08-24): transza grantu 2024-10-21 (24,42 szt., "
        "vest_id=%d) domknięta jako wydana w zbiorczym locie id=%d "
        "('vested_release:2025-08-28:3.71:101.396662') — zero zmian w lots/"
        "sale_allocations/sales", vest["id"], pooled_lot["id"])


def apply_all(conn: sqlite3.Connection) -> None:
    """Wołane raz przy starcie (main.py) — bezpieczne przy każdym restarcie,
    każda naprawa jest idempotentna.

    `fix_missing_espp_match_lot_2025_08` celowo NIE jest tu już wołane — jej
    założenie okazało się błędne (patrz `revert_phantom_espp_match_lot_2025_08`
    powyżej) i wywoływanie obu na każdym starcie dodawałoby fantomowy lot tylko
    po to, żeby natychmiast go usunąć (guard `fix_missing` sprawdza wyłącznie
    istnienie `natural_key`, nie to, że revert go właśnie skasował). Funkcja
    zostaje w pliku jako ślad audytowy tego, co kiedyś naprawiono/dlaczego —
    zgodnie z konwencją tego modułu.

    Kolejność ma znaczenie: `link_pooled_espp_match_2025_08` idzie PO revert —
    revert cofa transzę do `pending`/`lot_id=NULL`, dopiero wtedy jest co
    domykać przez `pooled_lot_id`."""
    revert_phantom_espp_match_lot_2025_08(conn)
    link_pooled_espp_match_2025_08(conn)
