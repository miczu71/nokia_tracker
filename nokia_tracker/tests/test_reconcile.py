"""Rekonstrukcja stanu bazy NA DZIEŃ wyciągu (E7, docs/PLAN_E7_uzgodnienie.md, krok 2).

Zero zapisu — model odczytu jak `integrity.py`/`cash.py`. Główne ryzyko: dzisiejsze
`reconcile_holdings` porównuje wyciąg z BIEŻĄCYM stanem bazy, co działa tylko dlatego, że
odpala się wyłącznie przy imporcie najnowszego pliku. Przeliczanie na żądanie wymaga
odtworzenia stanu NA DZIEŃ wyciągu — inaczej każda sprzedaż/import późniejszy dawałby
fałszywą niezgodność."""
from __future__ import annotations

import pytest

from nokia_tracker import reconcile
from nokia_tracker.tax import grants as grantsm
from nokia_tracker.tax import lots as taxlots


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


# --- shares_as_of ---

def test_shares_as_of_ignores_lot_acquired_after_the_date(conn):
    taxlots.add_lot(conn, "2026-02-01", "own", 10.0, 5.0)
    result = reconcile.shares_as_of(conn, "2026-01-31")
    assert result["total"] == 0.0


def test_shares_as_of_ignores_sale_after_the_date(conn):
    taxlots.add_lot(conn, "2026-01-01", "own", 10.0, 5.0)
    taxlots.record_sale(conn, "2026-02-01", 4.0, 6.0)
    result = reconcile.shares_as_of(conn, "2026-01-15")
    assert result["total"] == 10.0  # sprzedaż jest PO dacie D — nie pomniejsza stanu na D


def test_shares_as_of_subtracts_sale_before_the_date(conn):
    taxlots.add_lot(conn, "2026-01-01", "own", 10.0, 5.0)
    taxlots.record_sale(conn, "2026-02-01", 4.0, 6.0)
    result = reconcile.shares_as_of(conn, "2026-02-15")
    assert result["total"] == 6.0


def test_shares_as_of_nets_to_zero_when_acquired_and_sold_same_day(conn):
    taxlots.add_lot(conn, "2026-01-01", "own", 10.0, 5.0)
    taxlots.record_sale(conn, "2026-01-01", 10.0, 6.0)
    result = reconcile.shares_as_of(conn, "2026-01-01")
    assert result["total"] == 0.0


def test_shares_as_of_today_equals_sum_qty_remaining(conn):
    taxlots.add_lot(conn, "2026-01-01", "own", 10.0, 5.0)
    taxlots.add_lot(conn, "2026-01-05", "matched", 3.0, 0.0)
    taxlots.record_sale(conn, "2026-01-10", 2.0, 6.0)
    expected = conn.execute(
        "SELECT COALESCE(SUM(qty_remaining), 0) t FROM lots").fetchone()["t"]
    result = reconcile.shares_as_of(conn, "2026-06-01")
    assert result["total"] == pytest.approx(expected)


def test_shares_as_of_survives_partially_allocated_lot_before_and_after(conn):
    taxlots.add_lot(conn, "2026-01-01", "own", 100.0, 5.0)
    taxlots.record_sale(conn, "2026-02-01", 30.0, 6.0)  # przed D
    taxlots.record_sale(conn, "2026-04-01", 40.0, 6.0)  # po D
    result = reconcile.shares_as_of(conn, "2026-03-01")
    assert result["total"] == 70.0


def test_shares_as_of_breaks_down_by_lot_type_summing_to_total(conn):
    taxlots.add_lot(conn, "2026-01-01", "own", 10.0, 5.0)
    taxlots.add_lot(conn, "2026-01-01", "matched", 3.0, 0.0)
    result = reconcile.shares_as_of(conn, "2026-06-01")
    assert result["by_lot_type"]["own"] == 10.0
    assert result["by_lot_type"]["matched"] == 3.0
    assert sum(result["by_lot_type"].values()) == result["total"] == 13.0


def test_shares_as_of_after_reverse_sale_returns_to_pre_sale_state(conn):
    taxlots.add_lot(conn, "2026-01-01", "own", 10.0, 5.0)
    sale_id = taxlots.record_sale(conn, "2026-02-01", 4.0, 6.0)
    taxlots.reverse_sale(conn, sale_id)
    result = reconcile.shares_as_of(conn, "2026-06-01")
    assert result["total"] == 10.0


def test_shares_as_of_returns_none_when_allocation_predates_its_lot(conn):
    # Symulacja danych sprzed kroku 19 (open_lots(as_of=) nie istniało jeszcze) - alokacja
    # przypięta do lotu nabytego PO dacie sprzedaży. Rekonstrukcja nie jest do udowodnienia.
    lot_id = taxlots.add_lot(conn, "2026-03-01", "own", 10.0, 5.0)
    conn.execute(
        "INSERT INTO sales (sale_date, quantity, price_eur, fee_eur, revenue_pln) "
        "VALUES ('2026-01-01', 5.0, 6.0, 0.0, 100.0)")
    sale_id = conn.execute("SELECT id FROM sales WHERE sale_date = '2026-01-01'").fetchone()["id"]
    conn.execute(
        "INSERT INTO sale_allocations (sale_id, lot_id, quantity, cost_pln, revenue_pln) "
        "VALUES (?, ?, 5.0, 0.0, 100.0)", (sale_id, lot_id))
    conn.commit()
    assert reconcile.shares_as_of(conn, "2026-06-01") is None


def test_shares_as_of_data_written_by_record_sale_never_violates_the_guard(conn):
    taxlots.add_lot(conn, "2026-01-01", "own", 10.0, 5.0)
    taxlots.record_sale(conn, "2026-02-01", 4.0, 6.0)
    assert reconcile._allocation_date_violations(conn) == 0


# --- unvested_as_of ---

def _make_pending_vest(conn, vest_date, quantity, available_from=None, program="espp"):
    grant_id = grantsm.add_grant(conn, program, "2025-01-01", None, f"grant:{vest_date}:{quantity}")
    return grantsm.add_vest(conn, grant_id, vest_date, quantity, f"vest:{vest_date}:{quantity}",
                            available_from=available_from)


def _make_vested_vest(conn, vest_date, quantity, lot_acquired_date, program="lti"):
    grant_id = grantsm.add_grant(conn, program, "2025-01-01", None, f"grant:{vest_date}:{quantity}:v")
    vest_id = grantsm.add_vest(conn, grant_id, vest_date, quantity, f"vest:{vest_date}:{quantity}:v")
    lot_id = taxlots.add_lot(conn, lot_acquired_date, "lti" if program == "lti" else "matched",
                              quantity, 0.0)
    conn.execute("UPDATE vests SET status = 'vested', lot_id = ? WHERE id = ?", (lot_id, vest_id))
    conn.commit()
    return vest_id


def test_unvested_as_of_counts_vested_vest_whose_lot_is_later_than_the_date(conn):
    _make_vested_vest(conn, "2026-01-01", 50.0, lot_acquired_date="2026-07-09")
    result = reconcile.unvested_as_of(conn, "2026-06-01")
    assert result["total"] == 50.0


def test_unvested_as_of_excludes_vested_vest_whose_lot_predates_the_date(conn):
    _make_vested_vest(conn, "2026-01-01", 50.0, lot_acquired_date="2026-02-01")
    result = reconcile.unvested_as_of(conn, "2026-06-01")
    assert result["total"] == 0.0


def test_unvested_as_of_counts_pending_vest_scheduled_after_the_date(conn):
    _make_pending_vest(conn, "2026-08-01", 30.0)
    result = reconcile.unvested_as_of(conn, "2026-06-01")
    assert result["total"] == 30.0
    assert result["ambiguous_qty"] == 0.0


def test_unvested_as_of_uses_available_from_over_vest_date(conn):
    # ESPP: vest_date <= D < available_from (krok 21) - wciąż liczy się jako unvested,
    # bo Computershare jeszcze nie zaksięgowało dostępności.
    _make_pending_vest(conn, "2026-05-01", 20.0, available_from="2026-08-27")
    result = reconcile.unvested_as_of(conn, "2026-06-01")
    assert result["total"] == 20.0
    assert result["ambiguous_qty"] == 0.0


def test_unvested_as_of_reports_overdue_pending_as_ambiguous_not_in_total(conn):
    _make_pending_vest(conn, "2026-01-01", 15.0, available_from="2026-01-05")
    result = reconcile.unvested_as_of(conn, "2026-06-01")
    assert result["total"] == 0.0
    assert result["ambiguous_qty"] == 15.0
    assert len(result["ambiguous_items"]) == 1


def test_unvested_as_of_is_no_data_for_vested_without_lot_id(conn):
    grant_id = grantsm.add_grant(conn, "lti", "2025-01-01", None, "grant:orphan")
    vest_id = grantsm.add_vest(conn, grant_id, "2026-01-01", 10.0, "vest:orphan")
    conn.execute("UPDATE vests SET status = 'vested' WHERE id = ?", (vest_id,))
    conn.commit()
    result = reconcile.unvested_as_of(conn, "2026-06-01")
    assert result["total"] is None
    assert len(result["unreconstructable"]) == 1


def test_unvested_as_of_is_no_data_when_cancelled_vest_exists(conn):
    grant_id = grantsm.add_grant(conn, "lti", "2025-01-01", None, "grant:cancelled")
    vest_id = grantsm.add_vest(conn, grant_id, "2026-01-01", 10.0, "vest:cancelled")
    conn.execute("UPDATE vests SET status = 'cancelled' WHERE id = ?", (vest_id,))
    conn.commit()
    result = reconcile.unvested_as_of(conn, "2026-06-01")
    assert result["total"] is None
    assert len(result["unreconstructable"]) == 1


def test_unvested_as_of_matches_unvested_summary_upcoming_qty_for_today(conn):
    _make_pending_vest(conn, "2099-01-01", 30.0)   # daleko w przyszłości - zawsze "upcoming"
    today = "2026-08-23"
    summary = grantsm.unvested_summary(conn, today=today)
    result = reconcile.unvested_as_of(conn, today)
    assert result["total"] == summary["upcoming_qty"]


# --- unvested_as_of: transza wydana w zbiorczym locie (E9, docs/PLAN_E9_transza_w_puli.md) ---

def _make_pooled_vest(conn, vest_date, quantity, pool_acquired_date, program="espp"):
    """`status='vested'`, `lot_id=NULL`, `pooled_lot_id` = lot ZBIORCZY (dzielony z
    innymi transzami, np. Withhold-to-Cover) — mirror
    `data_fixes.py::link_pooled_espp_match_2025_08`."""
    grant_id = grantsm.add_grant(conn, program, "2025-01-01", None,
                                  f"grant:{vest_date}:{quantity}:pool")
    vest_id = grantsm.add_vest(conn, grant_id, vest_date, quantity,
                               f"vest:{vest_date}:{quantity}:pool")
    lot_id = taxlots.add_lot(conn, pool_acquired_date, "matched", quantity + 50.0, 0.0)
    conn.execute(
        "UPDATE vests SET status = 'vested', pooled_lot_id = ? WHERE id = ?",
        (lot_id, vest_id))
    conn.commit()
    return vest_id


def test_unvested_as_of_excludes_pooled_vest_whose_pool_predates_the_date(conn):
    _make_pooled_vest(conn, "2025-08-01", 24.42, pool_acquired_date="2025-08-28")
    result = reconcile.unvested_as_of(conn, "2026-08-18")
    assert result["total"] == 0.0
    assert result["ambiguous_qty"] == 0.0
    assert result["unreconstructable"] == []


def test_unvested_as_of_counts_pooled_vest_whose_pool_is_later_than_the_date(conn):
    _make_pooled_vest(conn, "2025-08-01", 24.42, pool_acquired_date="2026-09-01")
    result = reconcile.unvested_as_of(conn, "2026-08-18")
    assert result["total"] == 24.42


def test_unvested_as_of_is_no_data_when_both_lot_id_and_pooled_lot_id_are_null(conn):
    grant_id = grantsm.add_grant(conn, "espp", "2025-01-01", None, "grant:bare")
    vest_id = grantsm.add_vest(conn, grant_id, "2025-08-01", 24.42, "vest:bare")
    conn.execute("UPDATE vests SET status = 'vested' WHERE id = ?", (vest_id,))
    conn.commit()
    result = reconcile.unvested_as_of(conn, "2026-08-18")
    assert result["total"] is None
    assert len(result["unreconstructable"]) == 1


# --- pending_wtc_sales_as_of ---

def _make_wtc_conflict(conn, execution_date, quantity):
    conn.execute(
        "INSERT INTO imports (filename, file_sha256, as_of_date) VALUES ('x','x', ?)",
        (execution_date,))
    import_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    import json
    nk = f"wtc:{execution_date}:{quantity}:0.0"
    conn.execute(
        "INSERT INTO import_conflicts (import_id, entity_type, natural_key, existing_json, "
        "incoming_json) VALUES (?, 'withhold_to_cover_sale', ?, '{}', ?)",
        (import_id, nk, json.dumps({"execution_date": execution_date, "quantity": quantity,
                                     "net_proceeds_eur": 0.0})))
    conn.commit()


def test_pending_wtc_sale_after_as_of_is_not_subtracted(conn):
    _make_wtc_conflict(conn, "2026-08-01", 100.0)
    result = reconcile.pending_wtc_sales_as_of(conn, "2026-06-01")
    assert result == []


def test_pending_wtc_sale_before_as_of_is_subtracted(conn):
    _make_wtc_conflict(conn, "2026-01-01", 100.0)
    result = reconcile.pending_wtc_sales_as_of(conn, "2026-06-01")
    assert len(result) == 1
    assert result[0]["quantity"] == 100.0


def test_pending_wtc_sale_already_booked_is_not_returned_twice(conn):
    _make_wtc_conflict(conn, "2026-01-01", 100.0)
    conn.execute(
        "INSERT INTO sales (sale_date, quantity, price_eur, fee_eur, revenue_pln) "
        "VALUES ('2026-01-01', 100.0, 5.0, 0.0, 2000.0)")
    conn.commit()
    result = reconcile.pending_wtc_sales_as_of(conn, "2026-06-01")
    assert result == []


# --- shares_tolerance ---

def test_shares_tolerance_is_base_when_no_snapshot_lots(conn):
    assert reconcile.shares_tolerance(conn, "2026-06-01") == pytest.approx(0.02)


def test_shares_tolerance_grows_with_snapshot_lots_acquired_before_the_date(conn):
    for i in range(12):
        taxlots.add_lot(conn, "2026-01-01", "dividend_drip", 0.1, 6.0,
                         source="holdings_snapshot")
    assert reconcile.shares_tolerance(conn, "2026-06-01") == pytest.approx(0.14)


def test_shares_tolerance_ignores_snapshot_lot_acquired_after_the_date(conn):
    taxlots.add_lot(conn, "2026-08-01", "dividend_drip", 0.1, 6.0, source="holdings_snapshot")
    assert reconcile.shares_tolerance(conn, "2026-06-01") == pytest.approx(0.02)


def test_shares_tolerance_is_capped_at_two_shares(conn):
    for i in range(500):
        taxlots.add_lot(conn, "2026-01-01", "dividend_drip", 0.1, 6.0,
                         source="holdings_snapshot", natural_key=f"snap:{i}")
    assert reconcile.shares_tolerance(conn, "2026-06-01") == pytest.approx(2.0)


# --- reconcile() — silnik uzgodnienia (krok 3) ---

def _snapshot(**overrides):
    base = {
        "period_start": "2026-01-01", "period_end": "2026-08-18", "as_of_date": "2026-08-18",
        "shares_total": None, "restricted_units_total": None,
        "pending_tranches": [], "dividends": [], "purchases": [],
        "withhold_type_a": [], "withhold_type_b": [],
    }
    base.update(overrides)
    return base


def _position(positions, key):
    return next(p for p in positions if p.key == key)


def test_reconcile_shares_ok_when_within_tolerance(conn):
    taxlots.add_lot(conn, "2026-01-01", "own", 100.0, 5.0)
    positions = reconcile.reconcile(conn, _snapshot(shares_total=100.01))
    p = _position(positions, "shares")
    assert p.status == "ok"
    assert p.database == pytest.approx(100.0)


def test_reconcile_shares_mismatch_when_outside_tolerance(conn):
    taxlots.add_lot(conn, "2026-01-01", "own", 100.0, 5.0)
    positions = reconcile.reconcile(conn, _snapshot(shares_total=150.0))
    p = _position(positions, "shares")
    assert p.status == "mismatch"
    assert p.diff == pytest.approx(-50.0)


def test_reconcile_shares_no_data_when_statement_value_missing(conn):
    taxlots.add_lot(conn, "2026-01-01", "own", 100.0, 5.0)
    positions = reconcile.reconcile(conn, _snapshot(shares_total=None))
    assert _position(positions, "shares").status == "no_data"


def test_reconcile_shares_no_data_when_allocation_guard_trips(conn):
    lot_id = taxlots.add_lot(conn, "2026-03-01", "own", 10.0, 5.0)
    conn.execute(
        "INSERT INTO sales (sale_date, quantity, price_eur, fee_eur, revenue_pln) "
        "VALUES ('2026-01-01', 5.0, 6.0, 0.0, 100.0)")
    sale_id = conn.execute("SELECT id FROM sales WHERE sale_date='2026-01-01'").fetchone()["id"]
    conn.execute(
        "INSERT INTO sale_allocations (sale_id, lot_id, quantity, cost_pln, revenue_pln) "
        "VALUES (?, ?, 5.0, 0.0, 100.0)", (sale_id, lot_id))
    conn.commit()
    positions = reconcile.reconcile(conn, _snapshot(shares_total=5.0))
    p = _position(positions, "shares")
    assert p.status == "no_data"
    assert p.database is None


def test_reconcile_shares_subtracts_pending_unconfirmed_withhold_to_cover(conn):
    taxlots.add_lot(conn, "2026-01-01", "own", 100.0, 5.0)
    _make_wtc_conflict(conn, "2026-01-15", 20.0)
    positions = reconcile.reconcile(conn, _snapshot(shares_total=80.0))
    p = _position(positions, "shares")
    assert p.database == pytest.approx(80.0)
    assert p.status == "ok"


def test_reconcile_restricted_units_ok_when_matching(conn):
    _make_pending_vest(conn, "2027-01-01", 40.0)
    positions = reconcile.reconcile(conn, _snapshot(
        restricted_units_total=40.0, pending_tranches=[{"natural_key": "x", "quantity": 40.0}]))
    p = _position(positions, "restricted_units")
    assert p.status == "ok"
    assert p.database == pytest.approx(40.0)


def test_reconcile_restricted_units_mismatch(conn):
    _make_pending_vest(conn, "2027-01-01", 40.0)
    positions = reconcile.reconcile(conn, _snapshot(
        restricted_units_total=10.0, pending_tranches=[{"natural_key": "x", "quantity": 40.0}]))
    assert _position(positions, "restricted_units").status == "mismatch"


def test_reconcile_restricted_units_no_data_when_ambiguous_exceeds_tolerance(conn):
    # transza overdue-pending o dużej ilości - niepewność przewyższa tolerancję rzędu
    # setnych części akcji, więc pozycja nie może udawać pewności.
    _make_pending_vest(conn, "2026-01-01", 500.0, available_from="2026-01-05")
    positions = reconcile.reconcile(conn, _snapshot(
        restricted_units_total=0.0, pending_tranches=[]))
    p = _position(positions, "restricted_units")
    assert p.status == "no_data"
    assert "niepewn" in p.note.lower() or "przetermin" in p.note.lower()


def test_reconcile_restricted_units_counts_small_ambiguous_within_tolerance(conn):
    # ambiguous_qty maleńkie (poniżej tolerancji dla 1 wiersza, 0.02) - pozycja liczona
    # normalnie, z notatką, nie no_data.
    _make_pending_vest(conn, "2027-01-01", 40.0)
    _make_pending_vest(conn, "2026-01-01", 0.001, available_from="2026-01-05")
    positions = reconcile.reconcile(conn, _snapshot(
        restricted_units_total=40.0, pending_tranches=[{"natural_key": "x", "quantity": 40.0}]))
    p = _position(positions, "restricted_units")
    assert p.status == "ok"
    assert p.database == pytest.approx(40.0)


def test_reconcile_total_combines_shares_and_restricted_units(conn):
    taxlots.add_lot(conn, "2026-01-01", "own", 100.0, 5.0)
    _make_pending_vest(conn, "2027-01-01", 40.0)
    positions = reconcile.reconcile(conn, _snapshot(
        shares_total=100.0, restricted_units_total=40.0,
        pending_tranches=[{"natural_key": "x", "quantity": 40.0}]))
    p = _position(positions, "total")
    assert p.status == "ok"
    assert p.database == pytest.approx(140.0)
    assert p.statement == pytest.approx(140.0)


def test_reconcile_total_is_no_data_when_either_side_missing(conn):
    taxlots.add_lot(conn, "2026-01-01", "own", 100.0, 5.0)
    positions = reconcile.reconcile(conn, _snapshot(shares_total=100.0,
                                                      restricted_units_total=None))
    assert _position(positions, "total").status == "no_data"


def test_reconcile_broker_cash_is_always_no_data(conn):
    positions = reconcile.reconcile(conn, _snapshot())
    p = _position(positions, "broker_cash")
    assert p.status == "no_data"
    assert p.statement is None


def test_reconcile_pending_wtc_sales_is_informational_not_mismatch(conn):
    taxlots.add_lot(conn, "2026-01-01", "own", 100.0, 5.0)
    _make_wtc_conflict(conn, "2026-01-15", 20.0)
    positions = reconcile.reconcile(conn, _snapshot(shares_total=80.0))
    p = _position(positions, "pending_wtc_sales")
    assert p.status not in ("mismatch",)
    assert len(p.details) == 1
    assert p.details[0]["quantity"] == 20.0


def test_reconcile_dividends_in_period_ok_when_matching(conn, monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    from nokia_tracker.tax import dividends as taxdiv
    taxdiv.add_dividend(conn, record_date="2026-03-01", entitled_quantity=10.0,
                         gross_eur=5.0, taxes_eur=1.75)
    positions = reconcile.reconcile(conn, _snapshot(dividends=[
        {"record_date": "2026-03-01", "gross_dividend_payment_eur": 5.0}]))
    p = _position(positions, "dividends_in_period")
    assert p.status == "ok"
    assert p.database == pytest.approx(5.0)


def test_reconcile_espp_purchases_in_period_ok_when_matching(conn):
    nk = "purchase:2025-10-24:2026-02-02:19.21982"
    taxlots.add_lot(conn, "2026-02-02", "own", 19.21982, 5.48, natural_key=nk)
    positions = reconcile.reconcile(conn, _snapshot(purchases=[
        {"natural_key": nk, "quantity": 19.21982}]))
    p = _position(positions, "espp_purchases_in_period")
    assert p.status == "ok"
    assert p.database == pytest.approx(19.21982)


# --- latest_snapshot() — wspólny odczyt dla views/imports.py i views/account.py (krok 7) ---

def test_latest_snapshot_returns_none_when_no_import_yet(conn):
    assert reconcile.latest_snapshot(conn) is None


def test_latest_snapshot_returns_the_parsed_json_of_the_most_recent_as_of_date(conn):
    conn.execute(
        "INSERT INTO imports (filename, file_sha256, as_of_date) VALUES ('x','x','2026-01-01')")
    old_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute(
        "INSERT INTO statement_snapshots (import_id, as_of_date, snapshot_json) "
        "VALUES (?, '2026-01-01', '{\"as_of_date\": \"2026-01-01\"}')", (old_id,))
    conn.execute(
        "INSERT INTO imports (filename, file_sha256, as_of_date) VALUES ('y','y','2026-08-18')")
    new_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute(
        "INSERT INTO statement_snapshots (import_id, as_of_date, snapshot_json) "
        "VALUES (?, '2026-08-18', '{\"as_of_date\": \"2026-08-18\"}')", (new_id,))
    conn.commit()
    assert reconcile.latest_snapshot(conn) == {"as_of_date": "2026-08-18"}
