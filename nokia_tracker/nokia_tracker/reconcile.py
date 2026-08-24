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
ze statusu.

**Trzeci przypadek (E9, docs/PLAN_E9_transza_w_puli.md):** transza wydana w ZBIORCZYM
locie (`vests.pooled_lot_id`, nie `lot_id`) — Computershare łączy w jeden wiersz
Withhold-to-Cover kilka transz dopasowania ESPP odblokowanych tego samego dnia, więc taka
transza nigdy nie dostanie własnego lotu. Data uwolnienia jest wtedy `acquired_date` lotu
WSKAZANEGO PRZEZ `pooled_lot_id` — identyczna reguła jak dla `lot_id`, `COALESCE(lot_id,
pooled_lot_id)` w praktyce. `unreconstructable` tylko gdy OBA są `NULL`."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from .tax import dividends as taxdiv

_QTY_EPSILON = 0.001
_SHARES_TOLERANCE_CEILING = 2.0
_SHARES_TOLERANCE_BASE = 0.02
_SHARES_TOLERANCE_PER_SNAPSHOT_LOT = 0.01
_RESTRICTED_UNITS_TOLERANCE_BASE = 0.02
_RESTRICTED_UNITS_TOLERANCE_PER_ROW = 0.01
_DIVIDEND_TOLERANCE_EUR = 0.02


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


def latest_snapshot(conn: sqlite3.Connection) -> dict | None:
    """Najnowszy `statement_snapshot()` zapisany przy imporcie, sparsowany z JSON.
    `None` gdy nie wgrano jeszcze żadnego wyciągu — wspólny odczyt dla
    `views/imports.py` (pełna tabela) i `views/account.py` (skrót statusu, krok 7),
    żeby oba miejsca czytały DOKŁADNIE ten sam snapshot, nigdy dwie kopie zapytania."""
    row = conn.execute(
        "SELECT snapshot_json FROM statement_snapshots "
        "ORDER BY as_of_date DESC LIMIT 1").fetchone()
    return json.loads(row["snapshot_json"]) if row is not None else None


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
    - `unreconstructable` — `vested` bez `lot_id` I bez `pooled_lot_id`
      (`integrity.py::_vested_without_lot`) albo `cancelled` (nieosiągalne dziś w
      produkcji, ale w CHECK schematu) — w obu przypadkach nie ma daty, na podstawie
      której dałoby się rozstrzygnąć stan na D. Niepuste ⇒ `total = None`."""
    rows = conn.execute(
        "SELECT v.id AS vest_id, v.natural_key AS natural_key, v.status AS status, "
        "       v.quantity AS quantity, v.vest_date AS vest_date, "
        "       v.available_from AS available_from, "
        "       g.program AS program, l.acquired_date AS lot_acquired_date "
        "FROM vests v "
        "JOIN grants g ON g.id = v.grant_id "
        "LEFT JOIN lots l ON l.id = COALESCE(v.lot_id, v.pooled_lot_id)"
    ).fetchall()

    unreconstructable: list[dict] = []
    ambiguous_items: list[dict] = []
    unvested_items: list[dict] = []
    ambiguous_qty = 0.0
    unvested_qty = 0.0
    by_program: dict[str, float] = {}

    def _credit(program: str, qty: float, natural_key: str | None) -> None:
        nonlocal unvested_qty
        unvested_qty += qty
        by_program[program] = by_program.get(program, 0.0) + qty
        unvested_items.append({"natural_key": natural_key, "quantity": qty})

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
                _credit(r["program"], r["quantity"], r["natural_key"])
            continue
        # status == 'pending'
        effective_date = r["available_from"] or r["vest_date"]
        if effective_date < as_of:
            ambiguous_qty += r["quantity"]
            ambiguous_items.append(dict(r))
        else:
            _credit(r["program"], r["quantity"], r["natural_key"])

    total = None if unreconstructable else unvested_qty
    return {
        "total": total,
        "by_program": by_program,
        "ambiguous_qty": ambiguous_qty,
        "ambiguous_items": ambiguous_items,
        "unreconstructable": unreconstructable,
        "unvested_items": unvested_items,
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


@dataclass
class Position:
    """Jedna pozycja tabeli uzgodnienia (`templates/imports.html`, karta „Uzgodnienie
    z wyciągiem"). `status` ∈ {'ok', 'mismatch', 'no_data'} — `no_data` gdy strona
    wyciągu LUB strona bazy jest `None` (brak sekcji, naruszenie niezmiennika,
    niepewność ponad tolerancję), nigdy zgadywane jako niezgodność."""
    key: str
    label: str
    statement: float | None
    database: float | None
    diff: float | None
    tolerance: float
    status: str
    note: str = ""
    details: list[dict] = field(default_factory=list)


def _build_position(key: str, label: str, statement: float | None, database: float | None,
                    tolerance: float, note: str = "", details: list[dict] | None = None,
                    force_no_data: bool = False) -> Position:
    details = details or []
    if force_no_data or statement is None or database is None:
        return Position(key, label, statement, database, None, tolerance, "no_data", note,
                        details)
    diff = database - statement
    status = "ok" if abs(diff) <= tolerance else "mismatch"
    return Position(key, label, statement, database, diff, tolerance, status, note, details)


def _lots_qty_by_natural_key(conn: sqlite3.Connection, natural_keys: list[str]) -> dict:
    if not natural_keys:
        return {}
    placeholders = ",".join("?" for _ in natural_keys)
    rows = conn.execute(
        f"SELECT natural_key, quantity FROM lots WHERE natural_key IN ({placeholders})",
        tuple(natural_keys)).fetchall()
    return {r["natural_key"]: r["quantity"] for r in rows}


def _pending_tranches_position(unvested_db: dict, snapshot_tranches: list[dict]) -> Position:
    """Uzgodnienie PER WIERSZ (nie tylko suma) — dopasowanie po `natural_key` policzonym
    identycznie po obu stronach (`computershare_pdf.py::statement_snapshot` i
    `import_statement`). `details` zawiera transze obecne tylko w jednym źródle."""
    stmt_by_key = {t["natural_key"]: t["quantity"] for t in snapshot_tranches}
    if unvested_db["total"] is None:
        return _build_position(
            "pending_tranches", "Transze oczekujące — wiersze",
            sum(stmt_by_key.values()) if stmt_by_key else 0.0, None,
            _QTY_EPSILON, note="odtworzenie stanu transz na dzień wyciągu niemożliwe",
            force_no_data=True)

    db_by_key = {i["natural_key"]: i["quantity"] for i in unvested_db["unvested_items"]}
    missing_in_db = [{"natural_key": k, "quantity": v, "side": "tylko w wyciągu"}
                      for k, v in stmt_by_key.items() if k not in db_by_key]
    missing_in_stmt = [{"natural_key": k, "quantity": v, "side": "tylko w bazie"}
                        for k, v in db_by_key.items() if k not in stmt_by_key]
    all_keys = set(stmt_by_key) | set(db_by_key)
    tolerance = _QTY_EPSILON * max(1, len(all_keys))
    return _build_position(
        "pending_tranches", "Transze oczekujące — wiersze",
        sum(stmt_by_key.values()), sum(db_by_key.values()), tolerance,
        details=missing_in_db + missing_in_stmt)


def _dividends_position(conn: sqlite3.Connection, snapshot: dict) -> Position:
    """Suma dywidend WYPŁACONYCH w okresie wyciągu, po obu stronach grupowana po dniu
    wypłaty (`tax/dividends.py::payouts` — `pay_date` NIE jest unikalny, Computershare
    drukuje osobny wiersz na koszyk planu tej samej wypłaty, lekcja 0.17.2)."""
    period_start, period_end = snapshot.get("period_start"), snapshot.get("period_end")
    stmt_total = sum(r["gross_dividend_payment_eur"] for r in snapshot.get("dividends", []))
    if period_start is None or period_end is None:
        return _build_position(
            "dividends_in_period", "Dywidendy w okresie wyciągu", stmt_total, None,
            _DIVIDEND_TOLERANCE_EUR, note="brak zakresu okresu w wyciągu", force_no_data=True)
    payouts = taxdiv.payouts(conn)
    db_total = sum(
        p["gross_eur"] for p in payouts if period_start <= p["pay_date"] <= period_end)
    return _build_position(
        "dividends_in_period", "Dywidendy w okresie wyciągu", stmt_total, db_total,
        _DIVIDEND_TOLERANCE_EUR)


def _purchases_position(conn: sqlite3.Connection, snapshot: dict) -> Position:
    """Zakupy ESPP w okresie wyciągu, dopasowane po `natural_key` (`purchase:...`,
    identyczny wzór po obu stronach — patrz `_pending_tranches_position`)."""
    rows = snapshot.get("purchases", [])
    keys = [r["natural_key"] for r in rows]
    db_by_key = _lots_qty_by_natural_key(conn, keys)
    stmt_total = sum(r["quantity"] for r in rows)
    db_total = sum(db_by_key.get(k, 0.0) for k in keys)
    missing = [{"natural_key": k, "quantity": r["quantity"]}
               for k, r in zip(keys, rows) if k not in db_by_key]
    tolerance = _QTY_EPSILON * max(1, len(rows))
    return _build_position(
        "espp_purchases_in_period", "Zakupy ESPP w okresie wyciągu", stmt_total, db_total,
        tolerance, details=missing)


def _wtc_position(pending: list[dict]) -> Position:
    """Sprzedaże Withhold-to-Cover niepotwierdzone przez użytkownika — informacyjna, nie
    porównawcza (ich ilość jest już odjęta od pozycji „Akcje", żeby ta nie dawała
    fałszywej niezgodności). `status='no_data'` gdy cokolwiek wisi (świadomie nie
    'mismatch' — to nie jest rozjazd danych, tylko oczekująca decyzja użytkownika),
    `'ok'` gdy kolejka pusta."""
    if not pending:
        return Position("pending_wtc_sales", "Sprzedaże Withhold-to-Cover niepotwierdzone",
                        0.0, 0.0, 0.0, 0.0, "ok")
    return Position(
        "pending_wtc_sales", "Sprzedaże Withhold-to-Cover niepotwierdzone",
        None, None, None, 0.0, "no_data",
        note="odjęte od pozycji Akcje — potwierdź na /imports, żeby zniknęły stąd",
        details=pending)


def reconcile(conn: sqlite3.Connection, snapshot: dict) -> list[Position]:
    """Silnik uzgodnienia (E7, krok 3): jedna lista `Position` łącząca stronę wyciągu
    (`snapshot`, z `computershare_pdf.py::statement_snapshot`) ze stroną bazy
    zrekonstruowaną NA DZIEŃ `snapshot['as_of_date']` (funkcje wyżej w tym module)."""
    as_of = snapshot.get("as_of_date")
    if as_of is None:
        return []

    shares_db = shares_as_of(conn, as_of)
    wtc_pending = pending_wtc_sales_as_of(conn, as_of)
    shares_tol = shares_tolerance(conn, as_of)
    shares_total_db = None
    if shares_db is not None:
        shares_total_db = shares_db["total"] - sum(w["quantity"] for w in wtc_pending)
    positions = [_build_position(
        "shares", "Akcje", snapshot.get("shares_total"), shares_total_db, shares_tol,
        note="" if shares_db is not None
        else "naruszenie niezmiennika dat alokacji FIFO — patrz karta Spójność danych")]

    unvested_db = unvested_as_of(conn, as_of)
    tranche_rows = max(1, len(snapshot.get("pending_tranches", [])))
    restricted_tol = max(_RESTRICTED_UNITS_TOLERANCE_BASE,
                         _RESTRICTED_UNITS_TOLERANCE_PER_ROW * tranche_rows)
    restricted_total_db = unvested_db["total"]
    restricted_note = ""
    if restricted_total_db is not None and unvested_db["ambiguous_qty"] > restricted_tol:
        restricted_note = (
            f"{unvested_db['ambiguous_qty']:.4f} akcji ma przeterminowany, niepewny status "
            "vestingu (transza 'pending' z minioną datą dostępności) — zbyt duża "
            "niepewność, by policzyć pozycję")
        restricted_total_db = None
    elif unvested_db["ambiguous_qty"] > 0:
        restricted_note = (
            f"pomija {unvested_db['ambiguous_qty']:.4f} akcji o niepewnym statusie "
            "(poniżej tolerancji)")
    positions.append(_build_position(
        "restricted_units", "Transze oczekujące (RSU)", snapshot.get("restricted_units_total"),
        restricted_total_db, restricted_tol, note=restricted_note))

    stmt_shares, stmt_restricted = snapshot.get("shares_total"), snapshot.get("restricted_units_total")
    stmt_total = stmt_shares + stmt_restricted if None not in (stmt_shares, stmt_restricted) else None
    db_total = (shares_total_db + restricted_total_db
                if None not in (shares_total_db, restricted_total_db) else None)
    positions.append(_build_position(
        "total", "Suma (Akcje + RSU)", stmt_total, db_total, shares_tol + restricted_tol))

    positions.append(_pending_tranches_position(unvested_db, snapshot.get("pending_tranches", [])))
    positions.append(_dividends_position(conn, snapshot))
    positions.append(_purchases_position(conn, snapshot))
    positions.append(_wtc_position(wtc_pending))
    positions.append(Position(
        "broker_cash", "Gotówka u brokera", None, None, None, 0.0, "no_data",
        note="wyciąg Computershare nie zawiera salda gotówkowego — sekcja 'Assets by type' "
             "ma wyłącznie Shares/Restricted stock units (potwierdzone empirycznie)"))
    return positions
