"""Web UI (Flask): smoke testy tras + zapis formularzy portfela/dywidend/
ustawień + no-store na HTML (BLUEPRINT §3/§9, krok 9). Zero żywego AI —
/analyze-now mockuje analysis.run_daily_analysis."""
import io
import json
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
                                  "/dane"])
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
