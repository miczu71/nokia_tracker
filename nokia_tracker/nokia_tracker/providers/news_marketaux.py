"""MarketAux /v1/news/all — newsy z gotowym sentymentem (do porównania z
naszym w przyszłości), opcjonalny klucz, darmowy tier 100/dobę.

UWAGA: jak news_finnhub.py — brak klucza użytkownika, zbudowane na
udokumentowanym kształcie (https://www.marketaux.com/documentation),
NIE zweryfikowane na żywo.
"""
from __future__ import annotations

import sqlite3

import requests

from .. import cache, ratelimit
from .base import QuoteProviderError

_URL = "https://api.marketaux.com/v1/news/all"


def fetch(conn: sqlite3.Connection, symbols: str, api_key: str, limit: int = 20,
         cache_ttl_seconds: int = 1800) -> list[dict] | None:
    if not api_key:
        return None

    cache_key = f"{_URL}?symbols={symbols}&limit={limit}"
    cached = cache.get(conn, cache_key, cache_ttl_seconds)
    if cached is not None:
        import json
        return _parse(json.loads(cached))

    def _do_request():
        return requests.get(_URL, params={
            "symbols": symbols, "filter_entities": "true", "language": "en",
            "api_token": api_key, "limit": limit,
        }, timeout=15)

    resp = ratelimit.backoff_retry(_do_request, provider="marketaux")
    if resp is None:
        return None
    if resp.status_code in (401, 403):
        return None
    if resp.status_code != 200:
        raise QuoteProviderError(f"MarketAux {symbols}: HTTP {resp.status_code}")

    cache.set(conn, cache_key, resp.text)
    return _parse(resp.json())


def _parse(data: dict) -> list[dict]:
    items = []
    for d in data.get("data", []):
        items.append({
            "title": d.get("title", ""),
            "url": d.get("url", ""),
            "published_at": d.get("published_at"),
            "summary": d.get("snippet") or d.get("description"),
            "source_name": d.get("source"),
        })
    return items
