"""Silnik lotów i alokacji FIFO (BLUEPRINT §3a, krok 12).

Zero żywego HTTP — providers.fx_nbp.rate_for_event jest monkeypatchowane
na stałą wartość, bo to test lotów/FIFO, nie kursów NBP (te ma
test_fx_nbp.py osobno)."""
from __future__ import annotations

import pytest

from nokia_tracker.tax import lots


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    """Domyślnie każde zdarzenie dostaje stały kurs 4.30 PLN, żeby testy
    FIFO nie zależały od kalendarza NBP. Poszczególne testy mogą nadpisać."""
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.30, "stub"))
    return None


def test_add_lot_sets_qty_remaining_and_freezes_rate(conn):
    lot_id = lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    row = conn.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
    assert row["qty_remaining"] == 10
    assert row["nbp_rate"] == 4.30
    assert row["nbp_rate_date"] == "stub"
    assert row["cost_pln"] == pytest.approx(10 * 5.0 * 4.30)


def test_add_lot_idempotent_on_natural_key(conn):
    first = lots.add_lot(conn, "2024-01-10", "own", 10, 5.0, natural_key="k1")
    second = lots.add_lot(conn, "2024-01-10", "own", 10, 5.0, natural_key="k1")
    assert first == second
    count = conn.execute("SELECT COUNT(*) c FROM lots").fetchone()["c"]
    assert count == 1


def test_add_lot_nbp_unavailable_stores_null_rate(conn, monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: None)
    lot_id = lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    row = conn.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
    assert row["nbp_rate"] is None
    assert row["cost_pln"] is None


def test_record_sale_partial_single_lot(conn):
    lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    sale_id = lots.record_sale(conn, "2024-06-01", 4, 6.0)
    allocs = conn.execute(
        "SELECT * FROM sale_allocations WHERE sale_id = ?", (sale_id,)).fetchall()
    assert len(allocs) == 1
    assert allocs[0]["quantity"] == 4
    remaining = conn.execute("SELECT qty_remaining FROM lots").fetchone()["qty_remaining"]
    assert remaining == pytest.approx(6)


def test_record_sale_crosses_lot_boundary_fifo_order(conn):
    lot1 = lots.add_lot(conn, "2024-01-10", "own", 5, 5.0)
    lot2 = lots.add_lot(conn, "2024-03-01", "own", 5, 5.5)
    sale_id = lots.record_sale(conn, "2024-06-01", 8, 6.0)
    allocs = conn.execute(
        "SELECT * FROM sale_allocations WHERE sale_id = ? ORDER BY lot_id", (sale_id,)
    ).fetchall()
    assert len(allocs) == 2
    by_lot = {a["lot_id"]: a["quantity"] for a in allocs}
    assert by_lot[lot1] == 5  # najstarszy lot zjedzony w całości pierwszy
    assert by_lot[lot2] == 3
    remaining_lot2 = conn.execute(
        "SELECT qty_remaining FROM lots WHERE id = ?", (lot2,)).fetchone()["qty_remaining"]
    assert remaining_lot2 == pytest.approx(2)


def test_record_sale_fifo_orders_by_acquired_date_not_insertion_order(conn):
    # Lot2 wstawiony PIERWSZY do bazy (mniejsze id), ale ma PÓŹNIEJSZĄ datę
    # nabycia -> FIFO musi zjeść starszy (lot1) mimo większego id.
    lot2 = lots.add_lot(conn, "2024-03-01", "own", 5, 5.5)
    lot1 = lots.add_lot(conn, "2024-01-10", "own", 5, 5.0)
    sale_id = lots.record_sale(conn, "2024-06-01", 3, 6.0)
    allocs = conn.execute(
        "SELECT * FROM sale_allocations WHERE sale_id = ?", (sale_id,)).fetchall()
    assert len(allocs) == 1
    assert allocs[0]["lot_id"] == lot1


def test_record_sale_fractional_shares_no_ghost_remainder(conn):
    lots.add_lot(conn, "2024-01-10", "own", 1.0, 5.0)
    lots.record_sale(conn, "2024-06-01", 1.0 / 3, 6.0)
    lots.record_sale(conn, "2024-06-02", 1.0 / 3, 6.0)
    lots.record_sale(conn, "2024-06-03", 1.0 / 3, 6.0)
    remaining = conn.execute("SELECT qty_remaining FROM lots").fetchone()["qty_remaining"]
    assert remaining == pytest.approx(0, abs=1e-9)
    open_lots = lots.open_lots(conn)
    assert open_lots == []  # epsilon musi wykluczyć lot z resztką 3e-16


def test_record_sale_insufficient_quantity_raises_and_rolls_back(conn):
    lots.add_lot(conn, "2024-01-10", "own", 5, 5.0)
    with pytest.raises(lots.InsufficientLotsError):
        lots.record_sale(conn, "2024-06-01", 10, 6.0)
    # transakcja wycofana w całości: brak sales, qty_remaining nietknięte
    assert conn.execute("SELECT COUNT(*) c FROM sales").fetchone()["c"] == 0
    remaining = conn.execute("SELECT qty_remaining FROM lots").fetchone()["qty_remaining"]
    assert remaining == 5


def test_backfill_missing_rates_fills_null_and_never_overwrites_frozen(conn, monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: None)
    lot_id = lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    row = conn.execute("SELECT nbp_rate FROM lots WHERE id = ?", (lot_id,)).fetchone()
    assert row["nbp_rate"] is None

    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.30, "2024-01-09"))
    lots.backfill_missing_rates(conn)
    row = conn.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
    assert row["nbp_rate"] == 4.30
    assert row["cost_pln"] == pytest.approx(10 * 5.0 * 4.30)

    # Kolejny backfill z INNYM kursem nie zmienia już zamrożonej wartości.
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (9.99, "should-not-apply"))
    lots.backfill_missing_rates(conn)
    row = conn.execute("SELECT nbp_rate FROM lots WHERE id = ?", (lot_id,)).fetchone()
    assert row["nbp_rate"] == 4.30


def test_open_lots_summary_groups_by_type(conn):
    lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    lots.add_lot(conn, "2024-02-10", "lti", 3, 0.0)
    summary = lots.lots_summary(conn)
    assert summary["own"]["qty_remaining"] == pytest.approx(10)
    assert summary["lti"]["qty_remaining"] == pytest.approx(3)
