"""Web UI (Flask): smoke testy tras + zapis formularzy portfela/dywidend/
ustawień + no-store na HTML (BLUEPRINT §3/§9, krok 9). Zero żywego AI —
/analyze-now mockuje analysis.run_daily_analysis."""
import pytest

from nokia_tracker import analysis, db as dbm
from nokia_tracker.web import create_app


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    conn.close()
    app = create_app(db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# --- smoke: każda strona GET zwraca 200 i ma no-store ---

@pytest.mark.parametrize("path", ["/", "/portfolio", "/lots", "/dividends", "/news",
                                  "/forecasts", "/settings"])
def test_page_returns_200_with_no_store(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-store"


def test_base_template_versions_static_assets(client):
    resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert "/static/app.css?v=" in html
    assert "/static/app.js?v=" in html
    assert "/static/chart.umd.min.js?v=" in html


def test_dashboard_shows_analyze_button(client):
    resp = client.get("/")
    assert b"analyze-now" in resp.data


# --- portfolio ---

def test_portfolio_post_updates_settings_and_redirects(client):
    resp = client.post("/portfolio", data={"position_qty": "150", "avg_cost_eur": "8.75"})
    assert resp.status_code == 302
    assert "saved=1" in resp.headers["Location"]

    resp2 = client.get("/portfolio")
    html = resp2.get_data(as_text=True)
    assert 'value="150.0"' in html
    assert 'value="8.75"' in html


def test_dashboard_reflects_saved_portfolio(client):
    client.post("/portfolio", data={"position_qty": "100", "avg_cost_eur": "8.0"})
    resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert "100.0" in html


# --- lots ---

@pytest.fixture
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


def test_lots_page_empty_state(client):
    resp = client.get("/lots")
    html = resp.get_data(as_text=True)
    assert "Brak lotów" in html
    assert "kalkulator pomocniczy" in html


def test_lots_post_adds_lot_and_redirects(client, _fake_nbp_rate):
    resp = client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    assert resp.status_code == 302
    assert "saved=1" in resp.headers["Location"]

    resp2 = client.get("/lots")
    html = resp2.get_data(as_text=True)
    assert "2024-01-10" in html
    assert "10.0000" in html


def test_lots_sell_post_consumes_fifo_and_redirects(client, _fake_nbp_rate):
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    resp = client.post("/lots/sell", data={
        "sale_date": "2024-06-01", "sale_quantity": "4",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    assert resp.status_code == 302
    assert "sold=1" in resp.headers["Location"]

    resp2 = client.get("/lots")
    html = resp2.get_data(as_text=True)
    assert "6.0000" in html  # qty_remaining po sprzedaży 4 z 10


def test_lots_sell_post_insufficient_quantity_redirects_with_error(client, _fake_nbp_rate):
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "5", "price_eur": "5.0", "fee_eur": "0",
    })
    resp = client.post("/lots/sell", data={
        "sale_date": "2024-06-01", "sale_quantity": "10",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    assert resp.status_code == 302
    assert "error=" in resp.headers["Location"]

    resp2 = client.get(resp.headers["Location"])
    assert "Brak pokrycia" in resp2.get_data(as_text=True)


def test_lots_page_shows_three_policies_comparison(client, _fake_nbp_rate):
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2024-06-01", "sale_quantity": "10",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    resp = client.get("/lots")
    html = resp.get_data(as_text=True)
    assert "Tylko własne" in html
    assert "Własne + dywidenda" in html
    assert "Wszystkie w wartości nabycia" in html


# --- dividends ---

def test_dividends_post_computes_tax_and_stores_row(client):
    resp = client.post("/dividends", data={
        "pay_date": "2026-06-15", "gross_eur": "100.0", "withholding_pct": "35.0",
    })
    assert resp.status_code == 302

    resp2 = client.get("/dividends")
    html = resp2.get_data(as_text=True)
    assert "2026-06-15" in html
    assert "65.00" in html  # net_received_eur = 100 - 35


def test_dividends_post_uses_default_withholding_when_blank(client):
    resp = client.post("/dividends", data={"pay_date": "2026-06-15", "gross_eur": "100.0"})
    assert resp.status_code == 302
    resp2 = client.get("/dividends")
    # domyślne finnish_withholding_pct=35 -> netto 65
    assert "65.00" in resp2.get_data(as_text=True)


def test_dividends_page_shows_disclaimer(client):
    resp = client.get("/dividends")
    html = resp.get_data(as_text=True)
    assert "kalkulator pomocniczy" in html
    assert "nie doradztwo podatkowe" in html


# --- settings ---

def test_settings_post_updates_and_redirects(client):
    resp = client.post("/settings", data={
        "ai_primary": "gemini", "ai_fallback": "anthropic",
        "local_llm_model": "custom-model", "gemini_model": "gemini-x",
        "anthropic_model": "claude-x",
        "alert_sentiment_drop": "0.7", "alert_price_move_pct": "5.0",
        "alert_min_interval_minutes": "60", "notify_service": "notify.family",
        "cost_basis_policy": "own_plus_drip",
    })
    assert resp.status_code == 302

    resp2 = client.get("/settings")
    html = resp2.get_data(as_text=True)
    assert 'value="notify.family"' in html
    assert 'selected' in html  # przynajmniej jeden select ma zapisaną wartość


def test_settings_checkbox_unchecked_when_omitted(client):
    # checkboxy HTML nie wysyłają nic, gdy odznaczone -> ustawienie=0
    client.post("/settings", data={
        "ai_primary": "local", "ai_fallback": "gemini",
        "local_llm_model": "m", "gemini_model": "m2", "anthropic_model": "m3",
        "alert_sentiment_drop": "0.5", "alert_price_move_pct": "3.0",
        "alert_min_interval_minutes": "120", "notify_service": "",
        "cost_basis_policy": "own_only",
        # brak ai_recommendations_enabled i alert_on_forecast_break
    })
    resp = client.get("/settings")
    html = resp.get_data(as_text=True)
    # brak "checked" przy tych dwóch polach
    assert html.count("checked") == 0


# --- news / forecasts (puste listy nie crashują) ---

def test_news_page_empty_state(client):
    resp = client.get("/news")
    assert "Brak newsów" in resp.get_data(as_text=True)


def test_forecasts_page_empty_state(client):
    resp = client.get("/forecasts")
    assert "Brak jeszcze".encode() in resp.data


# --- analyze-now ---

def test_analyze_now_calls_analysis_and_redirects(client, monkeypatch):
    calls = []
    monkeypatch.setattr(analysis, "run_daily_analysis",
                        lambda *a, **kw: (calls.append(a), True)[1])
    resp = client.post("/analyze-now")
    assert resp.status_code == 302
    assert "analyzed=1" in resp.headers["Location"]
    assert len(calls) == 1


def test_analyze_now_failure_still_redirects(client, monkeypatch):
    monkeypatch.setattr(analysis, "run_daily_analysis", lambda *a, **kw: False)
    resp = client.post("/analyze-now")
    assert resp.status_code == 302
    assert "analyzed=0" in resp.headers["Location"]


# --- populated dashboard: catches template errors the empty-state smoke
# tests can't (formatting real floats, attrs dicts, alert rows, forecasts) ---

def test_dashboard_renders_with_full_populated_data(tmp_path):
    from nokia_tracker import quotes
    from nokia_tracker.models import Candle

    db_path = str(tmp_path / "full.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)

    primary = quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia Oyj", "EUR", "primary")
    quotes.upsert_candles(conn, primary, "daily", [
        Candle(ts="2026-07-26T00:00:00+00:00", close=8.0),
        Candle(ts="2026-07-27T00:00:00+00:00", close=8.3),
    ])

    news_id = conn.execute(
        "INSERT INTO news (title, url_canonical, title_hash, published_at) "
        "VALUES ('Nokia news', 'https://x/1', 'h1', '2026-07-27T09:00:00+00:00')"
    ).lastrowid
    conn.execute(
        "INSERT INTO news_scores (news_id, sentiment, impact, horizon, thesis_pl, tags, model) "
        "VALUES (?, 0.5, 2, 'weeks', 'Teza.', '[\"kontrakt\"]', 'local')", (news_id,))

    conn.execute(
        "INSERT INTO forecasts (horizon, created_at, target_date, price_at_creation, "
        "predicted_price, ci_low, ci_high, confidence, model) VALUES "
        "('1w', '2026-07-27T10:00:00+00:00', '2026-08-03', 8.3, 8.5, 8.0, 9.0, 0.6, 'local')")
    conn.execute(
        "INSERT INTO briefings (generated_at, text, tts_text, sentiment_avg, news_count, "
        "verdict, key_risks, recommendation, recommendation_reason_pl, "
        "recommendation_confidence, model) VALUES "
        "('2026-07-27T18:00:00+00:00', 'Pełny briefing.', 'TTS.', 0.5, 1, 'trend rynkowy', "
        "'[\"ryzyko\"]', 'trzymaj', 'Uzasadnienie.', 0.6, 'local')")
    conn.execute(
        "INSERT INTO alerts_log (kind, severity, title, message, payload, fired_at) VALUES "
        "('price_move_pct', 'info', 'Tytuł alertu', 'Wiadomość.', '{}', '2026-07-27T20:00:00+00:00')")
    conn.execute(
        "INSERT INTO dividends (pay_date, gross_eur, withholding_pct, withholding_paid_eur, "
        "net_received_eur) VALUES ('2026-06-15', 100.0, 35.0, 35.0, 65.0)")
    conn.commit()
    conn.close()

    app = create_app(db_path)
    with app.test_client() as c:
        resp = c.get("/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Pełny briefing." in html
        assert "TRZYMAJ" in html
        assert "Tytuł alertu" in html

        assert c.get("/news").status_code == 200
        assert c.get("/forecasts").status_code == 200
        assert c.get("/dividends").status_code == 200
