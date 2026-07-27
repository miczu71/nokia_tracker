"""ECB eurofxref-daily.xml — fallback FX do PREZENTACJI (nie do podatków —
te idą przez fx_nbp.py, wymóg prawny kursu NBP D-1, patrz BLUEPRINT §1).
Darmowe, bez klucza, jedna publikacja dziennie."""
from __future__ import annotations

import logging
import sqlite3
import xml.etree.ElementTree as ET

import requests

from .. import cache, ratelimit
from .base import QuoteProviderError

logger = logging.getLogger(__name__)

_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_NS = {"ns": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}


def fetch_rate(conn: sqlite3.Connection, currency: str, cache_ttl_seconds: int = 3600
              ) -> tuple[float, str] | None:
    """Kurs EUR->currency + data publikacji (YYYY-MM-DD), albo None przy błędzie.
    ECB publikuje z bazą EUR, więc 'rate' z XML-a JEST kursem EUR->currency."""
    cached = cache.get(conn, _URL, cache_ttl_seconds)
    if cached is not None:
        return _parse(cached, currency)

    def _do_request():
        return requests.get(_URL, timeout=15)

    resp = ratelimit.backoff_retry(_do_request, provider="ecb")
    if resp is None or resp.status_code != 200:
        logger.warning("ECB fetch nieudany: HTTP %s",
                       resp.status_code if resp is not None else "brak odpowiedzi")
        return None

    cache.set(conn, _URL, resp.text)
    return _parse(resp.text, currency)


def _parse(xml_text: str, currency: str) -> tuple[float, str] | None:
    try:
        root = ET.fromstring(xml_text)
        cube_time = root.find(".//ns:Cube[@time]", _NS)
        date = cube_time.attrib["time"]
        for cube in cube_time.findall("ns:Cube", _NS):
            if cube.attrib.get("currency") == currency:
                return float(cube.attrib["rate"]), date
        return None
    except (ET.ParseError, KeyError, AttributeError) as exc:
        raise QuoteProviderError(f"ECB: nie udało się sparsować XML — {exc}") from exc
