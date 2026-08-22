"""Trasa /plan (krok 26, docs/PLAN_KROK_26_doradca.md) i jej podglądy JSON:
/api/preview/espp, /api/preview/sale-timing, /api/preview/exit-plan. Trzymane
w JEDNYM pliku (nie osobno HTML/JSON) — dzielą `views/plan.py::espp_scenario`/
`timing_scenario`/`exit_scenario` (E3 §3b), więc regresja dedupu na dowolnej
stronie tej pary łapie się tu, w jednym uruchomieniu. Wydzielone z
`test_web.py` (E3 — docs/ROADMAP_V3.md); fixture `client` w conftest.py."""
from nokia_tracker.web import create_app


# --- /plan (krok 26, docs/PLAN_KROK_26_doradca.md) ---

def _make_plan_app(tmp_path, monkeypatch, filename="krok26_plan.db", price_eur=8.0,
                   eurpln_rate=4.0):
    from nokia_tracker import db as dbm, quotes as quotesm, fx
    from nokia_tracker.models import Candle
    from nokia_tracker.tax import grants as grantsm, lots as taxlots

    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event", lambda conn, d: (4.0, "stub"))

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

    taxlots.add_lot(conn, "2025-10-27", "own", 29.24, 5.41, source="pdf_import")
    grant_id = grantsm.add_grant(conn, "espp", "2025-10-27", 29.24, "espp_grant:x")
    grantsm.add_vest(
        conn, grant_id, "2026-08-01", 29.24, "espp_vest:x", available_from="2099-01-01")

    conn.commit()
    conn.close()
    return create_app(db_path)


def test_plan_page_empty_state(client):
    html = client.get("/plan").get_data(as_text=True)
    assert "Plan" in html
    assert "Żaden lot własny nie jest dziś objęty ograniczeniem" in html
    assert "Brak oczekujących transz" in html
    assert "Podaj resztę majątku" in html


def test_plan_page_shows_forfeit_amount(tmp_path, monkeypatch):
    app = _make_plan_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get("/plan").get_data(as_text=True)
        # forfeit_value_pln = 29.24 * 8.0 * 4.0 = 935.68 -> money() -> "936"
        assert "936" in html


def test_plan_page_shows_days_until_free(tmp_path, monkeypatch):
    app = _make_plan_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get("/plan").get_data(as_text=True)
        assert "Uwolnienie za" in html
        assert "2099-01-01" in html


def test_plan_page_shows_vesting_timeline_dates(tmp_path, monkeypatch):
    app = _make_plan_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get("/plan").get_data(as_text=True)
        assert "2099-01-01" in html
        assert "ESPP" in html


def test_plan_page_espp_planner_renders_from_get_params(tmp_path, monkeypatch):
    # Kurs EUR/PLN musi być w bazie, żeby planer policzył nogę podatkową (bez FX
    # tax_pln zostaje None — patrz test_espp_plan_pln_none_without_fx).
    app = _make_plan_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get(
            "/plan?espp_monthly=200&espp_months=12&espp_price=8").get_data(as_text=True)
        assert "450" in html  # total_shares
        assert "912" in html  # tax_pln (own_only, eurpln=4.0 z fixture)


def test_plan_page_scenario_chip_minus_20_pct_has_exact_price(tmp_path, monkeypatch):
    app = _make_plan_app(tmp_path, monkeypatch, price_eur=8.222)
    with app.test_client() as c:
        html = c.get("/plan").get_data(as_text=True)
        assert "espp_price=6.5776" in html


def test_plan_page_concentration_warning_above_threshold(tmp_path, monkeypatch):
    from nokia_tracker import db as dbm, quotes as quotesm, fx
    from nokia_tracker.models import Candle
    from nokia_tracker.tax import lots as taxlots
    from nokia_tracker import settings as settingsm

    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event", lambda conn, d: (4.0, "stub"))

    db_path = str(tmp_path / "krok26_conc.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    primary_id = quotesm.ensure_instrument(conn, "NOKIA.HE", "Nokia Oyj", "EUR", "primary")
    eurpln_id = quotesm.ensure_instrument(conn, fx.EURPLN_SYMBOL, "EUR/PLN", "PLN", "fx")
    quotesm.upsert_candles(conn, primary_id, "daily",
                           [Candle(ts="2026-06-01T00:00:00+00:00", close=10.0)], source="yahoo")
    quotesm.upsert_candles(conn, eurpln_id, "daily",
                           [Candle(ts="2026-06-01T00:00:00+00:00", close=4.0)], source="yahoo")
    taxlots.add_lot(conn, "2020-01-01", "own", 100.0, 5.0, source="manual")
    settingsm.set_settings(
        conn, {"other_net_worth_pln": 100.0, "concentration_alert_pct": 25.0})
    conn.commit()
    conn.close()

    client = create_app(db_path).test_client()
    html = client.get("/plan").get_data(as_text=True)
    assert "Powyżej progu" in html
    assert "To jednocześnie Twój" in html


def test_plan_page_concentration_empty_state_links_to_settings(client):
    html = client.get("/plan").get_data(as_text=True)
    assert 'href="/settings"' in html


def test_preview_espp_returns_lines_http_200(client):
    resp = client.get("/api/preview/espp?espp_monthly=200&espp_months=12&espp_price=8")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert any(line["label"] == "Razem" for line in data["lines"])


def test_preview_espp_bad_input_returns_ok_false_http_200(client):
    resp = client.get("/api/preview/espp?espp_monthly=200&espp_months=12&espp_price=0")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False


def test_preview_espp_writes_nothing(client):
    before = client.get("/lots").get_data(as_text=True)
    client.get("/api/preview/espp?espp_monthly=200&espp_months=12&espp_price=8")
    after = client.get("/lots").get_data(as_text=True)
    assert before == after


# --- /api/preview/sale-timing i karta "Kiedy sprzedać" na /plan (krok 27) ---

def _make_timing_app(tmp_path, monkeypatch, filename="krok27_timing.db", price_eur=8.0,
                     eurpln_rate=4.0):
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
    return db_path, create_app(db_path)


def test_preview_sale_timing_returns_lines_http_200(tmp_path, monkeypatch):
    db_path, app = _make_timing_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        resp = c.get("/api/preview/sale-timing?timing_qty=10&timing_price=8")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert any(line["label"] == "Różnica netto (podatek + przepadek)"
                   for line in data["lines"])


def test_preview_sale_timing_bad_input_returns_ok_false_http_200(client):
    resp = client.get("/api/preview/sale-timing?timing_qty=-5&timing_price=8")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is False

    resp2 = client.get("/api/preview/sale-timing?timing_qty=abc&timing_price=8")
    assert resp2.status_code == 200
    assert resp2.get_json()["ok"] is False


def test_preview_sale_timing_insufficient_lots_returns_ok_false_with_error(client):
    resp = client.get("/api/preview/sale-timing?timing_qty=10&timing_price=8")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False
    assert data["error"]


def test_preview_sale_timing_writes_nothing(tmp_path, monkeypatch):
    from nokia_tracker import db as dbm

    db_path, app = _make_timing_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        conn = dbm.get_conn(db_path)
        before = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        conn.close()

        c.get("/api/preview/sale-timing?timing_qty=10&timing_price=8")

        conn = dbm.get_conn(db_path)
        after = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        conn.close()
        assert before == after == 0


def test_plan_page_timing_renders_result_in_html(tmp_path, monkeypatch):
    db_path, app = _make_timing_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get("/plan?timing_qty=10&timing_price=8").get_data(as_text=True)
        assert "Kiedy sprzedać" in html
        assert "Różnica netto" in html


# --- karta "Planer systematycznego wyjścia" i benchmark koncentracji (krok 31) ---

def test_plan_page_shows_concentration_benchmark(tmp_path, monkeypatch):
    from nokia_tracker import db as dbm, quotes as quotesm, fx
    from nokia_tracker.models import Candle
    from nokia_tracker.tax import lots as taxlots
    from nokia_tracker import settings as settingsm

    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event", lambda conn, d: (4.0, "stub"))

    db_path = str(tmp_path / "krok31_benchmark.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    primary_id = quotesm.ensure_instrument(conn, "NOKIA.HE", "Nokia Oyj", "EUR", "primary")
    eurpln_id = quotesm.ensure_instrument(conn, fx.EURPLN_SYMBOL, "EUR/PLN", "PLN", "fx")
    quotesm.upsert_candles(conn, primary_id, "daily",
                           [Candle(ts="2026-06-01T00:00:00+00:00", close=10.0)], source="yahoo")
    quotesm.upsert_candles(conn, eurpln_id, "daily",
                           [Candle(ts="2026-06-01T00:00:00+00:00", close=4.0)], source="yahoo")
    taxlots.add_lot(conn, "2020-01-01", "own", 100.0, 5.0, source="manual")
    settingsm.set_settings(conn, {"other_net_worth_pln": 100_000.0})
    conn.commit()
    conn.close()

    client = create_app(db_path).test_client()
    html = client.get("/plan").get_data(as_text=True)
    assert "Standard branżowy" in html
    assert "conc-bar-benchmark" in html


def test_plan_page_exit_plan_renders_from_get_params(tmp_path, monkeypatch):
    db_path, app = _make_timing_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get(
            "/plan?exit_qty=10&exit_freq=monthly&exit_periods=2").get_data(as_text=True)
        assert "Planer systematycznego wyjścia" in html
        assert "Sprzedanych akcji łącznie" in html


def test_plan_page_exit_plan_empty_params_renders_without_error(client):
    html = client.get("/plan").get_data(as_text=True)
    assert "Planer systematycznego wyjścia" in html


def test_plan_page_exit_plan_insufficient_lots_shows_error(tmp_path, monkeypatch):
    db_path, app = _make_timing_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get(
            "/plan?exit_qty=1000&exit_freq=monthly&exit_periods=1").get_data(as_text=True)
        assert "Brak pokrycia" in html


def test_preview_exit_plan_returns_lines_http_200(tmp_path, monkeypatch):
    db_path, app = _make_timing_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        resp = c.get("/api/preview/exit-plan?exit_qty=10&exit_freq=monthly&exit_periods=2")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert any(line["label"] == "Łącznie sprzedanych akcji" for line in data["lines"])


def test_preview_exit_plan_bad_input_returns_ok_false_http_200(client):
    resp = client.get("/api/preview/exit-plan?exit_qty=abc&exit_freq=monthly&exit_periods=2")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is False

    resp2 = client.get("/api/preview/exit-plan?exit_qty=10&exit_freq=weekly&exit_periods=2")
    assert resp2.status_code == 200
    assert resp2.get_json()["ok"] is False


def test_preview_exit_plan_insufficient_lots_returns_ok_false_with_error(client):
    resp = client.get("/api/preview/exit-plan?exit_qty=10&exit_freq=monthly&exit_periods=2")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False
    assert data["error"]


def test_preview_exit_plan_writes_nothing(tmp_path, monkeypatch):
    from nokia_tracker import db as dbm

    db_path, app = _make_timing_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        conn = dbm.get_conn(db_path)
        before = conn.execute("SELECT COUNT(*) FROM lots").fetchone()[0]
        conn.close()

        c.get("/api/preview/exit-plan?exit_qty=10&exit_freq=monthly&exit_periods=2")

        conn = dbm.get_conn(db_path)
        after = conn.execute("SELECT COUNT(*) FROM lots").fetchone()[0]
        conn.close()
        assert before == after


