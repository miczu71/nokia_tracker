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
