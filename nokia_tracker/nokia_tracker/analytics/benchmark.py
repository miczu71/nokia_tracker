"""Kontrfaktyczny benchmark (krok 25, docs/PLAN_KROK_25_wyniki.md).

„Gdyby te same wpłaty własne (co do dnia i kwoty) poszły w OMXH25/Ericsson
zamiast Nokii" — te same przepływy co `returns.build_xirr_cashflows()`
(tylko ujemne = realna gotówka wydana, wartość końcowa/sprzedaże ignorowane).
Dzienne notowania obu benchmarków już backfillowane 5 lat w `quotes` (0.1.0)
— zero nowych źródeł danych.
"""
from __future__ import annotations

import sqlite3


def _price_on_or_before(conn: sqlite3.Connection, instrument_id: int, d: str) -> float | None:
    row = conn.execute(
        "SELECT close FROM quotes WHERE instrument_id = ? AND granularity = 'daily' "
        "AND ts <= ? ORDER BY ts DESC LIMIT 1", (instrument_id, f"{d}T23:59:59")).fetchone()
    return row["close"] if row else None


def counterfactual(conn: sqlite3.Connection, own_cashflows: list[tuple[str, float]],
                   benchmark_instrument_id: int) -> float | None:
    """Wartość dziś, gdyby wpłaty `own` (ujemne przepływy) kupiły benchmark
    zamiast Nokii, po cenie zamknięcia z dnia każdej wpłaty (albo ostatniej
    znanej wcześniejszej, gdy dokładny dzień bez notowania). `None`, gdy
    brak jakiejkolwiek wpłaty własnej lub brak notowań benchmarku — nigdy
    nie zgaduje wartości."""
    units = 0.0
    for d, amount in own_cashflows:
        if amount >= 0:
            continue
        price = _price_on_or_before(conn, benchmark_instrument_id, d)
        if price is None:
            return None
        units += (-amount) / price

    if units == 0.0:
        return None

    latest_row = conn.execute(
        "SELECT close FROM quotes WHERE instrument_id = ? AND granularity = 'daily' "
        "ORDER BY ts DESC LIMIT 1", (benchmark_instrument_id,)).fetchone()
    if latest_row is None:
        return None
    return units * latest_row["close"]
