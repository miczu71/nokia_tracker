"""analyst.py — orkiestracja: Yahoo (główne) -> stockanalysis (fallback,
tylko za zgodą) -> zapis snapshotu `analyst_targets` -> warunkowy
wiersz-lustro w `forecasts` (docs/PLAN_0_26_0_konsensus.md §2-4)."""
from datetime import date, timedelta

import pytest

from nokia_tracker import analyst, forecasts
from nokia_tracker.models import AnalystConsensus
from nokia_tracker.providers.base import QuoteProviderError

_YAHOO = AnalystConsensus(low=4.65, mean=10.32455, median=10.125, high=18.0,
                          n_analysts=22, rating="hold", currency="EUR",
                          source="yahoo", trend={"period": "0m", "buy": 9})
_FALLBACK = AnalystConsensus(low=4.65, mean=10.32, median=10.13, high=18.0,
                             n_analysts=23, rating="Hold", currency="EUR",
                             source="stockanalysis")


def test_yahoo_success_stores_snapshot_and_skips_fallback(conn, monkeypatch):
    calls = {"stockanalysis": 0}
    monkeypatch.setattr("nokia_tracker.analyst.yahoo_analyst.fetch_consensus",
                        lambda *a, **kw: _YAHOO)
    monkeypatch.setattr("nokia_tracker.analyst.stockanalysis.fetch_consensus",
                        lambda *a, **kw: calls.__setitem__("stockanalysis", calls["stockanalysis"] + 1))

    result = analyst.fetch_and_store(conn, "NOKIA.HE", price_eur=8.72,
                                     allow_scrape_fallback=True)

    assert result == _YAHOO
    assert calls["stockanalysis"] == 0
    row = conn.execute("SELECT * FROM analyst_targets").fetchone()
    assert row["source"] == "yahoo"
    assert row["mean_eur"] == pytest.approx(10.32455)
    assert row["as_of_date"] == date.today().isoformat()


def test_yahoo_failure_falls_back_when_allowed(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.analyst.yahoo_analyst.fetch_consensus",
                        lambda *a, **kw: (_ for _ in ()).throw(QuoteProviderError("boom")))
    monkeypatch.setattr("nokia_tracker.analyst.stockanalysis.fetch_consensus",
                        lambda *a, **kw: _FALLBACK)

    result = analyst.fetch_and_store(conn, "NOKIA.HE", price_eur=8.72,
                                     allow_scrape_fallback=True)

    assert result == _FALLBACK
    row = conn.execute("SELECT * FROM analyst_targets").fetchone()
    assert row["source"] == "stockanalysis"


def test_yahoo_failure_without_fallback_permission_returns_none(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.analyst.yahoo_analyst.fetch_consensus",
                        lambda *a, **kw: (_ for _ in ()).throw(QuoteProviderError("boom")))
    fallback_calls = []
    monkeypatch.setattr("nokia_tracker.analyst.stockanalysis.fetch_consensus",
                        lambda *a, **kw: fallback_calls.append(1))

    result = analyst.fetch_and_store(conn, "NOKIA.HE", price_eur=8.72,
                                     allow_scrape_fallback=False)

    assert result is None
    assert fallback_calls == []
    assert conn.execute("SELECT COUNT(*) c FROM analyst_targets").fetchone()["c"] == 0


def test_both_sources_fail_returns_none_without_raising(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.analyst.yahoo_analyst.fetch_consensus",
                        lambda *a, **kw: (_ for _ in ()).throw(QuoteProviderError("boom")))
    monkeypatch.setattr("nokia_tracker.analyst.stockanalysis.fetch_consensus",
                        lambda *a, **kw: (_ for _ in ()).throw(QuoteProviderError("boom")))

    result = analyst.fetch_and_store(conn, "NOKIA.HE", price_eur=8.72,
                                     allow_scrape_fallback=True)
    assert result is None


def test_mirror_row_recorded_on_first_fetch(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.analyst.yahoo_analyst.fetch_consensus",
                        lambda *a, **kw: _YAHOO)
    analyst.fetch_and_store(conn, "NOKIA.HE", price_eur=8.72, allow_scrape_fallback=False)

    row = conn.execute("SELECT * FROM forecasts WHERE source = 'consensus'").fetchone()
    assert row is not None
    assert row["horizon"] == "12m"
    assert row["predicted_price"] == pytest.approx(10.32455)
    assert row["ci_low"] == pytest.approx(4.65)
    assert row["ci_high"] == pytest.approx(18.0)
    assert row["price_at_creation"] == pytest.approx(8.72)
    assert row["target_date"] == (date.today() + timedelta(days=365)).isoformat()


def test_mirror_row_not_duplicated_when_target_unchanged(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.analyst.yahoo_analyst.fetch_consensus",
                        lambda *a, **kw: _YAHOO)
    analyst.fetch_and_store(conn, "NOKIA.HE", price_eur=8.72, allow_scrape_fallback=False)
    analyst.fetch_and_store(conn, "NOKIA.HE", price_eur=8.80, allow_scrape_fallback=False)

    rows = conn.execute("SELECT * FROM forecasts WHERE source = 'consensus'").fetchall()
    assert len(rows) == 1  # ten sam mean -> brak nowego wiersza-lustra


def test_mirror_row_recorded_again_when_target_changes(conn, monkeypatch):
    consensus = {"value": _YAHOO}
    monkeypatch.setattr("nokia_tracker.analyst.yahoo_analyst.fetch_consensus",
                        lambda *a, **kw: consensus["value"])
    analyst.fetch_and_store(conn, "NOKIA.HE", price_eur=8.72, allow_scrape_fallback=False)

    revised = AnalystConsensus(low=5.0, mean=11.0, median=10.9, high=19.0,
                               n_analysts=22, rating="buy", currency="EUR", source="yahoo")
    consensus["value"] = revised
    analyst.fetch_and_store(conn, "NOKIA.HE", price_eur=9.0, allow_scrape_fallback=False)

    rows = conn.execute(
        "SELECT predicted_price FROM forecasts WHERE source = 'consensus' "
        "ORDER BY created_at").fetchall()
    assert [r["predicted_price"] for r in rows] == [pytest.approx(10.32455), pytest.approx(11.0)]


def test_mirror_row_settles_through_existing_settle_due(conn, monkeypatch):
    """Zero nowej matematyki: settle_due()/accuracy_pct() rozliczają wiersz
    konsensusu tą samą ścieżką co prognozy AI."""
    monkeypatch.setattr("nokia_tracker.analyst.yahoo_analyst.fetch_consensus",
                        lambda *a, **kw: _YAHOO)
    analyst.fetch_and_store(conn, "NOKIA.HE", price_eur=8.72, allow_scrape_fallback=False)
    # przewiń wirtualnie target_date w przeszłość, żeby settle_due go zobaczył
    conn.execute("UPDATE forecasts SET target_date = ? WHERE source = 'consensus'",
                ((date.today() - timedelta(days=1)).isoformat(),))
    conn.commit()

    settled = forecasts.settle_due(conn, current_price=10.0)
    assert settled == 1
    assert forecasts.accuracy_pct(conn, source="consensus") is not None


def test_latest_returns_most_recent_snapshot(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.analyst.yahoo_analyst.fetch_consensus",
                        lambda *a, **kw: _YAHOO)
    analyst.fetch_and_store(conn, "NOKIA.HE", price_eur=8.72, allow_scrape_fallback=False)

    result = analyst.latest(conn)
    assert result.mean == pytest.approx(10.32455)
    assert result.trend == {"period": "0m", "buy": 9}


def test_latest_returns_none_when_no_snapshot_yet(conn):
    assert analyst.latest(conn) is None
