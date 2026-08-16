"""Krok 28.5 (docs/PLAN_KROK_28_ux_mobile.md §5): "Dziś warto wiedzieć" na
pulpicie — 0-3 zdania DETERMINISTYCZNE (bez AI). TDD: testy przed kodem."""
from nokia_tracker import dashboard_insights as di


def test_no_signals_returns_empty_list():
    assert di.today_worth_knowing(
        change_pct_day=None, next_vest_date=None, next_vest_qty=None,
        loss_available_pln=0.0, income_pln_this_year=0.0) == []


def test_price_change_up_shown():
    lines = di.today_worth_knowing(
        change_pct_day=2.5, next_vest_date=None, next_vest_qty=None,
        loss_available_pln=0.0, income_pln_this_year=0.0)
    assert len(lines) == 1
    assert "wzrósł" in lines[0]
    assert "2,50" in lines[0] or "2.50" in lines[0]


def test_price_change_down_shown():
    lines = di.today_worth_knowing(
        change_pct_day=-1.2, next_vest_date=None, next_vest_qty=None,
        loss_available_pln=0.0, income_pln_this_year=0.0)
    assert len(lines) == 1
    assert "spadł" in lines[0]


def test_price_change_zero_still_shown_not_treated_as_none():
    # 0.0 to realny fakt (kurs się nie zmienił) - nie to samo co brak danych (None).
    lines = di.today_worth_knowing(
        change_pct_day=0.0, next_vest_date=None, next_vest_qty=None,
        loss_available_pln=0.0, income_pln_this_year=0.0)
    assert len(lines) == 1
    assert "0,00" in lines[0]


def test_next_vest_shown_with_days_until():
    lines = di.today_worth_knowing(
        change_pct_day=None, next_vest_date="2026-09-01", next_vest_qty=12.5,
        loss_available_pln=0.0, income_pln_this_year=0.0, today="2026-08-16")
    assert len(lines) == 1
    assert "2026-09-01" in lines[0]
    assert "16 dni" in lines[0]
    assert "12,50" in lines[0] or "12.5" in lines[0]


def test_next_vest_omitted_when_none():
    lines = di.today_worth_knowing(
        change_pct_day=None, next_vest_date=None, next_vest_qty=None,
        loss_available_pln=0.0, income_pln_this_year=0.0)
    assert lines == []


def test_tax_signal_shown_when_loss_available_and_gain_this_year():
    lines = di.today_worth_knowing(
        change_pct_day=None, next_vest_date=None, next_vest_qty=None,
        loss_available_pln=5000.0, income_pln_this_year=1200.0)
    assert len(lines) == 1
    assert "5" in lines[0] and "1" in lines[0]
    assert "kreator" in lines[0].lower() or "PIT-38" in lines[0]


def test_tax_signal_omitted_when_no_loss_available():
    lines = di.today_worth_knowing(
        change_pct_day=None, next_vest_date=None, next_vest_qty=None,
        loss_available_pln=0.0, income_pln_this_year=1200.0)
    assert lines == []


def test_tax_signal_omitted_when_no_gain_this_year_even_with_loss():
    lines = di.today_worth_knowing(
        change_pct_day=None, next_vest_date=None, next_vest_qty=None,
        loss_available_pln=5000.0, income_pln_this_year=0.0)
    assert lines == []


def test_tax_signal_omitted_when_income_negative():
    lines = di.today_worth_knowing(
        change_pct_day=None, next_vest_date=None, next_vest_qty=None,
        loss_available_pln=5000.0, income_pln_this_year=-300.0)
    assert lines == []


def test_all_three_signals_at_once():
    lines = di.today_worth_knowing(
        change_pct_day=1.0, next_vest_date="2026-09-01", next_vest_qty=1.0,
        loss_available_pln=100.0, income_pln_this_year=50.0, today="2026-08-16")
    assert len(lines) == 3
