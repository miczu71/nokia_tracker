"""E6 krok 6 (docs/PLAN_E6_wyplata.md) — spłata długu z E3 §3b:
`views/plan.py::timing_scenario` musi łapać `InsufficientLotsError`/
`CostBasisMissingError` wokół `advisor.optimize_sale_timing()`, tak samo jak
`exit_scenario` już robi to dla `exit_plan()`. Test na poziomie jednostkowym
(monkeypatch silnika), bo skonstruowanie stanu bazy, w którym
`forfeit_for_quantity` rzuca PO tym jak `simulate_sale` już się powiodło,
wymagałoby sztucznego naruszenia założenia współdzielonej `open_lots()` — sam
kod obronny jest tani i poprawny niezależnie od tego, czy ta konkretna ścieżka
jest dziś osiągalna przez UI."""
from __future__ import annotations

from nokia_tracker.tax import lots as taxlots
from nokia_tracker.views.plan import timing_scenario


def test_timing_scenario_returns_error_tuple_on_insufficient_lots(conn, monkeypatch):
    def _raise(*args, **kwargs):
        raise taxlots.InsufficientLotsError("brak pokrycia — test")

    monkeypatch.setattr("nokia_tracker.views.plan.advisorm.optimize_sale_timing", _raise)

    result, error = timing_scenario(conn, {}, 4.0, 10.0, 8.0)

    assert result is None
    assert error == "brak pokrycia — test"


def test_timing_scenario_returns_error_tuple_on_missing_cost_basis(conn, monkeypatch):
    def _raise(*args, **kwargs):
        raise taxlots.CostBasisMissingError("brak kursu — test")

    monkeypatch.setattr("nokia_tracker.views.plan.advisorm.optimize_sale_timing", _raise)

    result, error = timing_scenario(conn, {}, 4.0, 10.0, 8.0)

    assert result is None
    assert error == "brak kursu — test"


def test_timing_scenario_passes_through_result_on_success(conn, monkeypatch):
    sentinel = {"today": {}, "jan2_next_year": {}}
    monkeypatch.setattr(
        "nokia_tracker.views.plan.advisorm.optimize_sale_timing",
        lambda *a, **k: sentinel)

    result, error = timing_scenario(conn, {}, 4.0, 10.0, 8.0)

    assert result is sentinel
    assert error is None
