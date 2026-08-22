"""Trasy /dane — kopia zapasowa i przywracanie (krok 24,
docs/PLAN_KROK_24_backup.md). Wydzielone z `test_web.py`
(E3 — docs/ROADMAP_V3.md); fixture `client` w conftest.py."""
import io
import json
import sqlite3
import zipfile

from nokia_tracker import db as dbm
from nokia_tracker.web import create_app


# --- krok 24: /dane — kopia zapasowa i przywracanie (docs/PLAN_KROK_24_backup.md) ---

def test_data_page_renders_export_link_and_upload_form(client):
    html = client.get("/dane").get_data(as_text=True)
    assert 'href="/dane/eksport.zip"' in html
    assert 'action="/dane/import/preview"' in html
    assert 'name="backup_file"' in html


def test_data_export_zip_returns_zip_attachment(client):
    resp = client.get("/dane/eksport.zip")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/zip"
    assert "attachment; filename=nokia_tracker_" in resp.headers["Content-Disposition"]
    assert resp.data[:2] == b"PK"  # magiczne bajty ZIP


def test_data_import_preview_shows_diff_and_confirm_form(tmp_path):
    from io import BytesIO

    from nokia_tracker import backup

    db_path = str(tmp_path / "current.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    conn.close()

    other_path = str(tmp_path / "other.db")
    other_conn = dbm.get_conn(other_path)
    dbm.migrate(other_conn)
    other_conn.execute(
        "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur) "
        "VALUES ('2024-01-10', 'own', 10.0, 5.0)")
    other_conn.commit()
    other_conn.close()
    zip_bytes = backup.export_zip(other_path)

    app = create_app(db_path)
    with app.test_client() as c:
        resp = c.post("/dane/import/preview", data={
            "backup_file": (BytesIO(zip_bytes), "nokia_tracker_2026-01-01.zip"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 302
        assert "token=" in resp.headers["Location"]

        follow = c.get(resp.headers["Location"])
        html = follow.get_data(as_text=True)
        assert "lots" in html
        assert 'name="token"' in html
        assert "Potwierdź przywrócenie" in html


def test_data_import_confirm_applies_restore_and_redirects(tmp_path):
    from io import BytesIO

    from nokia_tracker import backup

    db_path = str(tmp_path / "current.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    conn.execute(
        "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur) "
        "VALUES ('2020-01-01', 'own', 1.0, 1.0)")
    conn.commit()
    conn.close()

    other_path = str(tmp_path / "other.db")
    other_conn = dbm.get_conn(other_path)
    dbm.migrate(other_conn)
    other_conn.execute(
        "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur) "
        "VALUES ('2024-01-10', 'own', 10.0, 5.0)")
    other_conn.commit()
    other_conn.close()
    zip_bytes = backup.export_zip(other_path)

    app = create_app(db_path)
    with app.test_client() as c:
        preview_resp = c.post("/dane/import/preview", data={
            "backup_file": (BytesIO(zip_bytes), "nokia_tracker_2026-01-01.zip"),
        }, content_type="multipart/form-data")
        token = preview_resp.headers["Location"].split("token=")[1]

        confirm_resp = c.post("/dane/import/confirm", data={"token": token})
        assert confirm_resp.status_code == 302
        assert "restored=1" in confirm_resp.headers["Location"]

    result = sqlite3.connect(db_path)
    rows = result.execute("SELECT quantity FROM lots").fetchall()
    assert rows == [(10.0,)]  # dane z "other.db" zastąpiły oryginalny lot
    result.close()


def test_data_import_confirm_unknown_token_redirects_safely(client):
    resp = client.post("/dane/import/confirm", data={"token": "nieistniejacy"})
    assert resp.status_code == 302
    assert "error=" in resp.headers["Location"]


def test_data_import_preview_incompatible_schema_shows_error(tmp_path):
    from io import BytesIO

    from nokia_tracker import backup

    db_path = str(tmp_path / "current.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    conn.close()
    client = create_app(db_path).test_client()

    zip_bytes = backup.export_zip(db_path)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        entries = {n: zf.read(n) for n in zf.namelist()}
    manifest = json.loads(entries["manifest.json"])
    manifest["schema_version"] = dbm.SCHEMA_VERSION + 1
    entries["manifest.json"] = json.dumps(manifest).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)

    resp = client.post("/dane/import/preview", data={
        "backup_file": (BytesIO(buf.getvalue()), "nokia_tracker_future.zip"),
    }, content_type="multipart/form-data")
    follow = client.get(resp.headers["Location"])
    html = follow.get_data(as_text=True)
    assert "nowsz" in html.lower()  # komunikat o niekompatybilnym (nowszym) schemacie
    assert "Potwierdź przywrócenie" not in html
    assert "Dywidendy brutto" not in html


