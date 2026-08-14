"""Kopia zapasowa i przywracanie (krok 24, docs/PLAN_KROK_24_backup.md).

Wszystkie testy operują na prawdziwych plikach SQLite w tmp_path, nie na
mockach — eksport/import muszą działać na realnym pliku bazy, nie na obiekcie
Connection w pamięci (żywy add-on trzyma bazę na dysku, `WRITE_LOCK` chroni
tylko dostęp z procesu, nie samą operację na pliku).
"""
from __future__ import annotations

import json
import sqlite3
import zipfile

import pytest

from nokia_tracker import __version__, backup, db as dbm


def _seed(conn: sqlite3.Connection, *, lot_id: int, sale_id: int, grant_id: int) -> None:
    conn.execute(
        "INSERT INTO lots (id, acquired_date, lot_type, quantity, price_eur) "
        "VALUES (?, '2024-01-10', 'own', 10.0, 5.0)", (lot_id,))
    conn.execute(
        "INSERT INTO sales (id, sale_date, quantity, price_eur) "
        "VALUES (?, '2024-06-01', 5.0, 6.0)", (sale_id,))
    conn.execute(
        "INSERT INTO sale_allocations (sale_id, lot_id, quantity, cost_pln, revenue_pln) "
        "VALUES (?, ?, 5.0, 100.0, 150.0)", (sale_id, lot_id))
    conn.execute(
        "INSERT INTO grants (id, program, grant_date) VALUES (?, 'espp', '2024-01-01')",
        (grant_id,))
    conn.execute(
        "INSERT INTO vests (grant_id, vest_date, quantity) VALUES (?, '2025-01-01', 3.0)",
        (grant_id,))
    conn.execute(
        "INSERT INTO dividends (pay_date, gross_eur) VALUES ('2024-03-01', 12.5)")
    conn.commit()


@pytest.fixture
def seeded_db_path(tmp_path):
    path = str(tmp_path / "current.db")
    conn = dbm.get_conn(path)
    dbm.migrate(conn)
    _seed(conn, lot_id=1, sale_id=1, grant_id=1)
    conn.close()
    return path


def test_export_zip_contains_manifest_db_and_csvs(seeded_db_path):
    data = backup.export_zip(seeded_db_path)
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert names == {
            "nokia.db", "manifest.json",
            "lots.csv", "sales.csv", "sale_allocations.csv",
            "grants.csv", "vests.csv", "dividends.csv",
        }
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["app_version"] == __version__
        assert manifest["schema_version"] == dbm.SCHEMA_VERSION
        assert manifest["row_counts"]["lots"] == 1
        assert manifest["row_counts"]["dividends"] == 1

        lots_csv = zf.read("lots.csv").decode("utf-8")
        assert "acquired_date" in lots_csv.splitlines()[0]
        assert "2024-01-10" in lots_csv


def test_export_zip_db_entry_has_same_data_as_source(seeded_db_path, tmp_path):
    data = backup.export_zip(seeded_db_path)
    out_path = tmp_path / "extracted.db"
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
        out_path.write_bytes(zf.read("nokia.db"))
    conn = sqlite3.connect(str(out_path))
    row = conn.execute("SELECT acquired_date, quantity FROM lots WHERE id = 1").fetchone()
    assert row == ("2024-01-10", 10.0)
    conn.close()


def test_restore_preview_computes_added_removed_unchanged(tmp_path):
    current_path = str(tmp_path / "current.db")
    conn = dbm.get_conn(current_path)
    dbm.migrate(conn)
    _seed(conn, lot_id=1, sale_id=1, grant_id=1)  # lot id=1 stays only in "current"
    conn.execute(
        "INSERT INTO lots (id, acquired_date, lot_type, quantity, price_eur) "
        "VALUES (2, '2024-02-01', 'own', 3.0, 4.0)")  # lot id=2 shared
    conn.commit()
    conn.close()

    incoming_path = str(tmp_path / "incoming.db")
    inc_conn = dbm.get_conn(incoming_path)
    dbm.migrate(inc_conn)
    inc_conn.execute(
        "INSERT INTO lots (id, acquired_date, lot_type, quantity, price_eur) "
        "VALUES (2, '2024-02-01', 'own', 3.0, 4.0)")  # shared with current
    inc_conn.execute(
        "INSERT INTO lots (id, acquired_date, lot_type, quantity, price_eur) "
        "VALUES (3, '2024-03-01', 'own', 1.0, 4.0)")  # only in incoming -> "added"
    inc_conn.commit()
    inc_conn.close()

    zip_bytes = backup.export_zip(incoming_path)
    result = backup.restore_preview(current_path, zip_bytes)

    assert result["diff"]["lots"] == {"added": 1, "removed": 1, "unchanged": 1}
    assert result["manifest"]["schema_version"] == dbm.SCHEMA_VERSION


def test_restore_preview_rejects_newer_schema(tmp_path, seeded_db_path):
    zip_bytes = backup.export_zip(seeded_db_path)
    # spreparowany manifest z przyszłej wersji schematu
    with zipfile.ZipFile(__import__("io").BytesIO(zip_bytes)) as zf:
        entries = {n: zf.read(n) for n in zf.namelist()}
    manifest = json.loads(entries["manifest.json"])
    manifest["schema_version"] = dbm.SCHEMA_VERSION + 1
    entries["manifest.json"] = json.dumps(manifest).encode("utf-8")

    buf = __import__("io").BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)

    with pytest.raises(backup.IncompatibleBackupError):
        backup.restore_preview(seeded_db_path, buf.getvalue())


def test_restore_apply_replaces_db_content(tmp_path):
    current_path = str(tmp_path / "current.db")
    conn = dbm.get_conn(current_path)
    dbm.migrate(conn)
    _seed(conn, lot_id=1, sale_id=1, grant_id=1)
    conn.close()

    incoming_path = str(tmp_path / "incoming.db")
    inc_conn = dbm.get_conn(incoming_path)
    dbm.migrate(inc_conn)
    _seed(inc_conn, lot_id=2, sale_id=2, grant_id=2)  # inne id-ki niż "current"
    inc_conn.close()

    zip_bytes = backup.export_zip(incoming_path)
    manifest = backup.restore_apply(current_path, zip_bytes)

    assert manifest["schema_version"] == dbm.SCHEMA_VERSION
    result = sqlite3.connect(current_path)
    ids = {r[0] for r in result.execute("SELECT id FROM lots")}
    assert ids == {2}  # lot id=1 z "current" zniknął, id=2 z kopii jest
    assert result.execute("PRAGMA user_version").fetchone()[0] == dbm.SCHEMA_VERSION
    result.close()


def test_restore_apply_removes_stale_wal_and_shm_sidecars(tmp_path):
    current_path = str(tmp_path / "current.db")
    conn = dbm.get_conn(current_path)
    dbm.migrate(conn)
    _seed(conn, lot_id=1, sale_id=1, grant_id=1)
    conn.close()

    (tmp_path / "current.db-wal").write_bytes(b"stale-wal-content")
    (tmp_path / "current.db-shm").write_bytes(b"stale-shm-content")

    incoming_path = str(tmp_path / "incoming.db")
    inc_conn = dbm.get_conn(incoming_path)
    dbm.migrate(inc_conn)
    _seed(inc_conn, lot_id=2, sale_id=2, grant_id=2)
    inc_conn.close()

    backup.restore_apply(current_path, backup.export_zip(incoming_path))

    assert not (tmp_path / "current.db-wal").exists() \
        or (tmp_path / "current.db-wal").read_bytes() != b"stale-wal-content"
    assert not (tmp_path / "current.db-shm").exists() \
        or (tmp_path / "current.db-shm").read_bytes() != b"stale-shm-content"
