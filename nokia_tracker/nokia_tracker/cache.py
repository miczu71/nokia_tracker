"""HTTP cache w SQLite (tabela http_cache) — przetrwa restart kontenera,
więc restart add-onu nie przepala darmowych limitów API (patrz BLUEPRINT §1)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


def get(conn: sqlite3.Connection, url: str, ttl_seconds: int) -> str | None:
    """Zwraca body z cache, jeśli świeższe niż ttl_seconds; inaczej None."""
    row = conn.execute(
        "SELECT body, fetched_at FROM http_cache WHERE url = ?", (url,)
    ).fetchone()
    if not row:
        return None
    fetched_at = datetime.fromisoformat(row["fetched_at"])
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - fetched_at > timedelta(seconds=ttl_seconds):
        return None
    return row["body"]


def set(conn: sqlite3.Connection, url: str, body: str, etag: str | None = None) -> None:
    conn.execute(
        "INSERT INTO http_cache (url, body, etag, fetched_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(url) DO UPDATE SET body = excluded.body, etag = excluded.etag, "
        "fetched_at = excluded.fetched_at",
        (url, body, etag, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def get_etag(conn: sqlite3.Connection, url: str) -> str | None:
    row = conn.execute("SELECT etag FROM http_cache WHERE url = ?", (url,)).fetchone()
    return row["etag"] if row else None
