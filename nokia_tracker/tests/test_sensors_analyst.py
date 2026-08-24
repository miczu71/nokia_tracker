"""sensors.analyst_values() — czyta ostatni snapshot `analyst_targets`
(zapisany przez analyst.py/main.py, NIE tutaj — views/sensory są
read-only, docs/PLAN_0_26_0_konsensus.md §5d)."""
import pytest

from nokia_tracker import sensors


def _insert_snapshot(conn, as_of_date="2026-08-24", mean=10.32455, source="yahoo",
                     n_analysts=22, rating="hold"):
    conn.execute(
        "INSERT INTO analyst_targets (as_of_date, fetched_at, low_eur, mean_eur, "
        "median_eur, high_eur, n_analysts, rating, currency, source) "
        "VALUES (?, '2026-08-24T10:00:00+00:00', 4.65, ?, 10.125, 18.0, ?, ?, 'EUR', ?)",
        (as_of_date, mean, n_analysts, rating, source))
    conn.commit()


def test_no_snapshot_returns_none(conn):
    v = sensors.analyst_values(conn, price_eur=8.72)
    assert v["analyst_target_mean_eur"] is None
    assert v["analyst_target_mean_eur_attrs"] == {}


def test_snapshot_present_computes_distance_to_mean(conn):
    _insert_snapshot(conn)
    v = sensors.analyst_values(conn, price_eur=8.72)
    assert v["analyst_target_mean_eur"] == pytest.approx(10.32455)
    attrs = v["analyst_target_mean_eur_attrs"]
    assert attrs["low"] == pytest.approx(4.65)
    assert attrs["high"] == pytest.approx(18.0)
    assert attrs["n_analysts"] == 22
    assert attrs["rating"] == "hold"
    assert attrs["source"] == "yahoo"
    assert attrs["as_of_date"] == "2026-08-24"
    assert attrs["distance_pct"] == pytest.approx((10.32455 - 8.72) / 8.72 * 100)


def test_distance_pct_none_when_price_missing(conn):
    _insert_snapshot(conn)
    v = sensors.analyst_values(conn, price_eur=None)
    assert v["analyst_target_mean_eur_attrs"]["distance_pct"] is None


def test_picks_most_recent_snapshot_by_date(conn):
    _insert_snapshot(conn, as_of_date="2026-08-20", mean=9.0, source="yahoo")
    _insert_snapshot(conn, as_of_date="2026-08-24", mean=10.32455, source="yahoo")
    v = sensors.analyst_values(conn, price_eur=8.72)
    assert v["analyst_target_mean_eur"] == pytest.approx(10.32455)
