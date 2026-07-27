from nokia_tracker import cache


def test_set_then_get_within_ttl(conn):
    cache.set(conn, "http://x", "body123")
    assert cache.get(conn, "http://x", ttl_seconds=300) == "body123"


def test_get_missing_url_returns_none(conn):
    assert cache.get(conn, "http://nope", ttl_seconds=300) is None


def test_get_expired_returns_none(conn):
    import sqlite3
    from datetime import datetime, timedelta, timezone
    cache.set(conn, "http://x", "old")
    # symulujemy stary wpis nadpisując fetched_at sprzed 1h
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn.execute("UPDATE http_cache SET fetched_at = ? WHERE url = ?", (old_ts, "http://x"))
    conn.commit()
    assert cache.get(conn, "http://x", ttl_seconds=300) is None


def test_set_overwrites_existing(conn):
    cache.set(conn, "http://x", "v1")
    cache.set(conn, "http://x", "v2")
    assert cache.get(conn, "http://x", ttl_seconds=300) == "v2"


def test_get_etag(conn):
    cache.set(conn, "http://x", "body", etag="abc123")
    assert cache.get_etag(conn, "http://x") == "abc123"
    assert cache.get_etag(conn, "http://missing") is None
