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
