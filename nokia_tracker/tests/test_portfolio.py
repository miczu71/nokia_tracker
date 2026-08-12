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


# --- krok 23 (docs/PLAN_KROK_23_portfel_kafelki.md): dashboard_buckets() ---
# składa position_values()/restricted_own_summary()/unvested_summary() (już liczone
# w web.py::dashboard) w trzy kubełki (wolne/z ograniczeniem/zablokowane) + sumę,
# bez ponownego liczenia niczego z DB.

def _position(qty_=100.0, market_eur=1000.0, market_pln=4300.0):
    return {
        "position_qty": qty_, "avg_cost_eur": 5.0, "cost_basis_eur": 500.0,
        "market_value_eur": market_eur, "unrealized_pnl_eur": 500.0,
        "unrealized_pnl_pct": 100.0, "total_return_pct": 100.0,
        "market_value_pln": market_pln, "cost_basis_pln": 2150.0,
        "unrealized_pnl_pln": 2150.0,
    }


def _restricted(qty_=0.0, value_eur=0.0, value_pln=0.0, free_until=None):
    return {
        "restricted_qty": qty_, "restricted_value_eur": value_eur,
        "restricted_value_pln": value_pln, "free_until": free_until, "items": [],
    }


def _unvested(upcoming_qty=0.0, upcoming_eur=0.0, upcoming_pln=0.0,
              next_date=None, next_qty=None):
    return {
        "pending_qty": upcoming_qty, "upcoming_qty": upcoming_qty,
        "upcoming_value_eur": upcoming_eur, "upcoming_value_pln": upcoming_pln,
        "overdue_qty": 0.0, "overdue_value_eur": 0.0, "overdue_value_pln": 0.0,
        "next_vest_date": next_date, "next_vest_qty": next_qty, "overdue_items": [],
    }


def test_dashboard_buckets_splits_free_from_restricted():
    b = portfolio.dashboard_buckets(
        _position(qty_=100.0, market_eur=1000.0, market_pln=4300.0),
        _restricted(qty_=30.0, value_eur=300.0, value_pln=1290.0, free_until="2026-08-01"),
        _unvested())
    assert b["free"]["qty"] == pytest.approx(70.0)
    assert b["free"]["value_eur"] == pytest.approx(700.0)
    assert b["free"]["value_pln"] == pytest.approx(3010.0)
    assert b["restricted"]["qty"] == pytest.approx(30.0)
    assert b["restricted"]["value_eur"] == pytest.approx(300.0)
    assert b["restricted"]["free_until"] == "2026-08-01"


def test_dashboard_buckets_no_restrictions_free_equals_position():
    b = portfolio.dashboard_buckets(_position(qty_=100.0), _restricted(), _unvested())
    assert b["free"]["qty"] == pytest.approx(100.0)
    assert b["restricted"]["qty"] == 0.0


def test_dashboard_buckets_locked_from_unvested_upcoming_only():
    b = portfolio.dashboard_buckets(
        _position(), _restricted(),
        _unvested(upcoming_qty=70.0, upcoming_eur=700.0, upcoming_pln=2800.0,
                  next_date="2027-07-05", next_qty=633.0))
    assert b["locked"]["qty"] == pytest.approx(70.0)
    assert b["locked"]["value_eur"] == pytest.approx(700.0)
    assert b["locked"]["value_pln"] == pytest.approx(2800.0)
    assert b["locked"]["next_date"] == "2027-07-05"
    assert b["locked"]["next_qty"] == pytest.approx(633.0)


def test_dashboard_buckets_total_is_position_plus_upcoming():
    b = portfolio.dashboard_buckets(
        _position(qty_=100.0, market_eur=1000.0, market_pln=4300.0),
        _restricted(),
        _unvested(upcoming_qty=70.0, upcoming_eur=700.0, upcoming_pln=2800.0))
    assert b["total"]["qty"] == pytest.approx(170.0)
    assert b["total"]["value_eur"] == pytest.approx(1700.0)
    assert b["total"]["value_pln"] == pytest.approx(7100.0)


def test_dashboard_buckets_none_price_propagates_to_none_not_zero():
    position = _position()
    position["market_value_eur"] = None
    position["market_value_pln"] = None
    b = portfolio.dashboard_buckets(position, _restricted(), _unvested(upcoming_eur=None, upcoming_pln=None))
    assert b["free"]["value_eur"] is None
    assert b["free"]["value_pln"] is None
    assert b["total"]["value_eur"] is None
    assert b["total"]["value_pln"] is None
