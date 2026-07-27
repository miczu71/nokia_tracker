"""Jedno miejsce: składa wartości wszystkich sensorów z danych w bazie.

Czyste (bez sieci) — czyta wyłącznie z SQLite, żeby dało się testować bez
mockowania HTTP. Krok 3: tylko rynek + technika. Benchmark/newsy/AI/portfel
dochodzą w kolejnych krokach jako kolejne funkcje _*_values() łączone tu.
"""
from __future__ import annotations

import sqlite3

from . import indicators as ind
from . import market, quotes


def market_values(conn: sqlite3.Connection, instrument_id: int) -> dict:
    """Sensory grupy 'Rynek' + 'Technika' + binary_sensor market_open."""
    latest = quotes.latest_quote(conn, instrument_id)
    today = quotes.today_daily_candle(conn, instrument_id)
    prev_close = quotes.prev_daily_close(conn, instrument_id)
    week52_high, week52_low = quotes.week52_high_low(conn, instrument_id)
    closes = quotes.daily_closes(conn, instrument_id)

    price = latest["close"] if latest else None
    change_abs = (price - prev_close) if (price is not None and prev_close) else None
    change_pct = (change_abs / prev_close * 100) if (change_abs is not None and prev_close) else None

    is_open = market.is_session_open()

    return {
        "price_eur": price,
        "change_pct_day": change_pct,
        "change_abs_day": change_abs,
        "day_high": today["high"] if today else None,
        "day_low": today["low"] if today else None,
        "prev_close": prev_close,
        "volume": today["volume"] if today else None,
        "week52_high": week52_high,
        "week52_low": week52_low,
        "market_state": "sesja otwarta" if is_open else "sesja zamknięta",
        "last_quote_ts": latest["ts"] if latest else None,
        "sma_20": ind.sma(closes, 20),
        "sma_50": ind.sma(closes, 50),
        "rsi_14": ind.rsi(closes, 14),
        "volatility_30d_pct": ind.volatility_pct(closes, 30),
        "trend": ind.trend(closes),
        "market_open": is_open,
    }
