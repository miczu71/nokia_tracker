"""Krok E5 (docs/ROADMAP_V3.md) — `views/account.py::account_view`, klocek
zasilający Stan konta (`/`). Baza pusta/zmigrowana wystarcza tu do
sprawdzenia KOMPLETU kluczy i braku wyjątku przy braku danych (np.
`broker_balance is None`, brak dywidend/vestingu) — sama poprawność liczb jest
już pokryta przez `test_web_account.py` (asercje przeniesione z dawnego
`test_web_dashboard.py`, bez zmiany) i `test_tax_*.py`/`test_cash.py`."""
from datetime import date

from nokia_tracker import settings as settingsm
from nokia_tracker.views.account import account_view
from nokia_tracker.views.market_context import instrument_ids


def test_account_view_on_empty_db_has_all_keys(conn):
    cfg = settingsm.get_settings(conn)
    ids = instrument_ids(conn)
    view = account_view(conn, cfg, ids, year=date.today().year)
    for key in ("position", "dividends", "unvested", "restricted", "buckets",
                "forfeit", "eurpln_rate", "ledger", "events", "insights"):
        assert key in view


def test_account_view_broker_balance_none_not_zero_on_empty_db(conn):
    cfg = settingsm.get_settings(conn)
    ids = instrument_ids(conn)
    view = account_view(conn, cfg, ids, year=date.today().year)
    assert view["ledger"]["broker_balance"] is None


def test_account_view_events_empty_on_empty_db(conn):
    # Brak vestingu/dywidend/restrykcji/zobowiązania podatkowego -> brak
    # zdarzeń, nie awaria.
    cfg = settingsm.get_settings(conn)
    ids = instrument_ids(conn)
    view = account_view(conn, cfg, ids, year=date.today().year)
    assert view["events"] == []


def test_account_view_survives_recorded_tax_payment(conn):
    # Poprawność liczby (`outstanding_pln`) jest zadaniem `cash.py`/testów
    # jego warstwy - tu sprawdzamy wyłącznie, że wpłata podatku nie wywraca
    # kompozycji widoku ani zdarzeń.
    conn.execute(
        "INSERT INTO tax_payments (tax_year, paid_date, amount_pln, notes) "
        "VALUES (?, ?, ?, ?)", (date.today().year - 1, "2026-03-01", 100.0, None))
    conn.commit()
    cfg = settingsm.get_settings(conn)
    ids = instrument_ids(conn)
    view = account_view(conn, cfg, ids, year=date.today().year - 1)
    assert isinstance(view["ledger"]["tax_liability"]["outstanding_pln"], (int, float))


def test_account_view_position_matches_settings_on_manual_position(conn):
    settingsm.set_settings(conn, {"position_qty": 100, "avg_cost_eur": 4.0})
    cfg = settingsm.get_settings(conn)
    ids = instrument_ids(conn)
    view = account_view(conn, cfg, ids, year=date.today().year)
    assert view["position"]["position_qty"] == 100
    assert view["buckets"]["total"]["qty"] == 100
