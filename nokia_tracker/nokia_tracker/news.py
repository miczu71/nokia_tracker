"""Agregacja newsów: kanonikalizacja URL + dedup, zapis do tabeli news na
podstawie włączonych źródeł w news_sources (BLUEPRINT §1).

Dedup ma dwie niezależne warstwy (obie jako UNIQUE w schemacie, patrz
db.py): url_canonical (ten sam news z tego samego źródła, różne parametry
śledzące) i (title_hash, published_at) (ten sam news z RÓŻNYCH źródeł,
inny URL ale ten sam tytuł i data — częste przy przedrukach).
"""
from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .providers import news_finnhub, news_gdelt, news_marketaux, news_rss
from .providers.base import QuoteProviderError

logger = logging.getLogger(__name__)

# Nie wyczerpująca lista — wystarczy do usunięcia najpospolitszego szumu
# śledzącego (utm_* to jawny wymóg z BLUEPRINT, reszta to pragmatyczny dodatek).
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_PARAMS = {"fbclid", "gclid", "oc", "ref", "ncid", "cmpid"}

_DEFAULT_SOURCES = [
    # (kind, url_lub_query, source_weight)
    ("rss", "https://news.google.com/rss/search?q=Nokia+Oyj&hl=pl&gl=PL&ceid=PL:pl", 1.0),
    ("gdelt", "Nokia Oyj", 0.7),
]


def canonicalize_url(url: str) -> str:
    """Usuwa parametry śledzące i fragment — ten sam news z różnych
    kampanii/klików trafia na ten sam url_canonical."""
    parts = urlsplit(url)
    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING_PREFIXES) and k.lower() not in _TRACKING_PARAMS
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def title_hash(title: str) -> str:
    normalized = re.sub(r"\s+", " ", title or "").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def seed_default_sources(conn: sqlite3.Connection) -> None:
    """Seeduje TYLKO świeżą instalację (tabela pusta) — użytkownik może
    dodać/wyłączyć źródła w UI, restart add-onu nie ma prawa tego cofnąć
    (ten sam wzorzec co _seed_categories w fuel_tracker/db.py)."""
    if conn.execute("SELECT 1 FROM news_sources LIMIT 1").fetchone():
        return
    for kind, url, weight in _DEFAULT_SOURCES:
        conn.execute(
            "INSERT INTO news_sources (kind, url, source_weight, enabled) VALUES (?, ?, ?, 1)",
            (kind, url, weight))
    conn.commit()


def _insert_item(conn: sqlite3.Connection, source_id: int | None, item: dict) -> bool:
    """True, jeśli news faktycznie dodany (nie duplikat)."""
    url_c = canonicalize_url(item["url"])
    t_hash = title_hash(item["title"])
    try:
        conn.execute(
            "INSERT INTO news (source_id, title, url_canonical, title_hash, "
            "published_at, raw_summary) VALUES (?, ?, ?, ?, ?, ?)",
            (source_id, item["title"], url_c, t_hash,
             item.get("published_at") or "", item.get("summary")))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def aggregate(conn: sqlite3.Connection, finnhub_api_key: str = "",
             marketaux_api_key: str = "", primary_symbol: str = "NOK") -> dict:
    """Pobiera newsy ze wszystkich włączonych źródeł + Finnhub/MarketAux
    (jeśli klucze skonfigurowane). Zwraca {'fetched':N,'inserted':M}."""
    fetched = inserted = 0

    for row in conn.execute("SELECT * FROM news_sources WHERE enabled = 1"):
        try:
            if row["kind"] == "rss":
                items = news_rss.fetch(conn, row["url"])
            elif row["kind"] == "gdelt":
                items = news_gdelt.fetch(conn, row["url"])  # 'url' = zapytanie
            else:
                logger.warning("Nieznany typ źródła newsów: %s", row["kind"])
                continue
        except QuoteProviderError as exc:
            logger.warning("Źródło %d (%s) niedostępne: %s", row["id"], row["kind"], exc)
            continue
        except Exception:
            logger.exception("Pobranie newsów ze źródła %d (%s) nieudane",
                            row["id"], row["kind"])
            continue
        fetched += len(items)
        for item in items:
            if item.get("url") and item.get("title") and _insert_item(conn, row["id"], item):
                inserted += 1

    for fetcher, key, label in (
        (lambda: news_finnhub.fetch(conn, primary_symbol, finnhub_api_key),
         finnhub_api_key, "Finnhub"),
        (lambda: news_marketaux.fetch(conn, primary_symbol, marketaux_api_key),
         marketaux_api_key, "MarketAux"),
    ):
        if not key:
            continue
        try:
            items = fetcher() or []
        except QuoteProviderError as exc:
            logger.warning("%s niedostępny: %s", label, exc)
            continue
        except Exception:
            logger.exception("Pobranie newsów z %s nieudane", label)
            continue
        fetched += len(items)
        for item in items:
            if item.get("url") and item.get("title") and _insert_item(conn, None, item):
                inserted += 1

    logger.info("Newsy: pobrano %d, dodano %d nowych", fetched, inserted)
    return {"fetched": fetched, "inserted": inserted}
