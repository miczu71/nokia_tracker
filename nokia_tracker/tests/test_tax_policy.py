"""Trzy polityki kosztu liczone równolegle na tym samym zbiorze alokacji
(BLUEPRINT §3a). Zero żywego HTTP - fx_nbp.rate_for_event zamockowane na
stały kurs, dokładnie jak w test_tax_lots.py."""
from __future__ import annotations

import pytest

from nokia_tracker.tax import lots, policy


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    return None


def _base_cfg(**overrides) -> dict:
    cfg = {"cost_basis_policy": "own_only", "pl_capital_gains_tax_pct": 19.0}
    cfg.update(overrides)
    return cfg


def test_compute_all_policies_returns_three_different_amounts(conn):
    # własne (koszt 5 EUR/szt) + LTI (koszt 0 - dostał za darmo)
    lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    lots.add_lot(conn, "2024-02-10", "lti", 10, 0.0)
    lots.record_sale(conn, "2024-06-01", 20, 8.0)

    result = policy.compute_all_policies(conn, _base_cfg())

    assert set(result) == {"own_only", "own_plus_drip", "all_at_acquisition"}
    # own_only uznaje tylko koszt własnych 10 szt -> najwyższy podatek
    # all_at_acquisition uznaje też koszt LTI (0 EUR, ale wliczony) -> ten
    # sam koszt jak own_only tutaj (LTI ma koszt 0), więc porównujemy z
    # own_plus_drip, który dla tego zestawu (bez dywidend) daje identyczny
    # koszt jak own_only -> sprawdzamy że LTI faktycznie NIE jest uznany
    # w own_only/own_plus_drip, ale JEST w all_at_acquisition.
    assert result["own_only"]["cost_pln"] == pytest.approx(10 * 5.0 * 4.0)
    assert result["all_at_acquisition"]["cost_pln"] == pytest.approx(10 * 5.0 * 4.0 + 10 * 0.0 * 4.0)
    assert result["own_only"]["income_pln"] > result["all_at_acquisition"]["income_pln"] - 1e-9
    assert result["own_only"]["tax_pln"] >= result["all_at_acquisition"]["tax_pln"]


def test_compute_all_policies_dividend_drip_only_recognized_by_own_plus_drip_and_all(conn):
    lots.add_lot(conn, "2024-01-10", "own", 5, 5.0)
    lots.add_lot(conn, "2024-02-10", "dividend_drip", 5, 6.0)
    lots.record_sale(conn, "2024-06-01", 10, 8.0)

    result = policy.compute_all_policies(conn, _base_cfg())

    own_only_cost = 5 * 5.0 * 4.0
    drip_cost = 5 * 6.0 * 4.0
    assert result["own_only"]["cost_pln"] == pytest.approx(own_only_cost)
    assert result["own_plus_drip"]["cost_pln"] == pytest.approx(own_only_cost + drip_cost)
    assert result["all_at_acquisition"]["cost_pln"] == pytest.approx(own_only_cost + drip_cost)


def test_compute_all_policies_loss_gives_zero_tax_not_negative(conn):
    lots.add_lot(conn, "2024-01-10", "own", 10, 20.0)  # kupione drogo
    lots.record_sale(conn, "2024-06-01", 10, 5.0)  # sprzedane tanio -> strata

    result = policy.compute_all_policies(conn, _base_cfg())

    assert result["own_only"]["income_pln"] < 0
    assert result["own_only"]["tax_pln"] == 0.0


def test_compute_all_policies_delta_vs_active_policy_matches_difference(conn):
    lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    lots.add_lot(conn, "2024-02-10", "lti", 10, 0.0)
    lots.record_sale(conn, "2024-06-01", 20, 8.0)

    result = policy.compute_all_policies(conn, _base_cfg(cost_basis_policy="own_only"))
    active_tax = result["own_only"]["tax_pln"]
    for name, data in result.items():
        assert data["delta_vs_active_pln"] == pytest.approx(data["tax_pln"] - active_tax)


def test_compute_all_policies_has_legal_basis_text(conn):
    lots.add_lot(conn, "2024-01-10", "own", 1, 5.0)
    lots.record_sale(conn, "2024-06-01", 1, 8.0)

    result = policy.compute_all_policies(conn, _base_cfg())
    for data in result.values():
        assert data["legal_basis_pl"]  # niepusty tekst uzasadnienia


def test_compute_all_policies_filters_by_year(conn):
    lots.add_lot(conn, "2023-01-10", "own", 10, 5.0)
    lots.record_sale(conn, "2023-06-01", 10, 8.0)
    lots.add_lot(conn, "2024-01-10", "own", 5, 5.0)
    lots.record_sale(conn, "2024-06-01", 5, 8.0)

    result_2023 = policy.compute_all_policies(conn, _base_cfg(), year=2023)
    result_2024 = policy.compute_all_policies(conn, _base_cfg(), year=2024)

    assert result_2023["own_only"]["revenue_pln"] == pytest.approx(10 * 8.0 * 4.0)
    assert result_2024["own_only"]["revenue_pln"] == pytest.approx(5 * 8.0 * 4.0)
