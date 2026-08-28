"""E10 (docs/PLAN_E10_dokumenty.md) — `breakdown.sale_traces()`, 8(+1)
śladów dla zrealizowanej sprzedaży (`/sales`, dokument dowodowy). Sprawdza
domykanie (shown ≈ recomputed), kompletność kluczy i obsługę nadpisania
zgłoszonej wartości (krok 20) — poprawność samej matematyki podatkowej
jest już pokryta przez `test_tax_trace.py`/`test_web_lots_sales.py`."""
from __future__ import annotations

import pytest

from nokia_tracker import breakdown as bd
from nokia_tracker import settings as settingsm
from nokia_tracker.views.sales import sale_detail

_CORE_KEYS = (
    "sprzedaz.quantity", "sprzedaz.gross_eur", "sprzedaz.revenue_eur",
    "sprzedaz.revenue_pln", "sprzedaz.cost", "sprzedaz.income",
    "sprzedaz.tax", "sprzedaz.net",
)


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "2026-07-27"))


def _seeded_sale(conn, seed, *, fee_eur=0.0):
    seed.lot("2020-01-01", 100.0, 3.0, source="manual")
    cfg = settingsm.get_settings(conn)
    seed.sale("2026-07-28", 10.0, 8.0, fee_eur=fee_eur)
    row = conn.execute("SELECT * FROM sales ORDER BY id DESC LIMIT 1").fetchone()
    return cfg, row


def _traces_for(conn, cfg, sale_row):
    ctx = bd.build_ctx(conn)
    item = sale_detail(conn, cfg, sale_row, ctx=ctx)
    return item, ctx


def test_all_core_keys_close(conn, seed):
    cfg, sale_row = _seeded_sale(conn, seed, fee_eur=2.0)
    item, _ = _traces_for(conn, cfg, sale_row)
    traces = item["traces"]
    assert item["trace_failures"] == []
    for key in _CORE_KEYS:
        assert key in traces, f"brak śladu {key}"
        t = traces[key]
        assert abs(t.shown - t.recomputed) <= 0.011, f"{key}: {t.shown} != {t.recomputed}"


def test_quantity_trace_matches_sale_quantity(conn, seed):
    cfg, sale_row = _seeded_sale(conn, seed)
    item, _ = _traces_for(conn, cfg, sale_row)
    assert item["traces"]["sprzedaz.quantity"].amount == pytest.approx(10.0)


def test_quantity_trace_components_reference_the_lot(conn, seed):
    cfg, sale_row = _seeded_sale(conn, seed)
    item, _ = _traces_for(conn, cfg, sale_row)
    quantity = item["traces"]["sprzedaz.quantity"]
    assert len(quantity.components) == 1
    assert "lot #" in quantity.components[0].label
    assert quantity.components[0].sources[0].kind == "manual"


def test_revenue_eur_shows_fee_as_separate_component(conn, seed):
    cfg, sale_row = _seeded_sale(conn, seed, fee_eur=2.5)
    item, _ = _traces_for(conn, cfg, sale_row)
    revenue_eur = item["traces"]["sprzedaz.revenue_eur"]
    labels = {c.label: c.value for c in revenue_eur.components}
    assert labels["brutto"] == pytest.approx(80.0)
    assert labels["prowizja maklerska"] == pytest.approx(-2.5)
    assert revenue_eur.amount == pytest.approx(77.5)


def test_revenue_pln_trace_carries_nbp_source(conn, seed):
    cfg, sale_row = _seeded_sale(conn, seed)
    item, _ = _traces_for(conn, cfg, sale_row)
    revenue_pln = item["traces"]["sprzedaz.revenue_pln"]
    sources = revenue_pln.components[0].sources
    assert any(s.kind == "manual" for s in sources)


def test_no_reported_override_trace_when_not_overridden(conn, seed):
    cfg, sale_row = _seeded_sale(conn, seed)
    item, _ = _traces_for(conn, cfg, sale_row)
    assert "sprzedaz.reported_override" not in item["traces"]


def test_reported_override_produces_correction_component_and_closes(conn, seed):
    cfg, sale_row = _seeded_sale(conn, seed)
    conn.execute(
        "UPDATE sales SET reported_revenue_pln = ?, reported_cost_pln = ? WHERE id = ?",
        (999.0, 111.0, sale_row["id"]))
    conn.commit()
    sale_row = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_row["id"],)).fetchone()

    item, _ = _traces_for(conn, cfg, sale_row)
    traces = item["traces"]
    assert item["trace_failures"] == []

    assert traces["sprzedaz.revenue_pln"].amount == pytest.approx(999.0)
    assert traces["sprzedaz.cost"].amount == pytest.approx(111.0)
    override = traces["sprzedaz.reported_override"]
    assert abs(override.shown - override.recomputed) <= 0.011
    # Ślad per lot (realny FIFO) zostaje widoczny mimo nadpisania.
    assert len(traces["sprzedaz.revenue_pln"].components) > 1
    assert any(
        c.label == "korekta do wartości zgłoszonej"
        for c in traces["sprzedaz.revenue_pln"].components)

    for key in _CORE_KEYS:
        t = traces[key]
        assert abs(t.shown - t.recomputed) <= 0.011, f"{key}: {t.shown} != {t.recomputed}"


def test_sale_traces_never_raises_and_collects_failures_on_odd_data(conn, seed):
    # Kontrakt: `sale_traces` sam nie rzuca — zwraca listę awarii jako drugi
    # element krotki (w odróżnieniu od `withdrawal_traces`, który je połyka).
    seed.lot("2020-01-01", 0.3, 3.0, source="manual")
    cfg = settingsm.get_settings(conn)
    seed.sale("2026-07-28", 0.3, 8.0)
    sale_row = conn.execute("SELECT * FROM sales ORDER BY id DESC LIMIT 1").fetchone()
    item, _ = _traces_for(conn, cfg, sale_row)
    assert isinstance(item["traces"], dict)
    assert isinstance(item["trace_failures"], list)


def test_sales_view_output_unchanged_by_with_traces_flag(conn, seed):
    from nokia_tracker.views.sales import sales_view

    seed.lot("2020-01-01", 100.0, 3.0, source="manual")
    seed.sale("2026-07-28", 10.0, 8.0, fee_eur=1.0)

    cfg = settingsm.get_settings(conn)
    without = sales_view(conn, cfg, None)
    with_traces = sales_view(conn, cfg, None, with_traces=True)

    assert without["totals"] == with_traces["totals"]
    assert without["sales"][0]["sale"] == with_traces["sales"][0]["sale"]
    assert without["sales"][0]["detail"] == with_traces["sales"][0]["detail"]
    assert "traces" not in without["sales"][0]
    assert "traces" in with_traces["sales"][0]
