"""'Co jeśli sprzedam teraz' (BLUEPRINT §3a, krok 15): symulacja sprzedaży
BEZ zapisu do bazy, na tej samej alokacji FIFO (`tax/lots.py::_plan_fifo`,
wydzielonej w tym kroku z `_allocate_fifo`) co realna `record_sale()` — więc
silnik nie kłamie: identyczne dane wejściowe dają identyczny wynik.
Zero żywego HTTP — fx_nbp.rate_for_event zamockowane."""
from __future__ import annotations

from datetime import datetime

import pytest

from nokia_tracker.tax import losses, lots, whatif


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


def test_simulate_sale_includes_detailed_trace(conn):
    # Krok 16: lots_consumed_detailed obok lots_consumed — rozbicie do numeru
    # tabeli NBP (tax/trace.py), bez psucia starego, płaskiego lots_consumed.
    lots.add_lot(conn, "2024-01-10", "own", 5, 5.0)
    result = whatif.simulate_sale(conn, _base_cfg(), 5, 8.0)
    assert len(result["lots_consumed"]) == 1
    detailed = result["lots_consumed_detailed"]
    assert len(detailed["allocations"]) == 1
    assert detailed["allocations"][0]["lot_type"] == "own"
    assert detailed["net_pln"] == pytest.approx(
        result["revenue_pln"] - result["policies"]["own_only"]["tax_pln"])


def test_simulate_sale_defaults_to_today_when_sale_date_omitted(conn, monkeypatch):
    lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    seen_dates = []

    def _capture(conn, event_date):
        seen_dates.append(event_date)
        return (4.0, "stub")

    monkeypatch.setattr("nokia_tracker.tax.whatif.fx_nbp.rate_for_event", _capture)
    whatif.simulate_sale(conn, _base_cfg(), 5, 8.0)
    assert seen_dates == [datetime.now().strftime("%Y-%m-%d")]


# --- krok 26 (docs/PLAN_KROK_26_doradca.md): _apply_policies wydzielone z simulate_sale ---

def test_apply_policies_called_directly_matches_simulate_sale(conn):
    lots.add_lot(conn, "2022-01-01", "own", 100.0, 5.0, source="manual")
    lots.add_lot(conn, "2023-01-01", "matched", 50.0, 0.0, source="manual")

    result = whatif.simulate_sale(conn, _base_cfg(), 120.0, 8.0, sale_date="2026-07-28")

    direct_policies, direct_active = whatif._apply_policies(
        result["lots_consumed"], sum(a["revenue_pln"] for a in result["lots_consumed"]),
        _base_cfg())

    assert direct_policies == result["policies"]
    assert direct_active == result["active_policy"]


# --- E6 krok 1 (docs/PLAN_E6_wyplata.md): silnik podatku rocznego wydzielony ---
# z advisor.py::optimize_sale_timing/exit_plan, żeby solve_for_net() (krok 3)
# miał jeden wspólny rdzeń zamiast trzeciej kopii tej samej arytmetyki.

def test_annual_tax_pure_core_matches_manual_arithmetic():
    r = whatif._annual_tax(
        base_income_pln=1000.0, sale_income_pln=500.0,
        loss_available_pln=200.0, tax_rate=0.19)

    assert r["combined_income_pln"] == pytest.approx(1500.0)
    assert r["tax_without_loss_pln"] == pytest.approx(285.0)
    assert r["usable_loss_pln"] == pytest.approx(200.0)
    assert r["income_after_loss_pln"] == pytest.approx(1300.0)
    assert r["tax_with_max_loss_pln"] == pytest.approx(247.0)


def test_annual_tax_pure_core_caps_usable_loss_at_available_amount():
    # Strata dostępna (5000) przewyższa dochód (300) — zużywa się tylko tyle,
    # ile trzeba, nie całą pulę (ta sama zasada co dzisiejszy advisor.py).
    r = whatif._annual_tax(
        base_income_pln=100.0, sale_income_pln=200.0,
        loss_available_pln=5000.0, tax_rate=0.19)

    assert r["usable_loss_pln"] == pytest.approx(300.0)
    assert r["income_after_loss_pln"] == pytest.approx(0.0)
    assert r["tax_with_max_loss_pln"] == pytest.approx(0.0)


def test_annual_tax_pure_core_floors_negative_combined_income_at_zero():
    r = whatif._annual_tax(
        base_income_pln=-1000.0, sale_income_pln=200.0,
        loss_available_pln=0.0, tax_rate=0.19)

    assert r["tax_without_loss_pln"] == pytest.approx(0.0)
    assert r["tax_with_max_loss_pln"] == pytest.approx(0.0)


def test_annual_tax_breakdown_reads_base_income_and_loss_from_database(conn):
    cfg = _base_cfg()
    # Strata w 2024 (jak test_tax_losses.py::_loss_year): kupno 10 po 10 EUR,
    # sprzedaż po 5 EUR -> strata 200 PLN przy kursie 4.0 z fixture.
    lots.add_lot(conn, "2024-01-10", "own", 10.0, 10.0, source="manual")
    lots.record_sale(conn, "2024-06-01", 10.0, 5.0)
    losses.rebuild(conn, cfg)

    r = whatif.annual_tax_breakdown(conn, cfg, year=2026, sale_income_pln=1000.0)

    loss_avail = losses.available_for_year(conn, cfg, 2026, policy="own_only")
    assert r["usable_loss_pln"] == pytest.approx(
        min(loss_avail["total_remaining_pln"], 1000.0))
    assert r["tax_with_max_loss_pln"] < r["tax_without_loss_pln"]


def test_annual_tax_breakdown_defaults_policy_from_cfg(conn):
    cfg = _base_cfg(cost_basis_policy="all_at_acquisition")
    lots.add_lot(conn, "2020-01-01", "own", 10.0, 5.0, source="manual")

    r = whatif.annual_tax_breakdown(conn, cfg, year=2026, sale_income_pln=100.0)
    explicit = whatif.annual_tax_breakdown(
        conn, cfg, year=2026, sale_income_pln=100.0, policy="all_at_acquisition")

    assert r == explicit


# --- E6 krok 3 (docs/PLAN_E6_wyplata.md): annual_net_for_quantity — kierunek B ---
# i finalny krok solve_for_net (kierunek A) kończą w TEJ SAMEJ funkcji: woła
# simulate_sale() dla śladu FIFO/NBP, potem annual_tax_breakdown() na jej
# income_pln aktywnej polityki — model roczny zamiast tax_pln pojedynczej
# sprzedaży z simulate_sale()["policies"].

def test_annual_net_for_quantity_uses_annual_model_not_flat_policy_tax(conn):
    cfg = _base_cfg()
    # Kolejność jak w test_optimize_sale_timing_uses_available_loss_to_reduce_tax:
    # lot straty + sprzedaż NAJPIERW (żeby FIFO skonsumował właśnie ten lot, nie
    # tańszy lot dodany później), dopiero potem otwarty lot do testu.
    lots.add_lot(conn, "2024-01-10", "own", 10.0, 10.0, source="manual")
    lots.record_sale(conn, "2024-06-01", 10.0, 5.0)  # strata 200 PLN
    losses.rebuild(conn, cfg)
    lots.add_lot(conn, "2020-01-01", "own", 50.0, 3.0, source="manual")

    r = whatif.annual_net_for_quantity(conn, cfg, 20.0, 8.0, sale_date="2026-07-28")

    flat_tax = r["policies"][r["active_policy"]]["tax_pln"]
    assert r["annual"]["tax_with_max_loss_pln"] < flat_tax
    assert r["net_pln"] == pytest.approx(
        round(r["revenue_pln"] - r["annual"]["tax_with_max_loss_pln"], 2))


def test_annual_net_for_quantity_does_not_write_to_database(conn):
    lots.add_lot(conn, "2020-01-01", "own", 20.0, 3.0, source="manual")
    before = conn.execute("SELECT COUNT(*) c FROM sales").fetchone()["c"]

    whatif.annual_net_for_quantity(conn, _base_cfg(), 5.0, 8.0)

    after = conn.execute("SELECT COUNT(*) c FROM sales").fetchone()["c"]
    assert before == after == 0


# --- solve_for_net() — jedyna nowa matematyka w E6 (bisekcja nad _plan_fifo) ---

def test_solve_for_net_round_trip_within_one_pln(conn):
    lots.add_lot(conn, "2020-01-01", "own", 100.0, 3.0, source="manual")

    target = 1500.0
    result = whatif.solve_for_net(conn, _base_cfg(), target, 8.0, sale_date="2026-07-28")
    confirm = whatif.annual_net_for_quantity(
        conn, _base_cfg(), result["quantity"], 8.0, sale_date="2026-07-28")

    assert confirm["net_pln"] == pytest.approx(target, abs=1.0)
    assert result["net_pln"] == pytest.approx(confirm["net_pln"])


def test_solve_for_net_result_never_exceeds_available_quantity(conn):
    lots.add_lot(conn, "2020-01-01", "own", 30.0, 3.0, source="manual")

    result = whatif.solve_for_net(conn, _base_cfg(), 100.0, 8.0, sale_date="2026-07-28")

    assert result["quantity"] <= 30.0 + lots._EPS


def test_solve_for_net_unreachable_target_raises_with_max_in_message(conn):
    lots.add_lot(conn, "2020-01-01", "own", 5.0, 3.0, source="manual")

    with pytest.raises(whatif.TargetUnreachableError):
        whatif.solve_for_net(conn, _base_cfg(), 1_000_000.0, 8.0, sale_date="2026-07-28")


def test_solve_for_net_insufficient_lots_raises_when_nothing_open(conn):
    with pytest.raises(lots.InsufficientLotsError):
        whatif.solve_for_net(conn, _base_cfg(), 100.0, 8.0, sale_date="2026-07-28")


def test_solve_for_net_rejects_non_positive_target_or_price(conn):
    lots.add_lot(conn, "2020-01-01", "own", 30.0, 3.0, source="manual")
    with pytest.raises(ValueError):
        whatif.solve_for_net(conn, _base_cfg(), 0.0, 8.0)
    with pytest.raises(ValueError):
        whatif.solve_for_net(conn, _base_cfg(), 100.0, 0.0)


def test_solve_for_net_handles_fractional_production_like_quantities(conn):
    # Wzorzec z produkcji: lot ESPP z ułamkową ilością (krok 30, 154.663115).
    lots.add_lot(conn, "2024-01-10", "own", 154.663115, 5.41, source="manual")

    result = whatif.solve_for_net(conn, _base_cfg(), 300.0, 8.0, sale_date="2026-07-28")

    assert 0 < result["quantity"] < 154.663115


def test_solve_for_net_fee_reduces_net_proceeds(conn):
    lots.add_lot(conn, "2020-01-01", "own", 100.0, 3.0, source="manual")

    no_fee = whatif.solve_for_net(conn, _base_cfg(), 1500.0, 8.0, sale_date="2026-07-28")
    with_fee = whatif.solve_for_net(
        conn, _base_cfg(), 1500.0, 8.0, fee_pct=1.0, sale_date="2026-07-28")

    # Ta sama kwota netto docelowa, ale opłata zjada część wpływu -> potrzeba
    # sprzedać WIĘCEJ akcji, żeby osiągnąć ten sam cel.
    assert with_fee["quantity"] > no_fee["quantity"]


def test_solve_for_net_converges_across_loss_carryforward_threshold(conn):
    # Skonstruowany przypadek z progiem (wymóg roadmapy E6): strata z lat
    # ubiegłych węższa niż potencjalny dochód całej sprzedaży, więc funkcja
    # netto ma załamanie nachylenia w środku zakresu bisekcji, nie na krawędzi.
    cfg = _base_cfg()
    lots.add_lot(conn, "2024-01-10", "own", 10.0, 10.0, source="manual")
    lots.record_sale(conn, "2024-06-01", 10.0, 5.0)  # strata 200 PLN
    losses.rebuild(conn, cfg)
    lots.add_lot(conn, "2020-01-01", "own", 100.0, 3.0, source="manual")

    # Cel poniżej wartości progu straty (mało akcji, podatek w pełni zneutralizowany)
    # i cel powyżej progu (strata wyczerpana w połowie sprzedaży) — obie ścieżki
    # muszą zbiegać i pozostać monotoniczne.
    small = whatif.solve_for_net(conn, cfg, 100.0, 8.0, sale_date="2026-07-28")
    large = whatif.solve_for_net(conn, cfg, 2000.0, 8.0, sale_date="2026-07-28")

    assert small["quantity"] < large["quantity"]
    confirm_small = whatif.annual_net_for_quantity(
        conn, cfg, small["quantity"], 8.0, sale_date="2026-07-28")
    confirm_large = whatif.annual_net_for_quantity(
        conn, cfg, large["quantity"], 8.0, sale_date="2026-07-28")
    assert confirm_small["net_pln"] == pytest.approx(100.0, abs=1.0)
    assert confirm_large["net_pln"] == pytest.approx(2000.0, abs=1.0)
