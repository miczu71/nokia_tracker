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
    assert version == 4  # v4: krok 20 - sales.reported_revenue_pln/reported_cost_pln


def test_get_conn_enables_wal_and_busy_timeout(conn):
    # WAL + busy_timeout: bez tego równoległe joby schedulera (publish_sensors,
    # fetch_news) na osobnych połączeniach dają "database is locked" — złapane
    # na żywo po kroku 6 (patrz komentarz w db.py::get_conn).
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000


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


def test_vests_has_reminder_sent_at_column(conn):
    # Krok 14 (migracja v2): kolumna do znaczenia, że przypomnienie o
    # nadchodzącym vestingu już wysłane - żeby nie przypominać drugi raz.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(vests)").fetchall()}
    assert "reminder_sent_at" in cols


def test_nbp_rates_has_table_no_column(conn):
    # Krok 16 (migracja v3): numer tabeli NBP dla linku do konkretnej publikacji.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(nbp_rates)").fetchall()}
    assert "table_no" in cols


def test_dividends_has_currency_column_defaulting_to_eur(conn):
    # Krok 16 (migracja v3): waluta dywidendy, dziś zawsze EUR (Nokia płaci w EUR),
    # ale kolumna jawna zamiast zakładać walutę domyślnie w UI.
    conn.execute(
        "INSERT INTO dividends (pay_date, gross_eur) VALUES ('2026-01-01', 1.0)")
    conn.commit()
    row = conn.execute("SELECT currency FROM dividends WHERE pay_date = '2026-01-01'").fetchone()
    assert row["currency"] == "EUR"


def test_sales_has_reported_columns(conn):
    # Krok 20 (migracja v4): zgłoszona wartość sprzedaży (np. z ręcznego arkusza
    # użytkownika), nadpisuje agregaty PIT-38 bez fałszowania sale_allocations/lots.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sales)").fetchall()}
    assert "reported_revenue_pln" in cols
    assert "reported_cost_pln" in cols


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
