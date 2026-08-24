"""stockanalysis.com — fallback konsensusu analityków, gdy Yahoo (główne
źródło, providers/yahoo_analyst.py) zawiedzie i użytkownik świadomie
włączył `allow_scrape_fallback` (docs/PLAN_0_26_0_konsensus.md §2). Ustawienie
istniało już od dawna (config.yaml/settings.py), zero konsumentów — ten
moduł jest jego pierwszym.

Bez klucza, bez crumba — zwykłe GET + parsowanie HTML (`beautifulsoup4`, już
w requirements.txt). Zweryfikowane na żywo 2026-08-24: liczby zgadzają się
co do centa z Yahoo tego samego dnia (4,65 / 10,32 / 10,13 / 18,00 EUR,
konsensus „Hold"), patrz tests/fixtures/stockanalysis_nokia_forecast.html.

Zasada „parser milknie, nie rzuca": strona jest niedokumentowanym HTML,
może zmienić strukturę bez zapowiedzi (redesign) — nierozpoznany kształt
zwraca None + log warning, NIGDY wyjątek. Błąd transportowy (HTTP) rzuca
QuoteProviderError jak pozostałe providery (retry/circuit breaker przez
ratelimit.backoff_retry ma sens tylko dla awarii sieci, nie dla redesignu)."""
from __future__ import annotations

import logging
import re
import sqlite3

import requests
from bs4 import BeautifulSoup

from .. import cache, ratelimit
from ..models import AnalystConsensus
from .base import QuoteProviderError

logger = logging.getLogger(__name__)

_URL = "https://stockanalysis.com/quote/{path}/forecast/"
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# Symbol wewnętrzny (instruments.symbol) -> ścieżka stockanalysis.com
# (giełda/ticker). Nieznany symbol = None bez sieci, zamiast zgadywania
# złego mapowania giełda/ticker.
_SYMBOL_PATHS = {"NOKIA.HE": "hel/NOKIA"}

_CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP"}

_ANALYSTS_RE = re.compile(
    r'According to (\d+) analysts polled by[^.]*?consensus rating of "(\w+)"')


def fetch_consensus(conn: sqlite3.Connection, symbol: str,
                    cache_ttl_seconds: int = 21600) -> AnalystConsensus | None:
    path = _SYMBOL_PATHS.get(symbol)
    if path is None:
        return None

    url = _URL.format(path=path)
    cached = cache.get(conn, url, cache_ttl_seconds)
    if cached is not None:
        return _parse(cached)

    def _do_request():
        return requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=15)

    resp = ratelimit.backoff_retry(_do_request, provider="stockanalysis")
    if resp is None or resp.status_code != 200:
        code = resp.status_code if resp is not None else "brak odpowiedzi"
        raise QuoteProviderError(f"stockanalysis {symbol}: HTTP {code}")

    cache.set(conn, url, resp.text)
    return _parse(resp.text)


def _parse(html: str) -> AnalystConsensus | None:
    try:
        soup = BeautifulSoup(html, "html.parser")
        header_row = soup.find("th", string="Low")
        price_row = header_row.find_parent("table").find("tbody").find("tr")
        cells = price_row.find_all("td")
        low, mean, median, high = (_parse_money(c.text) for c in cells[1:5])
        currency = _CURRENCY_SYMBOLS.get(cells[1].text.strip()[0])

        m = _ANALYSTS_RE.search(html)
        n_analysts = int(m.group(1)) if m else None
        rating = m.group(2) if m else None

        if mean is None or currency is None:
            raise ValueError("brak kluczowych pól w tabeli Price Target")
    except (AttributeError, ValueError, IndexError):
        logger.warning("stockanalysis: nierozpoznana struktura strony (redesign?)",
                       exc_info=True)
        return None

    return AnalystConsensus(
        low=low, mean=mean, median=median, high=high, n_analysts=n_analysts,
        rating=rating, currency=currency, source="stockanalysis", trend=None)


def _parse_money(text: str) -> float | None:
    match = re.search(r"[\d.,]+", text)
    return float(match.group(0).replace(",", "")) if match else None
