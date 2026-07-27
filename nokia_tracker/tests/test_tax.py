"""Kalkulator podatku od dywidend — zweryfikowany względem przykładu
z BLUEPRINT (100 EUR brutto, 35% u źródła -> 4 EUR dopłaty, 20 EUR do
odzyskania z Vero), krok 9."""
import pytest

from nokia_tracker import tax


def test_blueprint_worked_example():
    result = tax.compute_dividend_tax(
        gross_eur=100.0, withholding_pct=35.0,
        treaty_withholding_pct=15.0, pl_capital_gains_tax_pct=19.0)
    assert result["withholding_paid_eur"] == pytest.approx(35.0)
    assert result["net_received_eur"] == pytest.approx(65.0)
    assert result["pl_tax_due_eur"] == pytest.approx(4.0)
    assert result["reclaimable_from_finland_eur"] == pytest.approx(20.0)


def test_withholding_at_treaty_rate_no_reclaim_and_no_topup_diff():
    # Gdyby u źródła pobrano dokładnie stawkę traktatową (15%), nie ma nic
    # do odzyskania, a dopłata w PL = Belka - 15% (nadal 4 EUR na 100 EUR).
    result = tax.compute_dividend_tax(
        gross_eur=100.0, withholding_pct=15.0,
        treaty_withholding_pct=15.0, pl_capital_gains_tax_pct=19.0)
    assert result["reclaimable_from_finland_eur"] == pytest.approx(0.0)
    assert result["pl_tax_due_eur"] == pytest.approx(4.0)


def test_withholding_below_treaty_rate_credit_capped_at_actual_paid():
    # Zaliczenie nie może przekroczyć faktycznie pobranego podatku, nawet
    # jeśli stawka traktatowa jest wyższa.
    result = tax.compute_dividend_tax(
        gross_eur=100.0, withholding_pct=10.0,
        treaty_withholding_pct=15.0, pl_capital_gains_tax_pct=19.0)
    assert result["reclaimable_from_finland_eur"] == pytest.approx(0.0)
    # credit = min(10, 15) = 10 -> dopłata = 19 - 10 = 9
    assert result["pl_tax_due_eur"] == pytest.approx(9.0)


def test_zero_gross_all_zero():
    result = tax.compute_dividend_tax(0.0, 35.0, 15.0, 19.0)
    assert result == {
        "withholding_paid_eur": 0.0, "net_received_eur": 0.0,
        "pl_tax_due_eur": 0.0, "reclaimable_from_finland_eur": 0.0,
    }
