"""Orkiestracja EUR/PLN: Yahoo (primary) -> ECB (fallback) -> zapis do
quotes jak zwykły instrument (BLUEPRINT §1) — downstream (sensors.py) nie
musi wiedzieć, które źródło wygrało, czyta zawsze przez quotes.latest_quote().
"""
from __future__ import annotations

import logging
import sqlite3

from . import quotes
from .providers import fx_ecb
from .providers.base import QuoteProviderError
from .providers.yahoo import YahooQuoteProvider

logger = logging.getLogger(__name__)

EURPLN_SYMBOL = "EURPLN=X"


def refresh_eurpln(conn: sqlite3.Connection, instrument_id: int) -> None:
    provider = YahooQuoteProvider(conn)
    try:
        n = quotes.refresh_recent_daily(conn, instrument_id, EURPLN_SYMBOL, provider)
        if n:
            return
    except QuoteProviderError:
        pass
    logger.warning("Yahoo %s nieudane — fallback na ECB", EURPLN_SYMBOL)

    result = fx_ecb.fetch_rate(conn, "PLN")
    if result is None:
        logger.warning("ECB fallback też nieudany — eurpln_rate zostanie 'unknown'")
        return
    rate, ecb_date = result
    quotes.store_single_price(conn, instrument_id, rate, source="ecb",
                              ts=f"{ecb_date}T00:00:00+00:00")
