"""Web UI (Flask): smoke testy tras + zapis formularzy portfela/dywidend/
ustawień + no-store na HTML (BLUEPRINT §3/§9, krok 9). Zero żywego AI —
/analyze-now mockuje analysis.run_daily_analysis."""
import io
import json
import re
import sqlite3
import zipfile
from datetime import datetime

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

@pytest.mark.parametrize("path", ["/", "/portfolio", "/lots", "/sales", "/dividends", "/grants",
                                  "/imports", "/news", "/forecasts", "/settings", "/pit38",
                                  "/dane", "/wyniki", "/plan", "/pit38/kreator", "/asystent"])
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
        assert "aktywne źródło" in html
        assert "10.0000" in html  # ilość z lotów, nie z ustawień (które są 0)


def test_portfolio_page_falls_back_to_manual_when_no_lots(client):
    resp = client.get("/portfolio")
    html = resp.get_data(as_text=True)
    assert "aktywne źródło" not in html


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


def test_lots_sell_post_uses_optional_real_proceeds_over_price_times_quantity(
        client, _fake_nbp_rate):
    # krok 19: pole "Realne wpływy brutto" w formularzu /lots/sell zastępuje ilość×cenę.
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2024-06-01", "sale_quantity": "4",
        "sale_price_eur": "8.0", "sale_fee_eur": "1.0",
        "sale_proceeds_eur": "33.5",  # != 4*8.0=32.0
    })
    resp = client.get("/sales")
    html = resp.get_data(as_text=True)
    # revenue_pln = (33.5 - 1.0) * 4.0 (kurs stub) = 130.00, NIE (4*8.0-1.0)*4.0 = 124.00
    assert "130.00" in html
    assert "124.00" not in html


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


def test_lots_post_rejects_future_acquired_date(client):
    resp = client.post("/lots", data={
        "acquired_date": "2099-01-01", "lot_type": "own",
        "quantity": "5", "price_eur": "5.0", "fee_eur": "0",
    })
    assert resp.status_code == 302
    resp2 = client.get(resp.headers["Location"])
    assert "przyszłości" in resp2.get_data(as_text=True)
    assert "Brak lotów" in resp2.get_data(as_text=True)  # lot NIE został zapisany


def test_lots_sell_post_rejects_future_sale_date(client, _fake_nbp_rate):
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "5", "price_eur": "5.0", "fee_eur": "0",
    })
    resp = client.post("/lots/sell", data={
        "sale_date": "2099-01-01", "sale_quantity": "5",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    assert resp.status_code == 302
    resp2 = client.get(resp.headers["Location"])
    assert "przyszłości" in resp2.get_data(as_text=True)


def test_dividends_post_rejects_future_pay_date(client):
    resp = client.post("/dividends", data={"pay_date": "2099-01-01", "gross_eur": "100.0"})
    assert resp.status_code == 302
    resp2 = client.get(resp.headers["Location"])
    assert "przyszłości" in resp2.get_data(as_text=True)


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


# --- sales (krok 16: pełne rozbicie zrealizowanych sprzedaży) ---

def test_sales_page_empty_state(client):
    resp = client.get("/sales")
    html = resp.get_data(as_text=True)
    assert "Brak zarejestrowanych sprzedaży" in html


def test_sales_page_shows_realized_sale_breakdown(client, _fake_nbp_rate):
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2024-06-01", "sale_quantity": "4",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    resp = client.get("/sales")
    html = resp.get_data(as_text=True)
    assert "2024-06-01" in html
    assert "2024-01-10" in html  # data nabycia lotu widoczna w rozwinięciu
    # kurs NBP zamockowany na "stub" (nie realna data) — bez numeru tabeli/linków
    # tu, ale wyprowadzenie kursu (event -> D-1 -> kurs) ma się i tak wyrenderować.
    assert "dzień roboczy poprzedzający" in html


def test_sales_page_filters_by_year(client, _fake_nbp_rate):
    client.post("/lots", data={
        "acquired_date": "2023-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2023-06-01", "sale_quantity": "4",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2024-06-01", "sale_quantity": "4",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    resp_2023 = client.get("/sales?year=2023")
    resp_2024 = client.get("/sales?year=2024")
    assert "2023-06-01" in resp_2023.get_data(as_text=True)
    assert "2024-06-01" not in resp_2023.get_data(as_text=True)
    assert "2024-06-01" in resp_2024.get_data(as_text=True)


# ---- krok 20: zgłoszona wartość sprzedaży ----

def test_sales_report_post_sets_reported_values_and_affects_pit38(client, _fake_nbp_rate):
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2024-06-01", "sale_quantity": "10",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    import re
    html = client.get("/sales").get_data(as_text=True)
    m = re.search(r'action="(/sales/\d+/report)"', html)
    assert m, "brak formularza zgłoszonej wartości w HTML"
    report_url = m.group(1)

    resp = client.post(report_url, data={
        "reported_revenue_pln": "999.0", "reported_cost_pln": "111.0",
        "reported_note": "zgodnie z arkuszem",
    })
    assert resp.status_code == 302
    assert "reported=1" in resp.headers["Location"]

    html_after = client.get("/sales").get_data(as_text=True)
    assert "999" in html_after
    assert "111" in html_after

    pit38 = client.get("/pit38?year=2024").get_data(as_text=True)
    assert "888.00" in pit38 or "888,00" in pit38  # income = 999-111 = 888


def test_sales_report_post_can_be_cleared(client, _fake_nbp_rate):
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2024-06-01", "sale_quantity": "10",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    import re
    html = client.get("/sales").get_data(as_text=True)
    report_url = re.search(r'action="(/sales/\d+/report)"', html).group(1)
    client.post(report_url, data={"reported_revenue_pln": "999.0", "reported_cost_pln": "111.0"})

    resp = client.post(report_url, data={"reported_revenue_pln": "", "reported_cost_pln": ""})
    assert resp.status_code == 302

    # engine: revenue=10*8*4=320, cost=10*5*4=200, income=120 (nie 888 z override)
    pit38 = client.get("/pit38?year=2024").get_data(as_text=True)
    assert "888.00" not in pit38 and "888,00" not in pit38


def test_sales_report_unknown_sale_id_redirects_safely(client):
    resp = client.post("/sales/999/report", data={"reported_revenue_pln": "1"})
    assert resp.status_code == 302


def test_sales_delete_restores_qty_remaining_and_redirects(client, _fake_nbp_rate):
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2024-06-01", "sale_quantity": "4",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    resp_lots_before = client.get("/lots")
    assert "6.0000" in resp_lots_before.get_data(as_text=True)  # qty_remaining po sprzedaży

    import re
    html = client.get("/sales").get_data(as_text=True)
    sale_form_action = re.search(r'action="(/sales/\d+/delete)"', html)
    assert sale_form_action, "brak formularza cofnięcia sprzedaży w HTML"
    delete_url = sale_form_action.group(1)

    resp_del = client.post(delete_url)
    assert resp_del.status_code == 302
    assert "deleted=1" in resp_del.headers["Location"]

    resp_lots_after = client.get("/lots")
    assert "10.0000" in resp_lots_after.get_data(as_text=True)  # qty_remaining przywrócone

    resp_sales = client.get("/sales")
    assert "Brak zarejestrowanych sprzedaży" in resp_sales.get_data(as_text=True)


def test_sales_page_shows_year_totals(client, _fake_nbp_rate):
    # Krok 17: rejestr sprzedaży dostaje pasek KPI z sumą za rok (nie tylko
    # per-sprzedaż). Kafelki podsumowania formatują PLN bez miejsc po przecinku
    # (konwencja strony, patrz dividends.html/dashboard.html) — cena dobrana tak,
    # żeby podatek per sprzedaż wyszedł całkowity: revenue 4*11.25*4=180,
    # koszt 4*5*4=80, dochód 100, podatek 19.00; dwie takie sprzedaże = 38 razem.
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "20", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2024-03-01", "sale_quantity": "4",
        "sale_price_eur": "11.25", "sale_fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2024-06-01", "sale_quantity": "4",
        "sale_price_eur": "11.25", "sale_fee_eur": "0",
    })
    html = client.get("/sales?year=2024").get_data(as_text=True)
    assert "Podsumowanie 2024" in html
    assert '<span class="stat-value">38<span class="stat-unit">PLN</span></span>' in html


def test_sales_row_detail_rendered_server_side(client, _fake_nbp_rate):
    # Krok 17: /sales przeszło z <details> na rejestr wiersz + wiersz-detal;
    # detal musi być wyrenderowany po stronie serwera (schowany przez `hidden`,
    # nie doładowany JS-em po kliknięciu).
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2024-06-01", "sale_quantity": "4",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    html = client.get("/sales").get_data(as_text=True)
    assert 'class="row-detail"' in html
    assert "hidden" in html
    assert "2024-01-10" in html  # data nabycia lotu widoczna w detalu, mimo hidden


def test_alloc_detail_renders_sale_fx_once(client, _fake_nbp_rate):
    # Regresja: przed krokiem 17 wyprowadzenie kursu sprzedaży powtarzało się
    # w osobnym wierszu prozy PRZY KAŻDYM LOCIE (_alloc_detail.html/alloc-fx-row).
    # Sprzedaż konsumująca FIFO z dwóch lotów (3+1 z pierwszego, 3 z drugiego)
    # ma pokazać zdanie o kursie sprzedaży RAZ, nad tabelą alokacji.
    client.post("/lots", data={
        "acquired_date": "2024-01-05", "lot_type": "own",
        "quantity": "3", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots", data={
        "acquired_date": "2024-02-05", "lot_type": "own",
        "quantity": "3", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2024-06-01", "sale_quantity": "4",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    html = client.get("/sales").get_data(as_text=True)
    assert html.count("sprzedaż: 2024-06-01") == 1


# --- grants (ESPP/LTI) ---

def _make_grants_app(tmp_path):
    from nokia_tracker import db as dbm
    from nokia_tracker.tax import grants as grantsm
    from nokia_tracker.web import create_app

    db_path = str(tmp_path / "grants.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)

    # ESPP: grant 1:1 z transzą (jak zapisuje computershare_pdf.import_statement)
    espp_grant_id = grantsm.add_grant(
        conn, "espp", "2026-01-06", 12.0, "espp_grant:2026-01-06:12.0", match_pct=20.0)
    grantsm.add_vest(
        conn, espp_grant_id, "2026-07-06", 12.0, "espp_vest:2026-01-06:2026-07-06:12.0")

    # LTI: jeden grant, wiele transz (participation_description w natural_key)
    lti_grant_id = grantsm.add_grant(
        conn, "lti", "2025-07-07", None, "lti_grant:2025 RS AWARD 07-JUL-2025")
    grantsm.add_vest(
        conn, lti_grant_id, "2026-07-06", 634.0,
        "lti_vest:2025 RS AWARD 07-JUL-2025:2026-07-06:634.0")
    grantsm.add_vest(
        conn, lti_grant_id, "2027-07-06", 633.0,
        "lti_vest:2025 RS AWARD 07-JUL-2025:2027-07-06:633.0")
    conn.close()

    return create_app(db_path)


def test_grants_page_empty_state(client):
    resp = client.get("/grants")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Brak grantów ESPP" in html
    assert "Brak grantów LTI" in html


def test_grants_page_shows_espp_and_lti_grouped_with_tranches(tmp_path):
    app = _make_grants_app(tmp_path)
    with app.test_client() as c:
        html = c.get("/grants").get_data(as_text=True)
        assert "2026-01-06" in html
        assert "2025 RS AWARD 07-JUL-2025" in html
        assert "634" in html
        assert "633" in html
        assert "1267" in html  # suma transz LTI (634+633), bo grants.quantity=NULL


def test_grants_page_shows_overdue_badge_for_past_pending_tranches(tmp_path, monkeypatch):
    from datetime import datetime as _datetime
    # _make_grants_app tworzy transze datowane 2026-01-06/2026-07-06 - z ustalonym "dziś" w
    # przyszłości względem obu, żeby test nie zależał od realnego zegara systemowego.
    monkeypatch.setattr("nokia_tracker.tax.grants.datetime", type(
        "FixedDatetime", (), {"now": staticmethod(lambda tz=None: _datetime(2027, 1, 1))}))
    app = _make_grants_app(tmp_path)
    with app.test_client() as c:
        html = c.get("/grants").get_data(as_text=True)
        assert "zaległe — sprawdź wyciąg" in html


def test_grants_page_shows_valuation_for_open_and_realized_portions(tmp_path, monkeypatch):
    # Krok 16: transza dopasowana do lotu (reconcile_vesting), część sprzedana,
    # część nadal otwarta — strona ma pokazać obie wartości.
    from nokia_tracker import db as dbm
    from nokia_tracker.tax import grants as grantsm
    from nokia_tracker.tax import lots as taxlots
    from nokia_tracker.web import create_app

    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event", lambda conn, d: (4.0, d))

    db_path = str(tmp_path / "grants_valuation.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    grant_id = grantsm.add_grant(conn, "espp", "2022-10-26", 10.0, "espp_grant:x")
    grantsm.add_vest(conn, grant_id, "2023-08-01", 10.0, "espp_vest:x")
    taxlots.add_lot(conn, "2023-08-30", "matched", 10.0, 3.65, source="pdf_import")
    grantsm.reconcile_vesting(conn, today="2026-07-28")
    taxlots.record_sale(conn, "2026-01-15", 4.0, 9.0)
    conn.close()

    app = create_app(db_path)
    with app.test_client() as c:
        html = c.get("/grants").get_data(as_text=True)
        assert "2026-01-15" in html  # data zrealizowanej sprzedaży w rozwinięciu
        assert "niedopasowane" not in html  # jedyna transza tutaj jest reconciled=True


# --- imports ---

def test_imports_page_empty_state(client):
    resp = client.get("/imports")
    html = resp.get_data(as_text=True)
    assert "Brak historii importów" in html
    assert "Brak nierozwiązanych konfliktów" in html


def test_imports_upload_calls_import_statement_and_redirects(client, monkeypatch):
    from io import BytesIO

    from nokia_tracker.importers import computershare_pdf

    calls = []
    monkeypatch.setattr(
        computershare_pdf, "import_statement",
        lambda conn, data, filename, cfg=None: (
            calls.append((filename, len(data))),
            {"import_id": 1, "rows_inserted": 5, "rows_unchanged": 2, "rows_conflict": 1})[1])

    resp = client.post("/imports/upload", data={
        "pdf_file": (BytesIO(b"%PDF-fake-content"), "wyciag.pdf"),
    }, content_type="multipart/form-data")

    assert resp.status_code == 302
    assert "report=5%2F2%2F1" in resp.headers["Location"] or "report=5/2/1" in resp.headers["Location"]
    assert len(calls) == 1
    assert calls[0][0] == "wyciag.pdf"


def test_imports_upload_reconciles_vesting_after_import(tmp_path, monkeypatch):
    # Krok 14: /imports/upload musi wywołać reconcile_vesting() zaraz po
    # import_statement(), żeby /grants pokazywało rozwiązaną transzę bez
    # dodatkowego requestu (docs/PLAN_KROK_14_vesting_reconcile.md).
    from io import BytesIO

    from nokia_tracker.importers import computershare_pdf
    from nokia_tracker.tax import grants as grantsm
    from nokia_tracker.tax import lots as taxlots

    db_path = str(tmp_path / "reconcile.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    grant_id = grantsm.add_grant(conn, "espp", "2022-10-26", 7.33, "espp_grant:x")
    vest_id = grantsm.add_vest(conn, grant_id, "2023-08-01", 7.33, "espp_vest:x")
    taxlots.add_lot(conn, "2023-08-30", "matched", 7.33, 3.65, source="pdf_import")
    conn.close()

    monkeypatch.setattr(
        computershare_pdf, "import_statement",
        lambda conn, data, filename, cfg=None: {
            "import_id": 1, "rows_inserted": 0, "rows_unchanged": 0, "rows_conflict": 0})

    app = create_app(db_path)
    with app.test_client() as c:
        resp = c.post("/imports/upload", data={
            "pdf_file": (BytesIO(b"%PDF-fake-content"), "wyciag.pdf"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 302

    conn2 = dbm.get_conn(db_path)
    vest = conn2.execute("SELECT * FROM vests WHERE id = ?", (vest_id,)).fetchone()
    assert vest["status"] == "vested"
    conn2.close()


def test_imports_upload_without_file_redirects_without_calling_import(client, monkeypatch):
    from nokia_tracker.importers import computershare_pdf

    calls = []
    monkeypatch.setattr(
        computershare_pdf, "import_statement",
        lambda *a, **kw: calls.append(1))

    resp = client.post("/imports/upload", data={}, content_type="multipart/form-data")
    assert resp.status_code == 302
    assert len(calls) == 0


def _make_withhold_conflict_app(tmp_path, monkeypatch, quantity=784.0, sale_price_eur=5.31,
                                fees_eur=8.32, lot_quantity=1000.0):
    from nokia_tracker import db as dbm
    from nokia_tracker.tax import lots as taxlots
    from nokia_tracker.web import create_app
    import json as _json

    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))

    db_path = str(tmp_path / "confirm_sale.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    taxlots.add_lot(conn, "2022-01-01", "own", lot_quantity, 5.0)
    conn.execute(
        "INSERT INTO imports (filename, file_sha256, period_start, period_end, as_of_date) "
        "VALUES ('x.pdf', 'abc', '2025-01-01', '2026-01-01', '2026-01-01')")
    import_id = conn.execute("SELECT id FROM imports").fetchone()["id"]
    incoming = {
        "execution_date": "2025-10-27", "quantity": quantity, "sale_price_eur": sale_price_eur,
        "sale_proceeds_eur": quantity * sale_price_eur, "taxes_eur": 0.0, "fees_eur": fees_eur,
        "net_proceeds_eur": quantity * sale_price_eur - fees_eur,
    }
    conn.execute(
        "INSERT INTO import_conflicts (import_id, entity_type, natural_key, existing_json, "
        "incoming_json) VALUES (?, 'withhold_to_cover_sale', 'wtc:x', '{}', ?)",
        (import_id, _json.dumps(incoming)))
    conn.commit()
    conflict_id = conn.execute("SELECT id FROM import_conflicts").fetchone()["id"]
    conn.close()

    app = create_app(db_path)
    return app, conflict_id, db_path


def test_imports_page_shows_prefilled_sale_details_and_confirm_button(tmp_path, monkeypatch):
    app, conflict_id, _ = _make_withhold_conflict_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get("/imports").get_data(as_text=True)
        assert "784" in html
        assert "5.31" in html
        assert "Zatwierdź jako sprzedaż" in html
        assert f"/imports/conflicts/{conflict_id}/confirm-sale" in html


def test_imports_confirm_sale_books_real_sale_and_resolves_conflict(tmp_path, monkeypatch):
    app, conflict_id, db_path = _make_withhold_conflict_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        resp = c.post(f"/imports/conflicts/{conflict_id}/confirm-sale")
        assert resp.status_code == 302
        assert "sold=1" in resp.headers["Location"]

    from nokia_tracker import db as dbm
    conn = dbm.get_conn(db_path)
    conflict = conn.execute(
        "SELECT * FROM import_conflicts WHERE id = ?", (conflict_id,)).fetchone()
    assert conflict["resolved"] == 1
    assert "sale_id" in conflict["resolution"]
    sale = conn.execute("SELECT * FROM sales").fetchone()
    assert sale is not None
    assert sale["quantity"] == pytest.approx(784.0)
    assert sale["price_eur"] == pytest.approx(5.31)
    conn.close()


def test_imports_confirm_sale_uses_real_sale_proceeds_not_price_times_quantity(
        tmp_path, monkeypatch):
    # krok 19: Sale Price w PDF jest zaokrąglona do 2 miejsc, więc quantity*price != realne
    # Sale Proceeds z wyciągu (znalezione na realnych danych: 784*5.31=4162.94 EUR,
    # a wyciąg podaje 4161.47 EUR) - confirm-sale musi zaksięgować to drugie.
    app, conflict_id, db_path = _make_withhold_conflict_app(tmp_path, monkeypatch)
    from nokia_tracker import db as dbm
    conn = dbm.get_conn(db_path)
    import json as _json
    row = conn.execute(
        "SELECT * FROM import_conflicts WHERE id = ?", (conflict_id,)).fetchone()
    incoming = _json.loads(row["incoming_json"])
    incoming["sale_proceeds_eur"] = 4161.47  # różni się od 784*5.31=4162.94
    conn.execute(
        "UPDATE import_conflicts SET incoming_json = ? WHERE id = ?",
        (_json.dumps(incoming), conflict_id))
    conn.commit()
    conn.close()

    with app.test_client() as c:
        c.post(f"/imports/conflicts/{conflict_id}/confirm-sale")

    conn = dbm.get_conn(db_path)
    sale = conn.execute("SELECT * FROM sales").fetchone()
    # revenue_pln = (4161.47 - 8.32) * 4.0 (kurs stub), NIE (784*5.31 - 8.32) * 4.0
    assert sale["revenue_pln"] == pytest.approx((4161.47 - 8.32) * 4.0)
    conn.close()


def test_imports_confirm_sale_insufficient_lots_does_not_resolve_conflict(tmp_path, monkeypatch):
    # Lot dostępny tylko 10 szt, konflikt mówi o sprzedaży 784 - brak pokrycia.
    app, conflict_id, db_path = _make_withhold_conflict_app(tmp_path, monkeypatch, lot_quantity=10.0)
    with app.test_client() as c:
        resp = c.post(f"/imports/conflicts/{conflict_id}/confirm-sale")
        assert resp.status_code == 302
        assert "error=" in resp.headers["Location"]

    from nokia_tracker import db as dbm
    conn = dbm.get_conn(db_path)
    conflict = conn.execute(
        "SELECT * FROM import_conflicts WHERE id = ?", (conflict_id,)).fetchone()
    assert conflict["resolved"] == 0  # nie oznaczony rozwiązany przy błędzie
    assert conn.execute("SELECT COUNT(*) c FROM sales").fetchone()["c"] == 0
    conn.close()


def test_imports_confirm_sale_unknown_conflict_id_redirects_safely(client):
    resp = client.post("/imports/conflicts/999/confirm-sale")
    assert resp.status_code == 302


def test_imports_conflicts_queue_shows_unresolved_and_resolve_hides_it(tmp_path):
    from nokia_tracker import db as dbm
    from nokia_tracker.web import create_app

    db_path = str(tmp_path / "conflicts.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    conn.execute(
        "INSERT INTO imports (filename, file_sha256, period_start, period_end, as_of_date) "
        "VALUES ('x.pdf', 'abc', '2026-01-01', '2026-07-26', '2026-07-26')")
    import_id = conn.execute("SELECT id FROM imports").fetchone()["id"]
    conn.execute(
        "INSERT INTO import_conflicts (import_id, entity_type, natural_key, existing_json, "
        "incoming_json) VALUES (?, 'lot', 'purchase:x', '{}', '{\"quantity\": 5}')",
        (import_id,))
    conn.commit()
    conflict_id = conn.execute("SELECT id FROM import_conflicts").fetchone()["id"]
    conn.close()

    app = create_app(db_path)
    with app.test_client() as c:
        resp = c.get("/imports")
        assert "purchase:x" in resp.get_data(as_text=True)

        resp2 = c.post(f"/imports/conflicts/{conflict_id}/resolve",
                       data={"resolution": "wpisano ręcznie"})
        assert resp2.status_code == 302

        resp3 = c.get("/imports")
        assert "purchase:x" not in resp3.get_data(as_text=True)
        assert "Brak nierozwiązanych konfliktów" in resp3.get_data(as_text=True)


# --- dividends (krok 16: jedno źródło prawdy przez add_dividend, kwoty w PLN) ---

@pytest.fixture
def _fake_nbp_rate_dividends(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "2026-06-12"))


def test_dividends_post_computes_tax_and_stores_row(client, _fake_nbp_rate_dividends):
    resp = client.post("/dividends", data={
        "pay_date": "2026-06-15", "gross_eur": "100.0", "withholding_pct": "35.0",
    })
    assert resp.status_code == 302

    resp2 = client.get("/dividends")
    html = resp2.get_data(as_text=True)
    assert "2026-06-15" in html
    assert "400.00" in html  # gross_pln = 100 EUR * kurs 4.0
    # przykład BLUEPRINT skalowany kursem 4.0: 4 PLN dopłaty -> 16.00, 20 -> 80.00
    assert "16.00" in html
    assert "80.00" in html


def test_dividends_post_uses_default_withholding_when_blank(client, _fake_nbp_rate_dividends):
    resp = client.post("/dividends", data={"pay_date": "2026-06-15", "gross_eur": "100.0"})
    assert resp.status_code == 302
    resp2 = client.get("/dividends")
    # domyślne finnish_withholding_pct=35 -> ten sam wynik co jawne 35.0 powyżej
    assert "400.00" in resp2.get_data(as_text=True)


def test_dividends_post_with_drip_creates_lot_and_shows_reinvestment(
        client, _fake_nbp_rate_dividends):
    resp = client.post("/dividends", data={
        "pay_date": "2026-06-15", "gross_eur": "100.0", "withholding_pct": "35.0",
        "drip_purchase_date": "2026-06-20", "drip_price_eur": "3.50", "drip_shares": "18.5714",
    })
    assert resp.status_code == 302
    html = client.get("/dividends").get_data(as_text=True)
    assert "18.5714" in html
    assert "2026-06-20" in html
    assert "gotówka" not in html


def test_dividends_post_without_drip_shows_cash(client, _fake_nbp_rate_dividends):
    client.post("/dividends", data={"pay_date": "2026-06-15", "gross_eur": "100.0"})
    html = client.get("/dividends").get_data(as_text=True)
    assert "gotówka" in html


def test_dividends_page_shows_disclaimer(client):
    resp = client.get("/dividends")
    html = resp.get_data(as_text=True)
    assert "kalkulator pomocniczy" in html
    assert "nie doradztwo podatkowe" in html


# --- kalendarz i harmonogram dywidend (krok 30, 0.14.0) ---

def _seed_four_real_dividends(client, monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    for pay_date in ["2025-02-20", "2025-05-15", "2025-08-14", "2025-11-13"]:
        client.post("/dividends", data={
            "pay_date": pay_date, "gross_eur": "4.0", "quantity": "100.0",
            "withholding_pct": "35.0",
        })


def test_dividends_page_shows_calendar_and_schedule_cards(client, monkeypatch):
    _seed_four_real_dividends(client, monkeypatch)
    html = client.get("/dividends").get_data(as_text=True)
    assert "Kalendarz dywidend" in html
    assert "Ogłoszony harmonogram" in html
    assert "Założenia prognozy" in html


def test_dividends_empty_db_calendar_shows_reason_not_crash(client):
    resp = client.get("/dividends")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Za mało realnych wypłat" in html


def test_dividend_schedule_post_inserts_multiple_instalments(client):
    resp = client.post("/dividends/harmonogram", data={
        "fiscal_year": "2026",
        "record_date_1": "2026-02-18", "per_share_1": "0.04",
        "record_date_2": "2026-05-15", "per_share_2": "0.04",
    })
    assert resp.status_code == 302
    html = client.get("/dividends").get_data(as_text=True)
    assert "2026-02-18" in html
    assert "2026-05-15" in html


def test_dividend_schedule_post_skips_blank_instalments(client):
    client.post("/dividends/harmonogram", data={
        "fiscal_year": "2026",
        "record_date_1": "2026-02-18", "per_share_1": "0.04",
        "record_date_2": "", "per_share_2": "",
    })
    html = client.get("/dividends").get_data(as_text=True)
    assert html.count("2026-02-18") >= 1


def test_dividend_schedule_post_rejects_missing_fiscal_year(client):
    resp = client.post("/dividends/harmonogram", data={
        "record_date_1": "2026-02-18", "per_share_1": "0.04",
    })
    assert resp.status_code == 302
    resp2 = client.get(resp.headers["Location"])
    assert "rok obrotowy" in resp2.get_data(as_text=True).lower()


def test_dividend_schedule_post_rejects_all_blank_instalments(client):
    resp = client.post("/dividends/harmonogram", data={"fiscal_year": "2026"})
    assert resp.status_code == 302
    resp2 = client.get(resp.headers["Location"])
    assert "co najmniej jedną ratę" in resp2.get_data(as_text=True).lower()


def test_dividend_schedule_post_future_dates_are_accepted(client):
    """_is_future_date świadomie NIE stosuje się do harmonogramu — daty przyszłe
    są całym sensem tej tabeli."""
    resp = client.post("/dividends/harmonogram", data={
        "fiscal_year": "2030",
        "record_date_1": "2030-01-01", "per_share_1": "0.05",
    })
    assert resp.status_code == 302
    html = client.get("/dividends").get_data(as_text=True)
    assert "error" not in resp.headers.get("Location", "").lower()
    assert "2030-01-01" in html


def test_dividend_schedule_post_upserts_same_instalment(client):
    client.post("/dividends/harmonogram", data={
        "fiscal_year": "2026",
        "record_date_1": "2026-02-18", "per_share_1": "0.04",
    })
    client.post("/dividends/harmonogram", data={
        "fiscal_year": "2026",
        "record_date_1": "2026-02-20", "per_share_1": "0.045",
        "confirmed_1": "on",
    })
    html = client.get("/dividends").get_data(as_text=True)
    assert "2026-02-18" not in html
    assert "2026-02-20" in html
    assert html.count("potwierdzona") >= 1


def test_dividend_schedule_delete_removes_row(client):
    client.post("/dividends/harmonogram", data={
        "fiscal_year": "2026",
        "record_date_1": "2026-02-18", "per_share_1": "0.04",
    })
    html = client.get("/dividends").get_data(as_text=True)
    assert "2026-02-18" in html

    schedule_id = 1  # pierwszy wiersz w świeżej bazie testowej
    resp = client.post(f"/dividends/harmonogram/{schedule_id}/delete")
    assert resp.status_code == 302
    html2 = client.get("/dividends").get_data(as_text=True)
    assert "2026-02-18" not in html2


def test_dividends_lata_query_param_changes_horizon(client, monkeypatch):
    _seed_four_real_dividends(client, monkeypatch)
    html_1y = client.get("/dividends?lata=1").get_data(as_text=True)
    html_5y = client.get("/dividends?lata=5").get_data(as_text=True)
    assert "1 rok</strong>" in html_1y
    assert "5 lata</strong>" in html_5y


def test_dividends_lata_invalid_value_falls_back_to_default(client):
    resp = client.get("/dividends?lata=99")
    assert resp.status_code == 200
    assert "3 lata</strong>" in resp.get_data(as_text=True)


def test_dividends_totals_unchanged_by_schedule_rows(client, monkeypatch):
    """A4 (docs/PLAN_KROK_30_dywidendy.md): projekcja NIGDY nie wchodzi do `totals`
    liczonych z zaksięgowanej historii — dodanie harmonogramu nie może zmienić
    kafelków podsumowania na górze strony."""
    _seed_four_real_dividends(client, monkeypatch)
    before = client.get("/dividends").get_data(as_text=True)
    before_summary = before.split("Kalendarz dywidend")[0]

    client.post("/dividends/harmonogram", data={
        "fiscal_year": "2027",
        "record_date_1": "2027-02-18", "per_share_1": "0.10",
    })

    after = client.get("/dividends").get_data(as_text=True)
    after_summary = after.split("Kalendarz dywidend")[0]
    assert before_summary == after_summary


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


# --- PIT-38 (krok 15) ---

@pytest.fixture
def _fake_nbp_rate_pit38(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.whatif.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


def test_pit38_page_empty_state_shows_disclaimer(client):
    resp = client.get("/pit38")
    html = resp.get_data(as_text=True)
    assert "kalkulator pomocniczy" in html


def test_pit38_page_shows_three_policies_and_section_g(client, _fake_nbp_rate_pit38):
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2024-06-01", "sale_quantity": "10",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    resp = client.get("/pit38?year=2024")
    html = resp.get_data(as_text=True)
    assert "Tylko własne" in html
    assert "Własne + dywidenda" in html
    assert "Wszystkie w wartości nabycia" in html
    assert "Sekcja G" in html
    assert "PIT/ZG" in html


def test_pit38_page_year_selector_filters_sale_trace(client, _fake_nbp_rate_pit38):
    client.post("/lots", data={
        "acquired_date": "2023-01-10", "lot_type": "own",
        "quantity": "5", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2023-06-01", "sale_quantity": "5",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    resp_2023 = client.get("/pit38?year=2023")
    resp_2024 = client.get("/pit38?year=2024")
    assert "2023-01-10" in resp_2023.get_data(as_text=True)
    assert "2023-01-10" not in resp_2024.get_data(as_text=True)


def test_pit38_year_selector_lists_years_with_data(client, _fake_nbp_rate_pit38):
    # Krok 16 (§8.3): selektor to lista lat z rzeczywistymi zdarzeniami, nie
    # gołe pole liczbowe — 2023 (sprzedaż) i bieżący rok muszą się pojawić.
    client.post("/lots", data={
        "acquired_date": "2023-01-10", "lot_type": "own",
        "quantity": "5", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2023-06-01", "sale_quantity": "5",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    html = client.get("/pit38").get_data(as_text=True)
    assert '<option value="2023"' in html
    assert f'<option value="{datetime.now().year}"' in html


def test_pit38_shows_total_due(client, _fake_nbp_rate_pit38):
    # Krok 17: karta "Do wpisania w deklarację" pokazuje RAZEM DO ZAPŁATY =
    # podatek poz. C (wg aktywnej polityki) + dopłata sekcji G. Bez dywidend
    # w tym roku sekcja G = 0, więc razem = sam podatek poz. C: revenue
    # 10*8*4=320 - koszt 10*5*4=200 = 120 dochodu * 19% = 22.80.
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2024-06-01", "sale_quantity": "10",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    resp = client.get("/pit38?year=2024")
    html = resp.get_data(as_text=True)
    assert "RAZEM DO ZAPŁATY" in html
    assert "22.80" in html or "22,80" in html


def test_pit38_page_whatif_query_params_render_result(client, _fake_nbp_rate_pit38):
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    resp = client.get("/pit38?whatif_qty=5&whatif_price=8.0")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    # revenue (5*8*4=160) - cost (5*5*4=100) = 60 dochodu * 19% ~ 11.40
    assert "11.40" in html or "11,40" in html


def test_pit38_page_whatif_insufficient_lots_shows_error_not_500(client, _fake_nbp_rate_pit38):
    resp = client.get("/pit38?whatif_qty=999&whatif_price=8.0")
    assert resp.status_code == 200
    assert "Brak pokrycia" in resp.get_data(as_text=True)


def test_pit38_print_mode_marks_page_for_print(client):
    resp = client.get("/pit38?print=1")
    html = resp.get_data(as_text=True)
    assert "print-mode" in html


def test_pit38_export_csv_returns_csv_attachment(client, _fake_nbp_rate_pit38):
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    resp = client.get("/pit38/export.csv?year=2024")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert "attachment" in resp.headers["Content-Disposition"]
    assert "pit38_2024.csv" in resp.headers["Content-Disposition"]


def test_pit38_export_xlsx_returns_xlsx_attachment(client, _fake_nbp_rate_pit38):
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    resp = client.get("/pit38/export.xlsx?year=2024")
    assert resp.status_code == 200
    assert resp.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "pit38_2024.xlsx" in resp.headers["Content-Disposition"]
    # niepusty realny plik XLSX (magic bytes ZIP - openpyxl zapisuje jako zip)
    assert resp.data[:2] == b"PK"


# --- /pit38/kreator (krok 27, docs/PLAN_KROK_27_straty_kreator.md) ---

def _make_wizard_app(tmp_path, monkeypatch, filename="krok27_wizard.db"):
    """2023: strata (kupno 10 szt. po 10 EUR, sprzedaż po 5 EUR -> strata 200 PLN
    przy stałym kursie 4.0). 2024: dochód (kupno 10 szt. po 5 EUR, sprzedaż po
    8 EUR -> dochód 120 PLN, podatek 22.80 PLN bez odliczenia straty). Wywołuje
    `losses.rebuild()` od razu, żeby `tax_loss_carryforward` było zasilone
    niezależnie od tego, czy test w ogóle odwiedza /pit38/kreator (który sam
    robi rebuild) — patrz `tax/losses.py::rebuild`."""
    from nokia_tracker.tax import losses as lossesm
    from nokia_tracker.tax import lots as taxlots

    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event", lambda conn, d: (4.0, "stub"))

    db_path = str(tmp_path / filename)
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)

    taxlots.add_lot(conn, "2023-01-10", "own", 10, 10.0)
    taxlots.record_sale(conn, "2023-06-01", 10, 5.0)
    taxlots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    taxlots.record_sale(conn, "2024-06-01", 10, 8.0)
    conn.commit()

    cfg = {"cost_basis_policy": "own_only", "pl_capital_gains_tax_pct": 19.0}
    lossesm.rebuild(conn, cfg)
    conn.close()
    return create_app(db_path), db_path


def test_pit38_wizard_checklist_shows_missing_import(client):
    html = client.get("/pit38/kreator").get_data(as_text=True)
    assert "Wgraj wyciąg za ten rok" in html
    assert "Brak zaimportowanego wyciągu z datą &#39;as of&#39; w tym roku." in html \
        or "Brak zaimportowanego wyciągu z datą 'as of' w tym roku." in html


def test_pit38_wizard_page_lists_deduction_form_for_available_loss(tmp_path, monkeypatch):
    app, _db_path = _make_wizard_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get("/pit38/kreator?year=2024").get_data(as_text=True)
        assert "Strata z 2023" in html
        assert "Zapisz odliczenie" in html


def test_pit38_wizard_deduct_valid_amount_reduces_total_due(tmp_path, monkeypatch):
    app, _db_path = _make_wizard_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        before = c.get("/pit38?year=2024").get_data(as_text=True)
        assert "22.80" in before or "22,80" in before

        wizard_html = c.get("/pit38/kreator?year=2024").get_data(as_text=True)
        loss_id = re.search(r'name="loss_id" value="(\d+)"', wizard_html).group(1)

        resp = c.post(
            "/pit38/kreator/odlicz",
            data={"year": "2024", "loss_id": loss_id, "amount_pln": "50"},
            follow_redirects=False)
        assert resp.status_code == 302
        assert "deduct_error" not in resp.headers["Location"]

        # dochód po odliczeniu: 120 - 50 = 70 * 19% = 13.30
        after = c.get("/pit38?year=2024").get_data(as_text=True)
        assert "13.30" in after or "13,30" in after


def test_pit38_wizard_deduct_over_limit_redirects_with_error_and_no_write(tmp_path, monkeypatch):
    app, db_path = _make_wizard_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        wizard_html = c.get("/pit38/kreator?year=2024").get_data(as_text=True)
        loss_id = re.search(r'name="loss_id" value="(\d+)"', wizard_html).group(1)

        conn = dbm.get_conn(db_path)
        before_count = conn.execute("SELECT COUNT(*) FROM tax_loss_deductions").fetchone()[0]
        conn.close()

        resp = c.post(
            "/pit38/kreator/odlicz",
            data={"year": "2024", "loss_id": loss_id, "amount_pln": "99999"},
            follow_redirects=False)
        assert resp.status_code == 302
        assert "deduct_error" in resp.headers["Location"]

        conn = dbm.get_conn(db_path)
        after_count = conn.execute("SELECT COUNT(*) FROM tax_loss_deductions").fetchone()[0]
        conn.close()
        assert after_count == before_count == 0


def test_pit38_wizard_close_blocked_by_unresolved_balance_conflict(tmp_path, monkeypatch):
    app, db_path = _make_wizard_app(tmp_path, monkeypatch)
    conn = dbm.get_conn(db_path)
    conn.execute(
        "INSERT INTO imports (filename, file_sha256, as_of_date) VALUES ('x','y','2024-12-31')")
    import_id = conn.execute("SELECT id FROM imports").fetchone()[0]
    conn.execute(
        "INSERT INTO import_conflicts (import_id, entity_type, natural_key, existing_json, "
        "incoming_json, resolved) VALUES (?, 'balance', 'k', '{}', '{}', 0)", (import_id,))
    conn.commit()
    conn.close()

    with app.test_client() as c:
        resp = c.post("/pit38/kreator/zamknij", data={"year": "2024"}, follow_redirects=False)
        assert resp.status_code == 302
        assert "close_error=1" in resp.headers["Location"]

    conn = dbm.get_conn(db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM tax_year_closed WHERE year=2024").fetchone()[0] == 0
    conn.close()


def test_pit38_wizard_close_succeeds_and_writes_snapshot_when_steps_done(tmp_path, monkeypatch):
    app, db_path = _make_wizard_app(tmp_path, monkeypatch)
    conn = dbm.get_conn(db_path)
    conn.execute(
        "INSERT INTO imports (filename, file_sha256, as_of_date) VALUES ('x','y','2024-12-31')")
    conn.commit()
    conn.close()

    with app.test_client() as c:
        resp = c.post("/pit38/kreator/zamknij", data={"year": "2024"}, follow_redirects=False)
        assert resp.status_code == 302
        assert "closed=1" in resp.headers["Location"]

    conn = dbm.get_conn(db_path)
    row = conn.execute("SELECT * FROM tax_year_closed WHERE year=2024").fetchone()
    conn.close()
    assert row is not None
    assert row["total_due_pln_snapshot"] == pytest.approx(22.80)


def test_pit38_wizard_reopen_clears_tax_year_closed(tmp_path, monkeypatch):
    app, db_path = _make_wizard_app(tmp_path, monkeypatch)
    conn = dbm.get_conn(db_path)
    conn.execute(
        "INSERT INTO imports (filename, file_sha256, as_of_date) VALUES ('x','y','2024-12-31')")
    conn.commit()
    conn.close()

    with app.test_client() as c:
        c.post("/pit38/kreator/zamknij", data={"year": "2024"})
        resp = c.post("/pit38/kreator/odblokuj", data={"year": "2024"}, follow_redirects=False)
        assert resp.status_code == 302

    conn = dbm.get_conn(db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM tax_year_closed WHERE year=2024").fetchone()[0] == 0
    conn.close()


def test_pit38_page_shows_available_loss_card_and_wizard_link(tmp_path, monkeypatch):
    app, _db_path = _make_wizard_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get("/pit38?year=2024").get_data(as_text=True)
        assert "Straty z lat ubiegłych" in html
        assert "200.00" in html  # total_remaining_pln
        assert 'href="/pit38/kreator?year=2024"' in html


def test_pit38_page_shows_no_loss_message_when_none_available(client):
    html = client.get("/pit38").get_data(as_text=True)
    assert "Brak dostępnych strat" in html
    assert "/pit38/kreator" not in html


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


# --- krok 18: podgląd na żywo (/api/preview/lot, /sale, /dividend) ---

def test_preview_lot_returns_nbp_rate_and_cost_pln(client, _fake_nbp_rate):
    resp = client.get(
        "/api/preview/lot?acquired_date=2024-01-10&quantity=10&price_eur=5&fee_eur=0")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["nbp_rate"] == 4.0
    pln_line = next(l for l in data["lines"] if l["label"] == "Koszt" and l["unit"] == "PLN")
    assert pln_line["value"] == 200.0  # 10 * 5 * 4.0


def test_preview_sale_matches_recorded_sale(client, _fake_nbp_rate):
    # Regresja: podgląd (przed zapisem) i realna zapisana sprzedaż (po zapisie)
    # muszą dać DOKŁADNIE tę samą kwotę podatku — silnik (`simulate_sale`) jest
    # ten sam co pod `/lots/sell`.
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "20", "price_eur": "5.0", "fee_eur": "0",
    })
    preview = client.get(
        "/api/preview/sale?sale_date=2024-06-01&quantity=4&price_eur=11.25&fee_eur=0"
    ).get_json()
    assert preview["ok"] is True
    preview_tax = next(l["value"] for l in preview["lines"] if l["label"] == "Podatek")
    preview_net = next(l["value"] for l in preview["lines"] if l["label"] == "Na rękę")

    client.post("/lots/sell", data={
        "sale_date": "2024-06-01", "sale_quantity": "4",
        "sale_price_eur": "11.25", "sale_fee_eur": "0",
    })
    html = client.get("/sales").get_data(as_text=True)
    assert f"{preview_tax:.2f}" in html
    assert f"{preview_net:.2f}" in html


def test_preview_sale_insufficient_lots_returns_ok_false_not_500(client):
    resp = client.get(
        "/api/preview/sale?sale_date=2024-06-01&quantity=5&price_eur=8&fee_eur=0")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False
    assert "error" in data


def test_preview_dividend_matches_stored_row(client, _fake_nbp_rate):
    preview = client.get(
        "/api/preview/dividend?pay_date=2024-06-15&gross_eur=100&withholding_pct=35"
    ).get_json()
    assert preview["ok"] is True
    preview_due = next(l["value"] for l in preview["lines"] if l["label"] == "Dopłata w PL")

    client.post("/dividends", data={
        "pay_date": "2024-06-15", "gross_eur": "100", "withholding_pct": "35"})
    html = client.get("/dividends").get_data(as_text=True)
    assert f"{preview_due:.2f}" in html


def test_preview_rejects_future_date(client):
    resp = client.get(
        "/api/preview/lot?acquired_date=2099-01-01&quantity=1&price_eur=1&fee_eur=0")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False
    assert "przyszłości" in data["error"]


# --- krok 18: jedna matematyka dywidendowa (PLN na kursie NBP zamrożonym) ---

def test_dividends_totals_use_frozen_nbp_not_current_rate(client, _fake_nbp_rate):
    client.post("/dividends", data={
        "pay_date": "2024-06-15", "gross_eur": "100", "withholding_pct": "35"})
    client.post("/dividends", data={
        "pay_date": "2024-07-15", "gross_eur": "50", "withholding_pct": "35"})
    html = client.get("/dividends").get_data(as_text=True)
    # Kafelek "Brutto" musi być sumą wierszy tabeli (400.00 + 200.00), oba na
    # zamrożonym kursie NBP 4.0 z fixture'a — nie osobną kalkulacją EUR na
    # kursie bieżącym (dawny sensors.dividends_values w tej trasie).
    assert "400.00" in html
    assert "200.00" in html
    assert '<span class="stat-value">600<span class="stat-unit">PLN</span></span>' in html


def test_dividends_yield_on_cost_uses_lots_not_manual_settings(client, _fake_nbp_rate):
    import re
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "100", "price_eur": "3.5", "fee_eur": "0",
    })
    client.post("/dividends", data={
        "pay_date": "2024-06-15", "gross_eur": "100", "withholding_pct": "35"})
    html = client.get("/dividends").get_data(as_text=True)
    m = re.search(r'Yield on cost</span>\s*<span class="stat-value">([^<]+)', html)
    assert m is not None
    value = m.group(1).strip()
    assert value != "—"
    float(value)  # musi się dać sparsować jako liczba, nie pozostać myślnikiem


# --- krok 18: /grants — brak fantomowych wierszy dla niezrealizowanych transz ---

def test_grants_no_phantom_rows_for_unrealized_vests(tmp_path):
    # _make_grants_app tworzy 3 transze (1 ESPP + 2 LTI), żadna nie ma
    # zrealizowanej sprzedaży — przed fixem każda dostawała pusty
    # `<tr><td colspan="N">` mimo braku treści do pokazania.
    app = _make_grants_app(tmp_path)
    with app.test_client() as c:
        html = c.get("/grants").get_data(as_text=True)
        assert 'colspan="8"' not in html
        assert 'colspan="6"' not in html


# --- krok 18: nawigacja w 5 sekcjach — fallback bez JS ---

def test_nav_groups_render_without_js(client):
    html = client.get("/").get_data(as_text=True)
    assert '<details class="nav-group' in html
    for label in ["Pulpit", "Portfel", "Loty", "Sprzedaże", "Granty", "Dywidendy",
                  "PIT-38", "Importy", "Newsy", "Prognozy", "Kopia zapasowa", "Ustawienia"]:
        assert label in html


# --- krok 18: /pit38 Sekcja G schowana, gdy brak dywidend ---

def test_pit38_section_g_hidden_when_no_dividends(client):
    html = client.get("/pit38").get_data(as_text=True)
    assert "Brak dywidend w" in html


# --- krok 24: /dane — kopia zapasowa i przywracanie (docs/PLAN_KROK_24_backup.md) ---

def test_data_page_renders_export_link_and_upload_form(client):
    html = client.get("/dane").get_data(as_text=True)
    assert 'href="/dane/eksport.zip"' in html
    assert 'action="/dane/import/preview"' in html
    assert 'name="backup_file"' in html


def test_data_export_zip_returns_zip_attachment(client):
    resp = client.get("/dane/eksport.zip")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/zip"
    assert "attachment; filename=nokia_tracker_" in resp.headers["Content-Disposition"]
    assert resp.data[:2] == b"PK"  # magiczne bajty ZIP


def test_data_import_preview_shows_diff_and_confirm_form(tmp_path):
    from io import BytesIO

    from nokia_tracker import backup

    db_path = str(tmp_path / "current.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    conn.close()

    other_path = str(tmp_path / "other.db")
    other_conn = dbm.get_conn(other_path)
    dbm.migrate(other_conn)
    other_conn.execute(
        "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur) "
        "VALUES ('2024-01-10', 'own', 10.0, 5.0)")
    other_conn.commit()
    other_conn.close()
    zip_bytes = backup.export_zip(other_path)

    app = create_app(db_path)
    with app.test_client() as c:
        resp = c.post("/dane/import/preview", data={
            "backup_file": (BytesIO(zip_bytes), "nokia_tracker_2026-01-01.zip"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 302
        assert "token=" in resp.headers["Location"]

        follow = c.get(resp.headers["Location"])
        html = follow.get_data(as_text=True)
        assert "lots" in html
        assert 'name="token"' in html
        assert "Potwierdź przywrócenie" in html


def test_data_import_confirm_applies_restore_and_redirects(tmp_path):
    from io import BytesIO

    from nokia_tracker import backup

    db_path = str(tmp_path / "current.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    conn.execute(
        "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur) "
        "VALUES ('2020-01-01', 'own', 1.0, 1.0)")
    conn.commit()
    conn.close()

    other_path = str(tmp_path / "other.db")
    other_conn = dbm.get_conn(other_path)
    dbm.migrate(other_conn)
    other_conn.execute(
        "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur) "
        "VALUES ('2024-01-10', 'own', 10.0, 5.0)")
    other_conn.commit()
    other_conn.close()
    zip_bytes = backup.export_zip(other_path)

    app = create_app(db_path)
    with app.test_client() as c:
        preview_resp = c.post("/dane/import/preview", data={
            "backup_file": (BytesIO(zip_bytes), "nokia_tracker_2026-01-01.zip"),
        }, content_type="multipart/form-data")
        token = preview_resp.headers["Location"].split("token=")[1]

        confirm_resp = c.post("/dane/import/confirm", data={"token": token})
        assert confirm_resp.status_code == 302
        assert "restored=1" in confirm_resp.headers["Location"]

    result = sqlite3.connect(db_path)
    rows = result.execute("SELECT quantity FROM lots").fetchall()
    assert rows == [(10.0,)]  # dane z "other.db" zastąpiły oryginalny lot
    result.close()


def test_data_import_confirm_unknown_token_redirects_safely(client):
    resp = client.post("/dane/import/confirm", data={"token": "nieistniejacy"})
    assert resp.status_code == 302
    assert "error=" in resp.headers["Location"]


def test_data_import_preview_incompatible_schema_shows_error(tmp_path):
    from io import BytesIO

    from nokia_tracker import backup

    db_path = str(tmp_path / "current.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    conn.close()
    client = create_app(db_path).test_client()

    zip_bytes = backup.export_zip(db_path)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        entries = {n: zf.read(n) for n in zf.namelist()}
    manifest = json.loads(entries["manifest.json"])
    manifest["schema_version"] = dbm.SCHEMA_VERSION + 1
    entries["manifest.json"] = json.dumps(manifest).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)

    resp = client.post("/dane/import/preview", data={
        "backup_file": (BytesIO(buf.getvalue()), "nokia_tracker_future.zip"),
    }, content_type="multipart/form-data")
    follow = client.get(resp.headers["Location"])
    html = follow.get_data(as_text=True)
    assert "nowsz" in html.lower()  # komunikat o niekompatybilnym (nowszym) schemacie
    assert "Potwierdź przywrócenie" not in html
    assert "Dywidendy brutto" not in html


# --- krok 25 (0.9.0): /wyniki - XIRR/TWR/atrybucja/benchmark ---

def test_wyniki_page_empty_state(client):
    html = client.get("/wyniki").get_data(as_text=True)
    assert "Wyniki" in html
    assert "Brak jeszcze danych" in html


def test_wyniki_page_shows_xirr_twr_and_attribution(tmp_path, monkeypatch):
    from nokia_tracker import fx
    from nokia_tracker import quotes as quotesm
    from nokia_tracker.models import Candle
    from nokia_tracker.tax import lots as taxlots

    db_path = str(tmp_path / "wyniki.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda c, event_date: (4.0, "stub"))
    taxlots.add_lot(conn, "2023-01-01", "own", 10, 5.0)
    conn.execute(
        "INSERT INTO portfolio_history (date, position_qty, market_value_eur, "
        "market_value_pln) VALUES ('2024-01-01', 10.0, 60.0, 270.0), "
        "('2024-06-01', 10.0, 80.0, 360.0)")

    primary_id = quotesm.ensure_instrument(conn, "NOKIA.HE", "Nokia Oyj", "EUR", "primary")
    eurpln_id = quotesm.ensure_instrument(conn, fx.EURPLN_SYMBOL, "EUR/PLN", "PLN", "fx")
    quotesm.upsert_candles(conn, primary_id, "daily",
                           [Candle(ts="2024-06-01T00:00:00+00:00", close=8.0)], source="yahoo")
    quotesm.upsert_candles(conn, eurpln_id, "daily",
                           [Candle(ts="2024-06-01T00:00:00+00:00", close=4.5)], source="yahoo")
    conn.commit()
    conn.close()

    client = create_app(db_path).test_client()
    resp = client.get("/wyniki")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "XIRR" in html
    assert "TWR" in html
    assert "Atrybucja" in html or "atrybucj" in html.lower()


def test_wyniki_page_yearly_table_from_portfolio_history(tmp_path):
    db_path = str(tmp_path / "wyniki2.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    conn.execute(
        "INSERT INTO portfolio_history (date, position_qty, market_value_eur, "
        "market_value_pln) VALUES "
        "('2023-06-01', 5.0, 50.0, 220.0), ('2023-12-30', 5.0, 55.0, 245.0), "
        "('2024-01-02', 5.0, 55.0, 245.0), ('2024-06-01', 5.0, 60.0, 270.0)")
    conn.commit()
    conn.close()

    client = create_app(db_path).test_client()
    html = client.get("/wyniki").get_data(as_text=True)
    assert "2023" in html
    assert "2024" in html


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


def test_settings_post_saves_other_net_worth_and_threshold(client):
    resp = client.post("/settings", data={
        "ai_primary": "local", "ai_fallback": "gemini",
        "other_net_worth_pln": "150000.5", "concentration_alert_pct": "30.0",
    })
    assert resp.status_code == 302
    html = client.get("/settings").get_data(as_text=True)
    assert 'value="150000.5"' in html
    assert 'value="30.0"' in html


def test_dashboard_restricted_note_shows_forfeit_amount(tmp_path, monkeypatch):
    app = _make_full_portfolio_dashboard_app(tmp_path, monkeypatch, "krok26_dashboard.db")
    with app.test_client() as c:
        html = c.get("/").get_data(as_text=True)
        # grant_a: match_rate = 50/100 = 0.5, lot 100 szt -> forfeit_qty=50,
        # forfeit_value_pln = 50 * 10 (price) * 4 (eurpln) = 2000
        assert "utratę 50,00 akcji dopasowania" in html
        assert "2\xa0000" in html


def test_nav_contains_plan_link(client):
    html = client.get("/").get_data(as_text=True)
    assert 'href="/plan"' in html


# --- /asystent (krok 29): zero żywego AI, ai_chat.ask() mockowane ---

def test_nav_contains_asystent_link(client):
    html = client.get("/").get_data(as_text=True)
    assert 'href="/asystent"' in html


def test_assistant_get_shows_empty_history(client):
    resp = client.get("/asystent")
    assert resp.status_code == 200
    assert "Asystent" in resp.get_data(as_text=True)


def test_assistant_post_calls_ask_and_redirects_without_question_in_url(client, monkeypatch):
    from nokia_tracker.ai import chat as ai_chat
    calls = []
    monkeypatch.setattr(ai_chat, "ask", lambda conn, cfg, q: (calls.append(q), {
        "ok": True, "intent": "ile_moge_sprzedac", "params": {}, "title": "Ile mogę sprzedać",
        "lines": [], "detail_url": "/plan", "error": None, "answer_pl": "Możesz sprzedać 0 akcji.",
    })[1])
    resp = client.post("/asystent", data={"question": "Ile mogę sprzedać?"})
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/asystent"  # bez ?question= — odświeżenie nie powtarza pytania
    assert calls == ["Ile mogę sprzedać?"]


def test_assistant_post_with_empty_question_does_not_call_ask(client, monkeypatch):
    from nokia_tracker.ai import chat as ai_chat
    calls = []
    monkeypatch.setattr(ai_chat, "ask", lambda conn, cfg, q: calls.append(q))
    resp = client.post("/asystent", data={"question": "   "})
    assert resp.status_code == 302
    assert calls == []


def test_assistant_get_with_q_param_asks_then_redirects_to_plain_url(client, monkeypatch):
    # ?q= (pole szybkiego pytania na pulpicie, krok 29.7) MUSI przekierować do
    # czystego /asystent po odpowiedzi — inaczej odświeżenie strony powtarzałoby
    # zapytanie AI (ten sam powód co POST-redirect-GET dla formularza).
    from nokia_tracker.ai import chat as ai_chat
    calls = []
    monkeypatch.setattr(ai_chat, "ask", lambda conn, cfg, q: (calls.append(q), {
        "ok": True, "intent": "inne", "params": {}, "title": "x", "lines": [],
        "detail_url": None, "error": None, "answer_pl": "x",
    })[1])
    resp = client.get("/asystent?q=Ile+zarobi%C5%82em%3F")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/asystent"
    assert calls == ["Ile zarobiłem?"]


def test_assistant_get_without_q_does_not_call_ask(client, monkeypatch):
    from nokia_tracker.ai import chat as ai_chat
    calls = []
    monkeypatch.setattr(ai_chat, "ask", lambda conn, cfg, q: calls.append(q))
    client.get("/asystent")
    assert calls == []


def _insert_chat_log_row(tmp_path, db_name, **overrides):
    db_path = str(tmp_path / db_name)
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    row = {
        "question": "Ile mogę sprzedać?", "intent": "ile_moge_sprzedac",
        "params_json": "{}", "result_json": "{}",
        "answer_pl": "Możesz sprzedać 0 akcji bez ograniczeń.",
        "provider": "local", "ok": 1, "error": None,
    }
    row.update(overrides)
    conn.execute(
        "INSERT INTO chat_log (question, intent, params_json, result_json, answer_pl, "
        "provider, ok, error) VALUES (:question, :intent, :params_json, :result_json, "
        ":answer_pl, :provider, :ok, :error)", row)
    conn.commit()
    conn.close()
    return db_path


def test_assistant_get_renders_history(tmp_path):
    db_path = _insert_chat_log_row(tmp_path, "history.db")
    app = create_app(db_path)
    with app.test_client() as c:
        html = c.get("/asystent").get_data(as_text=True)
    assert "Ile mogę sprzedać?" in html
    assert "Możesz sprzedać 0 akcji bez ograniczeń." in html


def test_assistant_disabled_skips_ask_and_shows_message(client, monkeypatch):
    from nokia_tracker.ai import chat as ai_chat
    calls = []
    monkeypatch.setattr(ai_chat, "ask", lambda conn, cfg, q: calls.append(q))
    client.post("/settings", data={})  # brak pola = odznaczony checkbox = wyłączony
    resp = client.post("/asystent", data={"question": "Ile zarobiłem?"})
    assert resp.status_code == 302
    assert calls == []
    html = client.get("/asystent").get_data(as_text=True)
    assert "wyłączony" in html.lower()


def test_assistant_answer_text_is_escaped_not_raw_html(tmp_path):
    db_path = _insert_chat_log_row(
        tmp_path, "xss.db", question="test", intent="inne",
        answer_pl="<script>alert(1)</script>")
    app = create_app(db_path)
    with app.test_client() as c:
        html = c.get("/asystent").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_api_assistant_get_returns_json(client, monkeypatch):
    from nokia_tracker.ai import chat as ai_chat
    monkeypatch.setattr(ai_chat, "ask", lambda conn, cfg, q: {
        "ok": True, "intent": "ile_moge_sprzedac", "params": {}, "title": "Ile mogę sprzedać",
        "lines": [{"label": "Wolne", "value": 10, "unit": "szt."}], "detail_url": "/plan",
        "error": None, "answer_pl": "Możesz sprzedać 10 akcji.",
    })
    resp = client.get("/api/asystent?q=Ile+mog%C4%99+sprzeda%C4%87%3F")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["answer_pl"] == "Możesz sprzedać 10 akcji."


def test_api_assistant_post_accepts_json_body(client, monkeypatch):
    from nokia_tracker.ai import chat as ai_chat
    calls = []
    monkeypatch.setattr(ai_chat, "ask", lambda conn, cfg, q: (calls.append(q), {
        "ok": True, "intent": "inne", "params": {}, "title": "x", "lines": [],
        "detail_url": None, "error": None, "answer_pl": "x",
    })[1])
    resp = client.post("/api/asystent", json={"question": "Kiedy mam vesting?"})
    assert resp.status_code == 200
    assert calls == ["Kiedy mam vesting?"]


def test_api_assistant_disabled_returns_ok_false_without_calling_ask(client, monkeypatch):
    from nokia_tracker.ai import chat as ai_chat
    calls = []
    monkeypatch.setattr(ai_chat, "ask", lambda conn, cfg, q: calls.append(q))
    client.post("/settings", data={})
    resp = client.get("/api/asystent?q=test")
    data = resp.get_json()
    assert data["ok"] is False
    assert calls == []


def test_assistant_page_shows_ai_status_bar(client):
    html = client.get("/asystent").get_data(as_text=True)
    assert "local (freellmapi)" in html


# --- /api/preview/copilot (krok 33) — zero skutków ubocznych, patrz
# ai/copilot.py::preview() i tests/test_ai_copilot.py dla logiki warunków ---

def test_preview_copilot_returns_ok_shape(client):
    resp = client.get("/api/preview/copilot")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["would_send"] is False  # pusta baza -> brak warunków
    assert data["conditions"] == []
    assert data["lines"] == []


def test_preview_copilot_rejects_malformed_today_param(client):
    resp = client.get("/api/preview/copilot?today=nie-data")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False
    assert "error" in data
