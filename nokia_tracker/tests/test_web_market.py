"""Trasy rynku i newsów: /rynek, /api/chart, /news, /forecasts. Krok E5
(docs/ROADMAP_V3.md): dawny endpoint `dashboard` (`/`) stał się `/rynek` —
testy portfelowe/gotówkowe/podatkowe przeniosły się do `test_web_account.py`
(nowy `/`). Wydzielone z `test_web.py` (E3); fixture `client` w conftest.py."""
import pytest

from nokia_tracker import db as dbm
from nokia_tracker.web import create_app


def test_market_shows_analyze_button(client):
    resp = client.get("/rynek")
    assert b"analyze-now" in resp.data


# --- wykres rynku z konfigurowalnym zakresem (krok 16) ---

def test_market_shows_chart_range_buttons(client):
    html = client.get("/rynek").get_data(as_text=True)
    for r in ["1d", "1w", "1m", "3m", "6m", "1y", "3y", "5y", "max"]:
        assert f'data-range="{r}"' in html


@pytest.mark.parametrize("range_key", ["1d", "1w", "1m", "3m", "6m", "1y", "3y", "5y", "max"])
def test_chart_api_returns_points_for_every_range(client, range_key):
    resp = client.get(f"/api/chart?range={range_key}")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-store"
    data = resp.get_json()
    assert data["range"] == range_key
    assert "points" in data
    expected_granularity = "intraday" if range_key == "1d" else "daily"
    assert data["granularity"] == expected_granularity


def test_chart_api_returns_seeded_daily_points(tmp_path):
    from nokia_tracker import db as dbm
    from nokia_tracker.models import Candle
    from nokia_tracker.quotes import ensure_instrument, upsert_candles

    db_path = str(tmp_path / "chart.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    iid = ensure_instrument(conn, "NOKIA.HE", "Nokia Oyj", "EUR", "primary")
    upsert_candles(conn, iid, "daily", [
        Candle(ts="2026-07-01T00:00:00+00:00", close=9.0),
        Candle(ts="2026-07-15T00:00:00+00:00", close=9.5),
    ])
    conn.close()

    app = create_app(db_path)
    with app.test_client() as c:
        data = c.get("/api/chart?range=3m").get_json()
        assert data["points"] == [
            ["2026-07-01T00:00:00+00:00", 9.0], ["2026-07-15T00:00:00+00:00", 9.5]]


def test_chart_api_defaults_to_3m_for_unknown_range(client):
    resp = client.get("/api/chart?range=bogus")
    assert resp.get_json()["granularity"] == "daily"


# --- news / forecasts (puste listy nie crashują) ---

def test_news_page_empty_state(client):
    resp = client.get("/news")
    assert "Brak newsów" in resp.get_data(as_text=True)


def test_forecasts_page_empty_state(client):
    resp = client.get("/forecasts")
    assert "Brak jeszcze".encode() in resp.data


# --- populated /rynek: catches template errors the empty-state smoke
# tests can't (formatting real floats, attrs dicts, alert rows, forecasts) ---

def test_market_renders_with_full_populated_data(tmp_path):
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
        resp = c.get("/rynek")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Pełny briefing." in html
        assert "TRZYMAJ" in html
        assert "Tytuł alertu" in html

        assert c.get("/news").status_code == 200
        assert c.get("/forecasts").status_code == 200
        assert c.get("/dividends").status_code == 200
