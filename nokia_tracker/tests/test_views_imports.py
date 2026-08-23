"""views/imports.py (E7 — docs/PLAN_E7_uzgodnienie.md, krok 6). Zero zapisu — kontrakt
views/__init__.py. Konflikty/historia to logika przeniesiona jeden do jednego z
web/routes_dane.py (jedyna trasa, która nie przeszła przez refaktor E3); uzgodnienie
to nowa kompozycja nad reconcile.reconcile() i najnowszym statement_snapshots."""
from __future__ import annotations

import json

from nokia_tracker.views.imports import imports_view


def test_imports_view_empty_db_has_no_history_no_conflicts_no_reconciliation(conn):
    result = imports_view(conn)
    assert result["history"] == []
    assert result["conflicts"] == []
    assert result["reconciliation"] is None
    assert result["reconciliation_as_of"] is None


def test_imports_view_parses_conflict_json_columns(conn):
    conn.execute(
        "INSERT INTO imports (filename, file_sha256, as_of_date) VALUES ('x','x','2026-08-18')")
    import_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute(
        "INSERT INTO import_conflicts (import_id, entity_type, natural_key, existing_json, "
        "incoming_json) VALUES (?, 'balance', 'balance:2026-08-18', '{\"a\": 1}', '{\"b\": 2}')",
        (import_id,))
    conn.commit()
    result = imports_view(conn)
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["existing"] == {"a": 1}
    assert result["conflicts"][0]["incoming"] == {"b": 2}


def test_imports_view_excludes_resolved_conflicts(conn):
    conn.execute(
        "INSERT INTO imports (filename, file_sha256, as_of_date) VALUES ('x','x','2026-08-18')")
    import_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute(
        "INSERT INTO import_conflicts (import_id, entity_type, natural_key, existing_json, "
        "incoming_json, resolved) VALUES (?, 'balance', 'balance:x', '{}', '{}', 1)",
        (import_id,))
    conn.commit()
    assert imports_view(conn)["conflicts"] == []


def test_imports_view_reconciles_from_latest_saved_snapshot(conn):
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

    result = imports_view(conn)
    assert result["reconciliation_as_of"] == "2026-08-18"
    keys = {p.key for p in result["reconciliation"]}
    assert "shares" in keys
    assert "broker_cash" in keys


def test_imports_view_uses_the_most_recent_snapshot_when_several_exist(conn):
    conn.execute(
        "INSERT INTO imports (filename, file_sha256, as_of_date) VALUES ('x','x','2026-01-01')")
    older_import_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute(
        "INSERT INTO statement_snapshots (import_id, as_of_date, snapshot_json) "
        "VALUES (?, '2026-01-01', '{\"as_of_date\": \"2026-01-01\"}')", (older_import_id,))
    conn.execute(
        "INSERT INTO imports (filename, file_sha256, as_of_date) VALUES ('y','y','2026-08-18')")
    newer_import_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    conn.execute(
        "INSERT INTO statement_snapshots (import_id, as_of_date, snapshot_json) "
        "VALUES (?, '2026-08-18', '{\"as_of_date\": \"2026-08-18\"}')", (newer_import_id,))
    conn.commit()

    result = imports_view(conn)
    assert result["reconciliation_as_of"] == "2026-08-18"
