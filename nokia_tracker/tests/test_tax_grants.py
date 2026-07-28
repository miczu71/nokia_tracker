"""Granty i transze vestingu (BLUEPRINT §3a, krok 13) - CRUD idempotentny po natural_key,
ten sam wzorzec co tax/lots.py::add_lot. Auto-tworzenie lotów matched/lti na podstawie tych
transz to scheduler kroku 14 (tax/vesting.py) - tu tylko zapis do bazy."""
from __future__ import annotations

import pytest

from nokia_tracker.tax import grants


def test_add_grant_inserts_new_row(conn):
    grant_id = grants.add_grant(conn, "espp", "2025-10-27", 29.24, "espp_grant:2025-10-27:29.24")
    row = conn.execute("SELECT * FROM grants WHERE id = ?", (grant_id,)).fetchone()
    assert row["program"] == "espp"
    assert row["grant_date"] == "2025-10-27"
    assert row["quantity"] == 29.24


def test_add_grant_idempotent_on_natural_key(conn):
    first = grants.add_grant(conn, "lti", "2025-07-07", 633.0, "lti_grant:2025 RS AWARD 07-JUL-2025")
    second = grants.add_grant(conn, "lti", "2025-07-07", 633.0, "lti_grant:2025 RS AWARD 07-JUL-2025")
    assert first == second
    count = conn.execute("SELECT COUNT(*) c FROM grants").fetchone()["c"]
    assert count == 1


def test_add_vest_links_to_grant_and_defaults_to_pending(conn):
    grant_id = grants.add_grant(conn, "espp", "2025-10-27", 29.24, "espp_grant:2025-10-27:29.24")
    vest_id = grants.add_vest(
        conn, grant_id, "2026-08-01", 29.24, "espp_vest:2025-10-27:2026-08-01:29.24")
    row = conn.execute("SELECT * FROM vests WHERE id = ?", (vest_id,)).fetchone()
    assert row["grant_id"] == grant_id
    assert row["vest_date"] == "2026-08-01"
    assert row["status"] == "pending"
    assert row["lot_id"] is None


def test_add_vest_idempotent_on_natural_key(conn):
    grant_id = grants.add_grant(conn, "lti", "2025-07-07", 633.0, "lti_grant:g1")
    first = grants.add_vest(conn, grant_id, "2027-07-05", 633.0, "lti_vest:g1:2027-07-05:633.0")
    second = grants.add_vest(conn, grant_id, "2027-07-05", 633.0, "lti_vest:g1:2027-07-05:633.0")
    assert first == second
    count = conn.execute("SELECT COUNT(*) c FROM vests").fetchone()["c"]
    assert count == 1


def test_one_lti_grant_gets_multiple_vest_tranches(conn):
    grant_id = grants.add_grant(conn, "lti", "2025-07-07", 1900.0, "lti_grant:2025 RS AWARD 07-JUL-2025")
    v1 = grants.add_vest(conn, grant_id, "2026-07-09", 634.0, "lti_vest:g:2026-07-09:634.0")
    v2 = grants.add_vest(conn, grant_id, "2027-07-05", 633.0, "lti_vest:g:2027-07-05:633.0")
    v3 = grants.add_vest(conn, grant_id, "2028-07-05", 633.0, "lti_vest:g:2028-07-05:633.0")
    rows = conn.execute(
        "SELECT * FROM vests WHERE grant_id = ? ORDER BY vest_date", (grant_id,)).fetchall()
    assert len(rows) == 3
    assert {v1, v2, v3} == {r["id"] for r in rows}


def test_find_grant_by_natural_key_returns_none_when_absent(conn):
    assert grants.find_grant_by_natural_key(conn, "nope") is None


def test_find_grant_by_natural_key_returns_dict_when_present(conn):
    grants.add_grant(conn, "espp", "2025-10-27", 29.24, "espp_grant:x")
    found = grants.find_grant_by_natural_key(conn, "espp_grant:x")
    assert found["quantity"] == 29.24


def test_list_espp_returns_grant_and_vest_joined_1to1(conn):
    grant_id = grants.add_grant(
        conn, "espp", "2025-10-27", 29.24, "espp_grant:2025-10-27:29.24", match_pct=20.0)
    grants.add_vest(conn, grant_id, "2026-08-01", 29.24, "espp_vest:2025-10-27:2026-08-01:29.24")
    rows = grants.list_espp(conn)
    assert len(rows) == 1
    assert rows[0]["grant_date"] == "2025-10-27"
    assert rows[0]["vest_date"] == "2026-08-01"
    assert rows[0]["quantity"] == 29.24
    assert rows[0]["match_pct"] == 20.0


def test_list_espp_empty_when_no_espp_grants(conn):
    assert grants.list_espp(conn) == []


def test_list_lti_grouped_sums_multiple_tranches_and_extracts_description(conn):
    grant_id = grants.add_grant(
        conn, "lti", "2025-07-07", None, "lti_grant:2025 RS AWARD 07-JUL-2025")
    grants.add_vest(conn, grant_id, "2026-07-09", 634.0, "lti_vest:g:2026-07-09:634.0")
    grants.add_vest(conn, grant_id, "2027-07-05", 633.0, "lti_vest:g:2027-07-05:633.0")

    result = grants.list_lti_grouped(conn)
    assert len(result) == 1
    assert result[0]["participation_description"] == "2025 RS AWARD 07-JUL-2025"
    assert result[0]["total_quantity"] == pytest.approx(1267.0)
    assert len(result[0]["vests"]) == 2


def test_list_lti_grouped_empty_when_no_lti_grants(conn):
    assert grants.list_lti_grouped(conn) == []


# --- overdue flag (krok 13.6, docs/PLAN_KROK_13_6_vesting_gap.md) ---

def test_list_espp_marks_pending_past_vest_date_as_overdue(conn):
    grant_id = grants.add_grant(conn, "espp", "2022-10-26", 7.33, "espp_grant:x", match_pct=50.0)
    grants.add_vest(conn, grant_id, "2023-08-01", 7.33, "espp_vest:x")
    rows = grants.list_espp(conn, today="2026-07-28")
    assert rows[0]["overdue"] is True


def test_list_espp_does_not_mark_future_vest_date_as_overdue(conn):
    grant_id = grants.add_grant(conn, "espp", "2026-04-27", 17.37, "espp_grant:y", match_pct=50.0)
    grants.add_vest(conn, grant_id, "2026-08-01", 17.37, "espp_vest:y")
    rows = grants.list_espp(conn, today="2026-07-28")
    assert rows[0]["overdue"] is False


def test_list_espp_does_not_mark_already_vested_status_as_overdue(conn):
    # Status już ustawiony ręcznie na 'vested' - nie chcemy fałszywie oznaczać jako
    # zaległe czegoś, co zostało już rozwiązane.
    grant_id = grants.add_grant(conn, "espp", "2022-10-26", 7.33, "espp_grant:z", match_pct=50.0)
    grants.add_vest(conn, grant_id, "2023-08-01", 7.33, "espp_vest:z", status="vested")
    rows = grants.list_espp(conn, today="2026-07-28")
    assert rows[0]["overdue"] is False


def test_list_lti_grouped_marks_past_pending_vest_as_overdue_per_tranche(conn):
    grant_id = grants.add_grant(conn, "lti", "2023-07-06", None, "lti_grant:g1")
    grants.add_vest(conn, grant_id, "2026-07-06", 2100.0, "lti_vest:g1:2026-07-06")
    result = grants.list_lti_grouped(conn, today="2026-07-28")
    assert result[0]["vests"][0]["overdue"] is True


def test_list_lti_grouped_does_not_mark_future_tranche_as_overdue(conn):
    grant_id = grants.add_grant(conn, "lti", "2025-07-07", None, "lti_grant:g2")
    grants.add_vest(conn, grant_id, "2027-07-05", 633.0, "lti_vest:g2:2027-07-05")
    result = grants.list_lti_grouped(conn, today="2026-07-28")
    assert result[0]["vests"][0]["overdue"] is False


# --- reconcile_vesting (krok 14, docs/PLAN_KROK_14_vesting_reconcile.md) ---

def test_reconcile_vesting_resolves_unique_exact_match(conn):
    from nokia_tracker.tax import lots as taxlots
    grant_id = grants.add_grant(conn, "espp", "2022-10-26", 7.33, "espp_grant:x")
    vest_id = grants.add_vest(conn, grant_id, "2023-08-01", 7.33, "espp_vest:x")
    lot_id = taxlots.add_lot(conn, "2023-08-30", "matched", 7.33, 3.65, source="pdf_import")

    resolved = grants.reconcile_vesting(conn, today="2026-07-28")

    assert resolved == 1
    vest = conn.execute("SELECT * FROM vests WHERE id = ?", (vest_id,)).fetchone()
    assert vest["status"] == "vested"
    assert vest["lot_id"] == lot_id


def test_reconcile_vesting_does_not_resolve_when_two_vests_same_quantity(conn):
    from nokia_tracker.tax import lots as taxlots
    grant_id = grants.add_grant(conn, "espp", "2022-10-26", 7.33, "espp_grant:x")
    grants.add_vest(conn, grant_id, "2023-08-01", 7.33, "espp_vest:x")
    grants.add_vest(conn, grant_id, "2024-08-01", 7.33, "espp_vest:y")
    taxlots.add_lot(conn, "2023-08-30", "matched", 7.33, 3.65, source="pdf_import")

    resolved = grants.reconcile_vesting(conn, today="2026-07-28")

    assert resolved == 0
    rows = conn.execute("SELECT * FROM vests WHERE status = 'pending'").fetchall()
    assert len(rows) == 2


def test_reconcile_vesting_does_not_resolve_when_two_unlinked_lots_same_quantity(conn):
    from nokia_tracker.tax import lots as taxlots
    grant_id = grants.add_grant(conn, "espp", "2022-10-26", 7.33, "espp_grant:x")
    grants.add_vest(conn, grant_id, "2023-08-01", 7.33, "espp_vest:x")
    taxlots.add_lot(conn, "2023-08-30", "matched", 7.33, 3.65, source="pdf_import")
    taxlots.add_lot(conn, "2023-08-30", "matched", 7.33, 3.65, source="pdf_import",
                    natural_key="other_lot")

    resolved = grants.reconcile_vesting(conn, today="2026-07-28")

    assert resolved == 0
    vest = conn.execute("SELECT * FROM vests WHERE natural_key = 'espp_vest:x'").fetchone()
    assert vest["status"] == "pending"


def test_reconcile_vesting_does_not_resolve_future_vest_date(conn):
    from nokia_tracker.tax import lots as taxlots
    grant_id = grants.add_grant(conn, "lti", "2025-07-07", None, "lti_grant:g1")
    vest_id = grants.add_vest(conn, grant_id, "2027-07-05", 633.0, "lti_vest:g1")
    taxlots.add_lot(conn, "2026-07-09", "lti", 633.0, 10.22, source="pdf_import")

    resolved = grants.reconcile_vesting(conn, today="2026-07-28")

    assert resolved == 0
    vest = conn.execute("SELECT * FROM vests WHERE id = ?", (vest_id,)).fetchone()
    assert vest["status"] == "pending"


def test_reconcile_vesting_is_idempotent_on_already_vested(conn):
    from nokia_tracker.tax import lots as taxlots
    grant_id = grants.add_grant(conn, "lti", "2023-07-06", None, "lti_grant:g1")
    grants.add_vest(conn, grant_id, "2026-07-06", 2100.0, "lti_vest:g1")
    taxlots.add_lot(conn, "2026-07-09", "lti", 2100.0, 10.22, source="pdf_import")

    first = grants.reconcile_vesting(conn, today="2026-07-28")
    second = grants.reconcile_vesting(conn, today="2026-07-28")

    assert first == 1
    assert second == 0


# --- due_for_reminder / mark_reminder_sent (krok 14) ---

def test_due_for_reminder_returns_vest_within_window(conn):
    grant_id = grants.add_grant(conn, "espp", "2026-04-27", 17.37, "espp_grant:x")
    grants.add_vest(conn, grant_id, "2026-08-01", 17.37, "espp_vest:x")
    due = grants.due_for_reminder(conn, vest_reminder_days=7, today="2026-07-28")
    assert len(due) == 1
    assert due[0]["vest_date"] == "2026-08-01"
    assert due[0]["program"] == "espp"


def test_due_for_reminder_excludes_vest_too_far_in_future(conn):
    grant_id = grants.add_grant(conn, "espp", "2026-04-27", 17.37, "espp_grant:x")
    grants.add_vest(conn, grant_id, "2026-12-01", 17.37, "espp_vest:x")
    due = grants.due_for_reminder(conn, vest_reminder_days=7, today="2026-07-28")
    assert due == []


def test_due_for_reminder_excludes_vest_already_in_the_past(conn):
    grant_id = grants.add_grant(conn, "espp", "2022-10-26", 7.33, "espp_grant:x")
    grants.add_vest(conn, grant_id, "2023-08-01", 7.33, "espp_vest:x")
    due = grants.due_for_reminder(conn, vest_reminder_days=7, today="2026-07-28")
    assert due == []


def test_due_for_reminder_excludes_already_reminded_vest(conn):
    grant_id = grants.add_grant(conn, "espp", "2026-04-27", 17.37, "espp_grant:x")
    vest_id = grants.add_vest(conn, grant_id, "2026-08-01", 17.37, "espp_vest:x")
    grants.mark_reminder_sent(conn, vest_id)
    due = grants.due_for_reminder(conn, vest_reminder_days=7, today="2026-07-28")
    assert due == []


def test_due_for_reminder_includes_lti_participation_description(conn):
    grant_id = grants.add_grant(conn, "lti", "2025-07-07", None, "lti_grant:2025 RS AWARD 07-JUL-2025")
    grants.add_vest(conn, grant_id, "2026-08-01", 633.0, "lti_vest:x")
    due = grants.due_for_reminder(conn, vest_reminder_days=7, today="2026-07-28")
    assert len(due) == 1
    assert due[0]["participation_description"] == "2025 RS AWARD 07-JUL-2025"


def test_mark_reminder_sent_sets_timestamp(conn):
    grant_id = grants.add_grant(conn, "espp", "2026-04-27", 17.37, "espp_grant:x")
    vest_id = grants.add_vest(conn, grant_id, "2026-08-01", 17.37, "espp_vest:x")
    grants.mark_reminder_sent(conn, vest_id)
    vest = conn.execute("SELECT * FROM vests WHERE id = ?", (vest_id,)).fetchone()
    assert vest["reminder_sent_at"] is not None
