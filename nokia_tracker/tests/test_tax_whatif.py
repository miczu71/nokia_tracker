"""'Co jeśli sprzedam teraz' (BLUEPRINT §3a, krok 15): symulacja sprzedaży
BEZ zapisu do bazy, na tej samej alokacji FIFO (`tax/lots.py::_plan_fifo`,
wydzielonej w tym kroku z `_allocate_fifo`) co realna `record_sale()` — więc
silnik nie kłamie: identyczne dane wejściowe dają identyczny wynik.
Zero żywego HTTP — fx_nbp.rate_for_event zamockowane."""
from __future__ import annotations

from datetime import datetime

import pytest

from nokia_tracker.tax import lots, whatif


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.whatif.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    return None


def _base_cfg(**overrides) -> dict:
    cfg = {"cost_basis_policy": "own_only", "pl_capital_gains_tax_pct": 19.0}
    cfg.update(overrides)
    return cfg


def test_plan_fifo_matches_real_allocation_on_same_data(conn):
    """Dowód, że symulacja nie kłamie: te same loty i ta sama sprzedaż dają
    identyczną alokację przez czystą _plan_fifo co przez zapisaną
    record_sale() -> sale_allocations."""
    lots.add_lot(conn, "2024-01-10", "own", 5, 5.0)
    lots.add_lot(conn, "2024-03-01", "lti", 5, 0.0)

    open_rows = lots.open_lots(conn)
    plan = lots._plan_fifo(open_rows, 8, 8.0, 0.0, 4.0)

    sale_id = lots.record_sale(conn, "2024-06-01", 8, 8.0)
    saved = conn.execute(
        "SELECT lot_id, quantity, cost_pln, revenue_pln FROM sale_allocations "
        "WHERE sale_id = ? ORDER BY lot_id", (sale_id,)).fetchall()

    assert len(plan) == len(saved) == 2
    for planned, row in zip(sorted(plan, key=lambda a: a["lot_id"]), saved):
        assert planned["lot_id"] == row["lot_id"]
        assert planned["quantity"] == pytest.approx(row["quantity"])
        assert planned["cost_pln"] == pytest.approx(row["cost_pln"])
        assert planned["revenue_pln"] == pytest.approx(row["revenue_pln"])


def test_simulate_sale_does_not_write_to_database(conn):
    lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    lots_before = conn.execute(
        "SELECT id, qty_remaining FROM lots ORDER BY id").fetchall()
    sales_count_before = conn.execute("SELECT COUNT(*) c FROM sales").fetchone()["c"]

    whatif.simulate_sale(conn, _base_cfg(), 4, 8.0)

    lots_after = conn.execute(
        "SELECT id, qty_remaining FROM lots ORDER BY id").fetchall()
    sales_count_after = conn.execute("SELECT COUNT(*) c FROM sales").fetchone()["c"]

    assert [dict(r) for r in lots_before] == [dict(r) for r in lots_after]
    assert sales_count_before == sales_count_after == 0


def test_simulate_sale_returns_three_policies_and_lots_consumed(conn):
    lots.add_lot(conn, "2024-01-10", "own", 5, 5.0)
    lots.add_lot(conn, "2024-02-10", "lti", 5, 0.0)

    result = whatif.simulate_sale(conn, _base_cfg(), 8, 8.0)

    assert set(result["policies"]) == {"own_only", "own_plus_drip", "all_at_acquisition"}
    assert result["policies"]["own_only"]["tax_pln"] >= result["policies"]["all_at_acquisition"]["tax_pln"]
    assert len(result["lots_consumed"]) == 2
    assert result["revenue_pln"] == pytest.approx(8 * 8.0 * 4.0)
    assert result["nbp_rate"] == pytest.approx(4.0)


def test_simulate_sale_insufficient_lots_raises(conn):
    lots.add_lot(conn, "2024-01-10", "own", 3, 5.0)
    with pytest.raises(lots.InsufficientLotsError):
        whatif.simulate_sale(conn, _base_cfg(), 10, 8.0)
    # brak zapisu nawet przy wyjątku
    assert conn.execute("SELECT COUNT(*) c FROM sales").fetchone()["c"] == 0


def test_simulate_sale_net_proceeds_uses_active_policy_tax(conn):
    lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    result = whatif.simulate_sale(conn, _base_cfg(cost_basis_policy="own_only"), 10, 8.0)
    active_tax = result["policies"]["own_only"]["tax_pln"]
    assert result["net_proceeds_pln"] == pytest.approx(result["revenue_pln"] - active_tax)


def test_simulate_sale_defaults_to_today_when_sale_date_omitted(conn, monkeypatch):
    lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    seen_dates = []

    def _capture(conn, event_date):
        seen_dates.append(event_date)
        return (4.0, "stub")

    monkeypatch.setattr("nokia_tracker.tax.whatif.fx_nbp.rate_for_event", _capture)
    whatif.simulate_sale(conn, _base_cfg(), 5, 8.0)
    assert seen_dates == [datetime.now().strftime("%Y-%m-%d")]
