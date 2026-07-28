"""Loty i alokacja FIFO (BLUEPRINT §3a, krok 12).

Zasady, którymi rządzi się ten moduł:

- **Kurs NBP zamrożony raz na zawsze.** `add_lot`/`record_sale` łapią kurs
  przez `fx_nbp.rate_for_event` (art. 11a: dzień roboczy POPRZEDZAJĄCY
  zdarzenie) w momencie zapisu. `backfill_missing_rates` uzupełnia tylko
  wiersze, gdzie kurs jest jeszcze `NULL` — nigdy nie nadpisuje już
  ustawionej wartości.
- **`sale_allocations.cost_pln` to surowy koszt nabycia**, nie koszt po
  polityce. Filtr polityki (`own_only`/`own_plus_drip`/`all_at_acquisition`)
  nakłada się dopiero przy raportowaniu (`tax/policy.py`) — inaczej nie
  dałoby się policzyć trzech polityk równolegle z tych samych zapisów.
- **Brak pokrycia = wyjątek, nie ujemne `qty_remaining`.** Sprzedaż większa
  niż suma otwartych lotów podnosi `InsufficientLotsError` i nic w bazie
  się nie zmienia.
- **NBP niedostępne przy `add_lot` nie blokuje zapisu** — lot ląduje z
  `nbp_rate = NULL`/`cost_pln = NULL`, a `backfill_missing_rates` (job
  schedulera) domyka to później. Bez tego masowy import PDF-ów (krok 13)
  odpytywałby NBP synchronicznie dla każdego wiersza w trakcie requestu.
- **Porównania ilości z epsilonem** (`_EPS`), żeby resztki po podziale
  float (akcje ułamkowe) nie zostawiały „lotów-widm" z `qty_remaining`
  rzędu 1e-16.
"""
from __future__ import annotations

import sqlite3

from ..providers import fx_nbp

_EPS = 1e-9


class InsufficientLotsError(Exception):
    """Sprzedaż przekracza sumę `qty_remaining` dostępnych lotów."""


class CostBasisMissingError(Exception):
    """Lot lub sprzedaż nie ma jeszcze zamrożonego kursu NBP — uruchom
    `backfill_missing_rates` (lub poczekaj na job schedulera) zanim ten
    lot zostanie skonsumowany przez sprzedaż."""


def add_lot(conn: sqlite3.Connection, acquired_date: str, lot_type: str,
            quantity: float, price_eur: float, fee_eur: float = 0.0,
            source: str = "manual", natural_key: str | None = None,
            grant_id: int | None = None, notes: str | None = None) -> int:
    """Wstawia lot; idempotentne po `natural_key` (istniejący klucz zwraca
    id istniejącego lotu, nic nie wstawia ponownie — ten sam błąd zjadł
    depozyt w pv_roi 0.30.x, tu zapobiegamy mu od początku)."""
    if natural_key is not None:
        existing = conn.execute(
            "SELECT id FROM lots WHERE natural_key = ?", (natural_key,)).fetchone()
        if existing:
            return existing["id"]

    rate = fx_nbp.rate_for_event(conn, acquired_date)
    nbp_rate, nbp_rate_date = rate if rate else (None, None)
    cost_pln = (quantity * price_eur + fee_eur) * nbp_rate if nbp_rate is not None else None

    cur = conn.execute(
        "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur, fee_eur, "
        "nbp_rate, nbp_rate_date, cost_pln, grant_id, source, natural_key, "
        "qty_remaining, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (acquired_date, lot_type, quantity, price_eur, fee_eur, nbp_rate,
         nbp_rate_date, cost_pln, grant_id, source, natural_key, quantity, notes))
    conn.commit()
    return cur.lastrowid


def backfill_missing_rates(conn: sqlite3.Connection) -> int:
    """Uzupełnia `nbp_rate`/`nbp_rate_date`/`cost_pln` tam, gdzie kurs jest
    jeszcze `NULL`. Zwraca liczbę uzupełnionych lotów. Nigdy nie dotyka
    lotu, który już ma zamrożony kurs (filtr `nbp_rate IS NULL` też w
    samym UPDATE, na wypadek równoległego zapisu)."""
    rows = conn.execute(
        "SELECT id, acquired_date, quantity, price_eur, fee_eur FROM lots "
        "WHERE nbp_rate IS NULL").fetchall()
    filled = 0
    for row in rows:
        rate = fx_nbp.rate_for_event(conn, row["acquired_date"])
        if rate is None:
            continue
        nbp_rate, nbp_rate_date = rate
        cost_pln = (row["quantity"] * row["price_eur"] + row["fee_eur"]) * nbp_rate
        conn.execute(
            "UPDATE lots SET nbp_rate = ?, nbp_rate_date = ?, cost_pln = ? "
            "WHERE id = ? AND nbp_rate IS NULL",
            (nbp_rate, nbp_rate_date, cost_pln, row["id"]))
        filled += 1
    conn.commit()
    return filled


def open_lots(conn: sqlite3.Connection) -> list[dict]:
    """Loty z `qty_remaining > 0` (epsilon), w kolejności FIFO
    (`acquired_date`, potem `id` jako tie-break — NIE kolejność wstawienia)."""
    rows = conn.execute(
        "SELECT * FROM lots WHERE qty_remaining > ? ORDER BY acquired_date ASC, id ASC",
        (_EPS,)).fetchall()
    return [dict(r) for r in rows]


def lots_summary(conn: sqlite3.Connection) -> dict:
    """Sumy per `lot_type` do UI/sensorów. Koszt uznany WEDŁUG POLITYKI
    liczy `tax/policy.py` — ta funkcja daje tylko surowe ilości."""
    rows = conn.execute(
        "SELECT lot_type, COUNT(*) AS lot_count, SUM(quantity) AS qty_total, "
        "SUM(qty_remaining) AS qty_remaining FROM lots GROUP BY lot_type").fetchall()
    return {
        r["lot_type"]: {
            "lot_count": r["lot_count"],
            "qty_total": r["qty_total"] or 0.0,
            "qty_remaining": r["qty_remaining"] or 0.0,
        }
        for r in rows
    }


def record_sale(conn: sqlite3.Connection, sale_date: str, quantity: float,
                price_eur: float, fee_eur: float = 0.0) -> int:
    """Zapisuje sprzedaż z własnym zamrożonym kursem D-1 i konsumuje loty
    FIFO. Podnosi `InsufficientLotsError` (brak pokrycia, sprawdzone PRZED
    jakimkolwiek zapisem) lub `CostBasisMissingError` (lot/sprzedaż bez
    zamrożonego kursu NBP mimo backfillu) — w obu przypadkach baza
    zostaje nietknięta."""
    backfill_missing_rates(conn)

    available = open_lots(conn)
    total_available = sum(l["qty_remaining"] for l in available)
    if quantity - total_available > _EPS:
        raise InsufficientLotsError(
            f"Brak pokrycia: chcesz sprzedać {quantity}, dostępne {total_available}")

    rate = fx_nbp.rate_for_event(conn, sale_date)
    if rate is None:
        raise CostBasisMissingError(
            f"Brak kursu NBP dla dnia sprzedaży {sale_date} (spróbuj ponownie później)")
    nbp_rate, nbp_rate_date = rate
    revenue_pln = (quantity * price_eur - fee_eur) * nbp_rate

    try:
        cur = conn.execute(
            "INSERT INTO sales (sale_date, quantity, price_eur, fee_eur, nbp_rate, "
            "nbp_rate_date, revenue_pln) VALUES (?,?,?,?,?,?,?)",
            (sale_date, quantity, price_eur, fee_eur, nbp_rate, nbp_rate_date, revenue_pln))
        sale_id = cur.lastrowid
        _allocate_fifo(conn, sale_id, quantity, price_eur, fee_eur, nbp_rate)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return sale_id


def _allocate_fifo(conn: sqlite3.Connection, sale_id: int, sale_quantity: float,
                    price_eur: float, fee_eur: float, nbp_rate: float) -> None:
    remaining_to_allocate = sale_quantity
    candidates = conn.execute(
        "SELECT * FROM lots WHERE qty_remaining > ? ORDER BY acquired_date ASC, id ASC",
        (_EPS,)).fetchall()

    for lot in candidates:
        if remaining_to_allocate <= _EPS:
            break
        take = min(lot["qty_remaining"], remaining_to_allocate)
        if lot["cost_pln"] is None:
            raise CostBasisMissingError(
                f"Lot {lot['id']} nie ma zamrożonego kursu NBP mimo backfillu")

        per_share_cost_pln = lot["cost_pln"] / lot["quantity"]
        alloc_cost_pln = per_share_cost_pln * take
        fee_share_eur = fee_eur * (take / sale_quantity) if sale_quantity else 0.0
        alloc_revenue_pln = (take * price_eur - fee_share_eur) * nbp_rate

        conn.execute(
            "INSERT INTO sale_allocations (sale_id, lot_id, quantity, cost_pln, revenue_pln) "
            "VALUES (?,?,?,?,?)",
            (sale_id, lot["id"], take, alloc_cost_pln, alloc_revenue_pln))
        conn.execute(
            "UPDATE lots SET qty_remaining = ? WHERE id = ?",
            (lot["qty_remaining"] - take, lot["id"]))
        remaining_to_allocate -= take

    if remaining_to_allocate > _EPS:
        # Nie powinno się zdarzyć — sprawdzone w record_sale przed startem
        # transakcji — ale gdyby coś zmieniło loty między sprawdzeniem
        # a alokacją (np. przyszły import współbieżny), lepiej wywalić
        # wyjątek niż po cichu zaksięgować niepełną sprzedaż.
        raise InsufficientLotsError(
            "Pokrycie zmieniło się w trakcie alokacji — sprzedaż wycofana")
