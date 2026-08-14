from datetime import datetime, timezone

import pytest

from nokia_tracker import quotes, sensors
from nokia_tracker.models import Candle


def _iso(date_str: str) -> str:
    return f"{date_str}T00:00:00+00:00"


@pytest.fixture
def instrument_id(conn):
    return quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia Oyj", "EUR", "primary")


def test_market_values_computes_change_and_price(conn, instrument_id, monkeypatch):
    monkeypatch.setattr("nokia_tracker.market.is_session_open", lambda: True)
    today = datetime.now(timezone.utc).date().isoformat()

    quotes.upsert_candles(conn, instrument_id, "daily", [
        Candle(ts=_iso("2026-01-01"), close=10.0, high=10.5, low=9.5, volume=1000),
        Candle(ts=_iso(today), close=9.0, high=9.4, low=8.8, volume=2000),
    ])

    v = sensors.market_values(conn, instrument_id)

    assert v["price_eur"] == 9.0
    assert v["prev_close"] == 10.0
    assert v["change_abs_day"] == pytest.approx(-1.0)
    assert v["change_pct_day"] == pytest.approx(-10.0)
    assert v["day_high"] == 9.4
    assert v["day_low"] == 8.8
    assert v["volume"] == 2000
    assert v["market_state"] == "sesja otwarta"
    assert v["market_open"] is True


def test_market_values_market_closed_state(conn, instrument_id, monkeypatch):
    monkeypatch.setattr("nokia_tracker.market.is_session_open", lambda: False)
    v = sensors.market_values(conn, instrument_id)
    assert v["market_state"] == "sesja zamknięta"
    assert v["market_open"] is False


def test_market_values_no_data_returns_none_fields(conn, instrument_id, monkeypatch):
    monkeypatch.setattr("nokia_tracker.market.is_session_open", lambda: False)
    v = sensors.market_values(conn, instrument_id)
    assert v["price_eur"] is None
    assert v["change_abs_day"] is None
    assert v["change_pct_day"] is None
    assert v["sma_20"] is None
    assert v["trend"] is None


def test_market_values_week52_high_low(conn, instrument_id, monkeypatch):
    monkeypatch.setattr("nokia_tracker.market.is_session_open", lambda: False)
    quotes.upsert_candles(conn, instrument_id, "daily", [
        Candle(ts=_iso("2026-01-01"), close=10.0, high=12.0, low=8.0),
        Candle(ts=_iso("2026-02-01"), close=9.0, high=9.5, low=7.0),
    ])
    v = sensors.market_values(conn, instrument_id)
    assert v["week52_high"] == 12.0
    assert v["week52_low"] == 7.0


def test_market_values_prefers_latest_intraday_for_price(conn, instrument_id, monkeypatch):
    monkeypatch.setattr("nokia_tracker.market.is_session_open", lambda: True)
    today = datetime.now(timezone.utc).date().isoformat()
    quotes.upsert_candles(conn, instrument_id, "daily",
                          [Candle(ts=_iso(today), close=9.0)])
    quotes.upsert_candles(conn, instrument_id, "intraday",
                          [Candle(ts=f"{today}T15:30:00+00:00", close=9.15)])
    v = sensors.market_values(conn, instrument_id)
    assert v["price_eur"] == 9.15  # intraday nowsza niż dzienna -> wygrywa


# --- benchmark_values ---

@pytest.fixture
def bench_ids(conn):
    return {
        "primary": quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia", "EUR", "primary"),
        "ericsson": quotes.ensure_instrument(conn, "ERIC-B.ST", "Ericsson", "SEK", "benchmark"),
        "omxh25": quotes.ensure_instrument(conn, "^OMXH25", "OMXH25", "EUR", "benchmark"),
        "eurpln": quotes.ensure_instrument(conn, "EURPLN=X", "EUR/PLN", "PLN", "fx"),
        "eurusd": quotes.ensure_instrument(conn, "EURUSD=X", "EUR/USD", "USD", "fx"),
        "adr": quotes.ensure_instrument(conn, "NOK", "Nokia ADR", "USD", "adr"),
    }


def _seed_daily(conn, iid, closes: list[float]):
    from datetime import date, timedelta
    start = date(2020, 1, 1)  # start daleko w przeszłości - miejsce na >365 przyrostów dnia
    quotes.upsert_candles(conn, iid, "daily", [
        Candle(ts=_iso((start + timedelta(days=i)).isoformat()), close=c)
        for i, c in enumerate(closes)
    ])


def test_benchmark_values_price_pln_from_eurpln_rate(conn, bench_ids):
    _seed_daily(conn, bench_ids["primary"], [8.0, 9.0])
    _seed_daily(conn, bench_ids["eurpln"], [4.3])
    v = sensors.benchmark_values(conn, bench_ids["primary"], bench_ids["ericsson"],
                                 bench_ids["omxh25"], bench_ids["eurpln"])
    assert v["eurpln_rate"] == 4.3
    assert v["price_pln"] == pytest.approx(9.0 * 4.3)


def test_benchmark_values_rel_perf_1d(conn, bench_ids):
    _seed_daily(conn, bench_ids["primary"], [10.0, 11.0])   # +10%
    _seed_daily(conn, bench_ids["omxh25"], [100.0, 102.0])  # +2%
    v = sensors.benchmark_values(conn, bench_ids["primary"], bench_ids["ericsson"],
                                 bench_ids["omxh25"], bench_ids["eurpln"])
    assert v["rel_perf_1d_vs_omxh25"] == pytest.approx(8.0)  # 10% - 2%
    assert v["alpha_verdict"] == "specyficzne dla spółki"  # |8| > próg 2.0


def test_benchmark_values_alpha_verdict_market_trend(conn, bench_ids):
    _seed_daily(conn, bench_ids["primary"], [10.0, 10.1])   # +1.0%
    _seed_daily(conn, bench_ids["omxh25"], [100.0, 100.8])  # +0.8%
    v = sensors.benchmark_values(conn, bench_ids["primary"], bench_ids["ericsson"],
                                 bench_ids["omxh25"], bench_ids["eurpln"])
    assert v["rel_perf_1d_vs_omxh25"] == pytest.approx(0.2, abs=1e-6)
    assert v["alpha_verdict"] == "trend rynkowy"  # |0.2| < próg 0.5


def test_benchmark_values_beta_exact(conn, bench_ids):
    # WAŻNE: zwroty MUSZĄ mieć realną wariancję — stały % dziennie (np. same
    # +1%) ma wariancję ~0 matematycznie, a przez błąd zmiennoprzecinkowy
    # z powtarzanego mnożenia (x*1.01 ×65) daje ~1e-16 zamiast czystego zera,
    # co przy dzieleniu cov/var_b w beta() wywraca wynik numerycznie (złapane
    # w praktyce: dało 0.107 zamiast 2.0). Cykl RÓŻNYCH zwrotów -> realna
    # wariancja, a = 2*b element-po-elemencie -> beta MUSI wyjść dokładnie 2.0.
    cycle = [0.01, -0.02, 0.015, -0.005, 0.03, -0.012, 0.008]
    omxh25_closes = [100.0]
    nokia_closes = [10.0]
    for i in range(65):
        r = cycle[i % len(cycle)]
        omxh25_closes.append(omxh25_closes[-1] * (1 + r))
        nokia_closes.append(nokia_closes[-1] * (1 + 2 * r))
    _seed_daily(conn, bench_ids["primary"], nokia_closes)
    _seed_daily(conn, bench_ids["omxh25"], omxh25_closes)
    v = sensors.benchmark_values(conn, bench_ids["primary"], bench_ids["ericsson"],
                                 bench_ids["omxh25"], bench_ids["eurpln"])
    assert v["beta_60d"] == pytest.approx(2.0, abs=1e-4)


def test_benchmark_values_adr_and_spread(conn, bench_ids):
    _seed_daily(conn, bench_ids["primary"], [9.0])
    _seed_daily(conn, bench_ids["eurusd"], [1.1])
    _seed_daily(conn, bench_ids["adr"], [10.0])  # implikowany EUR = 10/1.1 = 9.0909
    v = sensors.benchmark_values(conn, bench_ids["primary"], bench_ids["ericsson"],
                                 bench_ids["omxh25"], bench_ids["eurpln"],
                                 adr_id=bench_ids["adr"], eurusd_id=bench_ids["eurusd"])
    assert v["adr_price_usd"] == 10.0
    expected_spread = (9.0 - 10.0 / 1.1) / 9.0 * 100
    assert v["spread_vs_adr"] == pytest.approx(expected_spread)


def test_benchmark_values_no_adr_configured_stays_none(conn, bench_ids):
    _seed_daily(conn, bench_ids["primary"], [9.0])
    v = sensors.benchmark_values(conn, bench_ids["primary"], bench_ids["ericsson"],
                                 bench_ids["omxh25"], bench_ids["eurpln"])
    assert v["adr_price_usd"] is None
    assert v["spread_vs_adr"] is None


def test_benchmark_values_no_data_all_none(conn, bench_ids):
    v = sensors.benchmark_values(conn, bench_ids["primary"], bench_ids["ericsson"],
                                 bench_ids["omxh25"], bench_ids["eurpln"])
    assert v["ericsson_price"] is None
    assert v["omxh25_value"] is None
    assert v["rel_perf_1d_vs_omxh25"] is None
    assert v["beta_60d"] is None
    assert v["alpha_verdict"] is None


# --- ai_values ---

def _insert_scored_news(conn, title, sentiment, impact, hours_ago=1):
    from datetime import datetime, timedelta, timezone
    published_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    cur = conn.execute(
        "INSERT INTO news (title, url_canonical, title_hash, published_at) VALUES (?, ?, ?, ?)",
        (title, f"https://example.com/{title}", f"hash-{title}", published_at))
    news_id = cur.lastrowid
    conn.execute(
        "INSERT INTO news_scores (news_id, sentiment, impact, horizon, thesis_pl, tags, model) "
        "VALUES (?, ?, ?, 'weeks', 'teza', '[\"kontrakt\"]', 'local')",
        (news_id, sentiment, impact))
    conn.commit()
    return news_id


def test_ai_values_no_scored_news_returns_none_fields(conn):
    v = sensors.ai_values(conn)
    assert v["sentiment_score"] is None
    assert v["sentiment_label"] is None
    assert v["impact_score"] is None
    assert v["news_count_24h"] == 0
    assert v["top_news"] is None
    assert v["top_news_attrs"] == {"items": []}


def test_ai_values_averages_sentiment_and_impact(conn):
    _insert_scored_news(conn, "Pozytywny news", 0.8, 3)
    _insert_scored_news(conn, "Neutralny news", 0.0, 1)
    v = sensors.ai_values(conn)
    assert v["sentiment_score"] == pytest.approx(0.4)
    assert v["impact_score"] == pytest.approx(2.0)
    assert v["news_count_24h"] == 2


def test_ai_values_sentiment_label_thresholds(conn):
    _insert_scored_news(conn, "Bardzo dobry", 0.5, 2)
    assert sensors.ai_values(conn)["sentiment_label"] == "pozytywny"


def test_ai_values_excludes_news_older_than_24h(conn):
    _insert_scored_news(conn, "Stary news", 0.9, 3, hours_ago=48)
    v = sensors.ai_values(conn)
    assert v["news_count_24h"] == 0
    assert v["sentiment_score"] is None


def test_ai_values_top_news_state_and_attrs(conn):
    _insert_scored_news(conn, "Ważny news", 0.5, 3)
    _insert_scored_news(conn, "Mniej ważny news", 0.1, 1)
    v = sensors.ai_values(conn)
    assert v["top_news"] == "Ważny news"  # impact DESC -> najważniejszy pierwszy
    items = v["top_news_attrs"]["items"]
    assert items[0]["title"] == "Ważny news"
    assert items[0]["tags"] == ["kontrakt"]


def test_ai_values_provider_active_and_calls_today(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.sensors.ai_provider.active_provider", lambda: "gemini")
    monkeypatch.setattr("nokia_tracker.sensors.ai_usage.calls_today", lambda c: 7)
    v = sensors.ai_values(conn)
    assert v["ai_provider_active"] == "gemini"
    assert v["ai_calls_today"] == 7


# --- forecast_values ---

def test_forecast_values_no_data_returns_empty_defaults(conn):
    v = sensors.forecast_values(conn)
    assert v["forecast_1w_eur"] is None
    assert v["forecast_1w_eur_attrs"] == {}
    assert v["forecast_accuracy_pct"] is None
    assert v["daily_briefing"] is None
    assert v["daily_briefing_attrs"] == {}
    assert v["ai_recommendation"] is None
    assert v["ai_recommendation_attrs"] == {}


def test_forecast_values_picks_latest_forecast_per_horizon(conn):
    from nokia_tracker import forecasts as forecastsm
    forecastsm.record_forecast(conn, "1w", "2026-08-03", 9.0, 9.2, 8.8, 9.6, 0.6, "local")
    forecastsm.record_forecast(conn, "1w", "2026-08-04", 9.1, 9.3, 8.9, 9.7, 0.65, "gemini")
    v = sensors.forecast_values(conn)
    assert v["forecast_1w_eur"] == 9.3  # nowsza (created_at DESC) wygrywa
    assert v["forecast_1w_eur_attrs"]["model"] == "gemini"
    assert v["forecast_1w_eur_attrs"]["ci_low"] == 8.9


def test_forecast_values_daily_briefing_and_recommendation(conn):
    conn.execute(
        "INSERT INTO briefings (generated_at, text, tts_text, sentiment_avg, news_count, "
        "verdict, key_risks, recommendation, recommendation_reason_pl, "
        "recommendation_confidence, model) VALUES "
        "('2026-07-27T18:00:00+00:00', 'Pełny tekst.', 'Tekst TTS.', 0.3, 5, "
        "'trend rynkowy', '[\"ryzyko A\"]', 'trzymaj', 'Uzasadnienie.', 0.6, 'local')")
    conn.commit()
    v = sensors.forecast_values(conn)
    assert v["daily_briefing"] == "2026-07-27 · trend rynkowy"
    assert v["daily_briefing_attrs"]["text"] == "Pełny tekst."
    assert v["daily_briefing_attrs"]["key_risks"] == ["ryzyko A"]
    assert v["ai_recommendation"] == "trzymaj"
    assert v["ai_recommendation_attrs"]["reason_pl"] == "Uzasadnienie."
    assert v["ai_recommendation_attrs"]["confidence"] == 0.6
    assert "edukacyjna" in v["ai_recommendation_attrs"]["disclaimer"]


# --- dividends_values ---

_TAX_CFG = {"finnish_withholding_pct": 35.0, "treaty_withholding_pct": 15.0,
           "pl_capital_gains_tax_pct": 19.0}


def _insert_dividend(conn, gross_eur, withholding_pct=None, pay_date="2026-06-15"):
    conn.execute(
        "INSERT INTO dividends (pay_date, gross_eur, withholding_pct) VALUES (?, ?, ?)",
        (pay_date, gross_eur, withholding_pct))
    conn.commit()


def test_dividends_values_no_rows_all_zero(conn):
    v = sensors.dividends_values(conn, _TAX_CFG, cost_basis_eur=800.0)
    assert v["dividends_gross_eur"] == 0.0
    assert v["dividends_net_eur"] == 0.0
    assert v["dividend_yield_on_cost_pct"] == 0.0


def test_dividends_values_matches_blueprint_example(conn):
    _insert_dividend(conn, 100.0, withholding_pct=35.0)
    v = sensors.dividends_values(conn, _TAX_CFG, cost_basis_eur=None)
    assert v["dividends_gross_eur"] == pytest.approx(100.0)
    assert v["dividends_net_eur"] == pytest.approx(65.0)
    assert v["withholding_paid_eur"] == pytest.approx(35.0)
    assert v["pl_tax_due_eur"] == pytest.approx(4.0)
    assert v["reclaimable_from_finland_eur"] == pytest.approx(20.0)
    assert v["dividend_yield_on_cost_pct"] is None  # brak cost_basis -> brak yieldu


def test_dividends_values_sums_multiple_rows(conn):
    _insert_dividend(conn, 100.0, withholding_pct=35.0, pay_date="2026-01-15")
    _insert_dividend(conn, 50.0, withholding_pct=35.0, pay_date="2026-06-15")
    v = sensors.dividends_values(conn, _TAX_CFG, cost_basis_eur=800.0)
    assert v["dividends_gross_eur"] == pytest.approx(150.0)
    assert v["dividend_yield_on_cost_pct"] == pytest.approx(150.0 / 800.0 * 100)


def test_dividends_values_missing_withholding_pct_uses_settings_default(conn):
    _insert_dividend(conn, 100.0, withholding_pct=None)
    v = sensors.dividends_values(conn, _TAX_CFG, cost_basis_eur=None)
    assert v["withholding_paid_eur"] == pytest.approx(35.0)  # domyślne 35% z ustawień


# --- lots_values ---

_LOTS_CFG = {"cost_basis_policy": "own_only", "pl_capital_gains_tax_pct": 19.0, "tax_year": 2024}


@pytest.fixture(autouse=False)
def _fake_nbp_rate_for_lots(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


def test_lots_values_empty_db_all_zero(conn):
    v = sensors.lots_values(conn, _LOTS_CFG)
    assert v["lots_total_qty"] == 0.0
    assert v["lots_open_count"] == 0
    assert v["lots_cost_basis_pln"] == 0.0
    assert v["realized_income_pln"] == 0.0
    assert v["realized_tax_pln"] == 0.0


def test_lots_values_open_lots_and_realized_sale(conn, _fake_nbp_rate_for_lots):
    from nokia_tracker.tax import lots as taxlots
    taxlots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    taxlots.add_lot(conn, "2024-02-10", "lti", 4, 0.0)
    taxlots.record_sale(conn, "2024-06-01", 6, 8.0)

    v = sensors.lots_values(conn, _LOTS_CFG)

    # 10 own - 6 sprzedane = 4 zostaje + 4 LTI otwarte = 8 total_qty, 2 loty otwarte
    assert v["lots_total_qty"] == pytest.approx(8.0)
    assert v["lots_open_count"] == 2
    # own_only: uznaje tylko koszt pozostałych 4 własnych akcji (4*5*4=80),
    # LTI (koszt 0 EUR) nie jest uznawany w own_only mimo że jest otwarty
    assert v["lots_cost_basis_pln"] == pytest.approx(4 * 5.0 * 4.0)
    # sprzedano 6 z 10 własnych po 8 EUR -> revenue 6*8*4=192, cost 6*5*4=120
    assert v["realized_income_pln"] == pytest.approx(192 - 120)
    assert v["realized_tax_pln"] == pytest.approx((192 - 120) * 0.19, abs=0.01)


def test_lots_values_defaults_tax_year_to_current_year_when_unset(conn, _fake_nbp_rate_for_lots, monkeypatch):
    from datetime import datetime
    from nokia_tracker.tax import lots as taxlots
    fixed_now = datetime(2024, 12, 1)
    monkeypatch.setattr("nokia_tracker.sensors.datetime", type(
        "FixedDatetime", (), {"now": staticmethod(lambda tz=None: fixed_now)}))
    taxlots.add_lot(conn, "2024-01-10", "own", 5, 5.0)
    taxlots.record_sale(conn, "2024-06-01", 5, 8.0)

    cfg_no_year = {"cost_basis_policy": "own_only", "pl_capital_gains_tax_pct": 19.0, "tax_year": 0}
    v = sensors.lots_values(conn, cfg_no_year)
    assert v["realized_income_pln"] == pytest.approx((5 * 8.0 - 5 * 5.0) * 4.0)


# --- grants_values ---

def test_grants_values_empty_db(conn):
    v = sensors.grants_values(conn)
    assert v["unvested_qty"] == 0.0
    assert v["next_vest_date"] is None
    assert v["next_vest_date_attrs"] == {"next_vest_qty": None}


def test_grants_values_unvested_qty_sums_all_pending_regardless_of_date(conn, monkeypatch):
    from datetime import datetime

    from nokia_tracker.tax import grants as grantsm
    monkeypatch.setattr("nokia_tracker.sensors.datetime", type(
        "FixedDatetime", (), {"now": staticmethod(lambda tz=None: datetime(2026, 7, 28))}))

    grant_id = grantsm.add_grant(conn, "lti", "2025-07-07", None, "lti_grant:g1")
    # 2026-07-06 już minęło (dziś 2026-07-28), ale status wciąż 'pending' - scheduler
    # vestingu (krok 14) jeszcze nie istnieje, więc liczy się mimo przeszłej daty.
    grantsm.add_vest(conn, grant_id, "2026-07-06", 634.0, "lti_vest:g1:2026-07-06:634.0")
    grantsm.add_vest(conn, grant_id, "2027-07-06", 633.0, "lti_vest:g1:2027-07-06:633.0")
    espp_grant_id = grantsm.add_grant(conn, "espp", "2025-10-27", 12.0, "espp_grant:x")
    grantsm.add_vest(conn, espp_grant_id, "2026-04-27", 12.0, "espp_vest:x")

    v = sensors.grants_values(conn)
    assert v["unvested_qty"] == pytest.approx(634.0 + 633.0 + 12.0)
    # next_vest_date liczy tylko przyszłe daty (mimo że 2026-07-06 też pending, już minęło)
    assert v["next_vest_date"] == "2027-07-06"
    assert v["next_vest_date_attrs"] == {"next_vest_qty": 633.0}


def test_grants_values_ignores_vested_and_cancelled_status(conn):
    from nokia_tracker.tax import grants as grantsm
    grant_id = grantsm.add_grant(conn, "lti", "2025-07-07", None, "lti_grant:g1")
    grantsm.add_vest(conn, grant_id, "2099-01-01", 100.0, "lti_vest:g1:vested", status="vested")
    grantsm.add_vest(conn, grant_id, "2099-02-01", 50.0, "lti_vest:g1:cancelled", status="cancelled")
    grantsm.add_vest(conn, grant_id, "2099-03-01", 25.0, "lti_vest:g1:pending", status="pending")

    v = sensors.grants_values(conn)
    assert v["unvested_qty"] == pytest.approx(25.0)
    assert v["next_vest_date"] == "2099-03-01"


# --- pit38_values ---

_PIT38_CFG = {
    "cost_basis_policy": "own_only", "pl_capital_gains_tax_pct": 19.0,
    "treaty_withholding_pct": 15.0, "finnish_withholding_pct": 35.0, "tax_year": 2024,
}


@pytest.fixture(autouse=False)
def _fake_nbp_rate_for_pit38(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


def test_pit38_values_empty_db_all_zero(conn):
    v = sensors.pit38_values(conn, _PIT38_CFG)
    assert v["pit38_income_pln"] == 0.0
    assert v["pit38_tax_pln"] == 0.0
    assert v["pit38_dividend_due_pln"] == 0.0
    assert v["pit38_reclaimable_pln"] == 0.0


def test_pit38_values_uses_active_policy_from_cfg(conn, _fake_nbp_rate_for_pit38):
    from nokia_tracker.tax import lots as taxlots
    taxlots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    taxlots.add_lot(conn, "2024-02-10", "lti", 10, 0.0)
    taxlots.record_sale(conn, "2024-06-01", 20, 8.0)

    v_own_only = sensors.pit38_values(conn, dict(_PIT38_CFG, cost_basis_policy="own_only"))
    v_all = sensors.pit38_values(conn, dict(_PIT38_CFG, cost_basis_policy="all_at_acquisition"))
    assert v_own_only["pit38_tax_pln"] >= v_all["pit38_tax_pln"]


def test_pit38_values_dividend_totals_match_section_g(conn, _fake_nbp_rate_for_pit38):
    from nokia_tracker.tax import dividends as taxdiv
    taxdiv.add_dividend(
        conn, record_date="2024-03-15", purchase_date="2024-04-01",
        entitled_quantity=1.0, gross_eur=100.0, taxes_eur=35.0, fees_eur=0.0,
        reinvested_eur=65.0, purchase_price_eur=1.0, purchased_shares=0.01)
    v = sensors.pit38_values(conn, _PIT38_CFG)
    # przykład BLUEPRINT skalowany kursem stub 4.0: 4 EUR dopłaty, 20 EUR do odzysku
    assert v["pit38_dividend_due_pln"] == pytest.approx(4.0 * 4.0)
    assert v["pit38_reclaimable_pln"] == pytest.approx(20.0 * 4.0)


def test_pit38_values_defaults_tax_year_to_current_year_when_unset(
        conn, _fake_nbp_rate_for_pit38, monkeypatch):
    from datetime import datetime
    from nokia_tracker.tax import lots as taxlots
    fixed_now = datetime(2024, 12, 1)
    monkeypatch.setattr("nokia_tracker.sensors.datetime", type(
        "FixedDatetime", (), {"now": staticmethod(lambda tz=None: fixed_now)}))
    taxlots.add_lot(conn, "2024-01-10", "own", 5, 5.0)
    taxlots.record_sale(conn, "2024-06-01", 5, 8.0)

    cfg_no_year = dict(_PIT38_CFG, tax_year=0)
    v = sensors.pit38_values(conn, cfg_no_year)
    assert v["pit38_income_pln"] == pytest.approx((5 * 8.0 - 5 * 5.0) * 4.0)


# --- whatif_values ---

def test_whatif_values_no_price_returns_none(conn):
    v = sensors.whatif_values(conn, _PIT38_CFG, price_eur=None)
    assert v["whatif_sell_all_tax_pln"] is None


def test_whatif_values_no_open_lots_returns_none(conn):
    v = sensors.whatif_values(conn, _PIT38_CFG, price_eur=10.0)
    assert v["whatif_sell_all_tax_pln"] is None


def test_whatif_values_sells_all_open_lots_at_current_price(conn, _fake_nbp_rate_for_pit38):
    from nokia_tracker.tax import lots as taxlots
    taxlots.add_lot(conn, "2024-01-10", "own", 10, 5.0)

    v = sensors.whatif_values(conn, _PIT38_CFG, price_eur=8.0)
    # revenue (10*8*4=320) - cost (10*5*4=200) = 120 dochodu * 19% = 22.80
    assert v["whatif_sell_all_tax_pln"] == pytest.approx(120 * 0.19, abs=0.01)

    # symulacja nie zmienia bazy — loty wciąż otwarte po wywołaniu
    remaining = conn.execute("SELECT qty_remaining FROM lots").fetchone()["qty_remaining"]
    assert remaining == pytest.approx(10)


# --- krok 25 (0.9.0): wyniki - XIRR/TWR/atrybucja/benchmark ---

def test_results_values_none_without_price_or_rate(conn):
    v = sensors.results_values(conn, price_eur=None, eurpln_rate=4.5,
                               benchmark_instrument_id=1)
    assert v == {
        "xirr_own_pct": None, "twr_pct": None, "fx_effect_pln": None,
        "benchmark_omxh25_counterfactual_pln": None,
    }
    v2 = sensors.results_values(conn, price_eur=6.0, eurpln_rate=None,
                                benchmark_instrument_id=1)
    assert v2["xirr_own_pct"] is None


def test_results_values_computes_xirr_and_fx_effect(conn, _fake_nbp_rate_for_pit38):
    from nokia_tracker.tax import lots as taxlots
    taxlots.add_lot(conn, "2023-01-01", "own", 10, 5.0)

    v = sensors.results_values(conn, price_eur=8.0, eurpln_rate=4.5,
                               benchmark_instrument_id=None)

    assert v["xirr_own_pct"] is not None
    assert v["xirr_own_pct"] > 0  # zarobił (kurs 5 -> 8)
    assert v["fx_effect_pln"] is not None
    assert v["benchmark_omxh25_counterfactual_pln"] is None  # brak instrument_id


def test_results_values_twr_none_without_portfolio_history(conn, _fake_nbp_rate_for_pit38):
    from nokia_tracker.tax import lots as taxlots
    taxlots.add_lot(conn, "2023-01-01", "own", 10, 5.0)

    v = sensors.results_values(conn, price_eur=8.0, eurpln_rate=4.5,
                               benchmark_instrument_id=None)

    assert v["twr_pct"] is None  # portfolio_history jeszcze puste (job nocny go wypełnia)


def test_results_values_twr_computed_from_portfolio_history(conn, _fake_nbp_rate_for_pit38):
    from nokia_tracker.tax import lots as taxlots
    taxlots.add_lot(conn, "2023-01-01", "own", 10, 5.0)
    conn.execute(
        "INSERT INTO portfolio_history (date, position_qty, market_value_eur) "
        "VALUES ('2024-01-01', 10.0, 100.0), ('2024-01-02', 10.0, 110.0)")
    conn.commit()

    v = sensors.results_values(conn, price_eur=8.0, eurpln_rate=4.5,
                               benchmark_instrument_id=None)

    assert v["twr_pct"] == pytest.approx(10.0, abs=0.01)  # 100 -> 110, bez wpłat tego dnia


def test_results_values_benchmark_counterfactual(conn, _fake_nbp_rate_for_pit38):
    from nokia_tracker.tax import lots as taxlots
    taxlots.add_lot(conn, "2023-01-01", "own", 10, 5.0)
    bench_id = quotes.ensure_instrument(conn, "^OMXH25", "OMXH25", "EUR", "benchmark")
    quotes.upsert_candles(conn, bench_id, "daily",
                          [Candle(ts="2023-01-01T00:00:00+00:00", close=100.0),
                           Candle(ts="2024-06-01T00:00:00+00:00", close=110.0)],
                          source="yahoo")

    v = sensors.results_values(conn, price_eur=8.0, eurpln_rate=4.5,
                               benchmark_instrument_id=bench_id)

    assert v["benchmark_omxh25_counterfactual_pln"] == pytest.approx(55.0, abs=0.01)
