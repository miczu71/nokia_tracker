"""Dane dla /imports (E7 — docs/PLAN_E7_uzgodnienie.md, krok 6). Historia importów
i kolejka konfliktów (logika przeniesiona z `web/routes_dane.py`, jedyna trasa, która
nie przeszła przez refaktor E3) plus uzgodnienie z NAJNOWSZEGO zapisanego snapshotu
wyciągu, przeliczone NA ŻĄDANIE przez `reconcile.reconcile()` — bez ponownego
wgrywania PDF. Zero zapisu — kontrakt `views/__init__.py`."""
from __future__ import annotations

import json
import sqlite3

from .. import reconcile as reconcilem


def imports_view(conn: sqlite3.Connection) -> dict:
    history = [dict(r) for r in conn.execute(
        "SELECT * FROM imports ORDER BY imported_at DESC").fetchall()]

    conflict_rows = conn.execute(
        "SELECT * FROM import_conflicts WHERE resolved = 0 ORDER BY id DESC").fetchall()
    conflicts = []
    for r in conflict_rows:
        d = dict(r)
        d["existing"] = json.loads(d["existing_json"]) if d["existing_json"] else {}
        d["incoming"] = json.loads(d["incoming_json"]) if d["incoming_json"] else {}
        conflicts.append(d)

    snapshot = reconcilem.latest_snapshot(conn)
    reconciliation = reconcilem.reconcile(conn, snapshot) if snapshot else None
    reconciliation_as_of = snapshot.get("as_of_date") if snapshot else None

    return {
        "history": history,
        "conflicts": conflicts,
        "reconciliation": reconciliation,
        "reconciliation_as_of": reconciliation_as_of,
    }
