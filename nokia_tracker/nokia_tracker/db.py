"""SQLite: połączenie i migracje (PRAGMA user_version).

Schemat v1 obejmuje od razu tabele podatkowe (lots/sales/grants/vests/
dividends/nbp_rates/imports/import_conflicts), mimo że 0.1.0 używa ich
minimalnie — patrz docs/BLUEPRINT.md sekcja 3a. Dzięki temu wydanie 0.2.0
(rozliczenie PIT-38) dokłada logikę i UI, a nie migrację danych
produkcyjnych z instalacji 0.1.0.
"""
from __future__ import annotations

import os
import sqlite3

_MIGRATIONS = [
    # v1 — schemat początkowy: rynek, AI, portfel, podatki, importy
    """
    -- ============ RYNEK ============
    CREATE TABLE instruments (
        id INTEGER PRIMARY KEY,
        symbol TEXT NOT NULL UNIQUE,          -- np. 'NOKIA.HE', 'ERIC-B.ST', '^OMXH25'
        name TEXT NOT NULL,
        currency TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'benchmark' -- 'primary' | 'benchmark' | 'fx'
    );

    CREATE TABLE quotes (
        id INTEGER PRIMARY KEY,
        instrument_id INTEGER NOT NULL REFERENCES instruments(id),
        ts TEXT NOT NULL,                     -- ISO8601 UTC
        granularity TEXT NOT NULL,            -- 'intraday' | 'daily'
        open REAL, high REAL, low REAL, close REAL NOT NULL, volume REAL,
        source TEXT NOT NULL DEFAULT 'yahoo',
        UNIQUE(instrument_id, ts, granularity)
    );
    CREATE INDEX idx_quotes_instrument_ts ON quotes(instrument_id, ts);

    -- ============ NEWSY I AI ============
    CREATE TABLE news_sources (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,                   -- 'rss' | 'gdelt' | 'finnhub' | 'marketaux'
        url TEXT NOT NULL,
        source_weight REAL NOT NULL DEFAULT 1.0,
        enabled INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE news (
        id INTEGER PRIMARY KEY,
        source_id INTEGER REFERENCES news_sources(id),
        title TEXT NOT NULL,
        url_canonical TEXT NOT NULL,
        title_hash TEXT NOT NULL,
        published_at TEXT NOT NULL,
        raw_summary TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(url_canonical),
        UNIQUE(title_hash, published_at)
    );

    CREATE TABLE news_scores (
        id INTEGER PRIMARY KEY,
        news_id INTEGER NOT NULL REFERENCES news(id) ON DELETE CASCADE,
        sentiment REAL, impact INTEGER, horizon TEXT,
        thesis_pl TEXT, price_effect_pct_est REAL, tags TEXT, -- tags: JSON array
        model TEXT NOT NULL, scored_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(news_id)
    );

    CREATE TABLE forecasts (
        id INTEGER PRIMARY KEY,
        horizon TEXT NOT NULL,                -- '1w' | '1m' | '12m'
        created_at TEXT NOT NULL,
        target_date TEXT NOT NULL,
        price_at_creation REAL NOT NULL,
        predicted_price REAL NOT NULL,
        ci_low REAL, ci_high REAL, confidence REAL,
        realized_price REAL, error_pct REAL,   -- wypełniane po target_date
        model TEXT NOT NULL
    );

    CREATE TABLE briefings (
        id INTEGER PRIMARY KEY,
        generated_at TEXT NOT NULL,
        text TEXT NOT NULL, tts_text TEXT NOT NULL,
        sentiment_avg REAL, news_count INTEGER, verdict TEXT,
        key_risks TEXT,                       -- JSON array
        recommendation TEXT,                  -- kup|akumuluj|trzymaj|redukuj|sprzedaj
        recommendation_reason_pl TEXT, recommendation_confidence REAL,
        model TEXT NOT NULL
    );

    -- ============ PODATKI I PORTFEL (0.2.0, schemat gotowy od v1) ============
    CREATE TABLE lots (
        id INTEGER PRIMARY KEY,
        acquired_date TEXT NOT NULL,
        lot_type TEXT NOT NULL CHECK(lot_type IN ('own','matched','lti','dividend_drip')),
        quantity REAL NOT NULL,
        price_eur REAL NOT NULL,
        fee_eur REAL NOT NULL DEFAULT 0,
        nbp_rate REAL,
        nbp_rate_date TEXT,
        cost_pln REAL,
        grant_id INTEGER REFERENCES grants(id),
        source TEXT NOT NULL DEFAULT 'manual',
        natural_key TEXT UNIQUE,               -- klucz idempotencji importu, NULL dla ręcznych
        qty_remaining REAL,
        notes TEXT
    );

    CREATE TABLE sales (
        id INTEGER PRIMARY KEY, sale_date TEXT NOT NULL, quantity REAL NOT NULL,
        price_eur REAL NOT NULL, fee_eur REAL NOT NULL DEFAULT 0,
        nbp_rate REAL, nbp_rate_date TEXT, revenue_pln REAL, notes TEXT
    );

    CREATE TABLE sale_allocations (
        id INTEGER PRIMARY KEY,
        sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
        lot_id  INTEGER NOT NULL REFERENCES lots(id),
        quantity REAL NOT NULL, cost_pln REAL NOT NULL, revenue_pln REAL NOT NULL
    );

    CREATE TABLE grants (
        id INTEGER PRIMARY KEY,
        program TEXT NOT NULL CHECK(program IN ('espp','lti')),
        grant_date TEXT NOT NULL, declared_amount_eur REAL, quantity REAL,
        match_pct REAL NOT NULL DEFAULT 0,
        natural_key TEXT UNIQUE,
        notes TEXT
    );

    CREATE TABLE vests (
        id INTEGER PRIMARY KEY,
        grant_id INTEGER NOT NULL REFERENCES grants(id) ON DELETE CASCADE,
        vest_date TEXT NOT NULL, quantity REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','vested','cancelled')),
        lot_id INTEGER REFERENCES lots(id),
        natural_key TEXT UNIQUE
    );

    CREATE TABLE dividends (
        id INTEGER PRIMARY KEY, pay_date TEXT NOT NULL,
        gross_per_share_eur REAL, quantity REAL, gross_eur REAL NOT NULL,
        withholding_pct REAL, withholding_paid_eur REAL, net_received_eur REAL,
        nbp_rate REAL, nbp_rate_date TEXT, gross_pln REAL, pl_tax_due_pln REAL,
        reinvested_lot_id INTEGER REFERENCES lots(id),
        natural_key TEXT UNIQUE,
        notes TEXT
    );

    CREATE TABLE nbp_rates (
        date TEXT PRIMARY KEY, rate REAL NOT NULL, effective_date TEXT NOT NULL
    );

    CREATE TABLE imports (
        id INTEGER PRIMARY KEY,
        filename TEXT NOT NULL, file_sha256 TEXT NOT NULL,
        period_start TEXT, period_end TEXT, as_of_date TEXT,
        imported_at TEXT NOT NULL DEFAULT (datetime('now')),
        rows_inserted INTEGER NOT NULL DEFAULT 0,
        rows_unchanged INTEGER NOT NULL DEFAULT 0,
        rows_conflict INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE import_conflicts (
        id INTEGER PRIMARY KEY,
        import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
        entity_type TEXT NOT NULL,             -- 'lot' | 'vest' | 'dividend'
        natural_key TEXT NOT NULL,
        existing_json TEXT NOT NULL, incoming_json TEXT NOT NULL,
        resolved INTEGER NOT NULL DEFAULT 0, resolution TEXT
    );

    -- ============ INFRA ============
    CREATE TABLE settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    );

    CREATE TABLE api_usage (
        id INTEGER PRIMARY KEY,
        provider TEXT NOT NULL, day TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0,
        UNIQUE(provider, day)
    );

    CREATE TABLE ai_usage (
        id INTEGER PRIMARY KEY,
        provider TEXT NOT NULL, model TEXT NOT NULL, task TEXT NOT NULL,
        day TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0,
        total_tokens INTEGER NOT NULL DEFAULT 0,
        UNIQUE(provider, model, task, day)
    );

    CREATE TABLE http_cache (
        url TEXT PRIMARY KEY, body TEXT NOT NULL, etag TEXT,
        fetched_at TEXT NOT NULL
    );

    CREATE TABLE alerts_log (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL, severity TEXT NOT NULL,
        title TEXT NOT NULL, message TEXT NOT NULL,
        payload TEXT,                          -- JSON
        fired_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX idx_alerts_kind_fired ON alerts_log(kind, fired_at);
    """,
]


def get_conn(db_path: str | None = None) -> sqlite3.Connection:
    """WAL + busy_timeout — bez tego równoległe joby APScheduler (publish_sensors,
    fetch_news) na osobnych połączeniach dawały 'database is locked' (złapane
    na żywo po kroku 6, gdy ai/scoring.py dodał więcej zapisów w fetch_news;
    WAL pozwala na jednoczesny odczyt podczas zapisu, busy_timeout dogrywa
    resztę kolizji zamiast rzucać natychmiast)."""
    path = db_path or os.environ.get("DB_PATH", "/data/nokia_tracker.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, script in enumerate(_MIGRATIONS[version:], start=version + 1):
        conn.executescript(script)
        conn.execute(f"PRAGMA user_version = {i}")
        conn.commit()
