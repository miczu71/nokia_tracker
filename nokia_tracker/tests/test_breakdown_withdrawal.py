"""E8 (docs/PLAN_E8_slad.md) — `breakdown.withdrawal_traces()`, 9 śladów dla
/wyplata. Sprawdza WYŁĄCZNIE domykanie (shown ≈ recomputed) i kompletność
kluczy — poprawność samej matematyki podatkowej jest już pokryta przez
`test_tax_whatif.py`."""
from __future__ import annotations

import pytest

from nokia_tracker import breakdown as bd
from nokia_tracker import settings as settingsm
from nokia_tracker.tax import lots as taxlots
from nokia_tracker.views.withdrawal import withdrawal_view


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.whatif.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


def _setup(conn):
    taxlots.add_lot(conn, "2020-01-01", "own", 100.0, 3.0, source="manual")
    return settingsm.get_settings(conn)


def _build(conn, cfg, direction, **kwargs):
    result, error = withdrawal_view(
        conn, cfg, direction, price_eur=8.0, fee_pct=0.0, sale_date="2026-07-28", **kwargs)
    assert error is None, error
    return result


_ALL_KEYS = (
    "wyplata.quantity", "wyplata.gross_eur", "wyplata.revenue_pln",
    "wyplata.cost_fifo", "wyplata.income", "wyplata.usable_loss",
    "wyplata.tax", "wyplata.net",
)


def _traces_for(conn, cfg, result):
    from nokia_tracker.tax import whatif as taxwhatif
    from nokia_tracker import advisor as advisorm

    if result["direction"] == "target":
        engine = taxwhatif.solve_for_net(
            conn, cfg, 1000.0, result["price_eur"], fee_pct=result["fee_pct"],
            sale_date=result["sale_date"])
    else:
        fee_eur = result["fee_pct"] / 100 * result["quantity"] * result["price_eur"]
        engine = taxwhatif.annual_net_for_quantity(
            conn, cfg, result["quantity"], result["price_eur"], fee_eur,
            sale_date=result["sale_date"])
    forfeit = advisorm.forfeit_for_quantity(
        conn, result["quantity"], result["price_eur"], engine["nbp_rate"],
        today=result["sale_date"])
    ctx = bd.build_ctx(conn)
    return bd.withdrawal_traces(conn, ctx, cfg, result, engine, forfeit,
                                target_net_pln=1000.0 if result["direction"] == "target" else None)


def test_all_core_keys_close_for_direction_target(conn):
    cfg = _setup(conn)
    result = _build(conn, cfg, "target", target_net_pln=1000.0)
    traces = _traces_for(conn, cfg, result)
    for key in _ALL_KEYS:
        assert key in traces, f"brak śladu {key}"
        t = traces[key]
        assert abs(t.shown - t.recomputed) <= 0.011, f"{key}: {t.shown} != {t.recomputed}"


def test_all_core_keys_close_for_direction_quantity(conn):
    cfg = _setup(conn)
    result = _build(conn, cfg, "quantity", quantity=10.0)
    traces = _traces_for(conn, cfg, result)
    for key in _ALL_KEYS:
        assert key in traces
        t = traces[key]
        assert abs(t.shown - t.recomputed) <= 0.011


def test_quantity_trace_direction_quantity_is_identity_of_user_input(conn):
    cfg = _setup(conn)
    result = _build(conn, cfg, "quantity", quantity=10.0)
    traces = _traces_for(conn, cfg, result)
    assert traces["wyplata.quantity"].amount == pytest.approx(10.0)


def test_revenue_trace_components_reference_the_open_lot(conn):
    cfg = _setup(conn)
    result = _build(conn, cfg, "quantity", quantity=10.0)
    traces = _traces_for(conn, cfg, result)
    revenue = traces["wyplata.revenue_pln"]
    assert len(revenue.components) == 1
    assert "lot #" in revenue.components[0].label
    assert revenue.components[0].sources[0].kind == "manual"


def test_forfeit_trace_absent_when_nothing_restricted(conn):
    cfg = _setup(conn)
    result = _build(conn, cfg, "quantity", quantity=10.0)
    traces = _traces_for(conn, cfg, result)
    assert "wyplata.forfeit" not in traces


def test_withdrawal_traces_never_raises_even_with_odd_inputs(conn):
    # Kontrakt degradacji: pojedynczy rozjazd domykania nie wywala orkiestratora
    # (żaden BreakdownNotClosedError nie wycieka na zewnątrz).
    cfg = _setup(conn)
    result = _build(conn, cfg, "quantity", quantity=0.5)
    traces = _traces_for(conn, cfg, result)
    assert isinstance(traces, dict)
