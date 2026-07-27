"""Finnhub /quote — ADR Nokii (NOK, NYSE) jako fallback live po zamknięciu
sesji w Helsinkach (BLUEPRINT §1). Opcjonalny: bez finnhub_api_key sensory
adr_price_usd/spread_vs_adr po prostu zostają 'unknown'.

UWAGA: w przeciwieństwie do Yahoo/ECB/NBP (zweryfikowane na żywo
2026-07-27), ten moduł budowany jest na udokumentowanym, stabilnym od lat
kształcie odpowiedzi (https://finnhub.io/docs/api/quote) — użytkownik nie
dostarczył klucza, więc nie dało się przetestować na żywym API. Kształt:
{"c": kurs, "h": high, "l": low, "o": open, "pc": prev_close, "t": epoch}.
"""
from __future__ import annotations

import logging
import sqlite3

import requests

from .. import cache, ratelimit
from .base import QuoteProviderError

logger = logging.getLogger(__name__)

_URL = "https://finnhub.io/api/v1/quote"


def fetch_quote(conn: sqlite3.Connection, symbol: str, api_key: str,
                cache_ttl_seconds: int = 300) -> dict | None:
    """Zwraca {'price','high','low','open','prev_close'} albo None (brak
    klucza / błąd — nigdy nie podnosi wyjątku na brak klucza, bo provider
    jest opcjonalny)."""
    if not api_key:
        return None

    cache_key = f"{_URL}?symbol={symbol}"
    cached = cache.get(conn, cache_key, cache_ttl_seconds)
    if cached is not None:
        import json
        return _parse(json.loads(cached))

    def _do_request():
        return requests.get(_URL, params={"symbol": symbol, "token": api_key}, timeout=15)

    resp = ratelimit.backoff_retry(_do_request, provider="finnhub")
    if resp is None:
        return None
    if resp.status_code == 401:
        logger.warning("Finnhub: nieprawidłowy klucz API")
        return None
    if resp.status_code != 200:
        raise QuoteProviderError(f"Finnhub {symbol}: HTTP {resp.status_code}")

    cache.set(conn, cache_key, resp.text)
    return _parse(resp.json())


def _parse(data: dict) -> dict | None:
    price = data.get("c")
    if price is None or price == 0:
        # Finnhub zwraca same zera dla nieznanego/nieaktywnego symbolu
        # zamiast błędu HTTP — traktujemy to jak brak danych.
        return None
    return {
        "price": float(price),
        "high": data.get("h"),
        "low": data.get("l"),
        "open": data.get("o"),
        "prev_close": data.get("pc"),
    }
