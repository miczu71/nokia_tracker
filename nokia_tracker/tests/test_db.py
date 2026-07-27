"""Migracja v1 tworzy pełny schemat (rynek + AI + podatki), nie tylko to,
czego 0.1.0 faktycznie używa — patrz docs/BLUEPRINT.md sekcja 3a."""


def test_migrate_creates_all_tables(conn):
    tables = {
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    expected = {
        # rynek i AI
        "instruments", "quotes", "news", "news_scores", "news_sources",
        "forecasts", "briefings",
        # podatki i portfel (0.2.0, gotowe od v1)
        "lots", "sales", "sale_allocations", "grants", "vests", "dividends",
        "nbp_rates", "imports", "import_conflicts",
        # infra
        "settings", "api_usage", "ai_usage", "http_cache", "alerts_log",
    }
    assert expected <= tables


def test_migrate_sets_user_version(conn):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 1


def test_migrate_is_idempotent(conn):
    # migrate() drugi raz na tym samym połączeniu nie powinno próbować
    # ponownie tworzyć tabel (user_version już na najnowszej wersji).
    from nokia_tracker import db as dbm
    dbm.migrate(conn)  # nie może rzucić "table already exists"


def test_lots_lot_type_check_constraint(conn):
    import sqlite3
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur) "
            "VALUES ('2026-01-01', 'nieznany_typ', 1.0, 1.0)")


def test_lots_natural_key_unique(conn):
    import sqlite3
    import pytest
    conn.execute(
        "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur, natural_key) "
        "VALUES ('2026-01-01', 'own', 1.0, 1.0, 'k1')")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur, natural_key) "
            "VALUES ('2026-02-01', 'own', 2.0, 2.0, 'k1')")
