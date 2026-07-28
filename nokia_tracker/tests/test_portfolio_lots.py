"""portfolio.lots_based_position_values() - Portfel/Pulpit liczony z realnych lotów zamiast
ręcznie wpisywanej position_qty/avg_cost_eur (domknięcie luki znalezionej po pierwszym realnym
imporcie PDF, docs/PLAN_KROK_13_5_gaps.md)."""
from __future__ import annotations

import pytest

from nokia_tracker import portfolio
from nokia_tracker.tax import lots as taxlots


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


_CFG_OWN_ONLY = {"cost_basis_policy": "own_only"}


def test_lots_based_position_values_total_qty_counts_all_lot_types(conn):
    taxlots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    taxlots.add_lot(conn, "2024-02-10", "lti", 3, 0.0)
    taxlots.add_lot(conn, "2024-03-10", "dividend_drip", 1, 6.0)

    result = portfolio.lots_based_position_values(conn, _CFG_OWN_ONLY, price_eur=8.0,
                                                   eurpln_rate=4.3)

    assert result["position_qty"] == pytest.approx(14.0)  # 10+3+1, wszystkie typy


def test_lots_based_position_values_cost_basis_follows_active_policy(conn):
    # own_only: tylko 'own' ma uznany koszt; 'lti' (koszt 0 EUR) i tak nie wnosi kosztu
    taxlots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    taxlots.add_lot(conn, "2024-02-10", "lti", 5, 0.0)

    result = portfolio.lots_based_position_values(conn, _CFG_OWN_ONLY, price_eur=8.0,
                                                   eurpln_rate=4.3)

    assert result["position_qty"] == pytest.approx(15.0)  # 10 own + 5 lti fizycznie posiadane
    assert result["cost_basis_eur"] == pytest.approx(10 * 5.0)  # tylko own liczy się do kosztu
    # Wartość rynkowa liczy WSZYSTKIE posiadane akcje po bieżącej cenie
    assert result["market_value_eur"] == pytest.approx(15.0 * 8.0)
    # Niezrealizowany zysk wygląda "wysoko", bo darmowe LTI wnoszą wartość bez kosztu - to
    # poprawne odzwierciedlenie odroczonego opodatkowania, nie błąd.
    assert result["unrealized_pnl_eur"] == pytest.approx(15.0 * 8.0 - 10 * 5.0)


def test_lots_based_position_values_own_plus_drip_policy_counts_drip_cost(conn):
    taxlots.add_lot(conn, "2024-01-10", "own", 5, 5.0)
    taxlots.add_lot(conn, "2024-02-10", "dividend_drip", 5, 6.0)
    cfg = {"cost_basis_policy": "own_plus_drip"}

    result = portfolio.lots_based_position_values(conn, cfg, price_eur=8.0, eurpln_rate=4.3)

    assert result["cost_basis_eur"] == pytest.approx(5 * 5.0 + 5 * 6.0)


def test_lots_based_position_values_empty_db_gives_zeros_not_crash(conn):
    result = portfolio.lots_based_position_values(conn, _CFG_OWN_ONLY, price_eur=8.0,
                                                   eurpln_rate=4.3)
    assert result["position_qty"] == 0.0
    assert result["cost_basis_eur"] == 0.0
    assert result["market_value_eur"] == 0.0


def test_lots_based_position_values_includes_dividends_in_total_return(conn):
    taxlots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    result = portfolio.lots_based_position_values(
        conn, _CFG_OWN_ONLY, price_eur=8.0, eurpln_rate=4.3, dividends_net_total_eur=20.0)
    # total_return_pct = (unrealized_pnl + dywidendy) / koszt bazowy
    expected_pnl = 10 * 8.0 - 10 * 5.0
    expected_return = (expected_pnl + 20.0) / (10 * 5.0) * 100
    assert result["total_return_pct"] == pytest.approx(expected_return)


def test_lots_based_position_values_pln_twins_present(conn):
    taxlots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    result = portfolio.lots_based_position_values(conn, _CFG_OWN_ONLY, price_eur=8.0,
                                                   eurpln_rate=4.3)
    assert result["market_value_pln"] == pytest.approx(result["market_value_eur"] * 4.3)
    assert result["cost_basis_pln"] == pytest.approx(result["cost_basis_eur"] * 4.3)
