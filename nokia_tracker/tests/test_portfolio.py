"""Stan posiadania -> P&L w EUR i PLN (BLUEPRINT §3, krok 9)."""
import pytest

from nokia_tracker import portfolio


def test_position_values_computes_unrealized_pnl():
    v = portfolio.position_values(100.0, 8.0, 9.0, None)
    assert v["cost_basis_eur"] == 800.0
    assert v["market_value_eur"] == 900.0
    assert v["unrealized_pnl_eur"] == pytest.approx(100.0)
    assert v["unrealized_pnl_pct"] == pytest.approx(12.5)


def test_position_values_no_price_returns_none_market_fields():
    v = portfolio.position_values(100.0, 8.0, None, None)
    assert v["market_value_eur"] is None
    assert v["unrealized_pnl_eur"] is None
    assert v["unrealized_pnl_pct"] is None
    assert v["total_return_pct"] is None


def test_position_values_zero_position_no_division_by_zero():
    v = portfolio.position_values(0.0, 0.0, 9.0, 4.3)
    assert v["cost_basis_eur"] == 0.0
    assert v["unrealized_pnl_pct"] is None
    assert v["total_return_pct"] is None


def test_position_values_pln_conversion():
    v = portfolio.position_values(100.0, 8.0, 9.0, 4.3)
    assert v["market_value_pln"] == pytest.approx(900.0 * 4.3)
    assert v["cost_basis_pln"] == pytest.approx(800.0 * 4.3)
    assert v["unrealized_pnl_pln"] == pytest.approx(100.0 * 4.3)


def test_position_values_no_eurpln_rate_pln_fields_none():
    v = portfolio.position_values(100.0, 8.0, 9.0, None)
    assert v["market_value_pln"] is None
    assert v["cost_basis_pln"] is None
    assert v["unrealized_pnl_pln"] is None


def test_position_values_total_return_includes_dividends():
    v = portfolio.position_values(100.0, 8.0, 9.0, None, dividends_net_total_eur=20.0)
    # unrealized 100 EUR + dywidendy netto 20 EUR na koszcie bazowym 800 EUR
    assert v["total_return_pct"] == pytest.approx((100.0 + 20.0) / 800.0 * 100)


def test_position_values_negative_pnl():
    v = portfolio.position_values(50.0, 10.0, 8.0, None)
    assert v["unrealized_pnl_eur"] == pytest.approx(-100.0)
    assert v["unrealized_pnl_pct"] == pytest.approx(-20.0)
