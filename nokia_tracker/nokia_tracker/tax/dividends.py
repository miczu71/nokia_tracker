"""Prosty kalkulator podatku od dywidend 0.1.0 — u źródła w Finlandii,
zaliczenie w Polsce ograniczone do stawki traktatowej, kwota do odzyskania
z fińskiego Vero (BLUEPRINT §2, sekcja "Dywidendy i podatki").

Świadome uproszczenie: liczy na kursie bieżącym (eurpln_rate prezentacyjny),
NIE na zamrożonym kursie NBP D-1 wymaganym przez art. 11a ustawy o PIT dla
faktycznego rozliczenia — to dochodzi w kroku 11 (0.2.0) razem z resztą
silnika podatkowego opartego na lotach. Wartości tutaj są orientacyjne/
edukacyjne, nie do bezpośredniego wpisania do PIT-38 — patrz DISCLAIMER
w ai/prompts.py, ten sam duch dotyczy tej funkcji.

Zweryfikowane względem przykładu z BLUEPRINT: 100 EUR brutto, 35% u źródła
-> 65 EUR netto, zaliczenie 15 EUR, Belka 19 EUR -> 4 EUR dopłaty w PL,
20 EUR do odzyskania z Vero.
"""
from __future__ import annotations

import sqlite3

from ..providers import fx_nbp
from . import lots as taxlots


def add_dividend(conn: sqlite3.Connection, record_date: str, entitled_quantity: float,
                 gross_eur: float, taxes_eur: float, fees_eur: float = 0.0,
                 reinvested_eur: float | None = None, purchase_date: str | None = None,
                 purchase_price_eur: float | None = None,
                 purchased_shares: float | None = None, currency: str = "EUR",
                 gross_per_share_eur: float | None = None,
                 natural_key: str | None = None,
                 reinvested_lot_id: int | None = None,
                 notes: str | None = None) -> int:
    """Zapisuje dywidendę (rejestr, krok 13) i OPCJONALNIE tworzy JEDNOCZEŚNIE lot
    `dividend_drip` (DRIP nie ma odroczonego vestingu jak ESPP match/LTI — reinwestycja
    wykonuje się natychmiast, więc lot powstaje od razu, nie przez scheduler kroku 14).

    Krok 16: `purchase_date`/`purchase_price_eur`/`purchased_shares` są teraz opcjonalne
    (`None` domyślnie) — dywidenda wypłacona gotówką (bez reinwestycji) nie tworzy lotu,
    `reinvested_lot_id` zostaje `NULL`. To ujednolica JEDYNE miejsce zapisu dywidend: web.py
    wołało dawniej surowy `INSERT INTO dividends` z formularza ręcznego, pomijając kurs NBP
    i `natural_key` — stąd rozjazd, gdzie ręcznie wpisane dywidendy nie miały ani waluty osi
    czasu (kurs zamrożony), ani śladu reinwestycji. Teraz WSZYSTKIE dywidendy (import PDF i
    formularz ręczny) przechodzą przez tę jedną funkcję.

    `withholding_pct` liczone z REALNYCH `taxes_eur/gross_eur` per wiersz (dokładniejsze
    niż stała z ustawień — potwierdzone na 5 niezależnych dywidendach w zakresie
    34,9-35,0%, patrz BLUEPRINT §3a). Kurs NBP zamrożony na Record Date (dzień uzyskania
    przychodu wg art. 11a), NIE na Purchase Date (dzień reinwestycji).

    `pl_tax_due_pln` (zaliczenie stawki traktatowej + Belka) celowo zostaje `NULL` —
    wymaga ustawień treaty/Belka z configu, to zakres orkiestracji kroku 14
    (`tax/dividends.py`: u źródła/zaliczenie/odzysk z Vero), nie samego zapisu do rejestru.

    `reinvested_lot_id` (krok 20): gdy podany, dywidenda linkuje się do JUŻ
    ISTNIEJĄCEGO lotu `dividend_drip` zamiast tworzyć nowy — dla przypadku, gdy
    lot już powstał gdzie indziej (np. `parse_vested_dividend_shares` w
    `importers/computershare_pdf.py`, bo źródło dla lat bez sekcji transakcyjnej
    ma tylko dane do lotu, nie do pełnego rekordu dywidendy — sekcja G dopisuje
    się tu OSOBNO, przy odtworzeniu szacunkowego brutto/podatku). Ma pierwszeństwo
    przed `purchase_date`/`purchase_price_eur`/`purchased_shares` — gdyby ktoś
    podał oba, żadnego nowego `add_lot()` się nie wywołuje (uniknięcie
    zdublowania akcji)."""
    if natural_key is None:
        natural_key = f"dividend:{record_date}:{purchase_date}:{entitled_quantity}"
    existing = conn.execute(
        "SELECT id FROM dividends WHERE natural_key = ?", (natural_key,)).fetchone()
    if existing:
        return existing["id"]

    withholding_pct = (taxes_eur / gross_eur * 100) if gross_eur else None
    withholding_paid_eur = taxes_eur
    net_received_eur = gross_eur - taxes_eur

    rate = fx_nbp.rate_for_event(conn, record_date)
    nbp_rate, nbp_rate_date = rate if rate else (None, None)
    gross_pln = gross_eur * nbp_rate if nbp_rate is not None else None

    cur = conn.execute(
        "INSERT INTO dividends (pay_date, quantity, gross_per_share_eur, gross_eur, "
        "withholding_pct, withholding_paid_eur, net_received_eur, nbp_rate, nbp_rate_date, "
        "gross_pln, currency, natural_key, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (record_date, entitled_quantity, gross_per_share_eur, gross_eur, withholding_pct,
         withholding_paid_eur, net_received_eur, nbp_rate, nbp_rate_date, gross_pln,
         currency, natural_key, notes))
    dividend_id = cur.lastrowid
    conn.commit()

    lot_id = reinvested_lot_id
    if lot_id is None:
        has_drip = purchase_date is not None and purchase_price_eur is not None \
            and purchased_shares is not None
        if has_drip:
            drip_natural_key = f"drip:{record_date}:{purchase_date}:{entitled_quantity}"
            lot_id = taxlots.add_lot(
                conn, purchase_date, "dividend_drip", purchased_shares, purchase_price_eur,
                natural_key=drip_natural_key)
    if lot_id is not None:
        conn.execute(
            "UPDATE dividends SET reinvested_lot_id = ? WHERE id = ?", (lot_id, dividend_id))
        conn.commit()
    return dividend_id


def backfill_missing_dividend_rates(conn: sqlite3.Connection) -> int:
    """Uzupełnia `nbp_rate`/`nbp_rate_date`/`gross_pln` dywidendom, które go
    jeszcze nie mają — krok 16, lustrzane do `tax/lots.py::backfill_missing_rates`.
    Dotyczy głównie dywidend wpisanych ręcznie PRZED ujednoliceniem formularza na
    `add_dividend()` (dawny surowy INSERT w web.py nie zamrażał kursu). NIGDY nie
    nadpisuje już zamrożonego `nbp_rate` (filtr `nbp_rate IS NULL` też w samym
    UPDATE) — kurs raz zamrożony jest prawnie ostateczny."""
    rows = conn.execute(
        "SELECT id, pay_date, gross_eur FROM dividends WHERE nbp_rate IS NULL").fetchall()
    filled = 0
    for row in rows:
        rate = fx_nbp.rate_for_event(conn, row["pay_date"])
        if rate is None:
            continue
        nbp_rate, nbp_rate_date = rate
        gross_pln = row["gross_eur"] * nbp_rate
        conn.execute(
            "UPDATE dividends SET nbp_rate = ?, nbp_rate_date = ?, gross_pln = ? "
            "WHERE id = ? AND nbp_rate IS NULL",
            (nbp_rate, nbp_rate_date, gross_pln, row["id"]))
        filled += 1
    conn.commit()
    return filled


def is_estimated(row) -> bool:
    """CZYSTA: `row` (sqlite3.Row lub dict) jest wierszem odtworzonym z "Vested
    Dividend Shares" (brutto/podatek u źródła policzone z założenia
    `finnish_withholding_pct`, nie zmierzone z wyciągu — patrz
    importers/computershare_pdf.py:589-625) wtedy i tylko wtedy, gdy `notes` jest
    niepuste. Ten sam sygnał, którego już używa `tax/pit38.py::_section_g` — jedna
    definicja "to szacunek", nie dwie rozjeżdżające się z czasem (krok 30, 0.14.0)."""
    return bool(row["notes"])


def payouts(conn: sqlite3.Connection, year: str | int | None = None) -> list[dict]:
    """Wypłaty dywidendy, POGRUPOWANE po `pay_date` — jedna wypłata = jeden element,
    niezależnie od tego, ile wierszy `dividends` Computershare dla niej wydrukował.

    Fakt (krok 0.17.2, znaleziony na realnym wyciągu 2026-08-19): Computershare drukuje
    w sekcji "Dividend (Reinvested)" OSOBNY wiersz na każdy koszyk planu (ESPP, LTI)
    uprawniony do TEJ SAMEJ wypłaty — bez identyfikatora planu w kolumnach, nierozróżnialne
    poza ilością (wypłata 2026-07-24: 2734 akcji LTI + 154.663115 akcji ESPP). `pay_date`
    w `dividends` więc NIE jest unikalny. Ta funkcja jest JEDYNYM miejscem, które o tym
    wie — analogicznie do `is_estimated()` powyżej ("jedna definicja, nie dwie rozjeżdżające
    się z czasem"). Każdy konsument tabeli `dividends`, dla którego liczy się WYPŁATA a nie
    WIERSZ (liczba dywidend, mediana stawki/kadencji, dopasowanie harmonogramu), powinien
    czytać stąd, nie bezpośrednio z `dividends`.

    Filtrowanie szacunków (`is_estimated()`) dzieje się PRZED grupowaniem, na poziomie
    wiersza — grupa przeżywa, jeśli ma choć jeden wiersz realny; wiersze szacunkowe w niej
    są zliczone w `estimated_row_count`, ale nie wchodzą do sum. Dwa realne koszyki tej
    samej wypłaty nigdy nie mieszają się z wierszem szacunkowym z odtworzenia "Vested
    Dividend Shares" (inne lata, inna semantyka `gross_eur/quantity` — patrz docstring
    `dividend_outlook.py`).

    Każdy element: `pay_date`, `ids` (id wierszy rosnąco — `ids[0]` to deterministyczny
    reprezentant grupy, bezpieczny bo SQLite rowid tylko rośnie, więc później zaimportowany
    koszyk nigdy nie wyprze wcześniejszego), `gross_eur`/`quantity` (sumy po wierszach
    realnych), `withholding_pct` (średnia ważona `gross_eur` po wierszach realnych z
    niepustym `withholding_pct`, `None` gdy żaden), `real_row_count`, `estimated_row_count`,
    `is_real` (`real_row_count > 0`). Posortowane rosnąco po `pay_date`."""
    query = "SELECT * FROM dividends"
    params: tuple = ()
    if year is not None:
        query += " WHERE strftime('%Y', pay_date) = ?"
        params = (str(year),)
    query += " ORDER BY pay_date ASC, id ASC"
    rows = conn.execute(query, params).fetchall()

    grouped: dict[str, dict] = {}
    for r in rows:
        p = grouped.setdefault(r["pay_date"], {
            "pay_date": r["pay_date"], "ids": [], "gross_eur": 0.0, "quantity": 0.0,
            "withholding_sum": 0.0, "withholding_weight": 0.0,
            "real_row_count": 0, "estimated_row_count": 0})
        p["ids"].append(r["id"])
        if is_estimated(r):
            p["estimated_row_count"] += 1
            continue
        if not (r["quantity"] and r["quantity"] > 0):
            continue
        p["real_row_count"] += 1
        p["gross_eur"] += r["gross_eur"]
        p["quantity"] += r["quantity"]
        if r["withholding_pct"] is not None:
            p["withholding_sum"] += r["withholding_pct"] * r["gross_eur"]
            p["withholding_weight"] += r["gross_eur"]

    result = []
    for p in grouped.values():
        p["is_real"] = p["real_row_count"] > 0
        p["withholding_pct"] = (
            p["withholding_sum"] / p["withholding_weight"] if p["withholding_weight"] else None)
        del p["withholding_sum"], p["withholding_weight"]
        result.append(p)
    return result


def compute_dividend_tax(gross_eur: float, withholding_pct: float,
                         treaty_withholding_pct: float,
                         pl_capital_gains_tax_pct: float) -> dict:
    withholding_paid_eur = gross_eur * withholding_pct / 100
    net_received_eur = gross_eur - withholding_paid_eur

    treaty_cap_eur = gross_eur * treaty_withholding_pct / 100
    credit_eur = min(withholding_paid_eur, treaty_cap_eur)
    belka_eur = gross_eur * pl_capital_gains_tax_pct / 100
    pl_tax_due_eur = max(0.0, belka_eur - credit_eur)
    reclaimable_from_finland_eur = max(0.0, withholding_paid_eur - treaty_cap_eur)

    return {
        "withholding_paid_eur": round(withholding_paid_eur, 2),
        "net_received_eur": round(net_received_eur, 2),
        "pl_tax_due_eur": round(pl_tax_due_eur, 2),
        "reclaimable_from_finland_eur": round(reclaimable_from_finland_eur, 2),
    }


def compute_dividend_tax_pln(row, cfg: dict) -> dict:
    """Krok 15 — sekcja G PIT-38: ten sam łańcuch co `compute_dividend_tax()`
    (u źródła -> zaliczenie ograniczone stawką traktatową -> Belka -> dopłata
    w PL / kwota do odzysku z Vero), ale liczony w PLN na `row['gross_pln']`,
    czyli na kursie NBP ZAMROŻONYM na Record Date (art. 11a) przez
    `add_dividend()`. `row` może być `sqlite3.Row` lub `dict` — używa tylko
    `gross_pln`/`withholding_pct`.

    Zamrożony jest kurs, NIE stawki procentowe: `treaty_withholding_pct`/
    `pl_capital_gains_tax_pct` z `cfg` stosowane są w momencie wywołania, więc
    zmiana ustawień w UI przelicza kwoty PLN na nowo (po kursach z dnia
    zdarzenia) — to celowe, nie niedopatrzenie (patrz `backfill_pl_tax_due`).

    Zwraca `None` dla obu kwot, gdy `gross_pln` jeszcze nie istnieje (kurs
    NBP nie został jeszcze zamrożony, np. NBP było niedostępne przy zapisie).
    """
    gross_pln = row["gross_pln"]
    if gross_pln is None:
        return {"pl_tax_due_pln": None, "reclaimable_from_finland_pln": None}

    withholding_pct = row["withholding_pct"]
    if withholding_pct is None:
        withholding_pct = cfg.get("finnish_withholding_pct", 35.0)

    withholding_paid_pln = gross_pln * withholding_pct / 100
    treaty_cap_pln = gross_pln * cfg["treaty_withholding_pct"] / 100
    credit_pln = min(withholding_paid_pln, treaty_cap_pln)
    belka_pln = gross_pln * cfg["pl_capital_gains_tax_pct"] / 100
    pl_tax_due_pln = max(0.0, belka_pln - credit_pln)
    reclaimable_from_finland_pln = max(0.0, withholding_paid_pln - treaty_cap_pln)

    return {
        "withholding_paid_pln": round(withholding_paid_pln, 2),
        "belka_pln": round(belka_pln, 2),
        "credit_pln": round(credit_pln, 2),
        "pl_tax_due_pln": round(pl_tax_due_pln, 2),
        "reclaimable_from_finland_pln": round(reclaimable_from_finland_pln, 2),
    }


def backfill_pl_tax_due(conn: sqlite3.Connection, cfg: dict) -> int:
    """Przelicza `pl_tax_due_pln` dla wszystkich dywidend z już zamrożonym
    kursem NBP (`gross_pln IS NOT NULL`) na podstawie AKTUALNYCH stawek
    traktat/Belka z `cfg`.

    W odróżnieniu od `tax/lots.py::backfill_missing_rates` (które nigdy nie
    nadpisuje zamrożonego kursu, bo kurs jest prawnie zamrożony na dobre),
    to przeliczenie robi się na nowo za KAŻDYM wywołaniem — to czysta
    arytmetyka na już zamrożonym `gross_pln`, zero wywołań NBP, więc zmiana
    ustawień w UI ma się odzwierciedlić natychmiast po następnym backfillu
    (job schedulera lub odczyt strony `/pit38`), a nie zostać w zawieszeniu
    do ręcznej edycji rekordu. Wiersze bez zamrożonego kursu (`gross_pln IS
    NULL`) są pomijane — nie ma z czego policzyć, `backfill_missing_rates`
    dla lotów to inny mechanizm i nie dotyczy tabeli `dividends`.

    Zwraca liczbę zaktualizowanych wierszy."""
    rows = conn.execute(
        "SELECT id, gross_pln, withholding_pct FROM dividends "
        "WHERE gross_pln IS NOT NULL").fetchall()
    updated = 0
    for row in rows:
        result = compute_dividend_tax_pln(row, cfg)
        conn.execute(
            "UPDATE dividends SET pl_tax_due_pln = ? WHERE id = ?",
            (result["pl_tax_due_pln"], row["id"]))
        updated += 1
    conn.commit()
    return updated
