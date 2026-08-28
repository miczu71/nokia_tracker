"""Trasy /portfolio (+ pulpit oparty na lotach). Wydzielone z `test_web.py`
(E3 — docs/ROADMAP_V3.md); fixture `client`/`seed` w conftest.py.

Etap 3 (docs/PLAN_0_28_0_ui_porzadki.md): `POST /portfolio` (ręczny stan
posiadania) usunięty — /portfolio jest teraz czystym podglądem z lotów,
loty pochodzą wyłącznie z wyciągów Computershare. `cfg.position_qty`/
`avg_cost_eur` i `settingsm.set_settings()` zostają w silniku (fallback dla
/, gdy nie ma jeszcze żadnego lotu) — zasiewane w testach przez `seed.position()`,
nie przez usuniętą trasę."""
# --- portfolio ---

def test_portfolio_post_removed_returns_405(client):
    resp = client.post("/portfolio", data={"position_qty": "150", "avg_cost_eur": "8.75"})
    assert resp.status_code == 405


def test_dashboard_reflects_saved_portfolio(client, seed):
    seed.position(100, 8.0)
    resp = client.get("/")
    html = resp.get_data(as_text=True)
    # krok 23: kubełek „Wolne" pokazuje ilość z 2 miejscami (formatter qty(), nie surowy float)
    assert "100,00" in html


# --- portfel z lotów (domknięcie luki po pierwszym realnym imporcie PDF) ---

def test_portfolio_page_shows_lots_summary_when_lots_exist(tmp_path, monkeypatch):
    from nokia_tracker import db as dbm
    from nokia_tracker.tax import lots as taxlots
    from nokia_tracker.web import create_app

    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))

    db_path = str(tmp_path / "lots_portfolio.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    taxlots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    conn.close()

    app = create_app(db_path)
    with app.test_client() as c:
        resp = c.get("/portfolio")
        html = resp.get_data(as_text=True)
        assert "Stan posiadania — z lotów" in html
        assert "10.0000" in html  # ilość z lotów, nie z ustawień (które są 0)
        assert 'method="post"' not in html  # brak formularza ręcznego wpisu


def test_portfolio_page_falls_back_to_empty_state_when_no_lots(client):
    resp = client.get("/portfolio")
    html = resp.get_data(as_text=True)
    assert "Stan posiadania — z lotów" not in html
    assert "Brak lotów" in html
    assert 'href="/imports"' in html


def test_dashboard_shows_lots_based_qty_not_manual_settings(tmp_path, monkeypatch):
    from nokia_tracker import db as dbm
    from nokia_tracker.tax import lots as taxlots
    from nokia_tracker.web import create_app

    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))

    db_path = str(tmp_path / "lots_dashboard.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    taxlots.add_lot(conn, "2024-01-10", "own", 12.5, 5.0)
    conn.execute(
        "INSERT INTO settings (key, value) VALUES ('position_qty', '999'), "
        "('avg_cost_eur', '1')")
    conn.commit()
    conn.close()

    app = create_app(db_path)
    with app.test_client() as c:
        resp = c.get("/")
        html = resp.get_data(as_text=True)
        assert "999" not in html
        # krok 23: kubełek „Wolne" formatuje ilość z przecinkiem (qty()), nie surowy float
        assert "12,50" in html
