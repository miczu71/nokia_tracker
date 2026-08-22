"""Trasy pulpitu i rynku: /, /api/chart, /news, /forecasts — w tym pulpit w
pełnym zakresie (populated/PLN/krok21 bloki portfela). Wydzielone z
`test_web.py` (E3 — docs/ROADMAP_V3.md); fixture `client` w conftest.py."""
import pytest

from nokia_tracker import db as dbm
from nokia_tracker.web import create_app


def test_dashboard_shows_analyze_button(client):
    resp = client.get("/")
    assert b"analyze-now" in resp.data


def test_dashboard_shows_quick_question_field_submitting_to_asystent(client):
    html = client.get("/").get_data(as_text=True)
    assert 'action="/asystent"' in html
    assert 'name="q"' in html


# --- wykres pulpitu z konfigurowalnym zakresem (krok 16) ---

def test_dashboard_shows_chart_range_buttons(client):
    html = client.get("/").get_data(as_text=True)
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
    from nokia_tracker.web import create_app

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


# --- krok 18: PLN na pulpicie (kurs bieżący, nie NBP) ---

def _make_pln_dashboard_app(tmp_path, filename="pln_dashboard.db"):
    from nokia_tracker import db as dbm, quotes as quotesm
    from nokia_tracker.web import create_app

    db_path = str(tmp_path / filename)
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    eurpln_id = quotesm.ensure_instrument(conn, "EURPLN=X", "EUR/PLN", "PLN", "fx")
    quotesm.store_single_price(conn, eurpln_id, 4.3, source="yahoo",
                               ts="2026-08-10T16:00:00+00:00")
    primary_id = quotesm.ensure_instrument(conn, "NOKIA.HE", "Nokia Oyj", "EUR", "primary")
    quotesm.store_single_price(conn, primary_id, 4.0, source="yahoo",
                               ts="2026-08-10T16:00:00+00:00")
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES "
        "('position_qty', '100'), ('avg_cost_eur', '3.5')")
    conn.commit()
    conn.close()
    return create_app(db_path)


def test_dashboard_shows_pln_alongside_eur(tmp_path):
    app = _make_pln_dashboard_app(tmp_path)
    with app.test_client() as c:
        html = c.get("/").get_data(as_text=True)
        assert "zł" in html
        assert "≈" in html
        # market_value_eur = 100 * 4.0 = 400 EUR -> PLN = 400 * 4.3 = 1720
        # krok 23: hero „Wartość całkowita" formatuje z separatorem tysięcy (NBSP) — money()
        assert "1\xa0720" in html


def test_dashboard_labels_current_rate_not_nbp(tmp_path):
    app = _make_pln_dashboard_app(tmp_path)
    with app.test_client() as c:
        html = c.get("/").get_data(as_text=True)
        assert "kurs bieżący, nie tabela NBP" in html


def test_dashboard_omits_pln_when_no_fx_rate(client):
    html = client.get("/").get_data(as_text=True)
    assert "zł" not in html
    assert "None" not in html
    assert "kurs EUR/PLN niedostępny" in html


# --- krok 21: całkowite zestawienie portfela (uwolnione + z ograniczeniem + zablokowane) ---
# docs/PLAN_KROK_21_portfel_calkowity.md

def _make_full_portfolio_dashboard_app(tmp_path, monkeypatch, filename="krok21_dashboard.db"):
    from nokia_tracker import db as dbm, quotes as quotesm
    from nokia_tracker.tax import grants as grantsm, lots as taxlots
    from nokia_tracker.web import create_app

    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event", lambda conn, d: (4.0, "stub"))

    db_path = str(tmp_path / filename)
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    eurpln_id = quotesm.ensure_instrument(conn, "EURPLN=X", "EUR/PLN", "PLN", "fx")
    quotesm.store_single_price(conn, eurpln_id, 4.0, source="yahoo",
                               ts="2026-08-10T16:00:00+00:00")
    primary_id = quotesm.ensure_instrument(conn, "NOKIA.HE", "Nokia Oyj", "EUR", "primary")
    quotesm.store_single_price(conn, primary_id, 10.0, source="yahoo",
                               ts="2026-08-10T16:00:00+00:00")

    # Lot własny 100 szt. nabyty 2025-10-27 - restricted_own_summary dopasuje go do
    # grantu A (ta sama Allocation Date) i uzna za ograniczony do 2099-01-01.
    taxlots.add_lot(conn, "2025-10-27", "own", 100.0, 5.0, source="pdf_import")

    grant_a = grantsm.add_grant(conn, "espp", "2025-10-27", 50.0, "espp_grant:a")
    grantsm.add_vest(
        conn, grant_a, "2099-01-01", 50.0, "espp_vest:a", available_from="2099-01-01")
    grant_b = grantsm.add_grant(conn, "lti", "2020-01-01", None, "lti_grant:b")
    grantsm.add_vest(
        conn, grant_b, "2099-02-01", 20.0, "lti_vest:b", available_from="2099-02-01")
    grant_c = grantsm.add_grant(conn, "espp", "2019-01-01", 5.0, "espp_grant:c")
    grantsm.add_vest(
        conn, grant_c, "2020-01-01", 5.0, "espp_vest:c", available_from="2020-01-01")

    conn.close()
    return create_app(db_path)


def test_dashboard_shows_three_portfolio_blocks_with_correct_totals(tmp_path, monkeypatch):
    # krok 23 (docs/PLAN_KROK_23_portfel_kafelki.md): trzy kubełki (Wolne/Z ograniczeniem/
    # Zablokowane) + hero „Wartość całkowita" zastąpiły dawne "W posiadaniu"/"Razem", liczby
    # przez money()/qty() (separator tysięcy NBSP, 2 miejsca dla ilości zamiast 4).
    app = _make_full_portfolio_dashboard_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get("/").get_data(as_text=True)
        assert "Wolne" in html
        assert "Zablokowane" in html
        assert "Wartość całkowita" in html
        # zablokowane: upcoming_qty = 50 (grant A) + 20 (grant B) = 70
        assert "70,00" in html
        assert "700" in html  # upcoming_value_eur = 70 * 10
        assert "2\xa0800" in html  # upcoming_value_pln = 700 * 4
        # razem: position_qty (100) + upcoming_qty (70) = 170
        assert "170,00" in html
        assert "1\xa0700" in html  # 1000 (market_value) + 700 (upcoming)
        assert "6\xa0800" in html  # 4000 + 2800


def test_dashboard_shows_restriction_line_when_own_lot_restricted(tmp_path, monkeypatch):
    app = _make_full_portfolio_dashboard_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get("/").get_data(as_text=True)
        assert "Z ograniczeniem" in html  # krok 23: tytuł kubełka (Title Case, jak reszta trójki)
        assert "100,00" in html  # restricted_qty = cały lot własny
        assert "2099-01-01" in html  # free_until


def test_dashboard_hides_restriction_line_when_nothing_restricted(client):
    html = client.get("/").get_data(as_text=True)
    assert "Z ograniczeniem" not in html  # krok 23: kubełek renderuje się tylko gdy restricted_qty > 0


def test_dashboard_shows_overdue_warning_when_present(tmp_path, monkeypatch):
    app = _make_full_portfolio_dashboard_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get("/").get_data(as_text=True)
        assert "5.0000" in html  # overdue_qty (grant C)
        assert "wgraj najnowszy wyciąg" in html
        assert "/grants" in html
        assert "/imports" in html


def test_dashboard_hides_overdue_warning_when_none_overdue(client):
    html = client.get("/").get_data(as_text=True)
    assert "wgraj najnowszy wyciąg" not in html


def test_dashboard_empty_portfolio_blocks_render_without_error(client):
    # zero grantów, zero lotów - kubełek „Zablokowane"/hero „Wartość całkowita" (krok 23,
    # dawniej "Razem") muszą renderować się z zerami, nie wywalać szablonu (już pokryte przez
    # test_page_returns_200_with_no_store dla "/", ale sprawdzamy tu jawnie treść nowych bloków).
    html = client.get("/").get_data(as_text=True)
    assert "Zablokowane" in html
    assert "Wartość całkowita" in html


def test_dashboard_restricted_note_shows_forfeit_amount(tmp_path, monkeypatch):
    app = _make_full_portfolio_dashboard_app(tmp_path, monkeypatch, "krok26_dashboard.db")
    with app.test_client() as c:
        html = c.get("/").get_data(as_text=True)
        # grant_a: match_rate = 50/100 = 0.5, lot 100 szt -> forfeit_qty=50,
        # forfeit_value_pln = 50 * 10 (price) * 4 (eurpln) = 2000
        assert "utratę 50,00 akcji dopasowania" in html
        assert "2\xa0000" in html


