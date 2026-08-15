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
        # analityka wyników (krok 25, 0.9.0)
        "portfolio_history",
        # straty z lat ubiegłych + zamknięcie roku (krok 27, 0.11.0)
        "tax_loss_carryforward", "tax_loss_deductions", "tax_year_closed",
    }
    assert expected <= tables


def test_migrate_sets_user_version(conn):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 8  # v8: krok 27 - straty z lat ubiegłych


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


def test_vests_has_available_from_column(conn):
    # Krok 21 (migracja v5): data realnego wpłynięcia akcji na konto ("Available from"
    # z wyciągu Computershare) - odrębna od vest_date, patrz docs/PLAN_KROK_21_portfel_calkowity.md.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(vests)").fetchall()}
    assert "available_from" in cols


def test_news_has_notified_at_column(conn):
    # Krok 22 (migracja v6): znacznik wysyłki push per news (notifier.py).
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(news)").fetchall()}
    assert "notified_at" in cols


def test_migration_v6_marks_pre_existing_news_as_notified(tmp_path):
    """Newsy zapisane PRZED aktualizacją do 0.7.0 muszą wyjść z migracji już
    oznaczone jako wysłane — inaczej pierwszy fetch_news() po starcie
    zobaczyłby całą historię jako "nową" i wystrzeliłby lawinę pushy na
    telefon (patrz komentarz przy migracji v6 w db.py)."""
    from nokia_tracker import db as dbm

    c = dbm.get_conn(str(tmp_path / "legacy.db"))
    for script in dbm._MIGRATIONS[:5]:  # tylko v1..v5, jak przed 0.7.0
        c.executescript(script)
    c.execute("PRAGMA user_version = 5")
    c.commit()

    c.execute(
        "INSERT INTO news (title, url_canonical, title_hash, published_at) "
        "VALUES ('Stary news sprzed aktualizacji', 'https://example.com/old', 'h1', "
        "'2026-01-01T00:00:00+00:00')")
    c.commit()

    dbm.migrate(c)  # dogania do v6

    row = c.execute("SELECT notified_at FROM news WHERE url_canonical = "
                    "'https://example.com/old'").fetchone()
    assert row["notified_at"] is not None
    c.close()


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
