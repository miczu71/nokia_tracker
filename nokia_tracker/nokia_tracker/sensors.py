"""Jedno miejsce: składa wartości wszystkich sensorów z danych w bazie.

Czyste (bez sieci) — czyta wyłącznie z SQLite, żeby dało się testować bez
mockowania HTTP. Newsy/AI/portfel dochodzą w kolejnych krokach jako
kolejne funkcje _*_values() łączone tu.
"""
from __future__ import annotations

import sqlite3

from . import indicators as ind
from . import market, quotes

# Progi alpha_verdict: rel_perf_1d_vs_omxh25 w punktach procentowych.
# Świadome uproszczenie: |różnica| liczona wprost, bez normalizacji betą —
# to szybki, czytelny sygnał kierunkowy, nie precyzyjny rozkład ryzyka.
_ALPHA_MARKET_THRESHOLD = 0.5
_ALPHA_COMPANY_THRESHOLD = 2.0


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


def _pct_change(closes: list[float], sessions_back: int) -> float | None:
    if len(closes) <= sessions_back:
        return None
    a, b = closes[-1], closes[-1 - sessions_back]
    if b == 0:
        return None
    return (a - b) / b * 100


def benchmark_values(conn: sqlite3.Connection, primary_id: int, ericsson_id: int,
                     omxh25_id: int, eurpln_id: int, adr_id: int | None = None,
                     eurusd_id: int | None = None) -> dict:
    """Sensory grupy 'Benchmark' + bliźniaki PLN + ADR.

    Uwaga: serie Nokii/Ericssona/OMXH25 wyrównywane POZYCYJNIE (ostatnie N
    sesji), nie po dacie — Sztokholm i Helsinki mają niemal identyczny,
    ale nie w 100% tożsamy kalendarz świąt giełdowych. Świadome
    uproszczenie 0.1.0, ten sam kompromis co market.py (BLUEPRINT §1).
    """
    nokia_closes = quotes.daily_closes(conn, primary_id)
    ericsson_closes = quotes.daily_closes(conn, ericsson_id)
    omxh25_closes = quotes.daily_closes(conn, omxh25_id)

    ericsson_price = ericsson_closes[-1] if ericsson_closes else None
    omxh25_value = omxh25_closes[-1] if omxh25_closes else None

    nokia_1d = _pct_change(nokia_closes, 1)
    omxh25_1d = _pct_change(omxh25_closes, 1)
    rel_perf_1d = (nokia_1d - omxh25_1d) if (nokia_1d is not None and omxh25_1d is not None) else None

    nokia_1m = _pct_change(nokia_closes, 21)  # ~21 sesji = miesiąc handlowy
    ericsson_1m = _pct_change(ericsson_closes, 21)
    rel_perf_1m = ((nokia_1m - ericsson_1m)
                  if (nokia_1m is not None and ericsson_1m is not None) else None)

    nokia_returns = ind.daily_returns(nokia_closes)
    omxh25_returns = ind.daily_returns(omxh25_closes)
    beta_60d = ind.beta(nokia_returns[-60:], omxh25_returns[-60:])

    if rel_perf_1d is None:
        alpha_verdict = None
    elif abs(rel_perf_1d) < _ALPHA_MARKET_THRESHOLD:
        alpha_verdict = "trend rynkowy"
    elif abs(rel_perf_1d) > _ALPHA_COMPANY_THRESHOLD:
        alpha_verdict = "specyficzne dla spółki"
    else:
        alpha_verdict = "mieszane"

    eurpln_latest = quotes.latest_quote(conn, eurpln_id, granularity="daily")
    eurpln_rate = eurpln_latest["close"] if eurpln_latest else None
    price_eur = nokia_closes[-1] if nokia_closes else None
    price_pln = (price_eur * eurpln_rate) if (price_eur is not None and eurpln_rate) else None

    adr_price_usd = None
    spread_vs_adr = None
    if adr_id is not None:
        adr_latest = quotes.latest_quote(conn, adr_id, granularity="daily")
        adr_price_usd = adr_latest["close"] if adr_latest else None
        if adr_price_usd and price_eur and eurusd_id is not None:
            eurusd_latest = quotes.latest_quote(conn, eurusd_id, granularity="daily")
            eurusd_rate = eurusd_latest["close"] if eurusd_latest else None
            if eurusd_rate:
                # ADR (USD) -> implikowany kurs EUR na Helsinkach; różnica
                # względem realnego price_eur pokazuje rozjazd sesji
                # amerykańskiej z europejską (po zamknięciu Helsinek jedyny
                # żywy sygnał, patrz BLUEPRINT §1).
                implied_eur = adr_price_usd / eurusd_rate
                spread_vs_adr = (price_eur - implied_eur) / price_eur * 100

    return {
        "ericsson_price": ericsson_price,
        "omxh25_value": omxh25_value,
        "rel_perf_1d_vs_omxh25": rel_perf_1d,
        "rel_perf_1m_vs_ericsson": rel_perf_1m,
        "beta_60d": beta_60d,
        "alpha_verdict": alpha_verdict,
        "eurpln_rate": eurpln_rate,
        "price_pln": price_pln,
        "adr_price_usd": adr_price_usd,
        "spread_vs_adr": spread_vs_adr,
    }
