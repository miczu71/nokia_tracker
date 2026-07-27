"""Generyczny parser RSS/Atom (feedparser) — działa z DOWOLNYM dobrze
sformowanym feedem (Google News, Nokia IR, Kauppalehti, Yle...), bo lista
źródeł żyje w tabeli news_sources, edytowalna w UI bez nowego wydania
(BLUEPRINT §1). Zweryfikowane na żywo 2026-07-27 na Google News RSS
(fixture google_news_rss_nokia.xml).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import feedparser
import requests

from .. import cache, ratelimit
from .base import QuoteProviderError


def fetch(conn: sqlite3.Connection, url: str, cache_ttl_seconds: int = 1800
         ) -> list[dict]:
    """Zwraca listę {'title','url','published_at','summary','source_name'}."""
    text = cache.get(conn, url, cache_ttl_seconds)
    if text is None:
        def _do_request():
            return requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)

        resp = ratelimit.backoff_retry(_do_request, provider="rss")
        if resp is None or resp.status_code != 200:
            code = resp.status_code if resp is not None else "brak odpowiedzi"
            raise QuoteProviderError(f"RSS {url}: HTTP {code}")
        text = resp.text
        cache.set(conn, url, text)

    parsed = feedparser.parse(text)
    items = []
    for e in parsed.entries:
        published = None
        if getattr(e, "published_parsed", None):
            published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc).isoformat()
        source = getattr(e, "source", None)
        items.append({
            "title": e.get("title", ""),
            "url": e.get("link", ""),
            "published_at": published,
            "summary": e.get("summary"),
            "source_name": getattr(source, "title", None) if source else None,
        })
    return items
