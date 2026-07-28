"""GDELT DOC 2.0 — globalna tonalność/wolumen wzmianek, bez klucza
(BLUEPRINT §1). Zweryfikowane na żywo 2026-07-27 (fixture gdelt_nokia.json).

UWAGA: limit GDELT to 1 zapytanie / 5 sekund (zmierzone empirycznie — 429
przy szybszych ponowieniach), stąd dłuższy domyślny cache_ttl_seconds niż
w news_rss.py i wywołanie przez ratelimit.backoff_retry (429 w
retryable_statuses).

UWAGA 2 (2026-07-28): 429 bywa też blokadą na poziomie IP, niezależną od
naszego tempa zapytań — zmierzone empirycznie z zewnątrz add-onu: curl co
6s (powyżej deklarowanego limitu 1/5s) i tak dostawał 429. W takiej sytuacji
ponawianie co cykl (co 30 min) tylko marnuje czas i zaśmieca logi
tracebackiem. Stąd cooldown po wyczerpanych ponowieniach: jeden zapis do
http_cache jako znacznik, źródło samo wraca po _COOLDOWN_SECONDS bez
udziału ratelimit._consecutive_failures (ten mechanizm jest per-proces i
nie przeżywa restartu, a add-ony restartują się często)."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from .. import cache, ratelimit
from .base import QuoteProviderError

logger = logging.getLogger(__name__)

_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

_COOLDOWN_KEY = "gdelt:cooldown"
_COOLDOWN_SECONDS = 6 * 3600


def fetch(conn: sqlite3.Connection, query: str, max_records: int = 20,
         cache_ttl_seconds: int = 1800,
         cooldown_seconds: int = _COOLDOWN_SECONDS) -> list[dict]:
    """Zwraca listę {'title','url','published_at','summary','source_name'}.

    Gdy GDELT jest w cooldownie po wcześniejszej porażce, zwraca []
    (no-op) zamiast bić głową w tę samą blokadę co cykl."""
    if cache.get(conn, _COOLDOWN_KEY, cooldown_seconds) is not None:
        logger.warning("GDELT w cooldownie po wcześniejszym błędzie, pomijam")
        return []

    cache_key = f"{_URL}?query={quote(query)}&maxrecords={max_records}"
    cached = cache.get(conn, cache_key, cache_ttl_seconds)
    if cached is not None:
        import json
        return _parse(json.loads(cached))

    def _do_request():
        return requests.get(_URL, params={
            "query": query, "mode": "artlist", "maxrecords": max_records,
            "format": "json",
        }, timeout=20)

    resp = ratelimit.backoff_retry(_do_request, provider="gdelt",
                                   max_attempts=2, base_delay=5.0,
                                   retryable_statuses=(429, 502, 503))
    if resp is None or resp.status_code != 200:
        code = resp.status_code if resp is not None else "brak odpowiedzi"
        cache.set(conn, _COOLDOWN_KEY, str(code))
        raise QuoteProviderError(f"GDELT: HTTP {code}")

    cache.set(conn, cache_key, resp.text)
    return _parse(resp.json())


def _parse(data: dict) -> list[dict]:
    items = []
    for a in data.get("articles", []):
        published = None
        seendate = a.get("seendate")
        if seendate:
            try:
                published = datetime.strptime(
                    seendate, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                published = None
        items.append({
            "title": a.get("title", ""),
            "url": a.get("url", ""),
            "published_at": published,
            "summary": None,
            "source_name": a.get("domain"),
        })
    return items
