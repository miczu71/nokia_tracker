"""Krzywa wartości portfela (krok 25, docs/PLAN_KROK_25_wyniki.md).

Rekonstrukcja BEZ sieci — czyta wyłącznie to, co już jest w bazie (loty,
alokacje sprzedaży, `quotes`, gęste `nbp_rates` z `fx_nbp.backfill_range()`).
`portfolio_history` jest materializowana i w pełni przeliczana od zera przy
każdym `rebuild()` — tańsze i bezpieczniejsze niż różnicowe update'y przy
imporcie/korekcie starych lotów (importer PDF potrafi cofnąć się w czasie).
"""
from __future__ import annotations

import sqlite3

from .. import quotes as quotesm


def _qty_events(conn: sqlite3.Connection) -> list[tuple[str, float]]:
    """`(data, delta_ilość)` posortowane chronologicznie — WSZYSTKIE typy
    lotu (`own`/`matched`/`lti`/`dividend_drip`), ta sama definicja
    `position_qty` co `portfolio.lots_based_position_values()`."""
    lot_rows = conn.execute("SELECT acquired_date, quantity FROM lots").fetchall()
    sale_rows = conn.execute(
        "SELECT s.sale_date AS d, sa.quantity AS q FROM sale_allocations sa "
        "JOIN sales s ON s.id = sa.sale_id").fetchall()
    events = [(r["acquired_date"], r["quantity"]) for r in lot_rows]
    events += [(r["d"], -r["q"]) for r in sale_rows]
    events.sort(key=lambda e: e[0])
    return events


def _nbp_rate_as_of(conn: sqlite3.Connection, d: str) -> float | None:
    row = conn.execute(
        "SELECT rate FROM nbp_rates WHERE effective_date <= ? "
        "ORDER BY effective_date DESC LIMIT 1", (d,)).fetchone()
    return row["rate"] if row else None


def rebuild(conn: sqlite3.Connection, instrument_id: int) -> int:
    """Przelicza `portfolio_history` od zera. Zwraca liczbę zapisanych
    wierszy (0, gdy nie ma jeszcze żadnego lotu — nic do rekonstrukcji)."""
    events = _qty_events(conn)
    conn.execute("DELETE FROM portfolio_history")
    if not events:
        conn.commit()
        return 0

    first_date = events[0][0]
    closes = quotesm.closes_in_range(conn, instrument_id, "daily")

    rows = 0
    cum_qty = 0.0
    event_idx = 0
    for ts, close in closes:
        d = ts[:10]
        if d < first_date:
            continue
        while event_idx < len(events) and events[event_idx][0] <= d:
            cum_qty += events[event_idx][1]
            event_idx += 1

        rate = _nbp_rate_as_of(conn, d)
        market_value_eur = cum_qty * close
        market_value_pln = market_value_eur * rate if rate is not None else None
        conn.execute(
            "INSERT INTO portfolio_history "
            "(date, position_qty, price_eur, eurpln_rate, market_value_eur, market_value_pln) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (d, cum_qty, close, rate, market_value_eur, market_value_pln))
        rows += 1
    conn.commit()
    return rows
