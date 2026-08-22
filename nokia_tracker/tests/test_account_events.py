"""Krok E5 (docs/ROADMAP_V3.md) — karta "Najbliższe zdarzenia" na Stanie
konta. Cztery możliwe źródła (vesting, dywidenda, koniec restrykcji ESPP,
termin PIT-38), złożone w jedną oś czasu. Funkcja czysta — zero `conn`, zero
nowej matematyki finansowej: kwoty przychodzą już policzone, ta funkcja tylko
liczy odstęp w dniach i sortuje. TDD: testy przed kodem (ten sam wzorzec co
`dashboard_insights.py`/`test_dashboard_insights.py`)."""
from nokia_tracker import account_events as ae


def test_no_sources_returns_empty_list():
    assert ae.upcoming_events(
        next_vest_date=None, next_vest_qty=None,
        next_dividend=None,
        free_until=None, days_until_free=None,
        forfeit_qty=None, forfeit_value_pln=None,
        tax_deadline=None, tax_outstanding_pln=None,
    ) == []


def test_vesting_event_shown():
    events = ae.upcoming_events(
        next_vest_date="2026-09-01", next_vest_qty=12.5,
        next_dividend=None,
        free_until=None, days_until_free=None,
        forfeit_qty=None, forfeit_value_pln=None,
        tax_deadline=None, tax_outstanding_pln=None,
        today="2026-08-22")
    assert len(events) == 1
    e = events[0]
    assert e["kind"] == "vesting"
    assert e["date"] == "2026-09-01"
    assert e["days"] == 10
    assert "12,50" in e["label"] or "12.5" in e["label"]


def test_dividend_event_shown():
    events = ae.upcoming_events(
        next_vest_date=None, next_vest_qty=None,
        next_dividend={"record_date": "2026-10-15", "gross_eur": 42.0,
                       "net_in_hand_eur": 30.5, "certainty": "estimated"},
        free_until=None, days_until_free=None,
        forfeit_qty=None, forfeit_value_pln=None,
        tax_deadline=None, tax_outstanding_pln=None,
        today="2026-08-22")
    assert len(events) == 1
    e = events[0]
    assert e["kind"] == "dividend"
    assert e["date"] == "2026-10-15"
    assert "estimated" not in e["label"]  # certainty idzie do `detail`, nie do `label`


def test_dividend_event_omitted_when_none():
    events = ae.upcoming_events(
        next_vest_date=None, next_vest_qty=None,
        next_dividend=None,
        free_until=None, days_until_free=None,
        forfeit_qty=None, forfeit_value_pln=None,
        tax_deadline=None, tax_outstanding_pln=None,
        today="2026-08-22")
    assert events == []


def test_restriction_end_event_shown():
    events = ae.upcoming_events(
        next_vest_date=None, next_vest_qty=None,
        next_dividend=None,
        free_until="2027-01-01", days_until_free=132,
        forfeit_qty=50.0, forfeit_value_pln=6800.0,
        tax_deadline=None, tax_outstanding_pln=None,
        today="2026-08-22")
    assert len(events) == 1
    e = events[0]
    assert e["kind"] == "restriction_end"
    assert e["date"] == "2027-01-01"
    assert e["days"] == 132
    assert "50" in e["label"]


def test_restriction_end_omitted_when_no_forfeit_qty():
    # `free_until` bez `forfeit_qty` (np. brak ograniczonych lotów) - nic do pokazania.
    events = ae.upcoming_events(
        next_vest_date=None, next_vest_qty=None,
        next_dividend=None,
        free_until="2027-01-01", days_until_free=132,
        forfeit_qty=0.0, forfeit_value_pln=0.0,
        tax_deadline=None, tax_outstanding_pln=None,
        today="2026-08-22")
    assert events == []


def test_tax_deadline_event_shown_only_when_outstanding_positive():
    events = ae.upcoming_events(
        next_vest_date=None, next_vest_qty=None,
        next_dividend=None,
        free_until=None, days_until_free=None,
        forfeit_qty=None, forfeit_value_pln=None,
        tax_deadline="2027-04-30", tax_outstanding_pln=1500.0,
        today="2026-08-22")
    assert len(events) == 1
    e = events[0]
    assert e["kind"] == "tax_deadline"
    assert e["date"] == "2027-04-30"
    assert "1" in e["label"] and "500" in e["label"]


def test_tax_deadline_omitted_when_outstanding_zero():
    events = ae.upcoming_events(
        next_vest_date=None, next_vest_qty=None,
        next_dividend=None,
        free_until=None, days_until_free=None,
        forfeit_qty=None, forfeit_value_pln=None,
        tax_deadline="2027-04-30", tax_outstanding_pln=0.0,
        today="2026-08-22")
    assert events == []


def test_tax_deadline_omitted_when_outstanding_negative():
    # Nadpłata - nie jest to zbliżający się obowiązek, nie pokazujemy.
    events = ae.upcoming_events(
        next_vest_date=None, next_vest_qty=None,
        next_dividend=None,
        free_until=None, days_until_free=None,
        forfeit_qty=None, forfeit_value_pln=None,
        tax_deadline="2027-04-30", tax_outstanding_pln=-50.0,
        today="2026-08-22")
    assert events == []


def test_events_sorted_ascending_by_date():
    events = ae.upcoming_events(
        next_vest_date="2027-01-01", next_vest_qty=1.0,
        next_dividend={"record_date": "2026-09-01", "gross_eur": 10.0,
                       "net_in_hand_eur": 7.0, "certainty": "confirmed"},
        free_until="2026-12-01", days_until_free=101,
        forfeit_qty=10.0, forfeit_value_pln=1000.0,
        tax_deadline="2027-04-30", tax_outstanding_pln=200.0,
        today="2026-08-22")
    assert [e["date"] for e in events] == [
        "2026-09-01", "2026-12-01", "2027-01-01", "2027-04-30"]
    assert [e["kind"] for e in events] == [
        "dividend", "restriction_end", "vesting", "tax_deadline"]


def test_event_today_has_zero_days():
    events = ae.upcoming_events(
        next_vest_date="2026-08-22", next_vest_qty=5.0,
        next_dividend=None,
        free_until=None, days_until_free=None,
        forfeit_qty=None, forfeit_value_pln=None,
        tax_deadline=None, tax_outstanding_pln=None,
        today="2026-08-22")
    assert events[0]["days"] == 0
    assert events[0]["severity"] == "warning"


def test_overdue_event_not_dropped_silently():
    # Data w przeszłości (np. vesting nie zreconciliowany na czas) - zostaje
    # widoczny z ujemnymi dniami, nie znika po cichu.
    events = ae.upcoming_events(
        next_vest_date="2026-08-01", next_vest_qty=3.0,
        next_dividend=None,
        free_until=None, days_until_free=None,
        forfeit_qty=None, forfeit_value_pln=None,
        tax_deadline=None, tax_outstanding_pln=None,
        today="2026-08-22")
    assert len(events) == 1
    assert events[0]["days"] == -21
    assert events[0]["severity"] == "warning"


def test_far_future_event_has_info_severity():
    events = ae.upcoming_events(
        next_vest_date="2027-06-01", next_vest_qty=3.0,
        next_dividend=None,
        free_until=None, days_until_free=None,
        forfeit_qty=None, forfeit_value_pln=None,
        tax_deadline=None, tax_outstanding_pln=None,
        today="2026-08-22")
    assert events[0]["severity"] == "info"
