"""Doradca planu pracowniczego (krok 26, docs/PLAN_KROK_26_doradca.md): ile tracę
sprzedając dziś, planer ESPP, ryzyko koncentracji, kompozytor `overview()`.

Silnik przepadku dopasowania NIE duplikuje reguły ograniczenia z `tax/grants.py`
(konsolidowanej w kroku 21) — czyta ją z `grants.restricted_own_lots()`. Zero żywego
HTTP — `fx_nbp.rate_for_event` zamockowane tam, gdzie `add_lot`/`simulate_sale` go
potrzebują."""
from __future__ import annotations

from datetime import datetime

import pytest

from nokia_tracker import advisor
from nokia_tracker.tax import grants, losses, lots as taxlots, whatif


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
    cfg = {
        "position_qty": 0.0, "avg_cost_eur": 0.0, "cost_basis_policy": "own_only",
        "pl_capital_gains_tax_pct": 19.0, "espp_match_pct": 50.0,
        "other_net_worth_pln": 0.0, "concentration_alert_pct": 25.0,
    }
    cfg.update(overrides)
    return cfg


# --- forfeit_summary ---

def test_forfeit_full_bucket_equals_pending_vest_qty(conn):
    taxlots.add_lot(conn, "2025-10-27", "own", 58.49, 5.41, source="pdf_import")
    grant_id = grants.add_grant(conn, "espp", "2025-10-27", 29.24, "espp_grant:x")
    grants.add_vest(
        conn, grant_id, "2026-08-01", 29.24, "espp_vest:x", available_from="2026-08-27")

    s = advisor.forfeit_summary(conn, price_eur=8.0, today="2026-07-28")

    assert s["forfeit_qty"] == pytest.approx(29.24)
    assert s["forfeit_value_eur"] == pytest.approx(29.24 * 8.0)


def test_forfeit_proportional_after_partial_sale(conn):
    lot_id = taxlots.add_lot(conn, "2025-10-27", "own", 58.49, 5.41, source="pdf_import")
    conn.execute("UPDATE lots SET qty_remaining = ? WHERE id = ?", (29.245, lot_id))
    conn.commit()
    grant_id = grants.add_grant(conn, "espp", "2025-10-27", 29.24, "espp_grant:x")
    grants.add_vest(
        conn, grant_id, "2026-08-01", 29.24, "espp_vest:x", available_from="2026-08-27")

    s = advisor.forfeit_summary(conn, today="2026-07-28")

    assert s["forfeit_qty"] == pytest.approx(14.62)


def test_forfeit_splits_one_grant_across_two_same_date_own_lots(conn):
    taxlots.add_lot(conn, "2025-10-27", "own", 30.0, 5.0, source="pdf_import",
                    natural_key="lot-a")
    taxlots.add_lot(conn, "2025-10-27", "own", 10.0, 5.0, source="pdf_import",
                    natural_key="lot-b")
    grant_id = grants.add_grant(conn, "espp", "2025-10-27", 20.0, "espp_grant:x")
    grants.add_vest(
        conn, grant_id, "2026-08-01", 20.0, "espp_vest:x", available_from="2026-08-27")

    s = advisor.forfeit_summary(conn, today="2026-07-28")

    assert s["forfeit_qty"] == pytest.approx(20.0)  # 15.0 + 5.0


def test_forfeit_same_date_denominator_includes_fully_sold_lot(conn):
    taxlots.add_lot(conn, "2025-10-27", "own", 30.0, 5.0, source="pdf_import",
                    natural_key="lot-a")
    sold_id = taxlots.add_lot(conn, "2025-10-27", "own", 10.0, 5.0, source="pdf_import",
                              natural_key="lot-b")
    conn.execute("UPDATE lots SET qty_remaining = 0 WHERE id = ?", (sold_id,))
    conn.commit()
    grant_id = grants.add_grant(conn, "espp", "2025-10-27", 20.0, "espp_grant:x")
    grants.add_vest(
        conn, grant_id, "2026-08-01", 20.0, "espp_vest:x", available_from="2026-08-27")

    s = advisor.forfeit_summary(conn, today="2026-07-28")

    # Pozostały lot (30) dostaje 15.0 z 20.0 (nie całe 20.0) - mianownik = 40.
    assert s["forfeit_qty"] == pytest.approx(15.0)


def test_forfeit_sums_two_pending_vests_on_one_grant_date(conn):
    taxlots.add_lot(conn, "2025-10-27", "own", 15.0, 5.0, source="pdf_import")
    grant_id = grants.add_grant(conn, "espp", "2025-10-27", 15.0, "espp_grant:x")
    grants.add_vest(
        conn, grant_id, "2026-08-01", 10.0, "espp_vest:a", available_from="2026-08-20")
    grants.add_vest(
        conn, grant_id, "2026-08-02", 5.0, "espp_vest:b", available_from="2026-08-27")

    s = advisor.forfeit_summary(conn, today="2026-07-28")

    assert s["forfeit_qty"] == pytest.approx(15.0)
    assert s["free_until"] == "2026-08-27"


def test_forfeit_ignores_lti_grant_on_same_date(conn):
    taxlots.add_lot(conn, "2025-07-07", "own", 12.0, 5.0, source="pdf_import")
    grant_id = grants.add_grant(conn, "lti", "2025-07-07", None, "lti_grant:x")
    grants.add_vest(
        conn, grant_id, "2026-07-09", 3.0, "lti_vest:x", available_from="2026-07-09")

    s = advisor.forfeit_summary(conn, today="2026-07-01")

    assert s["restricted_qty"] == pytest.approx(12.0)
    assert s["forfeit_qty"] == 0.0


def test_forfeit_zero_when_nothing_restricted(conn):
    taxlots.add_lot(conn, "2020-01-01", "own", 10.0, 3.0, source="pdf_import")

    s = advisor.forfeit_summary(conn, today="2026-07-28")

    assert s["forfeit_qty"] == 0.0
    assert s["free_until"] is None
    assert s["days_until_free"] is None
    assert s["items"] == []


def test_forfeit_days_until_free_is_calendar_days(conn):
    taxlots.add_lot(conn, "2025-10-27", "own", 29.24, 5.41, source="pdf_import")
    grant_id = grants.add_grant(conn, "espp", "2025-10-27", 29.24, "espp_grant:x")
    grants.add_vest(
        conn, grant_id, "2026-08-01", 29.24, "espp_vest:x", available_from="2026-08-27")

    s = advisor.forfeit_summary(conn, today="2026-07-28")

    assert s["days_until_free"] == 30


def test_forfeit_days_until_free_clamped_to_zero_when_overdue(conn):
    taxlots.add_lot(conn, "2025-01-01", "own", 5.0, 5.0, source="pdf_import")
    grant_id = grants.add_grant(conn, "espp", "2025-01-01", 5.0, "espp_grant:x")
    grants.add_vest(
        conn, grant_id, "2025-06-01", 5.0, "espp_vest:x", available_from="2025-06-01")

    s = advisor.forfeit_summary(conn, today="2026-07-28")

    assert s["days_until_free"] == 0


# --- forfeit_for_quantity / forfeit_for_allocations ---

def test_forfeit_for_quantity_skips_older_free_lot_in_fifo(conn):
    taxlots.add_lot(conn, "2020-01-01", "own", 10.0, 3.0, source="pdf_import")
    taxlots.add_lot(conn, "2025-10-27", "own", 20.0, 5.0, source="pdf_import")
    grant_id = grants.add_grant(conn, "espp", "2025-10-27", 10.0, "espp_grant:x")
    grants.add_vest(
        conn, grant_id, "2026-08-01", 10.0, "espp_vest:x", available_from="2026-08-27")

    result = advisor.forfeit_for_quantity(conn, 15.0, today="2026-07-28")

    # 10 z wolnego lotu 2020 + 5 z ograniczonego (match_rate 0.5) = 2.5 przepadku.
    assert result["forfeit_qty"] == pytest.approx(2.5)


def test_forfeit_for_quantity_full_position_equals_bucket(conn):
    taxlots.add_lot(conn, "2025-10-27", "own", 29.24, 5.41, source="pdf_import")
    grant_id = grants.add_grant(conn, "espp", "2025-10-27", 29.24, "espp_grant:x")
    grants.add_vest(
        conn, grant_id, "2026-08-01", 29.24, "espp_vest:x", available_from="2026-08-27")

    bucket = advisor.forfeit_summary(conn, today="2026-07-28")
    full = advisor.forfeit_for_quantity(conn, 29.24, today="2026-07-28")

    assert full["forfeit_qty"] == pytest.approx(bucket["forfeit_qty"])


def test_forfeit_for_allocations_matches_simulate_sale_trace(conn):
    taxlots.add_lot(conn, "2020-01-01", "own", 10.0, 3.0, source="pdf_import")
    taxlots.add_lot(conn, "2025-10-27", "own", 20.0, 5.0, source="pdf_import")
    grant_id = grants.add_grant(conn, "espp", "2025-10-27", 10.0, "espp_grant:x")
    grants.add_vest(
        conn, grant_id, "2026-08-01", 10.0, "espp_vest:x", available_from="2026-08-27")

    sim = whatif.simulate_sale(conn, _base_cfg(), 15.0, 8.0, sale_date="2026-07-28")
    rates = {item["lot_id"]: item["match_rate"]
            for item in grants.restricted_own_lots(conn, today="2026-07-28")}
    via_allocations = advisor.forfeit_for_allocations(sim["lots_consumed"], rates)
    via_quantity = advisor.forfeit_for_quantity(conn, 15.0, today="2026-07-28")

    assert via_allocations["forfeit_qty"] == pytest.approx(via_quantity["forfeit_qty"])


def test_forfeit_values_none_without_price(conn):
    taxlots.add_lot(conn, "2025-10-27", "own", 29.24, 5.41, source="pdf_import")
    grant_id = grants.add_grant(conn, "espp", "2025-10-27", 29.24, "espp_grant:x")
    grants.add_vest(
        conn, grant_id, "2026-08-01", 29.24, "espp_vest:x", available_from="2026-08-27")

    s = advisor.forfeit_summary(conn, today="2026-07-28")

    assert s["forfeit_value_eur"] is None
    assert s["forfeit_value_pln"] is None


# --- espp_plan ---

def test_espp_plan_shares():
    r = advisor.espp_plan(200.0, 12, 8.0)
    assert r["own_shares"] == pytest.approx(300.0)
    assert r["matched_shares"] == pytest.approx(150.0)
    assert r["total_shares"] == pytest.approx(450.0)
    assert r["end_value_eur"] == pytest.approx(3600.0)


def test_espp_plan_tax_own_only():
    r = advisor.espp_plan(200.0, 12, 8.0, eurpln_rate=4.0)
    assert r["revenue_pln"] == pytest.approx(14400.0)
    assert r["policies"]["own_only"]["cost_pln"] == pytest.approx(9600.0)
    assert r["tax_pln"] == pytest.approx(912.0)
    assert r["net_proceeds_pln"] == pytest.approx(13488.0)


def test_espp_plan_tax_all_at_acquisition_is_zero_on_flat_price():
    r = advisor.espp_plan(200.0, 12, 8.0, eurpln_rate=4.0,
                          cost_basis_policy="all_at_acquisition")
    assert r["tax_pln"] == pytest.approx(0.0)


def test_espp_plan_uses_settings_match_pct():
    r = advisor.espp_plan(200.0, 12, 8.0, match_pct=30.0)
    assert r["matched_shares"] == pytest.approx(90.0)


def test_espp_plan_is_pure_no_conn_argument():
    import inspect
    params = inspect.signature(advisor.espp_plan).parameters
    assert "conn" not in params


def test_espp_plan_rejects_zero_price():
    with pytest.raises(ValueError):
        advisor.espp_plan(200.0, 12, 0.0)


def test_espp_plan_rejects_zero_months():
    with pytest.raises(ValueError):
        advisor.espp_plan(200.0, 0, 8.0)


def test_espp_plan_pln_none_without_fx():
    r = advisor.espp_plan(200.0, 12, 8.0)
    assert r["own_shares"] == pytest.approx(300.0)
    assert r["tax_pln"] is None
    assert r["end_value_pln"] is None


# --- concentration ---

def test_concentration_pct_basic():
    c = advisor.concentration(100000.0, 300000.0)
    assert c["pct"] == pytest.approx(25.0)
    assert c["over_threshold"] is False


def test_concentration_over_threshold_is_strict():
    c = advisor.concentration(100001.0, 300000.0)
    assert c["over_threshold"] is True


def test_concentration_unconfigured_returns_none_not_hundred():
    c = advisor.concentration(100000.0, 0.0)
    assert c["pct"] is None
    assert c["configured"] is False
    assert c["over_threshold"] is False


# --- overview (kompozytor) ---

def test_overview_composes_forfeit_timeline_concentration(conn):
    taxlots.add_lot(conn, "2025-10-27", "own", 29.24, 5.41, source="pdf_import")
    grant_id = grants.add_grant(conn, "espp", "2025-10-27", 29.24, "espp_grant:x")
    grants.add_vest(
        conn, grant_id, "2026-08-01", 29.24, "espp_vest:x", available_from="2026-08-27")

    o = advisor.overview(conn, _base_cfg(), price_eur=8.0, eurpln_rate=4.0, today="2026-07-28")

    assert o["forfeit"]["forfeit_qty"] == pytest.approx(29.24)
    assert o["timeline"]["tranches"][0]["quantity"] == pytest.approx(29.24)
    assert o["concentration"]["configured"] is False  # other_net_worth_pln=0.0 w cfg bazowym


def test_overview_survives_simulate_sale_failure(conn, monkeypatch):
    taxlots.add_lot(conn, "2025-10-27", "own", 29.24, 5.41, source="pdf_import")
    grant_id = grants.add_grant(conn, "espp", "2025-10-27", 29.24, "espp_grant:x")
    grants.add_vest(
        conn, grant_id, "2026-08-01", 29.24, "espp_vest:x", available_from="2026-08-27")
    monkeypatch.setattr(
        "nokia_tracker.tax.whatif.fx_nbp.rate_for_event", lambda conn, d: None)

    o = advisor.overview(conn, _base_cfg(), price_eur=8.0, eurpln_rate=4.0, today="2026-07-28")

    assert o["sale_today"] is None
    assert o["forfeit"]["forfeit_qty"] == pytest.approx(29.24)


# --- optimize_sale_timing ---

def test_optimize_sale_timing_returns_both_scenarios_with_full_coverage(conn):
    taxlots.add_lot(conn, "2020-01-01", "own", 50.0, 3.0, source="manual")

    r = advisor.optimize_sale_timing(
        conn, _base_cfg(), 10.0, 8.0, eurpln_rate=4.0, today="2026-07-28")

    assert r["today"] is not None
    assert r["jan2_next_year"] is not None
    assert r["today"]["sale_date"] == "2026-07-28"
    assert r["jan2_next_year"]["sale_date"] == "2027-01-02"
    assert r["recommendation_pl"] is not None


def test_optimize_sale_timing_insufficient_lots_returns_none_without_raising(conn):
    # Baza pusta — żadnych otwartych lotów, sprzedaż 10 szt. nie ma pokrycia.
    r = advisor.optimize_sale_timing(
        conn, _base_cfg(), 10.0, 8.0, eurpln_rate=4.0, today="2026-07-28")

    assert r["today"] is None
    assert r["jan2_next_year"] is None
    assert r["delta_tax_pln"] is None
    assert r["delta_forfeit_pln"] is None
    assert r["delta_total_pln"] is None
    assert r["recommendation_pl"] is None


def test_optimize_sale_timing_deltas_match_scenario_arithmetic(conn):
    taxlots.add_lot(conn, "2020-01-01", "own", 50.0, 3.0, source="manual")

    r = advisor.optimize_sale_timing(
        conn, _base_cfg(), 10.0, 8.0, eurpln_rate=4.0, today="2026-07-28")

    expected_delta_tax = round(
        r["jan2_next_year"]["tax_with_max_loss_pln"] - r["today"]["tax_with_max_loss_pln"], 2)
    expected_delta_forfeit = round(
        r["jan2_next_year"]["forfeit_value_pln"] - r["today"]["forfeit_value_pln"], 2)
    assert r["delta_tax_pln"] == pytest.approx(expected_delta_tax)
    assert r["delta_forfeit_pln"] == pytest.approx(expected_delta_forfeit)
    assert r["delta_total_pln"] == pytest.approx(
        round(expected_delta_tax + expected_delta_forfeit, 2))


def test_optimize_sale_timing_uses_available_loss_to_reduce_tax(conn):
    cfg = _base_cfg()
    # Strata w 2024: kupno 10 szt. po 10 EUR, sprzedaż po 5 EUR -> strata 200 PLN
    # (kurs 4.0 z fixture), jak w test_tax_losses.py::_loss_year.
    taxlots.add_lot(conn, "2024-01-10", "own", 10.0, 10.0, source="manual")
    taxlots.record_sale(conn, "2024-06-01", 10.0, 5.0)
    losses.rebuild(conn, cfg)

    # Lot niepowiązany ze stratą, do sprzedaży "dziś" z zyskiem.
    taxlots.add_lot(conn, "2020-01-01", "own", 50.0, 3.0, source="manual")

    r = advisor.optimize_sale_timing(conn, cfg, 20.0, 8.0, eurpln_rate=4.0, today="2026-07-28")

    assert r["today"]["usable_loss_pln"] > 0
    assert r["today"]["tax_with_max_loss_pln"] < r["today"]["tax_without_loss_pln"]
