"""Metryki ryzyka portfela (krok 32, docs/PLAN_KROK_32_ryzyko.md).

Wejście `daily_values: [(data, market_value_eur), ...]` — ten sam kształt co
`returns.twr()`. Annualizacja stałą `sqrt(252)` (dni sesyjne), bo
`portfolio_history` jest już budowana wyłącznie z dni notowania
(`history.py::rebuild()`), patrz uzasadnienie w module `analytics/risk.py`."""
import pytest

from nokia_tracker.analytics import risk


# ---- max_drawdown(): peak-to-trough, nie wymaga zwrotów ----

def test_max_drawdown_known_example():
    result = risk.max_drawdown(
        [("2024-01-01", 100.0), ("2024-01-02", 120.0),
         ("2024-01-03", 90.0), ("2024-01-04", 110.0)])
    assert result == pytest.approx(-0.25, abs=1e-6)


def test_max_drawdown_no_drop_is_zero():
    result = risk.max_drawdown(
        [("2024-01-01", 100.0), ("2024-01-02", 110.0), ("2024-01-03", 120.0)])
    assert result == pytest.approx(0.0, abs=1e-9)


def test_max_drawdown_single_point_is_zero():
    assert risk.max_drawdown([("2024-01-01", 100.0)]) == pytest.approx(0.0)


def test_max_drawdown_empty_is_none():
    assert risk.max_drawdown([]) is None


# ---- volatility_annualized(): stdev próbki dziennych zwrotów * sqrt(252) ----

def test_volatility_annualized_known_example():
    # Zwroty dokładnie [+0.2, -0.2] (100 -> 120 -> 96): mean=0,
    # sample stdev = sqrt(0.08) ≈ 0.282843, * sqrt(252) ≈ 4.489989.
    result = risk.volatility_annualized(
        [("2024-01-01", 100.0), ("2024-01-02", 120.0), ("2024-01-03", 96.0)])
    assert result == pytest.approx(4.489989, abs=0.001)


def test_volatility_annualized_none_with_fewer_than_three_points():
    assert risk.volatility_annualized([("2024-01-01", 100.0)]) is None
    assert risk.volatility_annualized(
        [("2024-01-01", 100.0), ("2024-01-02", 110.0)]) is None
    assert risk.volatility_annualized([]) is None


# ---- sharpe_ratio(): (annual_return - risk_free) / annual_volatility ----

def test_sharpe_ratio_known_example():
    # Ta sama seria [100, 120, 96]: mean daily = 0 -> annual_return = 0.
    # rf=3% -> sharpe = (0 - 0.03) / 4.489989 ≈ -0.006682.
    result = risk.sharpe_ratio(
        [("2024-01-01", 100.0), ("2024-01-02", 120.0), ("2024-01-03", 96.0)],
        risk_free_rate_pct=3.0)
    assert result == pytest.approx(-0.006682, abs=0.0001)


def test_sharpe_ratio_positive_drift():
    # Zwroty dokładnie [+0.1, -0.1, +0.1] (100->110->99->108.9):
    # mean=1/30, annual_return=8.4, sample stdev=sqrt(1/75)≈0.1154701,
    # annual_vol≈1.833030, rf=3% -> sharpe=(8.4-0.03)/1.833030≈4.5663.
    result = risk.sharpe_ratio(
        [("2024-01-01", 100.0), ("2024-01-02", 110.0),
         ("2024-01-03", 99.0), ("2024-01-04", 108.9)],
        risk_free_rate_pct=3.0)
    assert result == pytest.approx(4.5663, abs=0.001)


def test_sharpe_ratio_none_with_fewer_than_three_points():
    assert risk.sharpe_ratio(
        [("2024-01-01", 100.0), ("2024-01-02", 110.0)], risk_free_rate_pct=3.0) is None
    assert risk.sharpe_ratio([], risk_free_rate_pct=3.0) is None


def test_sharpe_ratio_none_when_volatility_is_zero():
    result = risk.sharpe_ratio(
        [("2024-01-01", 100.0), ("2024-01-02", 110.0), ("2024-01-03", 121.0)],
        risk_free_rate_pct=3.0)
    assert result is None
