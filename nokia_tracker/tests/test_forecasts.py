"""Zapis prognoz + rozliczanie po target_date (MAPE jako forecast_accuracy_pct)
na sztucznej historii — BLUEPRINT §1: uczciwość prognoz."""
from datetime import date, timedelta

import pytest

from nokia_tracker import forecasts


def _iso(days_offset: int) -> str:
    return (date.today() + timedelta(days=days_offset)).isoformat()


def test_record_forecast_stores_row(conn):
    forecasts.record_forecast(conn, "1w", _iso(7), 10.0, 10.5, 9.5, 11.5, 0.7, "local")
    row = conn.execute("SELECT * FROM forecasts").fetchone()
    assert row["horizon"] == "1w"
    assert row["predicted_price"] == 10.5
    assert row["realized_price"] is None
    assert row["model"] == "local"


def test_settle_due_only_touches_past_target_dates(conn):
    forecasts.record_forecast(conn, "1w", _iso(-1), 10.0, 10.5, 9.5, 11.5, 0.7, "local")  # dojrzała
    forecasts.record_forecast(conn, "1m", _iso(20), 10.0, 11.0, 9.5, 12.5, 0.6, "local")  # nie
    settled = forecasts.settle_due(conn, current_price=10.8)
    assert settled == 1
    rows = {r["horizon"]: r for r in conn.execute("SELECT * FROM forecasts").fetchall()}
    assert rows["1w"]["realized_price"] == 10.8
    assert rows["1w"]["error_pct"] == pytest.approx(abs(10.8 - 10.5) / 10.8 * 100)
    assert rows["1m"]["realized_price"] is None


def test_settle_due_is_idempotent(conn):
    forecasts.record_forecast(conn, "1w", _iso(-1), 10.0, 10.5, 9.5, 11.5, 0.7, "local")
    forecasts.settle_due(conn, current_price=10.8)
    settled_again = forecasts.settle_due(conn, current_price=11.5)
    assert settled_again == 0  # już rozliczona -> nie nadpisuje ponownie
    row = conn.execute("SELECT realized_price FROM forecasts").fetchone()
    assert row["realized_price"] == 10.8


def test_accuracy_pct_none_when_nothing_settled(conn):
    forecasts.record_forecast(conn, "1w", _iso(20), 10.0, 10.5, 9.5, 11.5, 0.7, "local")
    assert forecasts.accuracy_pct(conn) is None


def test_accuracy_pct_perfect_forecast_is_100(conn):
    forecasts.record_forecast(conn, "1w", _iso(-1), 10.0, 10.0, 9.5, 10.5, 0.9, "local")
    forecasts.settle_due(conn, current_price=10.0)  # trafiona idealnie
    assert forecasts.accuracy_pct(conn) == pytest.approx(100.0)


def test_accuracy_pct_averages_multiple_settled(conn):
    forecasts.record_forecast(conn, "1w", _iso(-1), 10.0, 10.0, 9.0, 11.0, 0.5, "local")  # idealna
    forecasts.record_forecast(conn, "1m", _iso(-2), 10.0, 9.0, 8.0, 10.0, 0.5, "local")   # -10%
    settled = forecasts.settle_due(conn, current_price=10.0)
    assert settled == 2
    acc = forecasts.accuracy_pct(conn, n=10)
    assert acc == pytest.approx(100.0 - (0.0 + 10.0) / 2)


def test_accuracy_pct_limits_to_last_n(conn):
    for i in range(5):
        forecasts.record_forecast(conn, "1w", _iso(-1 - i), 10.0, 10.0, 9.0, 11.0, 0.5, "local")
    forecasts.settle_due(conn, current_price=10.0)  # wszystkie idealne -> 100% niezależnie od n
    assert forecasts.accuracy_pct(conn, n=2) == pytest.approx(100.0)
