"""taxdiv.payouts() (krok 0.17.3): grupowanie wierszy `dividends` po `pay_date` w
jednostkę WYPŁATY — jedyna definicja tego faktu w kodzie (patrz jej docstring), z której
czytają `dividend_outlook.py::per_share_history()`/`reconcile_schedule()` (osobno w
test_dividend_outlook.py/test_dividend_schedule.py) i `tax/pit38.py::_section_g` (osobno
w test_tax_pit38.py). Ten plik pokrywa tylko kontrakt samej funkcji."""
from __future__ import annotations

import pytest

from nokia_tracker.tax import dividends as taxdiv


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


def test_payouts_one_row_per_date_is_one_payout_with_that_row_as_representative(conn):
    dividend_id = taxdiv.add_dividend(
        conn, record_date="2026-01-30", entitled_quantity=100.0,
        gross_eur=4.0, taxes_eur=1.4, fees_eur=0.0)

    result = taxdiv.payouts(conn)

    assert len(result) == 1
    p = result[0]
    assert p["pay_date"] == "2026-01-30"
    assert p["ids"] == [dividend_id]
    assert p["gross_eur"] == pytest.approx(4.0)
    assert p["quantity"] == pytest.approx(100.0)
    assert p["real_row_count"] == 1
    assert p["estimated_row_count"] == 0
    assert p["is_real"] is True


def test_payouts_groups_two_plan_bucket_rows_on_same_pay_date_into_one_payout(conn):
    # Regresja produkcyjna: Computershare drukuje osobny wiersz na każdy koszyk planu
    # (ESPP, LTI) tej samej wypłaty - 2026-07-24, 2734 akcji LTI + 154.663115 ESPP.
    id_lti = taxdiv.add_dividend(
        conn, record_date="2026-07-24", entitled_quantity=2734.0,
        gross_eur=109.36, taxes_eur=38.27, fees_eur=0.0)
    id_espp = taxdiv.add_dividend(
        conn, record_date="2026-07-24", entitled_quantity=154.663115,
        gross_eur=6.18, taxes_eur=2.16, fees_eur=0.0)

    result = taxdiv.payouts(conn)

    assert len(result) == 1
    p = result[0]
    assert p["ids"] == sorted([id_lti, id_espp])
    assert p["quantity"] == pytest.approx(2734.0 + 154.663115)
    assert p["gross_eur"] == pytest.approx(109.36 + 6.18)
    assert p["real_row_count"] == 2


def test_payouts_ids_ascending_lowest_id_is_representative(conn):
    id_a = taxdiv.add_dividend(
        conn, record_date="2026-07-24", entitled_quantity=100.0,
        gross_eur=4.0, taxes_eur=1.4, fees_eur=0.0)
    id_b = taxdiv.add_dividend(
        conn, record_date="2026-07-24", entitled_quantity=50.0,
        gross_eur=2.0, taxes_eur=0.7, fees_eur=0.0)

    p = taxdiv.payouts(conn)[0]

    assert p["ids"] == sorted([id_a, id_b]) == [id_a, id_b]
    assert p["ids"][0] == id_a


def test_payouts_withholding_pct_is_gross_weighted_mean_of_real_rows(conn):
    # Koszyk 1: 109.36 EUR brutto, withholding_pct 35%. Koszyk 2: 6.18 EUR brutto, 30%.
    # Ważona: (109.36*35 + 6.18*30) / (109.36+6.18)
    taxdiv.add_dividend(
        conn, record_date="2026-07-24", entitled_quantity=2734.0,
        gross_eur=109.36, taxes_eur=109.36 * 0.35, fees_eur=0.0)
    taxdiv.add_dividend(
        conn, record_date="2026-07-24", entitled_quantity=154.663115,
        gross_eur=6.18, taxes_eur=6.18 * 0.30, fees_eur=0.0)

    p = taxdiv.payouts(conn)[0]

    expected = (109.36 * 35 + 6.18 * 30) / (109.36 + 6.18)
    assert p["withholding_pct"] == pytest.approx(expected)


def test_payouts_withholding_pct_none_when_no_real_row_carries_one(conn):
    taxdiv.add_dividend(
        conn, record_date="2026-07-24", entitled_quantity=0.04,
        gross_eur=0.14, taxes_eur=0.0, fees_eur=0.0,
        notes="SZACUNEK: brutto/podatek u źródła odtworzone z założenia 35%")

    p = taxdiv.payouts(conn)[0]

    assert p["withholding_pct"] is None


def test_payouts_estimated_rows_excluded_from_sums_but_counted_and_kept_in_ids(conn):
    real_id = taxdiv.add_dividend(
        conn, record_date="2026-07-24", entitled_quantity=100.0,
        gross_eur=4.0, taxes_eur=1.4, fees_eur=0.0)
    est_id = taxdiv.add_dividend(
        conn, record_date="2026-07-24", entitled_quantity=0.04,
        gross_eur=0.14, taxes_eur=0.049, fees_eur=0.0,
        notes="SZACUNEK: brutto/podatek u źródła odtworzone z założenia 35%")

    p = taxdiv.payouts(conn)[0]

    assert p["gross_eur"] == pytest.approx(4.0)   # szacunek NIE wchodzi do sumy
    assert p["quantity"] == pytest.approx(100.0)
    assert p["real_row_count"] == 1
    assert p["estimated_row_count"] == 1
    assert p["is_real"] is True
    assert set(p["ids"]) == {real_id, est_id}      # ale wiersz JEST w grupie


def test_payouts_group_with_only_estimated_rows_is_not_real(conn):
    taxdiv.add_dividend(
        conn, record_date="2026-07-24", entitled_quantity=0.04,
        gross_eur=0.14, taxes_eur=0.049, fees_eur=0.0,
        notes="SZACUNEK: brutto/podatek u źródła odtworzone z założenia 35%")

    p = taxdiv.payouts(conn)[0]

    assert p["is_real"] is False
    assert p["real_row_count"] == 0
    assert p["gross_eur"] == 0.0


def test_payouts_sorted_ascending_by_pay_date(conn):
    for pay_date in ["2026-07-24", "2026-01-30", "2026-04-24"]:
        taxdiv.add_dividend(
            conn, record_date=pay_date, entitled_quantity=100.0,
            gross_eur=4.0, taxes_eur=1.4, fees_eur=0.0)

    result = taxdiv.payouts(conn)

    assert [p["pay_date"] for p in result] == ["2026-01-30", "2026-04-24", "2026-07-24"]


def test_payouts_year_filter_returns_only_that_year(conn):
    taxdiv.add_dividend(
        conn, record_date="2023-03-15", entitled_quantity=100.0,
        gross_eur=4.0, taxes_eur=1.4, fees_eur=0.0)
    taxdiv.add_dividend(
        conn, record_date="2024-03-15", entitled_quantity=100.0,
        gross_eur=4.0, taxes_eur=1.4, fees_eur=0.0)

    assert [p["pay_date"] for p in taxdiv.payouts(conn, year=2024)] == ["2024-03-15"]
    assert [p["pay_date"] for p in taxdiv.payouts(conn, year="2023")] == ["2023-03-15"]


def test_payouts_empty_db_returns_empty_list(conn):
    assert taxdiv.payouts(conn) == []
