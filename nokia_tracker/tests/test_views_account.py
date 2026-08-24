"""Krok E5 (docs/ROADMAP_V3.md) — `views/account.py::account_view`, klocek
zasilający Stan konta (`/`). Baza pusta/zmigrowana wystarcza tu do
sprawdzenia KOMPLETU kluczy i braku wyjątku przy braku danych (np.
`broker_balance is None`, brak dywidend/vestingu) — sama poprawność liczb jest
już pokryta przez `test_web_account.py` (asercje przeniesione z dawnego
`test_web_dashboard.py`, bez zmiany) i `test_tax_*.py`/`test_cash.py`."""
from datetime import date

import pytest

from nokia_tracker import settings as settingsm
from nokia_tracker.views.account import account_view
from nokia_tracker.views.market_context import instrument_ids


def test_account_view_on_empty_db_has_all_keys(conn):
    cfg = settingsm.get_settings(conn)
    ids = instrument_ids(conn)
    view = account_view(conn, cfg, ids, year=date.today().year)
    for key in ("position", "dividends", "unvested", "restricted", "buckets",
                "forfeit", "eurpln_rate", "ledger", "events", "insights",
                "reconciliation_summary", "analyst_scenarios"):
        assert key in view


def test_account_view_analyst_scenarios_none_without_snapshot(conn):
    cfg = settingsm.get_settings(conn)
    ids = instrument_ids(conn)
    view = account_view(conn, cfg, ids, year=date.today().year)
    assert view["analyst_scenarios"] is None


def test_account_view_analyst_scenarios_reuses_position_values_auto(conn):
    """Zero nowej matematyki (docs/PLAN_0_26_0_konsensus.md §5b):
    scenariusz 'mean' musi dać dokładnie taką samą wartość, co ręczne
    wywołanie position_values_auto() z ceną konsensusu."""
    conn.execute(
        "INSERT INTO analyst_targets (as_of_date, fetched_at, low_eur, mean_eur, "
        "median_eur, high_eur, n_analysts, rating, currency, source) VALUES "
        "('2026-08-24', '2026-08-24T10:00:00+00:00', 4.65, 10.32455, 10.125, 18.0, "
        "22, 'hold', 'EUR', 'yahoo')")
    conn.commit()

    cfg = settingsm.get_settings(conn)
    cfg["position_qty"] = 100.0
    cfg["avg_cost_eur"] = 5.0
    ids = instrument_ids(conn)

    view = account_view(conn, cfg, ids, year=date.today().year)
    scenarios = view["analyst_scenarios"]
    assert scenarios is not None
    assert scenarios["consensus"].mean == pytest.approx(10.32455)

    from nokia_tracker import portfolio as portfoliom
    expected_mean = portfoliom.position_values_auto(
        conn, cfg, 10.32455, view["eurpln_rate"],
        dividends_net_total_eur=view["dividends"]["dividends_net_eur"])
    assert scenarios["mean"]["market_value_eur"] == expected_mean["market_value_eur"]
    assert scenarios["low"]["market_value_eur"] is not None
    assert scenarios["high"]["market_value_eur"] is not None


def test_account_view_reconciliation_summary_none_before_first_import(conn):
    # E7 (krok 7): brak wyciągu do porównania - "brak danych", nie "niezgodność".
    cfg = settingsm.get_settings(conn)
    ids = instrument_ids(conn)
    view = account_view(conn, cfg, ids, year=date.today().year)
    assert view["reconciliation_summary"] is None


def test_account_view_reconciliation_summary_reflects_latest_snapshot(conn):
    import json

    conn.execute(
        "INSERT INTO imports (filename, file_sha256, as_of_date) VALUES ('x','x','2026-08-18')")
    import_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    snapshot = {
        "period_start": "2026-01-01", "period_end": "2026-08-18", "as_of_date": "2026-08-18",
        "shares_total": 0.0, "restricted_units_total": None,
        "pending_tranches": [], "dividends": [], "purchases": [],
        "withhold_type_a": [], "withhold_type_b": [],
    }
    conn.execute(
        "INSERT INTO statement_snapshots (import_id, as_of_date, snapshot_json) "
        "VALUES (?, '2026-08-18', ?)", (import_id, json.dumps(snapshot)))
    conn.commit()

    cfg = settingsm.get_settings(conn)
    ids = instrument_ids(conn)
    view = account_view(conn, cfg, ids, year=date.today().year)
    summary = view["reconciliation_summary"]
    assert summary["as_of_date"] == "2026-08-18"
    assert summary["mismatch_count"] == 0  # 0.0 == 0.0, brak rozjazdu akcji


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
