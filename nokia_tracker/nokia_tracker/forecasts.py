"""Zapis prognoz AI i ich rozliczanie po target_date — MAPE jako
forecast_accuracy_pct. Uczciwość prognoz to feature, nie ozdoba (BLUEPRINT
§1): bez rozliczania realized_price prognozy LLM-a są nieweryfikowalną
narracją.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone


def record_forecast(conn: sqlite3.Connection, horizon: str, target_date: str,
                    price_at_creation: float, predicted_price: float, ci_low: float,
                    ci_high: float, confidence: float, model: str) -> None:
    conn.execute(
        "INSERT INTO forecasts (horizon, created_at, target_date, price_at_creation, "
        "predicted_price, ci_low, ci_high, confidence, model) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (horizon, datetime.now(timezone.utc).isoformat(), target_date, price_at_creation,
         predicted_price, ci_low, ci_high, confidence, model))
    conn.commit()


def settle_due(conn: sqlite3.Connection, current_price: float) -> int:
    """Rozlicza prognozy, których target_date już minął: zapisuje
    realized_price = current_price i error_pct = |realized-predicted|/realized*100
    (MAPE). Zwraca liczbę rozliczonych rekordów."""
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT id, predicted_price FROM forecasts "
        "WHERE target_date <= ? AND realized_price IS NULL", (today,)
    ).fetchall()
    for row in rows:
        error_pct = abs(current_price - row["predicted_price"]) / current_price * 100
        conn.execute(
            "UPDATE forecasts SET realized_price = ?, error_pct = ? WHERE id = ?",
            (current_price, error_pct, row["id"]))
    conn.commit()
    return len(rows)


def accuracy_pct(conn: sqlite3.Connection, n: int = 10) -> float | None:
    """100 - średni błąd % (MAPE) z ostatnich N rozliczonych prognoz (po
    target_date), albo None, gdy żadna nie została jeszcze rozliczona."""
    rows = conn.execute(
        "SELECT error_pct FROM forecasts WHERE realized_price IS NOT NULL "
        "ORDER BY target_date DESC LIMIT ?", (n,)
    ).fetchall()
    if not rows:
        return None
    mape = sum(r["error_pct"] for r in rows) / len(rows)
    return max(0.0, 100.0 - mape)
