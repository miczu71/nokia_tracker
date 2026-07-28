"""Avanza market-guide API — dodatkowe, niezależne źródło ŻYWEJ ceny dla
instrumentu głównego (BLUEPRINT §1, decyzja użytkownika po zdiagnozowanym
bugu zamrożenia price_eur w 0.1.0, patrz CHANGELOG 0.1.1).

Publiczne API, bez klucza: `https://www.avanza.se/_api/market-guide/stock/{orderbookId}`.
UWAGA: starszy, szeroko udokumentowany w internecie endpoint
`_mobile/market/stock/{id}` jest martwy — zwraca dziś samą aplikację SPA
(HTML), nie JSON. Ten endpoint to ten sam, którego realnie używa
zainstalowana w tym HA integracja `avanza_stock` (paczka `pyavanza`,
`AVANZA_API_STOCK_URL`), zweryfikowany na żywo 2026-07-28 dla orderbookId
Nokii (52784) — kształt w tests/fixtures/avanza_stock_nokia.json.

Rola ograniczona do ŻYWEJ CENY (quotes.py::refresh_live_price()) — to NIE
jest QuoteProvider z historią OHLC do backfillu/wskaźników, tylko bieżący
punkt. Historia i benchmarki (Ericsson/OMXH25/EURUSD) zostają wyłącznie na
Yahoo.

Świadome odstępstwo od stylu providers/finnhub.py: `fetch_quote()` NIGDY
nie podnosi wyjątku, nawet przy nieoczekiwanym kodzie HTTP — tylko loguje
ostrzeżenie i zwraca None. Finnhub ma udokumentowane, stabilne API;
Avanza `_api` jest całkowicie nieoficjalne i może zmienić kształt bez
zapowiedzi, a to źródło jest opcjonalne/dodatkowe — jego awaria nie może
ubić reszty publish_sensors() (patrz main.py, gdzie wywołanie i tak jest
dodatkowo owinięte we własny try/except dla pewności)."""
from __future__ import annotations

import json
import logging
import sqlite3

import requests

from .. import cache, ratelimit

logger = logging.getLogger(__name__)

_URL = "https://www.avanza.se/_api/market-guide/stock/{orderbook_id}"
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"


def fetch_quote(conn: sqlite3.Connection, orderbook_id: str,
                cache_ttl_seconds: int = 300) -> dict | None:
    """Zwraca {'price','high','low','prev_close','updated_ms'} albo None —
    brak orderbook_id, błąd sieci/HTTP albo nieoczekiwany kształt odpowiedzi
    nigdy nie podnoszą wyjątku, bo provider jest opcjonalny/dodatkowy."""
    if not orderbook_id:
        return None

    url = _URL.format(orderbook_id=orderbook_id)
    cached = cache.get(conn, url, cache_ttl_seconds)
    if cached is not None:
        return _parse(json.loads(cached))

    def _do_request():
        return requests.get(url, headers={"User-Agent": _USER_AGENT,
                                          "Accept": "application/json"}, timeout=15)

    resp = ratelimit.backoff_retry(_do_request, provider="avanza")
    if resp is None:
        return None
    if resp.status_code != 200:
        logger.warning("Avanza %s: HTTP %s", orderbook_id, resp.status_code)
        return None

    cache.set(conn, url, resp.text)
    return _parse(resp.json())


def _parse(data: dict) -> dict | None:
    quote = data.get("quote") or {}
    price = quote.get("last")
    if price is None:
        return None
    change = quote.get("change")
    return {
        "price": float(price),
        "high": quote.get("highest"),
        "low": quote.get("lowest"),
        "prev_close": (float(price) - change) if change is not None else None,
        "updated_ms": quote.get("updated"),
    }
