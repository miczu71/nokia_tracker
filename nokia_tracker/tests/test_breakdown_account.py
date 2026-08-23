"""E8 (docs/PLAN_E8_slad.md) — `breakdown.account_traces()`, 11 śladów dla /
(Stan konta). Sprawdza domykanie (shown ≈ recomputed), kompletność kluczy i
że pojedynczy rozjazd trafia do listy `failures`, nie wywala orkiestratora."""
from __future__ import annotations

import pytest

from nokia_tracker import breakdown as bd
from nokia_tracker import cash as cashm
from nokia_tracker import portfolio as portfoliom
from nokia_tracker import sensors
from nokia_tracker import settings as settingsm
from nokia_tracker.tax import grants as grantsm
from nokia_tracker.tax import lots as taxlots

PRICE_EUR = 8.0
EURPLN = 4.3


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


def _build_inputs(conn, cfg, year):
    ctx = bd.build_ctx(conn)
    cost_basis_eur = cfg["position_qty"] * cfg["avg_cost_eur"]
    dividends = sensors.dividends_values(conn, cfg, cost_basis_eur)
    position = portfoliom.position_values_auto(
        conn, cfg, PRICE_EUR, EURPLN, dividends_net_total_eur=dividends["dividends_net_eur"])
    unvested = grantsm.unvested_summary(conn, PRICE_EUR, EURPLN)
    restricted = grantsm.restricted_own_summary(conn, PRICE_EUR, EURPLN)
    buckets = portfoliom.dashboard_buckets(position, restricted, unvested)
    ledger = cashm.ledger(conn, cfg, year)
    return ctx, position, dividends, buckets, restricted, unvested, ledger


def _traces(conn, cfg, year=2026):
    ctx, position, dividends, buckets, restricted, unvested, ledger = _build_inputs(
        conn, cfg, year)
    return bd.account_traces(
        conn, ctx, cfg, year, price_eur=PRICE_EUR, eurpln_rate=EURPLN,
        position=position, dividends=dividends, buckets=buckets, restricted=restricted,
        unvested=unvested, ledger=ledger)


def test_empty_db_has_no_data_dependent_traces_and_no_failures(conn):
    # Loty/gotówka/dywidendy nie istnieją -> te ślady muszą się pominąć
    # ("brak danych", nie "0 zł" udawane bez składników). `portfel.total` i
    # `cash.tax_outstanding` NADAL się budują (poprawnie: 0 = 0 + 0, jeden
    # trywialny składnik) - to nie jest błąd, tylko uczciwy zerowy stan.
    cfg = settingsm.get_settings(conn)
    traces, failures = _traces(conn, cfg)
    assert failures == []
    for key in ("portfel.cost_basis", "portfel.restricted", "portfel.locked",
                "portfel.dividends_net", "cash.broker_balance", "cash.sale_proceeds"):
        assert key not in traces, f"{key} nie powinien istnieć bez danych źródłowych"


def test_portfolio_traces_close_with_one_open_lot(conn):
    taxlots.add_lot(conn, "2020-01-01", "own", 100.0, 3.0, source="manual")
    cfg = settingsm.get_settings(conn)
    traces, failures = _traces(conn, cfg)
    assert failures == []
    for key in ("portfel.total", "portfel.free", "portfel.cost_basis",
                "portfel.unrealized", "portfel.total_return"):
        assert key in traces, f"brak śladu {key}"
        t = traces[key]
        assert abs(t.shown - t.recomputed) <= 0.011, f"{key}: {t.shown} != {t.recomputed}"


def test_cost_basis_trace_absent_without_lots_manual_position(conn):
    settingsm.set_settings(conn, {"position_qty": 100, "avg_cost_eur": 4.0})
    cfg = settingsm.get_settings(conn)
    traces, failures = _traces(conn, cfg)
    assert "portfel.cost_basis" not in traces
    assert failures == []


def test_restricted_and_locked_traces_close_with_pending_espp_match(conn):
    grant_id = grantsm.add_grant(conn, "espp", "2024-03-15", 10.0, natural_key="grant1",
                                 match_pct=50.0)
    grantsm.add_vest(conn, grant_id, "2099-03-15", 5.0, natural_key="vest1")
    taxlots.add_lot(conn, "2024-03-15", "own", 10.0, 3.0, source="manual")
    cfg = settingsm.get_settings(conn)
    traces, failures = _traces(conn, cfg)
    assert failures == []
    assert "portfel.restricted" in traces
    r = traces["portfel.restricted"]
    assert abs(r.shown - r.recomputed) <= 0.011
    assert len(r.components) == 1
    assert "portfel.locked" in traces
    locked = traces["portfel.locked"]
    assert abs(locked.shown - locked.recomputed) <= 0.011


def test_dividends_net_trace_closes_and_labels_row_neutrally(conn):
    from nokia_tracker.tax import dividends as taxdiv
    taxdiv.add_dividend(conn, "2026-06-01", 10.0, gross_eur=5.0, taxes_eur=1.75)
    cfg = settingsm.get_settings(conn)
    traces, failures = _traces(conn, cfg)
    assert failures == []
    d = traces["portfel.dividends_net"]
    assert abs(d.shown - d.recomputed) <= 0.011
    assert len(d.components) == 1
    assert d.components[0].sources[0].kind == "dividend"


def test_cash_traces_close_with_sale_payment_and_broker_balance(conn):
    taxlots.add_lot(conn, "2020-01-01", "own", 100.0, 3.0, source="manual")
    taxlots.record_sale(conn, "2026-07-28", 10.0, 8.0)
    cfg = settingsm.get_settings(conn)
    cashm.add_tax_payment(conn, 2026, "2026-07-01", 50.0)
    cashm.record_broker_balance(conn, "2026-08-01", 100.0)

    traces, failures = _traces(conn, cfg, year=2026)
    assert failures == []
    for key in ("cash.broker_balance", "cash.sale_proceeds", "cash.tax_outstanding"):
        assert key in traces, f"brak śladu {key}"
        t = traces[key]
        assert abs(t.shown - t.recomputed) <= 0.011


def test_broker_balance_trace_absent_without_any_reading(conn):
    cfg = settingsm.get_settings(conn)
    traces, failures = _traces(conn, cfg)
    assert "cash.broker_balance" not in traces
    assert failures == []


def test_forced_mismatch_lands_in_failures_not_raised(conn):
    # Kontrakt degradacji na poziomie orkiestratora: sfałszowany rozjazd w
    # jednym budowniczym trafia do `failures`, reszta śladów wciąż się buduje.
    taxlots.add_lot(conn, "2020-01-01", "own", 100.0, 3.0, source="manual")
    cfg = settingsm.get_settings(conn)
    ctx, position, dividends, buckets, restricted, unvested, ledger = _build_inputs(
        conn, cfg, 2026)
    position = dict(position)
    position["unrealized_pnl_pln"] = position["unrealized_pnl_pln"] + 1000.0  # zepsute
    traces, failures = bd.account_traces(
        conn, ctx, cfg, 2026, price_eur=PRICE_EUR, eurpln_rate=EURPLN,
        position=position, dividends=dividends, buckets=buckets, restricted=restricted,
        unvested=unvested, ledger=ledger)
    assert any(f.key == "portfel.unrealized" for f in failures)
    assert "portfel.total" in traces  # inne ślady nadal się zbudowały
