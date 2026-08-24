"""Yahoo Finance v10 quoteSummary — konsensus analityków (cena docelowa +
rating) dla NOKIA.HE, obok prognozy AI (docs/PLAN_0_26_0_konsensus.md).

W odróżnieniu od providers/yahoo.py (v8 /chart, bez autoryzacji), v10
/quoteSummary wymaga pary cookie+crumb — zweryfikowane na żywo 2026-08-24:
  1. GET https://fc.yahoo.com -> HTTP 404 (OCZEKIWANY!), ustawia cookies A1/A3
  2. GET .../v1/test/getcrumb z tymi cookies -> crumb
  3. GET .../v10/finance/quoteSummary/{symbol}?...&crumb=... z TYMI SAMYMI
     cookies -> 200; ten sam crumb BEZ cookie -> 401 "Invalid Crumb"

Każde wywołanie robi pełny handshake od nowa (świeża requests.Session()) —
prościej niż trzymać cookies+crumb w stanie modułu między wywołaniami, a
job odpala się raz dziennie, więc koszt (2 dodatkowe round-tripy) jest
nieistotny. Na 401 (crumb odrzucony w trakcie) — jeden ponowny PEŁNY
handshake, nie zwykły retry na tym samym crumbie (ratelimit.backoff_retry
tego by nie załatwił, bo nie generuje nowego crumba).

Kształt odpowiedzi zweryfikowany na żywo dla NOKIA.HE (NIE 'NOK' — ADR
nowojorski zwraca zupełnie inne liczby w innej walucie), patrz
tests/fixtures/yahoo_quotesummary_nokia_he.json.
"""
from __future__ import annotations

import json
import logging
import sqlite3

import requests

from .. import cache, ratelimit
from ..models import AnalystConsensus
from .base import QuoteProviderError

logger = logging.getLogger(__name__)

_FC_URL = "https://fc.yahoo.com"
_CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
_QUOTE_SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
_MODULES = "financialData,recommendationTrend"


def fetch_consensus(conn: sqlite3.Connection, symbol: str,
                    cache_ttl_seconds: int = 21600) -> AnalystConsensus | None:
    """Zwraca konsensus analityków dla `symbol` (MUSI być notowaniem
    helsińskim, np. 'NOKIA.HE' — wołający odpowiada za symbol, ten moduł go
    nie zgaduje) albo None (pusty wynik / brak pokrycia analityków). Podnosi
    QuoteProviderError przy trwałej awarii HTTP — kształt API jest
    udokumentowany i zweryfikowany na żywo, więc traktujemy go jak Finnhub,
    nie jak opcjonalną Avanzę."""
    cache_key = f"{_QUOTE_SUMMARY_URL.format(symbol=symbol)}?modules={_MODULES}"
    cached = cache.get(conn, cache_key, cache_ttl_seconds)
    if cached is not None:
        return _parse(json.loads(cached))

    resp = _request_with_crumb_retry(symbol)
    if resp is None or resp.status_code != 200:
        code = resp.status_code if resp is not None else "brak odpowiedzi"
        raise QuoteProviderError(f"Yahoo consensus {symbol}: HTTP {code}")

    cache.set(conn, cache_key, resp.text)
    return _parse(resp.json())


def _request_with_crumb_retry(symbol: str):
    resp = ratelimit.backoff_retry(lambda: _handshake_and_fetch(symbol),
                                   provider="yahoo_analyst")
    if resp is not None and resp.status_code == 401:
        logger.info("Yahoo consensus %s: 401 (crumb odrzucony), ponawiam pełny handshake",
                   symbol)
        resp = ratelimit.backoff_retry(lambda: _handshake_and_fetch(symbol),
                                       provider="yahoo_analyst")
    return resp


def _handshake_and_fetch(symbol: str):
    session = requests.Session()
    session.headers["User-Agent"] = _USER_AGENT
    session.get(_FC_URL, timeout=15)  # 404 oczekiwany — ustawia cookies A1/A3
    crumb = session.get(_CRUMB_URL, timeout=15).text.strip()
    url = _QUOTE_SUMMARY_URL.format(symbol=symbol)
    return session.get(url, params={"modules": _MODULES, "crumb": crumb}, timeout=15)


def _parse(data: dict) -> AnalystConsensus | None:
    result = data.get("quoteSummary", {}).get("result")
    if not result:
        return None
    fd = result[0].get("financialData", {}) or {}
    mean = _raw(fd.get("targetMeanPrice"))
    if mean is None:
        return None
    trend_list = (result[0].get("recommendationTrend", {}) or {}).get("trend", [])
    return AnalystConsensus(
        low=_raw(fd.get("targetLowPrice")),
        mean=mean,
        median=_raw(fd.get("targetMedianPrice")),
        high=_raw(fd.get("targetHighPrice")),
        n_analysts=_raw(fd.get("numberOfAnalystOpinions")),
        rating=fd.get("recommendationKey"),
        currency=fd.get("financialCurrency") or "EUR",
        source="yahoo",
        trend=trend_list[0] if trend_list else None,
    )


def _raw(value):
    """Pola Yahoo bywają `{"raw": ..., "fmt": "..."}` albo gołą liczbą —
    zależnie od pola; zwraca liczbę w obu przypadkach."""
    if isinstance(value, dict):
        return value.get("raw")
    return value
