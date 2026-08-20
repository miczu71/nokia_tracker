"""Roczny raport PIT-38 (BLUEPRINT §3a, krok 15): poz. C (trzy polityki
kosztu, reuse tax/policy.py), sekcja G (dywidendy w PLN na zamrożonym
kursie, reuse tax/dividends.py), PIT/ZG, ślad obliczeń per lot i kwota do
odzysku z Vero. Zero żywego HTTP — fx_nbp.rate_for_event zamockowane na
stały kurs, jak w test_tax_lots.py/test_tax_policy.py."""
from __future__ import annotations

import pytest

from nokia_tracker.tax import dividends as taxdiv
from nokia_tracker.tax import lots, losses, pit38


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    return None


def _base_cfg(**overrides) -> dict:
    cfg = {
        "cost_basis_policy": "own_only",
        "pl_capital_gains_tax_pct": 19.0,
        "treaty_withholding_pct": 15.0,
        "finnish_withholding_pct": 35.0,
    }
    cfg.update(overrides)
    return cfg


def _add_dividend(conn, record_date, gross_eur=100.0, taxes_eur=35.0):
    return taxdiv.add_dividend(
        conn, record_date=record_date, purchase_date=record_date,
        entitled_quantity=1.0, gross_eur=gross_eur, taxes_eur=taxes_eur, fees_eur=0.0,
        reinvested_eur=gross_eur - taxes_eur, purchase_price_eur=1.0,
        purchased_shares=0.01, natural_key=f"div:{record_date}:{gross_eur}")


def test_annual_report_poz_c_matches_compute_all_policies(conn):
    lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    lots.add_lot(conn, "2024-02-10", "lti", 10, 0.0)
    lots.record_sale(conn, "2024-06-01", 20, 8.0)

    report = pit38.annual_report(conn, _base_cfg(), year=2024)
    from nokia_tracker.tax import policy as taxpolicy
    expected = taxpolicy.compute_all_policies(conn, _base_cfg(), year=2024)

    assert report["policies"] == expected


def test_annual_report_section_g_sums_dividends_in_pln(conn):
    _add_dividend(conn, "2024-03-15", gross_eur=100.0, taxes_eur=35.0)
    _add_dividend(conn, "2024-09-01", gross_eur=50.0, taxes_eur=17.5)

    report = pit38.annual_report(conn, _base_cfg(), year=2024)
    g = report["section_g"]

    # gross_pln = (100+50) EUR * 4.0 (kurs stub) = 600
    assert g["gross_pln"] == pytest.approx(600.0)
    assert g["dividend_count"] == 2
    # przykład z BLUEPRINT skalowany kursem 4.0: 4 EUR dopłaty * 4.0 na
    # każde 100 EUR -> tu dwie dywidendy o tej samej proporcji (35%/15%/19%)
    assert g["pl_tax_due_pln"] == pytest.approx((4.0 + 2.0) * 4.0)
    assert g["reclaimable_from_finland_pln"] == pytest.approx((20.0 + 10.0) * 4.0)


def test_annual_report_section_g_has_estimated_false_for_measured_dividends(conn):
    _add_dividend(conn, "2024-03-15", gross_eur=100.0, taxes_eur=35.0)
    report = pit38.annual_report(conn, _base_cfg(), year=2024)
    assert report["section_g"]["has_estimated"] is False


def test_annual_report_section_g_has_estimated_true_when_any_dividend_estimated(conn):
    # krok 20: sekcja G odtworzona z założenia 35% (Vested Dividend Shares,
    # lata bez sekcji transakcyjnej) musi być widocznie oznaczona jako szacunek.
    _add_dividend(conn, "2024-03-15", gross_eur=100.0, taxes_eur=35.0)
    taxdiv.add_dividend(
        conn, record_date="2024-06-01", entitled_quantity=0.04,
        gross_eur=0.14, taxes_eur=0.049, fees_eur=0.0,
        natural_key="dividend_estimated:2024-06-01:0.04",
        notes="SZACUNEK: brutto/podatek u źródła odtworzone z założenia 35%")

    report = pit38.annual_report(conn, _base_cfg(), year=2024)
    assert report["section_g"]["has_estimated"] is True


# --- krok 0.17.3: regresja produkcyjna po 0.17.2 - Computershare drukuje osobny wiersz
# `dividends` na każdy koszyk planu (ESPP, LTI) tej samej wypłaty (patrz `taxdiv.payouts()`
# docstring), więc `dividend_count` liczony jako `len(rows)` raportował 5 dywidend zamiast
# 4 dla 2026. Musi liczyć WYPŁATY (`taxdiv.payouts()`), nie wiersze.

def test_annual_report_section_g_dividend_count_counts_payouts_not_rows(conn):
    _add_dividend(conn, "2024-01-30", gross_eur=100.0, taxes_eur=35.0)
    _add_dividend(conn, "2024-04-24", gross_eur=50.0, taxes_eur=17.5)
    # Ta sama wypłata (2024-07-24), dwa koszyki planu - jeden logiczny payout, dwa wiersze.
    taxdiv.add_dividend(
        conn, record_date="2024-07-24", entitled_quantity=2734.0,
        gross_eur=109.36, taxes_eur=38.27, fees_eur=0.0,
        natural_key="dividend:2024-07-24:lti")
    taxdiv.add_dividend(
        conn, record_date="2024-07-24", entitled_quantity=154.663115,
        gross_eur=6.18, taxes_eur=2.16, fees_eur=0.0,
        natural_key="dividend:2024-07-24:espp")

    report = pit38.annual_report(conn, _base_cfg(), year=2024)

    assert conn.execute(
        "SELECT COUNT(*) c FROM dividends WHERE strftime('%Y', pay_date) = '2024'"
    ).fetchone()["c"] == 4
    assert report["section_g"]["dividend_count"] == 3


def test_annual_report_section_g_filters_by_year(conn):
    _add_dividend(conn, "2023-03-15", gross_eur=100.0, taxes_eur=35.0)
    _add_dividend(conn, "2024-03-15", gross_eur=50.0, taxes_eur=17.5)

    report_2023 = pit38.annual_report(conn, _base_cfg(), year=2023)
    report_2024 = pit38.annual_report(conn, _base_cfg(), year=2024)

    assert report_2023["section_g"]["dividend_count"] == 1
    assert report_2023["section_g"]["gross_pln"] == pytest.approx(400.0)
    assert report_2024["section_g"]["dividend_count"] == 1
    assert report_2024["section_g"]["gross_pln"] == pytest.approx(200.0)


def test_annual_report_pit_zg_mirrors_dividend_foreign_tax(conn):
    _add_dividend(conn, "2024-03-15", gross_eur=100.0, taxes_eur=35.0)

    report = pit38.annual_report(conn, _base_cfg(), year=2024)
    pit_zg = report["pit_zg"]

    assert pit_zg["country"] == "Finlandia"
    assert pit_zg["foreign_income_pln"] == pytest.approx(400.0)
    assert pit_zg["foreign_tax_paid_pln"] == pytest.approx(35.0 * 4.0)


def test_annual_report_sale_trace_has_per_lot_breakdown(conn):
    lot1 = lots.add_lot(conn, "2024-01-10", "own", 5, 5.0)
    lot2 = lots.add_lot(conn, "2024-03-01", "lti", 5, 0.0)
    lots.record_sale(conn, "2024-06-01", 8, 8.0)

    report = pit38.annual_report(conn, _base_cfg(), year=2024)
    trace = report["sale_trace"]

    assert len(trace) == 2  # sprzedaż przecięła granicę dwóch lotów
    by_lot = {row["lot_id"]: row for row in trace}
    assert by_lot[lot1]["quantity"] == pytest.approx(5)
    assert by_lot[lot1]["lot_type"] == "own"
    assert by_lot[lot1]["acquired_date"] == "2024-01-10"
    assert by_lot[lot2]["quantity"] == pytest.approx(3)
    assert by_lot[lot2]["lot_type"] == "lti"
    for row in trace:
        assert row["lot_nbp_rate"] == pytest.approx(4.0)
        assert row["sale_nbp_rate"] == pytest.approx(4.0)
        assert row["sale_date"] == "2024-06-01"


def test_annual_report_sale_trace_filters_by_year(conn):
    lots.add_lot(conn, "2023-01-10", "own", 5, 5.0)
    lots.record_sale(conn, "2023-06-01", 5, 8.0)
    lots.add_lot(conn, "2024-01-10", "own", 5, 5.0)
    lots.record_sale(conn, "2024-06-01", 5, 8.0)

    report_2023 = pit38.annual_report(conn, _base_cfg(), year=2023)
    report_2024 = pit38.annual_report(conn, _base_cfg(), year=2024)

    assert len(report_2023["sale_trace"]) == 1
    assert report_2023["sale_trace"][0]["sale_date"] == "2023-06-01"
    assert len(report_2024["sale_trace"]) == 1
    assert report_2024["sale_trace"][0]["sale_date"] == "2024-06-01"


def test_annual_report_is_stable_across_repeated_calls(conn):
    lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    lots.record_sale(conn, "2024-06-01", 10, 8.0)
    _add_dividend(conn, "2024-03-15")

    first = pit38.annual_report(conn, _base_cfg(), year=2024)
    second = pit38.annual_report(conn, _base_cfg(), year=2024)
    assert first == second


def test_annual_report_empty_year_has_zeroed_sections_not_errors(conn):
    report = pit38.annual_report(conn, _base_cfg(), year=2099)
    assert report["section_g"]["dividend_count"] == 0
    assert report["section_g"]["gross_pln"] == 0.0
    assert report["sale_trace"] == []
    assert report["policies"]["own_only"]["revenue_pln"] == 0.0


def test_years_with_data_includes_current_year_even_when_empty(conn):
    from datetime import datetime
    assert pit38.years_with_data(conn) == [datetime.now().year]


def test_years_with_data_includes_sale_and_dividend_years_sorted_desc(conn):
    lots.add_lot(conn, "2020-01-10", "own", 10, 10.0)
    lots.record_sale(conn, "2020-06-01", 5, 12.0)
    _add_dividend(conn, "2022-03-15")
    years = pit38.years_with_data(conn)
    assert 2020 in years and 2022 in years
    assert years == sorted(years, reverse=True)


def _make_2020_loss(conn, cfg):
    # kupno 10 szt @10 EUR, sprzedaż 10 szt @5 EUR -> strata
    # (10*10 - 10*5) * kurs stub 4.0 = 200 PLN pod polityką own_only.
    lots.add_lot(conn, "2020-01-10", "own", 10, 10.0)
    lots.record_sale(conn, "2020-06-01", 10, 5.0)
    losses.rebuild(conn, cfg)


def _make_2024_profit(conn):
    # kupno 10 szt @5 EUR, sprzedaż 10 szt @8 EUR -> dochód
    # (10*8 - 10*5) * 4.0 = 120 PLN, podatek 19% = 22.80 PLN.
    lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    lots.record_sale(conn, "2024-06-01", 10, 8.0)


def test_annual_report_total_due_uses_recorded_deduction_not_available(conn):
    cfg = _base_cfg()
    _make_2020_loss(conn, cfg)
    _make_2024_profit(conn)

    report = pit38.annual_report(conn, cfg, year=2024)

    # Strata z 2020 jest dostępna do odliczenia w 2024, ale user nie podjął
    # jeszcze żadnej decyzji (record_deduction nie wywołane) -> total_due_pln
    # NIE spada, tak jakby straty w ogóle nie było.
    assert report["loss_carryforward"]["total_remaining_pln"] == pytest.approx(200.0)
    assert report["loss_carryforward"]["total_used_this_year_pln"] == 0.0
    assert report["total_due_pln"] == pytest.approx(22.80)
    assert report["loss_carryforward"]["income_after_loss_pln"] == pytest.approx(120.0)
    assert report["loss_carryforward"]["tax_after_loss_pln"] == pytest.approx(22.80)
    assert report["loss_carryforward"]["tax_before_loss_pln"] == pytest.approx(22.80)


def test_annual_report_total_due_drops_by_recorded_deduction_amount(conn):
    cfg = _base_cfg()
    _make_2020_loss(conn, cfg)
    _make_2024_profit(conn)

    without = pit38.annual_report(conn, cfg, year=2024)
    loss_id = without["loss_carryforward"]["items"][0]["loss_id"]

    losses.record_deduction(conn, cfg, loss_id, used_in_year=2024, amount_pln=100.0)
    report = pit38.annual_report(conn, cfg, year=2024)

    assert report["loss_carryforward"]["total_used_this_year_pln"] == pytest.approx(100.0)
    expected_drop = round(100.0 * 0.19, 2)
    assert report["total_due_pln"] == pytest.approx(
        round(without["total_due_pln"] - expected_drop, 2))
    assert report["loss_carryforward"]["income_after_loss_pln"] == pytest.approx(20.0)


def test_annual_report_loss_carryforward_empty_when_no_loss_available(conn):
    cfg = _base_cfg()
    _make_2024_profit(conn)

    report = pit38.annual_report(conn, cfg, year=2024)

    assert report["loss_carryforward"]["items"] == []
    assert report["loss_carryforward"]["total_remaining_pln"] == 0.0


def test_annual_report_income_after_loss_never_negative(conn):
    cfg = _base_cfg()
    _make_2020_loss(conn, cfg)
    _make_2024_profit(conn)

    without = pit38.annual_report(conn, cfg, year=2024)
    loss_id = without["loss_carryforward"]["items"][0]["loss_id"]

    # Odliczenie równe całemu dochodowi roku (dozwolone przez record_deduction,
    # bo strata 200 > dochód 120) -> dochód po stracie musi wylądować na
    # zerze, nie na ujemnej wartości.
    losses.record_deduction(conn, cfg, loss_id, used_in_year=2024, amount_pln=120.0)
    report = pit38.annual_report(conn, cfg, year=2024)

    assert report["loss_carryforward"]["income_after_loss_pln"] == 0.0
    assert report["loss_carryforward"]["tax_after_loss_pln"] == 0.0
    assert report["total_due_pln"] == 0.0
