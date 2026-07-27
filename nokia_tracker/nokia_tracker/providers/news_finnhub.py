"""Finnhub /company-news — newsy skorelowane z tickerem, opcjonalny klucz.

UWAGA: jak providers/finnhub.py (cena ADR) — brak klucza użytkownika,
zbudowane na udokumentowanym, stabilnym kształcie
(https://finnhub.io/docs/api/company-news), NIE zweryfikowane na żywo.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

import requests

from .. import cache, ratelimit
from .base import QuoteProviderError

_URL = "https://finnhub.io/api/v1/company-news"


def fetch(conn: sqlite3.Connection, symbol: str, api_key: str, days_back: int = 7,
         cache_ttl_seconds: int = 1800) -> list[dict] | None:
    if not api_key:
        return None

    to_date = date.today()
    from_date = to_date - timedelta(days=days_back)
    cache_key = f"{_URL}?symbol={symbol}&from={from_date}&to={to_date}"
    cached = cache.get(conn, cache_key, cache_ttl_seconds)
    if cached is not None:
        import json
        return _parse(json.loads(cached))

    def _do_request():
        return requests.get(_URL, params={
            "symbol": symbol, "from": from_date.isoformat(), "to": to_date.isoformat(),
            "token": api_key,
        }, timeout=15)

    resp = ratelimit.backoff_retry(_do_request, provider="finnhub")
    if resp is None:
        return None
    if resp.status_code == 401:
        return None
    if resp.status_code != 200:
        raise QuoteProviderError(f"Finnhub news {symbol}: HTTP {resp.status_code}")

    cache.set(conn, cache_key, resp.text)
    return _parse(resp.json())


def _parse(data: list) -> list[dict]:
    items = []
    for a in data or []:
        published = None
        ts = a.get("datetime")
        if ts:
            published = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        items.append({
            "title": a.get("headline", ""),
            "url": a.get("url", ""),
            "published_at": published,
            "summary": a.get("summary"),
            "source_name": a.get("source"),
        })
    return items
