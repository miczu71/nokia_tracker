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
