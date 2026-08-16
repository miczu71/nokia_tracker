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


# ---- cashflows nets out contributions (vesting/ESPP) so a big deposit day
# isn't mistaken for market volatility/risk — bug found on 0.16.0 production
# verification: a vesting day inflated volatility to +1015% (impossible for
# a real stock). Same fix pattern as returns.py::twr()'s cashflow netting. ----

def test_volatility_annualized_nets_out_contribution():
    # d0->d1: value jumps 100 -> 1100 purely from a +1000 contribution
    # (vesting/ESPP), zero real market move. d1->d2: genuine +10% market move,
    # no cashflow. Without netting, day 1's "return" would be +1000% and
    # dominate the stdev; with netting it's 0.
    daily_values = [("2024-01-01", 100.0), ("2024-01-02", 1100.0), ("2024-01-03", 1210.0)]
    cashflows = [("2024-01-02", 1000.0)]
    result = risk.volatility_annualized(daily_values, cashflows=cashflows)
    # returns [0.0, 0.10] -> sample stdev = sqrt(0.005) ~= 0.0707107, * sqrt(252) ~= 1.1225
    assert result == pytest.approx(1.1225, abs=0.001)


def test_volatility_annualized_without_cashflows_still_naive():
    # No `cashflows` arg (default) preserves old naive behaviour on raw
    # values — same known example as the non-cashflow test above.
    result = risk.volatility_annualized(
        [("2024-01-01", 100.0), ("2024-01-02", 120.0), ("2024-01-03", 96.0)])
    assert result == pytest.approx(4.489989, abs=0.001)


def test_sharpe_ratio_nets_out_contribution():
    daily_values = [("2024-01-01", 100.0), ("2024-01-02", 1100.0), ("2024-01-03", 1210.0)]
    cashflows = [("2024-01-02", 1000.0)]
    result = risk.sharpe_ratio(daily_values, risk_free_rate_pct=3.0, cashflows=cashflows)
    # mean=0.05, annual_return=12.6, annual_vol~=1.1225, rf=0.03
    # sharpe = (12.6 - 0.03) / 1.1225 ~= 11.20
    assert result == pytest.approx(11.20, abs=0.01)


def test_max_drawdown_nets_out_contribution():
    # A big ESPP/LTI contribution followed by a real 20% drop should show a
    # -20% drawdown, not something inflated/distorted by the deposit itself.
    daily_values = [("2024-01-01", 100.0), ("2024-01-02", 1100.0), ("2024-01-03", 880.0)]
    cashflows = [("2024-01-02", 1000.0)]
    result = risk.max_drawdown(daily_values, cashflows=cashflows)
    assert result == pytest.approx(-0.20, abs=1e-6)


def test_max_drawdown_nets_out_withdrawal():
    # A partial sale (negative cashflow) removing half the value shouldn't
    # register as a market drawdown when the price itself didn't move.
    daily_values = [("2024-01-01", 1000.0), ("2024-01-02", 500.0)]
    cashflows = [("2024-01-02", -500.0)]
    result = risk.max_drawdown(daily_values, cashflows=cashflows)
    assert result == pytest.approx(0.0, abs=1e-9)
