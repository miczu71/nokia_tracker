"""Migracja v14 (docs/PLAN_0_26_0_konsensus.md §3): tabela `analyst_targets`
+ kolumna `forecasts.source`.

Kryterium twarde z planu: wstawienie wiersza konsensusu do `forecasts` NIE
MOŻE zmienić ani jednej liczby, którą aplikacja pokazywała przed 0.26.0.
Bez kolumny `source` wiersz konsensusu z `horizon='12m'` przesłania prognozę
AI 12m w `sensors.forecast_values()` i miesza się do `accuracy_pct()`.
"""
from datetime import date, timedelta

import pytest

from nokia_tracker import db as dbm
from nokia_tracker import forecasts, sensors


def _iso(days_offset: int) -> str:
    return (date.today() + timedelta(days=days_offset)).isoformat()


# --- schemat -----------------------------------------------------------------

def test_schema_version_is_14(conn):
    assert dbm.SCHEMA_VERSION == 14
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 14


def test_analyst_targets_table_exists_with_expected_columns(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(analyst_targets)")}
    assert cols == {
        "id", "as_of_date", "fetched_at", "low_eur", "mean_eur", "median_eur",
        "high_eur", "n_analysts", "rating", "currency", "source", "trend_json",
    }


def test_analyst_targets_unique_per_day_and_source(conn):
    for _ in range(2):
        conn.execute(
            "INSERT INTO analyst_targets (as_of_date, fetched_at, low_eur, mean_eur, "
            "median_eur, high_eur, n_analysts, rating, currency, source) "
            "VALUES ('2026-08-24', '2026-08-24T10:00:00+00:00', 4.65, 10.32, 10.13, "
            "18.0, 22, 'hold', 'EUR', 'yahoo') "
            "ON CONFLICT(as_of_date, source) DO UPDATE SET mean_eur = excluded.mean_eur")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) c FROM analyst_targets").fetchone()["c"] == 1


def test_forecasts_has_source_column_defaulting_to_ai(conn):
    forecasts.record_forecast(conn, "1w", _iso(7), 10.0, 10.5, 9.5, 11.5, 0.7, "local")
    row = conn.execute("SELECT source FROM forecasts").fetchone()
    assert row["source"] == "ai"


# --- niezmienniki: konsensus nie zanieczyszcza liczb AI ----------------------

def _record_consensus(conn, predicted: float, target_days: int) -> None:
    forecasts.record_forecast(
        conn, "12m", _iso(target_days), 8.72, predicted, 4.65, 18.0, None,
        "consensus:yahoo", source="consensus")


def test_accuracy_pct_ignores_consensus_rows_by_default(conn):
    # AI trafiła idealnie, konsensus pomylił się o 50% — trafność AI musi
    # zostać 100%, inaczej po cichu zmieniliśmy istniejący wskaźnik.
    forecasts.record_forecast(conn, "1w", _iso(-1), 10.0, 10.0, 9.0, 11.0, 0.5, "local")
    _record_consensus(conn, predicted=5.0, target_days=-1)
    forecasts.settle_due(conn, current_price=10.0)

    assert forecasts.accuracy_pct(conn) == pytest.approx(100.0)
    assert forecasts.accuracy_pct(conn, source="consensus") == pytest.approx(50.0)


def test_settle_due_settles_consensus_rows_too(conn):
    """`settle_due()` celowo NIE filtruje po source — konsensus ma być
    rozliczany tą samą matematyką, bez drugiej implementacji MAPE."""
    _record_consensus(conn, predicted=9.0, target_days=-1)
    assert forecasts.settle_due(conn, current_price=10.0) == 1
    row = conn.execute("SELECT realized_price, error_pct FROM forecasts").fetchone()
    assert row["realized_price"] == 10.0
    assert row["error_pct"] == pytest.approx(10.0)


def test_forecast_values_does_not_show_consensus_as_ai_forecast(conn):
    """Regresja z planu §3b: wiersz konsensusu 12m jest nowszy niż prognoza
    AI 12m, więc bez filtra `ORDER BY created_at DESC LIMIT 1` wybrałby jego."""
    forecasts.record_forecast(conn, "12m", _iso(365), 8.72, 9.10, 8.0, 10.0, 0.4, "local")
    _record_consensus(conn, predicted=10.32, target_days=365)

    values = sensors.forecast_values(conn)
    assert values["forecast_12m_eur"] == 9.10
    assert values["forecast_12m_eur_attrs"]["model"] == "local"
