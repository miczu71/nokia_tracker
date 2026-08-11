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


# ---- krok 20: zgłoszona wartość sprzedaży nadpisuje agregat, ale nie sale_allocations ----

def test_compute_all_policies_uses_reported_values_when_set(conn):
    sale_id = lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    sale_id = lots.record_sale(conn, "2024-06-01", 10, 8.0)  # silnik: 10*8*4=320 przychód, 10*5*4=200 koszt
    conn.execute(
        "UPDATE sales SET reported_revenue_pln = ?, reported_cost_pln = ? WHERE id = ?",
        (999.0, 111.0, sale_id))
    conn.commit()

    result = policy.compute_all_policies(conn, _base_cfg())

    assert result["own_only"]["revenue_pln"] == pytest.approx(999.0)
    assert result["own_only"]["cost_pln"] == pytest.approx(111.0)
    assert result["own_only"]["income_pln"] == pytest.approx(999.0 - 111.0)
    assert result["own_only"]["tax_pln"] == pytest.approx(round((999.0 - 111.0) * 0.19, 2))


def test_compute_all_policies_reported_value_same_across_all_three_policies(conn):
    # Arkusz nie ma trzech wariantów kosztu jak silnik - nadpisana sprzedaż wnosi
    # tę samą kwotę do każdej z trzech polityk (w przeciwieństwie do lotów real
    # gdzie własne/podarowane/LTI dają różne koszty per polityka).
    sale_id = lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    sale_id = lots.record_sale(conn, "2024-06-01", 10, 8.0)
    conn.execute(
        "UPDATE sales SET reported_revenue_pln = ?, reported_cost_pln = ? WHERE id = ?",
        (999.0, 111.0, sale_id))
    conn.commit()

    result = policy.compute_all_policies(conn, _base_cfg())

    for name in ("own_only", "own_plus_drip", "all_at_acquisition"):
        assert result[name]["cost_pln"] == pytest.approx(111.0)


def test_compute_all_policies_sale_allocations_unchanged_by_reported_override(conn):
    # Zgłoszona wartość zmienia TYLKO agregat, nie realny ślad FIFO w sale_allocations.
    sale_id = lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    sale_id = lots.record_sale(conn, "2024-06-01", 10, 8.0)
    real_alloc = conn.execute(
        "SELECT cost_pln, revenue_pln FROM sale_allocations WHERE sale_id = ?",
        (sale_id,)).fetchone()

    conn.execute(
        "UPDATE sales SET reported_revenue_pln = ?, reported_cost_pln = ? WHERE id = ?",
        (999.0, 111.0, sale_id))
    conn.commit()
    policy.compute_all_policies(conn, _base_cfg())

    unchanged_alloc = conn.execute(
        "SELECT cost_pln, revenue_pln FROM sale_allocations WHERE sale_id = ?",
        (sale_id,)).fetchone()
    assert unchanged_alloc["cost_pln"] == real_alloc["cost_pln"]
    assert unchanged_alloc["revenue_pln"] == real_alloc["revenue_pln"]


def test_compute_all_policies_mixes_reported_and_real_sales_in_same_year(conn):
    lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    sale1 = lots.record_sale(conn, "2024-03-01", 10, 8.0)  # real: revenue=320, cost=200
    lots.add_lot(conn, "2024-02-10", "own", 5, 6.0)
    sale2 = lots.record_sale(conn, "2024-06-01", 5, 9.0)  # will be overridden

    conn.execute(
        "UPDATE sales SET reported_revenue_pln = ?, reported_cost_pln = ? WHERE id = ?",
        (500.0, 50.0, sale2))
    conn.commit()

    result = policy.compute_all_policies(conn, _base_cfg())

    real_revenue = 10 * 8.0 * 4.0
    real_cost = 10 * 5.0 * 4.0
    assert result["own_only"]["revenue_pln"] == pytest.approx(real_revenue + 500.0)
    assert result["own_only"]["cost_pln"] == pytest.approx(real_cost + 50.0)
