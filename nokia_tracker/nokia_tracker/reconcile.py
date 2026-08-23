"""Uzgodnienie z wyciągiem Computershare — rekonstrukcja stanu bazy NA DZIEŃ wyciągu
(E7, docs/PLAN_E7_uzgodnienie.md). Model odczytu, zero zapisu — ten sam kontrakt co
`integrity.py` ("świadomie READ-ONLY") i `cash.py` (E4).

**Dlaczego rekonstrukcja, nie odczyt bieżący.** Dzisiejsze `importers/computershare_pdf.py
::reconcile_holdings` porównuje wyciąg z BIEŻĄCYM `SUM(qty_remaining)` — działa tylko dlatego,
że odpala się wyłącznie w momencie importu najnowszego pliku (bramka świeżości). Przeliczanie
uzgodnienia NA ŻĄDANIE (z zapisanego snapshotu, wymóg E7) łamie to założenie: każda sprzedaż
lub kolejny import PO dacie wyciągu dałby fałszywą niezgodność. Ten moduł odtwarza więc stan
bazy na dokładnie ten dzień, na który wystawiony jest wyciąg.

**Dwa fakty, na których stoi rekonstrukcja akcji** (zweryfikowane grepem, nie założone):
- `lots.quantity` nigdy nie jest mutowane ani kasowane (jedyne `UPDATE lots` dotyczą
  `qty_remaining` albo kursu NBP) — `lots` to append-only log nabyć.
- Alokacja FIFO nigdy nie sięga lotu z przyszłości względem sprzedaży (`open_lots(as_of=)`,
  krok 19) — ale ten filtr istnieje dopiero od kroku 19; dane sprzed niego mogą łamać
  niezmiennik, stąd zapytanie-strażnik `_allocation_date_violations` przed każdą rekonstrukcją.

**Klucz rekonstrukcji transz:** `vests.lot_id`, nie `vests.status`. `status` jest stanem
BIEŻĄCYM bez znacznika czasu zmiany, ale przejście `pending → vested` zachodzi zawsze razem
z przypięciem lotu (`tax/grants.py::reconcile_vesting`, `data_fixes.py`), a ten lot ma
`acquired_date` = realna data uwolnienia z wyciągu. Transza `vested` była więc nieuwolniona
na dzień D wtedy i tylko wtedy, gdy `lots.acquired_date > D` — fakt z danych, nie zgadywanie
ze statusu."""
from __future__ import annotations

import json
import sqlite3

_QTY_EPSILON = 0.001
_SHARES_TOLERANCE_CEILING = 2.0
_SHARES_TOLERANCE_BASE = 0.02
_SHARES_TOLERANCE_PER_SNAPSHOT_LOT = 0.01


def _allocation_date_violations(conn: sqlite3.Connection) -> int:
    """Liczba alokacji FIFO przypiętych do lotu nabytego PO dacie sprzedaży — niezmiennik,
    który `open_lots(as_of=)` wymusza od kroku 19 (`tax/lots.py:95-114`), ale dane zapisane
    przed tą naprawą mogły go złamać. Musi wynosić 0 — inaczej rekonstrukcja akcji na
    dzień D zaniża stan (odejmuje ilość, która nigdy realnie nie została dodana do D)."""
    row = conn.execute(
        "SELECT COUNT(*) c FROM sale_allocations sa "
        "JOIN sales s ON s.id = sa.sale_id "
        "JOIN lots l ON l.id = sa.lot_id "
        "WHERE l.acquired_date > s.sale_date"
    ).fetchone()
    return row["c"]


def shares_as_of(conn: sqlite3.Connection, as_of: str) -> dict | None:
    """Akcje posiadane na dzień `as_of`, per `lot_type` i łącznie — `Σ lots.quantity`
    nabytych do D minus `Σ sale_allocations.quantity` sprzedanych do D, liczone PER LOT
    (nie dwiema płaskimi sumami), żeby alokacja przypięta do lotu spoza okna D została
    odrzucona przez `WHERE`, zamiast po cichu odjąć ilość, której nigdy nie dodano.

    `None` gdy `_allocation_date_violations` wykryje naruszenie — rekonstrukcja nie jest
    wtedy do udowodnienia, więc pozycja idzie na `no_data`, nigdy na błędną liczbę."""
    if _allocation_date_violations(conn) > 0:
        return None

    rows = conn.execute(
        "SELECT l.lot_type AS lot_type, "
        "       SUM(l.quantity) AS acquired_qty, "
        "       COALESCE(SUM(a.q), 0.0) AS sold_qty "
        "FROM lots l "
        "LEFT JOIN ("
        "    SELECT sa.lot_id AS lot_id, SUM(sa.quantity) AS q "
        "    FROM sale_allocations sa "
        "    JOIN sales s ON s.id = sa.sale_id "
        "    WHERE s.sale_date <= ? "
        "    GROUP BY sa.lot_id"
        ") a ON a.lot_id = l.id "
        "WHERE l.acquired_date <= ? "
        "GROUP BY l.lot_type",
        (as_of, as_of)).fetchall()

    by_lot_type = {r["lot_type"]: r["acquired_qty"] - r["sold_qty"] for r in rows}
    return {"total": sum(by_lot_type.values()), "by_lot_type": by_lot_type}


def unvested_as_of(conn: sqlite3.Connection, as_of: str) -> dict:
    """Transze oczekujące (unvested) na dzień `as_of`.

    Trzy kubełki:
    - `total` — transze pewne na dzień D: `vested` z lotem uwolnionym PO D, plus `pending`
      z `COALESCE(available_from, vest_date) >= D` (ten sam próg co `grants.unvested_summary`
      dla `upcoming`). `None` gdy istnieje transza nie do odtworzenia (patrz niżej).
    - `ambiguous_qty` — `pending` z terminem PRZED D (`< D`, próg `overdue` w
      `unvested_summary`): nie wiadomo, po której stronie (RSU/akcje) była transza na dzień D.
      Świadomie NIE wchodzi do `total` — decyzja `no_data` vs policzenie z notatką należy do
      `reconcile()` (porównanie z tolerancją), nie do tej funkcji.
    - `unreconstructable` — `vested` bez `lot_id` (`integrity.py::_vested_without_lot`) albo
      `cancelled` (nieosiągalne dziś w produkcji, ale w CHECK schematu) — w obu przypadkach
      nie ma daty, na podstawie której dałoby się rozstrzygnąć stan na D. Niepuste ⇒
      `total = None`."""
    rows = conn.execute(
        "SELECT v.id AS vest_id, v.status AS status, v.quantity AS quantity, "
        "       v.vest_date AS vest_date, v.available_from AS available_from, "
        "       g.program AS program, l.acquired_date AS lot_acquired_date "
        "FROM vests v "
        "JOIN grants g ON g.id = v.grant_id "
        "LEFT JOIN lots l ON l.id = v.lot_id"
    ).fetchall()

    unreconstructable: list[dict] = []
    ambiguous_items: list[dict] = []
    ambiguous_qty = 0.0
    unvested_qty = 0.0
    by_program: dict[str, float] = {}

    def _credit(program: str, qty: float) -> None:
        nonlocal unvested_qty
        unvested_qty += qty
        by_program[program] = by_program.get(program, 0.0) + qty

    for r in rows:
        if r["status"] == "cancelled":
            unreconstructable.append({"vest_id": r["vest_id"], "reason": "cancelled"})
            continue
        if r["status"] == "vested":
            if r["lot_acquired_date"] is None:
                unreconstructable.append(
                    {"vest_id": r["vest_id"], "reason": "vested_without_lot"})
                continue
            if r["lot_acquired_date"] > as_of:
                _credit(r["program"], r["quantity"])
            continue
        # status == 'pending'
        effective_date = r["available_from"] or r["vest_date"]
        if effective_date < as_of:
            ambiguous_qty += r["quantity"]
            ambiguous_items.append(dict(r))
        else:
            _credit(r["program"], r["quantity"])

    total = None if unreconstructable else unvested_qty
    return {
        "total": total,
        "by_program": by_program,
        "ambiguous_qty": ambiguous_qty,
        "ambiguous_items": ambiguous_items,
        "unreconstructable": unreconstructable,
    }


def pending_wtc_sales_as_of(conn: sqlite3.Connection, as_of: str) -> list[dict]:
    """Nierozstrzygnięte konflikty `withhold_to_cover_sale` z `execution_date <= as_of`,
    bez odpowiadającego wiersza w `sales`.

    Filtr `execution_date <= as_of` jest NOWY względem dzisiejszego `reconcile_holdings`
    (które odejmuje wszystkie nierozstrzygnięte konflikty bez patrzenia na datę, bo
    funkcja dotąd odpalała się tylko dla najnowszego wyciągu) — bez niego rekonstrukcja
    as-of dla starszego D odjęłaby sprzedaż, która na dzień D jeszcze nie istniała.

    Podkontrola „already_booked" zostaje jako siatka bezpieczeństwa: auto-rozstrzyganie
    z kroku 20 odpala się dopiero przy KOLEJNYM imporcie — konflikt zaksięgowany ręcznie
    przez `/loty` bez ponownego importu zostaje `resolved=0` mimo że sprzedaż już jest
    w `sales`; bez tej kontroli odjęlibyśmy ją drugi raz."""
    rows = conn.execute(
        "SELECT id, natural_key, incoming_json FROM import_conflicts "
        "WHERE entity_type = 'withhold_to_cover_sale' AND resolved = 0"
    ).fetchall()
    result = []
    for r in rows:
        incoming = json.loads(r["incoming_json"])
        exec_date = incoming.get("execution_date")
        if exec_date is None or exec_date > as_of:
            continue
        qty = incoming.get("quantity", 0.0)
        already_booked = conn.execute(
            "SELECT 1 FROM sales WHERE sale_date = ? AND ABS(quantity - ?) < ?",
            (exec_date, qty, _QTY_EPSILON)).fetchone()
        if already_booked:
            continue
        result.append({
            "conflict_id": r["id"], "natural_key": r["natural_key"],
            "execution_date": exec_date, "quantity": qty,
        })
    return result


def shares_tolerance(conn: sqlite3.Connection, as_of: str) -> float:
    """Tolerancja dla pozycji „Akcje", policzona z danych zamiast trzymana jako magiczna
    stała. Źródło błędu (dokumentowane już w `computershare_pdf.py::reconcile_holdings`
    przed E7) to loty `source='holdings_snapshot'` z `parse_vested_dividend_shares`
    (~0,01/wiersz, kumuluje się przez lata) — konkretne, policzalne loty, nie zgadywanie.
    Rośnie monotonicznie z `as_of` (dokładnie jak kumulacja), z sufitem 2,0 — jeśli wzór
    wyjdzie wyżej, lepiej nie rozmywać alarmu niż zgadywać dalej."""
    row = conn.execute(
        "SELECT COUNT(*) c FROM lots WHERE source = 'holdings_snapshot' "
        "AND lot_type = 'dividend_drip' AND acquired_date <= ?", (as_of,)).fetchone()
    n = row["c"]
    return min(_SHARES_TOLERANCE_CEILING,
               _SHARES_TOLERANCE_BASE + _SHARES_TOLERANCE_PER_SNAPSHOT_LOT * n)
