"""Trasa /wyplata i jej podgląd JSON /api/preview/wyplata — kalkulator wypłaty
(E6, docs/PLAN_E6_wyplata.md). `withdrawal_view` (poprawność kompozycji) i
`solve_for_net`/`annual_net_for_quantity` (poprawność matematyki) są już
pokryte przez `test_views_withdrawal.py`/`test_tax_whatif.py` — tu wyłącznie
parsowanie query stringów, walidacja, i że strona/JSON się renderują."""
from nokia_tracker.web import create_app


def _make_wyplata_app(tmp_path, monkeypatch, filename="e6_wyplata.db",
                      price_eur=8.0, eurpln_rate=4.0):
    from nokia_tracker import db as dbm, quotes as quotesm, fx
    from nokia_tracker.models import Candle
    from nokia_tracker.tax import lots as taxlots

    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event", lambda conn, d: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.whatif.fx_nbp.rate_for_event", lambda conn, d: (4.0, "stub"))

    db_path = str(tmp_path / filename)
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)

    primary_id = quotesm.ensure_instrument(conn, "NOKIA.HE", "Nokia Oyj", "EUR", "primary")
    eurpln_id = quotesm.ensure_instrument(conn, fx.EURPLN_SYMBOL, "EUR/PLN", "PLN", "fx")
    quotesm.upsert_candles(conn, primary_id, "daily",
                           [Candle(ts="2026-06-01T00:00:00+00:00", close=price_eur)],
                           source="yahoo")
    quotesm.upsert_candles(conn, eurpln_id, "daily",
                           [Candle(ts="2026-06-01T00:00:00+00:00", close=eurpln_rate)],
                           source="yahoo")

    taxlots.add_lot(conn, "2020-01-01", "own", 100.0, 3.0, source="manual")

    conn.commit()
    conn.close()
    return create_app(db_path)


def test_wyplata_page_empty_state_asks_for_input(client):
    html = client.get("/wyplata").get_data(as_text=True)
    assert "Wypłata" in html
    assert "Wpisz kwotę netto i policz" in html


def test_wyplata_page_default_direction_is_target(client):
    html = client.get("/wyplata").get_data(as_text=True)
    assert 'name="wyplata_target"' in html
    assert 'name="wyplata_qty"' not in html


def test_wyplata_page_quantity_direction_shows_qty_field(client):
    html = client.get("/wyplata?direction=quantity").get_data(as_text=True)
    assert 'name="wyplata_qty"' in html
    assert 'name="wyplata_target"' not in html


def test_wyplata_page_target_direction_renders_result(tmp_path, monkeypatch):
    app = _make_wyplata_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get(
            "/wyplata?direction=target&wyplata_target=1000&wyplata_price=8"
            "&wyplata_date=2026-07-28").get_data(as_text=True)
        assert "Na rękę" in html
        assert "disclaimer error" not in html


def test_wyplata_page_quantity_direction_renders_result(tmp_path, monkeypatch):
    app = _make_wyplata_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get(
            "/wyplata?direction=quantity&wyplata_qty=10&wyplata_price=8"
            "&wyplata_date=2026-07-28").get_data(as_text=True)
        assert "Na rękę" in html


def test_wyplata_page_unreachable_target_shows_error_not_500(tmp_path, monkeypatch):
    app = _make_wyplata_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        resp = c.get(
            "/wyplata?direction=target&wyplata_target=100000000&wyplata_price=8"
            "&wyplata_date=2026-07-28")
        assert resp.status_code == 200
        assert "disclaimer error" in resp.get_data(as_text=True)


def test_wyplata_page_no_open_lots_shows_error_not_500(client):
    resp = client.get(
        "/wyplata?direction=quantity&wyplata_qty=10&wyplata_price=8"
        "&wyplata_date=2026-07-28")
    assert resp.status_code == 200
    assert "disclaimer error" in resp.get_data(as_text=True)


def test_wyplata_page_returns_no_store_header(client):
    resp = client.get("/wyplata")
    assert resp.headers["Cache-Control"] == "no-store"


# --- /api/preview/wyplata ---

def test_preview_wyplata_target_returns_lines_http_200(tmp_path, monkeypatch):
    app = _make_wyplata_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        resp = c.get(
            "/api/preview/wyplata?direction=target&wyplata_target=1000"
            "&wyplata_price=8&wyplata_date=2026-07-28")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["ok"] is True
        assert any(line["label"] == "Na rękę" for line in data["lines"])


def test_preview_wyplata_quantity_returns_lines_http_200(tmp_path, monkeypatch):
    app = _make_wyplata_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        resp = c.get(
            "/api/preview/wyplata?direction=quantity&wyplata_qty=10"
            "&wyplata_price=8&wyplata_date=2026-07-28")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["ok"] is True


def test_preview_wyplata_bad_input_returns_ok_false_http_200(client):
    resp = client.get("/api/preview/wyplata?direction=target&wyplata_target=abc&wyplata_price=8")
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["ok"] is False


def test_preview_wyplata_unknown_direction_returns_ok_false(client):
    resp = client.get("/api/preview/wyplata?direction=bogus&wyplata_target=1000&wyplata_price=8")
    data = resp.get_json()
    assert data["ok"] is False


def test_preview_wyplata_writes_nothing(tmp_path, monkeypatch):
    from nokia_tracker import db as dbm

    app = _make_wyplata_app(tmp_path, monkeypatch)
    db_path = app.config.get("DATABASE") or None
    with app.test_client() as c:
        c.get(
            "/api/preview/wyplata?direction=quantity&wyplata_qty=10"
            "&wyplata_price=8&wyplata_date=2026-07-28")
    conn = dbm.get_conn(str(tmp_path / "e6_wyplata.db"))
    assert conn.execute("SELECT COUNT(*) c FROM sales").fetchone()["c"] == 0
    conn.close()


# --- przycisk na / prowadzi do /wyplata (nie /plan) ---

def test_account_page_links_to_wyplata_not_plan(client):
    html = client.get("/").get_data(as_text=True)
    assert 'href="/wyplata"' in html
