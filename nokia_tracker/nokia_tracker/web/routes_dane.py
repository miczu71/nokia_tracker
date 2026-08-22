"""Trasy importu/eksportu: /imports (+konflikty), /dane (kopia zapasowa)."""
from __future__ import annotations

import json
import os
import secrets
from datetime import date, datetime
from pathlib import Path

from flask import Flask, Response, redirect, render_template, request, url_for

from ._context import AppContext
from .. import __version__
from .. import backup as backupm
from .. import db as dbm
from .. import integrity as integritym
from ..importers import computershare_pdf
from .. import settings as settingsm
from ..tax import grants as grantsm
from ..tax import lots as taxlots


def _restore_dir(db_path: str) -> Path:
    d = Path(db_path).parent / "tmp_restore"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cleanup_stale_restore_files(restore_dir: Path, keep_token: str | None) -> None:
    """Pliki podglądu przywracania zawierają pełną kopię bazy (dane
    podatkowe) — nie zostają na dysku bezterminowo, jeśli użytkownik
    wgra plik i nigdy nie potwierdzi ani nie odrzuci podglądu."""
    cutoff = datetime.now().timestamp() - 3600
    for f in restore_dir.glob("*.zip"):
        if keep_token and f.stem == keep_token:
            continue
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)


def _backup_dir() -> Path:
    return Path(os.environ.get("BACKUP_SHARE", "/share/nokia_tracker")) / "backup"


def register_dane_routes(app: Flask, ctx: AppContext) -> None:
    _conn = ctx.conn
    db_path = ctx.db_path

    @app.get("/imports")
    def imports_get():
        conn = _conn()
        try:
            history = conn.execute(
                "SELECT * FROM imports ORDER BY imported_at DESC").fetchall()
            conflict_rows = conn.execute(
                "SELECT * FROM import_conflicts WHERE resolved = 0 ORDER BY id DESC"
            ).fetchall()
            conflicts = []
            for r in conflict_rows:
                d = dict(r)
                d["existing"] = json.loads(d["existing_json"]) if d["existing_json"] else {}
                d["incoming"] = json.loads(d["incoming_json"]) if d["incoming_json"] else {}
                conflicts.append(d)
            return render_template(
                "imports.html", active="imports", version=__version__,
                history=[dict(r) for r in history], conflicts=conflicts,
                report=request.args.get("report"), sold=request.args.get("sold") == "1",
                error=request.args.get("error"))
        finally:
            conn.close()

    @app.post("/imports/upload")
    def imports_upload():
        conn = _conn()
        try:
            uploaded = request.files.get("pdf_file")
            if not uploaded or not uploaded.filename:
                return redirect(url_for("imports_get"))
            data = uploaded.read()
            cfg = settingsm.get_settings(conn)
            with dbm.WRITE_LOCK:
                report = computershare_pdf.import_statement(
                    conn, data, uploaded.filename, cfg)
                grantsm.reconcile_vesting(conn)
            return redirect(url_for(
                "imports_get",
                report=f"{report['rows_inserted']}/{report['rows_unchanged']}/"
                       f"{report['rows_conflict']}"))
        finally:
            conn.close()

    @app.post("/imports/conflicts/<int:conflict_id>/resolve")
    def imports_resolve_conflict(conflict_id: int):
        conn = _conn()
        try:
            resolution = request.form.get("resolution", "")
            with dbm.WRITE_LOCK:
                conn.execute(
                    "UPDATE import_conflicts SET resolved = 1, resolution = ? WHERE id = ?",
                    (resolution, conflict_id))
                conn.commit()
            return redirect(url_for("imports_get"))
        finally:
            conn.close()

    @app.post("/imports/conflicts/<int:conflict_id>/confirm-sale")
    def imports_confirm_sale(conflict_id: int):
        """Zatwierdza jednym kliknięciem sprzedaż wykrytą w PDF (Withhold-to-Cover Typ B /
        „Sell (Shares)"), bez ręcznego przepisywania liczb do /lots/sell — czyta dane wprost
        z `incoming_json` zapisanego przy imporcie (krok 13)."""
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT * FROM import_conflicts WHERE id = ?", (conflict_id,)).fetchone()
            if row is None:
                return redirect(url_for("imports_get"))
            incoming = json.loads(row["incoming_json"])
            try:
                with dbm.WRITE_LOCK:
                    sale_id = taxlots.record_sale(
                        conn, incoming["execution_date"], incoming["quantity"],
                        incoming["sale_price_eur"], fee_eur=incoming.get("fees_eur", 0.0),
                        proceeds_eur=incoming.get("sale_proceeds_eur"))
                    conn.execute(
                        "UPDATE import_conflicts SET resolved = 1, resolution = ? WHERE id = ?",
                        (f"zaksięgowano automatycznie jako sprzedaż (sale_id={sale_id})",
                         conflict_id))
                    conn.commit()
                return redirect(url_for("imports_get", sold="1"))
            except (taxlots.InsufficientLotsError, taxlots.CostBasisMissingError) as e:
                return redirect(url_for("imports_get", error=str(e)))
        finally:
            conn.close()

    @app.get("/dane")
    def data_get():
        conn = _conn()
        try:
            token = request.args.get("token")
            restore_dir = _restore_dir(db_path)
            _cleanup_stale_restore_files(restore_dir, token)

            preview = None
            preview_error = None
            if token:
                zip_path = restore_dir / f"{token}.zip"
                if zip_path.exists():
                    try:
                        preview = backupm.restore_preview(db_path, zip_path.read_bytes())
                    except backupm.IncompatibleBackupError as e:
                        preview_error = str(e)
                else:
                    preview_error = "Podgląd wygasł — wgraj plik ponownie."

            conflicts_count = conn.execute(
                "SELECT COUNT(*) FROM import_conflicts WHERE resolved = 0").fetchone()[0]

            integrity_findings = integritym.check_all(conn)

            backup_dir = _backup_dir()
            last_snapshot = None
            if backup_dir.is_dir():
                snapshots = sorted(backup_dir.glob("nokia_*.zip"))
                if snapshots:
                    stat = snapshots[-1].stat()
                    last_snapshot = {
                        "name": snapshots[-1].name,
                        "size_kb": round(stat.st_size / 1024, 1),
                        "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    }

            return render_template(
                "data.html", active="data", version=__version__,
                token=token, preview=preview, preview_error=preview_error,
                conflicts_count=conflicts_count, last_snapshot=last_snapshot,
                integrity_findings=integrity_findings,
                restored=request.args.get("restored") == "1",
                error=request.args.get("error"))
        finally:
            conn.close()

    @app.get("/dane/eksport.zip")
    def data_export_zip():
        data = backupm.export_zip(db_path)
        filename = f"nokia_tracker_{date.today().isoformat()}.zip"
        return Response(
            data, mimetype="application/zip",
            headers={"Content-Disposition": f"attachment; filename={filename}"})

    @app.post("/dane/import/preview")
    def data_import_preview():
        uploaded = request.files.get("backup_file")
        if not uploaded or not uploaded.filename:
            return redirect(url_for("data_get"))
        token = secrets.token_urlsafe(16)
        (_restore_dir(db_path) / f"{token}.zip").write_bytes(uploaded.read())
        return redirect(url_for("data_get", token=token))

    @app.post("/dane/import/confirm")
    def data_import_confirm():
        token = request.form.get("token", "")
        zip_path = _restore_dir(db_path) / f"{token}.zip"
        if not token or not zip_path.exists():
            return redirect(url_for("data_get", error="Podgląd wygasł — wgraj plik ponownie."))
        try:
            with dbm.WRITE_LOCK:
                backupm.restore_apply(db_path, zip_path.read_bytes())
        except backupm.IncompatibleBackupError as e:
            return redirect(url_for("data_get", error=str(e)))
        finally:
            zip_path.unlink(missing_ok=True)
        return redirect(url_for("data_get", restored="1"))
