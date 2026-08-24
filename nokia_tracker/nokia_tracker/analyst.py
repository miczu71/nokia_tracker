"""Orkiestracja konsensusu analityków: Yahoo (główne) -> stockanalysis.com
(fallback, tylko za zgodą `allow_scrape_fallback`) -> zapis dziennego
snapshotu `analyst_targets` -> warunkowy wiersz-lustro w `forecasts`
(docs/PLAN_0_26_0_konsensus.md). Wzorzec `quotes.py`: jedyny writer do
`analyst_targets`.

Wiersz-lustro (`forecasts.source='consensus'`) zapisywany TYLKO przy
zmianie targetu wobec ostatniego zapisanego — codzienny zapis zalałby
`accuracy_pct()` szumem rewizji, których nie było. Dzięki temu istniejący
`forecasts.settle_due()`/`accuracy_pct()` rozliczają konsensus bez żadnej
zmiany — zero drugiej implementacji MAPE."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone

from . import forecasts as forecastsm
from .models import AnalystConsensus
from .providers import stockanalysis, yahoo_analyst
from .providers.base import QuoteProviderError

logger = logging.getLogger(__name__)

_MIRROR_HORIZON = "12m"
_MIRROR_MODEL = "consensus:yahoo"
_MIRROR_TARGET_DAYS = 365


def fetch_and_store(conn: sqlite3.Connection, symbol: str, price_eur: float | None,
                    allow_scrape_fallback: bool) -> AnalystConsensus | None:
    """Pobiera konsensus (Yahoo, potem stockanalysis jeśli dozwolone i Yahoo
    zawiódł), zapisuje snapshot i ewentualny wiersz-lustro. Nigdy nie
    podnosi wyjątku wyżej: awaria obu źródeł = None, karta pokazuje
    'brak danych', nie starą liczbę udającą świeżą."""
    consensus = _fetch(conn, symbol, allow_scrape_fallback)
    if consensus is None:
        return None

    _store_snapshot(conn, consensus)
    if price_eur is not None:
        _maybe_record_mirror(conn, consensus, price_eur)
    return consensus


def _fetch(conn: sqlite3.Connection, symbol: str,
          allow_scrape_fallback: bool) -> AnalystConsensus | None:
    try:
        return yahoo_analyst.fetch_consensus(conn, symbol)
    except QuoteProviderError:
        logger.warning("Konsensus Yahoo %s: błąd, próba fallbacku", symbol, exc_info=True)

    if not allow_scrape_fallback:
        return None
    try:
        return stockanalysis.fetch_consensus(conn, symbol)
    except QuoteProviderError:
        logger.warning("Konsensus stockanalysis %s: błąd", symbol, exc_info=True)
        return None


def _store_snapshot(conn: sqlite3.Connection, c: AnalystConsensus) -> None:
    today = date.today().isoformat()
    conn.execute(
        "INSERT INTO analyst_targets (as_of_date, fetched_at, low_eur, mean_eur, "
        "median_eur, high_eur, n_analysts, rating, currency, source, trend_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(as_of_date, source) DO UPDATE SET "
        "fetched_at=excluded.fetched_at, low_eur=excluded.low_eur, "
        "mean_eur=excluded.mean_eur, median_eur=excluded.median_eur, "
        "high_eur=excluded.high_eur, n_analysts=excluded.n_analysts, "
        "rating=excluded.rating, trend_json=excluded.trend_json",
        (today, datetime.now(timezone.utc).isoformat(), c.low, c.mean, c.median,
         c.high, c.n_analysts, c.rating, c.currency, c.source,
         json.dumps(c.trend, ensure_ascii=False) if c.trend else None))
    conn.commit()


def _maybe_record_mirror(conn: sqlite3.Connection, c: AnalystConsensus,
                         price_eur: float) -> None:
    if c.mean is None:
        return
    last = conn.execute(
        "SELECT predicted_price FROM forecasts WHERE source = 'consensus' "
        "ORDER BY created_at DESC LIMIT 1").fetchone()
    if last is not None and last["predicted_price"] == c.mean:
        return
    target_date = (date.today() + timedelta(days=_MIRROR_TARGET_DAYS)).isoformat()
    forecastsm.record_forecast(
        conn, _MIRROR_HORIZON, target_date, price_eur, c.mean,
        c.low, c.high, None, _MIRROR_MODEL, source="consensus")


def latest(conn: sqlite3.Connection) -> AnalystConsensus | None:
    """Najnowszy snapshot niezależnie od źródła — wejście dla kart /rynek i /."""
    row = conn.execute(
        "SELECT * FROM analyst_targets ORDER BY as_of_date DESC, fetched_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return AnalystConsensus(
        low=row["low_eur"], mean=row["mean_eur"], median=row["median_eur"],
        high=row["high_eur"], n_analysts=row["n_analysts"], rating=row["rating"],
        currency=row["currency"], source=row["source"],
        trend=json.loads(row["trend_json"]) if row["trend_json"] else None,
    )
