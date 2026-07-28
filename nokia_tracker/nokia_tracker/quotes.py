"""Orkiestracja providerów cen -> tabela quotes; backfill historii."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from .models import Candle
from .providers.base import QuoteProvider

logger = logging.getLogger(__name__)


def ensure_instrument(conn: sqlite3.Connection, symbol: str, name: str,
                      currency: str, role: str = "benchmark") -> int:
    """Get-or-create instrumentu; zwraca id."""
    row = conn.execute("SELECT id FROM instruments WHERE symbol = ?", (symbol,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO instruments (symbol, name, currency, role) VALUES (?, ?, ?, ?)",
        (symbol, name, currency, role))
    conn.commit()
    return cur.lastrowid


def upsert_candles(conn: sqlite3.Connection, instrument_id: int, granularity: str,
                   candles: list[Candle], source: str = "yahoo") -> int:
    """UPSERT (nie IGNORE) — dzisiejsza świeca dzienna jest prowizoryczna
    przed zamknięciem sesji i legalnie zmienia close przy kolejnym fetchu."""
    n = 0
    for c in candles:
        conn.execute(
            "INSERT INTO quotes (instrument_id, ts, granularity, open, high, low, "
            "close, volume, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(instrument_id, ts, granularity) DO UPDATE SET "
            "open=excluded.open, high=excluded.high, low=excluded.low, "
            "close=excluded.close, volume=excluded.volume, source=excluded.source",
            (instrument_id, c.ts, granularity, c.open, c.high, c.low, c.close,
             c.volume, source))
        n += 1
    conn.commit()
    return n


def backfill(conn: sqlite3.Connection, instrument_id: int, symbol: str,
            provider: QuoteProvider, years: int) -> int:
    """Pobiera i zapisuje historię dzienną za `years` lat wstecz."""
    since = (datetime.now(timezone.utc) - timedelta(days=365 * years)).date().isoformat()
    candles = provider.fetch(symbol, "daily", since=since)
    n = upsert_candles(conn, instrument_id, "daily", candles, source=provider.name)
    logger.info("Backfill %s: %d świec dziennych (%s lat)", symbol, n, years)
    return n


def has_history(conn: sqlite3.Connection, instrument_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM quotes WHERE instrument_id = ? AND granularity = 'daily' LIMIT 1",
        (instrument_id,)).fetchone()
    return row is not None


def refresh_recent_daily(conn: sqlite3.Connection, instrument_id: int, symbol: str,
                         provider: QuoteProvider, days: int = 5) -> int:
    """Lekki odśwież ostatnich `days` dni — tania alternatywa do pełnego
    backfill(), uruchamiana co poll_interval_minutes: dostarcza świeżą,
    wciąż-formującą się świecę dzisiejszą (high/low/volume/close rosną
    w trakcie sesji) bez ciągania 5 lat historii za każdym razem."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    candles = provider.fetch(symbol, "daily", since=since)
    return upsert_candles(conn, instrument_id, "daily", candles, source=provider.name)


def today_daily_candle(conn: sqlite3.Connection, instrument_id: int) -> dict | None:
    """Dzisiejsza świeca dzienna (UTC), jeśli już pobrana."""
    today = datetime.now(timezone.utc).date().isoformat()
    row = conn.execute(
        "SELECT * FROM quotes WHERE instrument_id = ? AND granularity = 'daily' "
        "AND ts LIKE ? ORDER BY ts DESC LIMIT 1",
        (instrument_id, f"{today}%")).fetchone()
    return dict(row) if row else None


def prev_daily_close(conn: sqlite3.Connection, instrument_id: int) -> float | None:
    """Zamknięcie sprzed najnowszej świecy dziennej (drugie od końca)."""
    rows = conn.execute(
        "SELECT close FROM quotes WHERE instrument_id = ? AND granularity = 'daily' "
        "ORDER BY ts DESC LIMIT 2", (instrument_id,)).fetchall()
    return rows[1]["close"] if len(rows) >= 2 else None


def week52_high_low(conn: sqlite3.Connection, instrument_id: int
                    ) -> tuple[float | None, float | None]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    row = conn.execute(
        "SELECT MAX(high) h, MIN(low) l FROM quotes WHERE instrument_id = ? AND "
        "granularity = 'daily' AND ts >= ? AND high IS NOT NULL AND low IS NOT NULL",
        (instrument_id, cutoff)).fetchone()
    return (row["h"], row["l"]) if row else (None, None)


def store_single_price(conn: sqlite3.Connection, instrument_id: int, price: float,
                       source: str, ts: str | None = None) -> None:
    """Zapisuje pojedynczy punkt (np. kurs ECB/NBP albo notowanie ADR z
    Finnhub) jako świecę dzienną — jedyne miejsce zapisu do `quotes`,
    żeby downstream (sensors.py) czytało wszystko jednym mechanizmem
    niezależnie od tego, który provider wygrał."""
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    upsert_candles(conn, instrument_id, "daily", [Candle(ts=ts, close=price)], source=source)


def refresh_live_price(conn: sqlite3.Connection, instrument_id: int, price: float,
                       source: str, fallback_ts_hour: int = 7) -> None:
    """Aktualizuje TYLKO close dzisiejszej świecy niezależnym, żywym źródłem
    (np. Avanza, patrz providers/avanza.py) — świadomie częściowy UPDATE, nie
    upsert_candles()/store_single_price(): te nadpisują open/high/low/volume
    na NULL przy konflikcie klucza, co dla FX/ADR jest nieszkodliwe (nikt nie
    czyta ich OHLC), ale dla instrumentu głównego zerowałoby day_high/day_low/
    volume zebrane przez Yahoo. Gdy dzisiejsza świeca jeszcze nie istnieje
    (np. Avanza zdążyła pierwsza), wstawia nową z samym close — Yahoo dopełni
    OHLC przy najbliższym własnym odświeżeniu (upsert_candles i tak nadpisuje
    tylko pola, które faktycznie dostarcza)."""
    today = today_daily_candle(conn, instrument_id)
    if today:
        conn.execute(
            "UPDATE quotes SET close = ?, source = ? "
            "WHERE instrument_id = ? AND ts = ? AND granularity = 'daily'",
            (price, source, instrument_id, today["ts"]))
        conn.commit()
    else:
        ts = (datetime.now(timezone.utc).date().isoformat()
             + f"T{fallback_ts_hour:02d}:00:00+00:00")
        upsert_candles(conn, instrument_id, "daily", [Candle(ts=ts, close=price)], source=source)


def refresh_intraday(conn: sqlite3.Connection, instrument_id: int, symbol: str,
                     provider: QuoteProvider) -> int:
    candles = provider.fetch(symbol, "intraday")
    return upsert_candles(conn, instrument_id, "intraday", candles, source=provider.name)


def latest_quote(conn: sqlite3.Connection, instrument_id: int,
                 granularity: str | None = None) -> dict | None:
    """Najnowsza świeca instrumentu (dowolnej granularności, chyba że podana)."""
    if granularity:
        row = conn.execute(
            "SELECT * FROM quotes WHERE instrument_id = ? AND granularity = ? "
            "ORDER BY ts DESC LIMIT 1", (instrument_id, granularity)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM quotes WHERE instrument_id = ? ORDER BY ts DESC LIMIT 1",
            (instrument_id,)).fetchone()
    return dict(row) if row else None


def daily_closes(conn: sqlite3.Connection, instrument_id: int, limit: int = 400
                 ) -> list[float]:
    """Ostatnie `limit` zamknięć dziennych, chronologicznie (najstarsze pierwsze) —
    wejście dla indicators.py."""
    rows = conn.execute(
        "SELECT close FROM quotes WHERE instrument_id = ? AND granularity = 'daily' "
        "ORDER BY ts DESC LIMIT ?", (instrument_id, limit)).fetchall()
    return [r["close"] for r in reversed(rows)]
