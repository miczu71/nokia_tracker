"""Trasy PIT-38: /pit38, eksporty CSV/XLSX, kreator /pit38/kreator/*."""
from __future__ import annotations

from datetime import datetime

from flask import Flask, Response, redirect, render_template, request, url_for

from ._context import AppContext
from .. import __version__
from .. import db as dbm
from .. import sensors
from .. import settings as settingsm
from ..exports import pit38 as exports_pit38
from ..providers import fx_nbp
from ..tax import dividends as taxdiv
from ..tax import losses as taxlosses
from ..tax import lots as taxlots
from ..tax import pit38 as taxpit38
from ..tax import whatif as taxwhatif
from ..views.market_context import instrument_ids as _ids
from ..views.pit38 import waterfall


def register_podatki_routes(app: Flask, ctx: AppContext) -> None:
    _conn = ctx.conn

    def _pit38_report_for_request(conn):
        """Wspólne dla /pit38 i obu eksportów: rok z ?year= (domyślnie
        cfg['tax_year'] lub bieżący rok) + roczny raport."""
        cfg = settingsm.get_settings(conn)
        taxdiv.backfill_pl_tax_due(conn, cfg)
        year = request.args.get("year", type=int) or cfg.get("tax_year") or datetime.now().year
        report = taxpit38.annual_report(conn, cfg, year)
        return cfg, year, report

    def _enrich_trace_row_for_export(conn, row: dict) -> dict:
        """Krok 16 (§8.4): dokłada do wiersza `report['sale_trace']` kwoty EUR
        (pochodne zamrożonego PLN — `pln / kurs`, więc zawsze spójne z tym,
        co widać na ekranie) i numery tabel NBP — eksport ma być tym samym
        dowodem co `/pit38`, nie jego uboższą wersją. Zero nowych zapytań do
        NBP: `table_no_for_effective_date` czyta tylko lokalną `nbp_rates`."""
        lot_rate = row["lot_nbp_rate"]
        sale_rate = row["sale_nbp_rate"]
        return {
            **row,
            "cost_eur": row["cost_pln"] / lot_rate if lot_rate else None,
            "revenue_eur": row["revenue_pln"] / sale_rate if sale_rate else None,
            "lot_table_no": (fx_nbp.table_no_for_effective_date(conn, row["lot_nbp_rate_date"])
                             if row["lot_nbp_rate_date"] else None),
            "sale_table_no": (fx_nbp.table_no_for_effective_date(conn, row["sale_nbp_rate_date"])
                              if row["sale_nbp_rate_date"] else None),
        }

    @app.get("/pit38")
    def pit38_get():
        conn = _conn()
        try:
            cfg, year, report = _pit38_report_for_request(conn)

            whatif_result = None
            whatif_error = None
            qty_raw = request.args.get("whatif_qty")
            price_raw = request.args.get("whatif_price")
            if qty_raw and price_raw:
                try:
                    whatif_result = taxwhatif.simulate_sale(
                        conn, cfg, float(qty_raw), float(price_raw))
                except (taxlots.InsufficientLotsError, taxlots.CostBasisMissingError) as e:
                    whatif_error = str(e)

            current_price = sensors.market_values(conn, _ids(conn)["primary"]).get("price_eur")
            waterfall_pit38 = waterfall(report, cfg)

            return render_template(
                "pit38.html", active="pit38", version=__version__,
                year=year, report=report, cfg=cfg,
                whatif_result=whatif_result, whatif_error=whatif_error,
                whatif_qty=qty_raw, whatif_price=price_raw,
                current_price=current_price, waterfall_pit38=waterfall_pit38,
                print_mode=request.args.get("print") == "1")
        finally:
            conn.close()

    @app.get("/pit38/export.csv")
    def pit38_export_csv():
        conn = _conn()
        try:
            _cfg, year, report = _pit38_report_for_request(conn)
            trace_rows = [_enrich_trace_row_for_export(conn, r) for r in report["sale_trace"]]
            csv_text = exports_pit38.to_csv(year, report, trace_rows)

            filename = f"pit38_{year}.csv"
            return Response(
                "﻿" + csv_text,
                mimetype="text/csv; charset=utf-8",
                headers={"Content-Disposition": f"attachment; filename={filename}"})
        finally:
            conn.close()

    @app.get("/pit38/export.xlsx")
    def pit38_export_xlsx():
        conn = _conn()
        try:
            _cfg, year, report = _pit38_report_for_request(conn)
            trace_rows = [_enrich_trace_row_for_export(conn, r) for r in report["sale_trace"]]
            xlsx_bytes = exports_pit38.to_xlsx(year, report, trace_rows)

            filename = f"pit38_{year}.xlsx"
            return Response(
                xlsx_bytes,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": f"attachment; filename={filename}"})
        finally:
            conn.close()

    @app.get("/pit38/kreator")
    def pit38_wizard_get():
        conn = _conn()
        try:
            cfg = settingsm.get_settings(conn)
            year = request.args.get("year", type=int) or cfg.get("tax_year") or datetime.now().year
            taxlosses.rebuild(conn, cfg)
            report = taxpit38.annual_report(conn, cfg, year)
            steps = taxlosses.wizard_steps(conn, cfg, year, report)
            closed = taxlosses.is_year_closed(conn, year)
            can_close = all(s["done"] for s in steps if s["key"] in ("import", "conflicts", "balance"))
            return render_template(
                "wizard.html", active="pit38", version=__version__,
                year=year, steps=steps, report=report,
                closed=closed, can_close=can_close, cfg=cfg,
                deduct_error=request.args.get("deduct_error"),
                close_error=request.args.get("close_error") == "1")
        finally:
            conn.close()

    @app.post("/pit38/kreator/odlicz")
    def pit38_wizard_deduct():
        conn = _conn()
        try:
            cfg = settingsm.get_settings(conn)
            year = int(request.form["year"])
            try:
                with dbm.WRITE_LOCK:
                    taxlosses.record_deduction(
                        conn, cfg, int(request.form["loss_id"]), year,
                        float(request.form["amount_pln"]))
            except ValueError as e:
                return redirect(url_for("pit38_wizard_get", year=year, deduct_error=str(e)))
            return redirect(url_for("pit38_wizard_get", year=year))
        finally:
            conn.close()

    @app.post("/pit38/kreator/zamknij")
    def pit38_wizard_close():
        conn = _conn()
        try:
            year = int(request.form["year"])
            cfg = settingsm.get_settings(conn)
            report = taxpit38.annual_report(conn, cfg, year)
            steps = taxlosses.wizard_steps(conn, cfg, year, report)
            blocking = [s for s in steps if s["key"] in ("import", "conflicts", "balance")
                        and not s["done"]]
            if blocking:
                return redirect(url_for("pit38_wizard_get", year=year, close_error="1"))
            with dbm.WRITE_LOCK:
                taxlosses.close_year(conn, cfg, year, report["total_due_pln"])
            return redirect(url_for("pit38_wizard_get", year=year, closed="1"))
        finally:
            conn.close()

    @app.post("/pit38/kreator/odblokuj")
    def pit38_wizard_reopen():
        conn = _conn()
        try:
            year = int(request.form["year"])
            with dbm.WRITE_LOCK:
                taxlosses.reopen_year(conn, year)
            return redirect(url_for("pit38_wizard_get", year=year))
        finally:
            conn.close()
