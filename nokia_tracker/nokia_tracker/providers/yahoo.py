"""Yahoo Finance v8 chart API — primary provider cen (BLUEPRINT §1).

Nieoficjalne API: brak klucza, ale wymaga User-Agent, ma limit 429 i bywa
niestabilne — stąd cache.py (TTL) i ratelimit.backoff_retry (429/502).
Kształt odpowiedzi zweryfikowany na żywym NOKIA.HE 2026-07-27, patrz
tests/fixtures/yahoo_chart_nokia_5d.json (prawdziwa odpowiedź) i
yahoo_chart_error_404.json (kształt błędu dla złego symbolu).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

import requests

from .. import cache, ratelimit
from ..models import Candle
from .base import QuoteProvider, QuoteProviderError

logger = logging.getLogger(__name__)

_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

# Zakresy dozwolone przez API, od najkrótszego — pierwszy, który mieści
# żądaną liczbę dni, wygrywa.
_RANGE_DAYS = [
    ("5d", 5), ("1mo", 31), ("3mo", 93), ("6mo", 186), ("1y", 366),
    ("2y", 732), ("5y", 1830), ("10y", 3660), ("max", None),
]


def _pick_daily_range(since: str | None) -> str:
    if since is None:
        return "5y"
    try:
        since_date = datetime.fromisoformat(since).date()
    except ValueError:
        return "5y"
    days = (datetime.now(timezone.utc).date() - since_date).days
    for label, max_days in _RANGE_DAYS:
        if max_days is None or days <= max_days:
            return label
    return "max"


class YahooQuoteProvider(QuoteProvider):
    name = "yahoo"

    def __init__(self, conn: sqlite3.Connection, cache_ttl_seconds: int = 300) -> None:
        self._conn = conn
        self._cache_ttl = cache_ttl_seconds

    def fetch(self, symbol: str, granularity: str, since: str | None = None
              ) -> list[Candle]:
        if granularity == "intraday":
            interval, range_ = "5m", "1d"
        else:
            interval, range_ = "1d", _pick_daily_range(since)

        url = _BASE_URL.format(symbol=symbol)
        params = {"interval": interval, "range": range_}
        full_url = f"{url}?interval={interval}&range={range_}"

        cached = cache.get(self._conn, full_url, self._cache_ttl)
        if cached is not None:
            return self._parse(json.loads(cached), symbol)

        def _do_request():
            return requests.get(url, params=params,
                                headers={"User-Agent": _USER_AGENT}, timeout=15)

        resp = ratelimit.backoff_retry(_do_request, provider=self.name)
        if resp is None or resp.status_code != 200:
            code = resp.status_code if resp is not None else "brak odpowiedzi"
            raise QuoteProviderError(f"Yahoo {symbol}: HTTP {code}")

        cache.set(self._conn, full_url, resp.text)
        return self._parse(resp.json(), symbol)

    @staticmethod
    def _parse(data: dict, symbol: str) -> list[Candle]:
        chart = data.get("chart", {})
        if chart.get("error"):
            raise QuoteProviderError(
                f"Yahoo {symbol}: {chart['error'].get('description', chart['error'])}")
        results = chart.get("result")
        if not results:
            raise QuoteProviderError(f"Yahoo {symbol}: pusty wynik")

        result = results[0]
        timestamps = result.get("timestamp", [])
        quote = result.get("indicators", {}).get("quote", [{}])[0]
        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])
        volumes = quote.get("volume", [])

        candles: list[Candle] = []
        for i, ts in enumerate(timestamps):
            close = closes[i] if i < len(closes) else None
            if close is None:
                # Dziura w danych (częste na granicach sesji/świąt) —
                # świeca bez close jest bezużyteczna, pomijamy.
                continue
            candles.append(Candle(
                ts=datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                close=float(close),
                open=_maybe_float(opens, i),
                high=_maybe_float(highs, i),
                low=_maybe_float(lows, i),
                volume=_maybe_float(volumes, i),
            ))
        return candles


def _maybe_float(values: list, i: int) -> float | None:
    if i >= len(values) or values[i] is None:
        return None
    return float(values[i])
