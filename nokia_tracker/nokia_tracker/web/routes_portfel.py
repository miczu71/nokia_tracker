"""Trasy portfela: /portfolio, /lots, /sales, /grants, /wyniki.

Etap 3 (docs/PLAN_0_28_0_ui_porzadki.md): jedynym źródłem prawdy dla loty/
sprzedaże/stan posiadania są wyciągi Computershare (`importers/
computershare_pdf.py`) — trasy ręcznego zapisu (`POST /lots`, `POST
/lots/sell`, `POST /portfolio`, oraz ich podglądy JSON) zostały usunięte.
Sprzedaż wykrytą w PDF księguje się jednym klikiem przez
`POST /imports/conflicts/<id>/confirm-sale` (routes_dane.py). `/sales`
zostaje — usuwanie sprzedaży i „Zgłoszona wartość" to narzędzia korekty,
nie wprowadzania nowych danych."""
from __future__ import annotations

from flask import Flask, redirect, render_template, request, url_for

from ._context import AppContext
from .. import __version__
from .. import db as dbm
from .. import portfolio as portfoliom
from .. import sensors
from .. import settings as settingsm
from ..tax import grants as grantsm
from ..tax import lots as taxlots
from ..tax import policy as taxpolicy
from ..views.market_context import instrument_ids as _ids
from ..views.market_context import latest_price_and_rate
from ..views.results import results_view
from ..views.sales import sales_view


def register_portfel_routes(app: Flask, ctx: AppContext) -> None:
    _conn = ctx.conn

    @app.get("/portfolio")
    def portfolio_get():
        conn = _conn()
        try:
            cfg = settingsm.get_settings(conn)
            lots_position = None
            if taxlots.open_lots(conn):
                price_eur, eurpln_rate = latest_price_and_rate(conn)
                lots_position = portfoliom.lots_based_position_values(
                    conn, cfg, price_eur, eurpln_rate)
            return render_template(
                "portfolio.html", active="portfolio", version=__version__, cfg=cfg,
                lots_position=lots_position)
        finally:
            conn.close()

    @app.get("/lots")
    def lots_get():
        conn = _conn()
        try:
            cfg = settingsm.get_settings(conn)
            taxlots.backfill_missing_rates(conn)
            rows = conn.execute(
                "SELECT * FROM lots ORDER BY acquired_date DESC, id DESC").fetchall()
            year = cfg.get("tax_year") or None
            policies = taxpolicy.compute_all_policies(conn, cfg, year=year)
            override_summary = taxpolicy.reported_override_summary(conn, cfg, year=year)
            return render_template(
                "lots.html", active="lots", version=__version__,
                lots=[dict(r) for r in rows], policies=policies, cfg=cfg,
                override_summary=override_summary)
        finally:
            conn.close()

    @app.get("/sales")
    def sales_get():
        """Zrealizowane sprzedaże — pełne rozbicie do numeru tabeli NBP per
        sprzedaż (krok 16), tym samym `_alloc_detail.html`/`tax/trace.py` co
        karta „co jeśli sprzedam teraz" na `/pit38` — jedno źródło matematyki
        i formatowania dla symulacji i rzeczywistości."""
        conn = _conn()
        try:
            cfg = settingsm.get_settings(conn)
            year = request.args.get("year", type=int)
            view = sales_view(conn, cfg, year)
            return render_template(
                "sales.html", active="sales", version=__version__, cfg=cfg,
                year=year, deleted=request.args.get("deleted") == "1", **view)
        finally:
            conn.close()

    @app.post("/sales/<int:sale_id>/delete")
    def sales_delete(sale_id: int):
        conn = _conn()
        try:
            with dbm.WRITE_LOCK:
                taxlots.reverse_sale(conn, sale_id)
            return redirect(url_for("sales_get", deleted="1"))
        finally:
            conn.close()

    @app.post("/sales/<int:sale_id>/report")
    def sales_report(sale_id: int):
        """Krok 20: zgłoszona wartość sprzedaży (np. zgodnie z ręcznym arkuszem
        użytkownika, gdy deklaracja już złożona i świadomie NIE jest korygowana —
        patrz docs/PLAN_KROK_20_reported_override.md). Nadpisuje TYLKO agregat
        PIT-38 (`tax/policy.py::compute_all_policies`) — `sale_allocations`/`lots`
        (realny ślad FIFO) zostają nietknięte. Puste pole = usuń nadpisanie
        (wróć do wyliczenia silnika)."""
        conn = _conn()
        try:
            exists = conn.execute("SELECT 1 FROM sales WHERE id = ?", (sale_id,)).fetchone()
            if not exists:
                return redirect(url_for("sales_get"))
            revenue_raw = (request.form.get("reported_revenue_pln") or "").strip()
            cost_raw = (request.form.get("reported_cost_pln") or "").strip()
            note_raw = (request.form.get("reported_note") or "").strip() or None
            reported_revenue = float(revenue_raw) if revenue_raw else None
            reported_cost = float(cost_raw) if cost_raw else None
            with dbm.WRITE_LOCK:
                conn.execute(
                    "UPDATE sales SET reported_revenue_pln = ?, reported_cost_pln = ?, "
                    "notes = ? WHERE id = ?",
                    (reported_revenue, reported_cost, note_raw, sale_id))
                conn.commit()
            return redirect(url_for("sales_get", reported="1"))
        finally:
            conn.close()

    @app.get("/grants")
    def grants_get():
        """Krok 16: dociąga bieżącą cenę/kurs dokładnie tak jak `portfolio_get`
        (patrz `lots_based_position_values` wyżej) i dokłada wycenę per transza
        (`tax/grants.py::valuation`) — aktualną dla części otwartej, z dnia
        sprzedaży dla części zrealizowanej."""
        conn = _conn()
        try:
            price_eur, eurpln_rate = latest_price_and_rate(conn)
            valuation = grantsm.valuation(conn, price_eur, eurpln_rate)

            espp = grantsm.list_espp(conn)
            lti = grantsm.list_lti_grouped(conn)
            # Krok 18: `sensors.grants_values` już liczy to dla MQTT — strona *o
            # vestingu* go dotąd nie pokazywała wcale.
            vesting = sensors.grants_values(conn)
            return render_template(
                "grants.html", active="grants", version=__version__,
                espp=espp, lti=lti, valuation=valuation, vesting=vesting)
        finally:
            conn.close()

    @app.get("/wyniki")
    def wyniki_get():
        """Krok 25 (docs/PLAN_KROK_25_wyniki.md): XIRR na wpłatach własnych,
        TWR z materializowanej `portfolio_history` (przeliczanej nocnym jobem
        — `rebuild_portfolio_history_job` w main.py), atrybucja zysku,
        kontrfaktyczny benchmark OMXH25 — jako krzywa (`counterfactual_series`)
        obok krzywej wartości portfela na tym samym wykresie."""
        conn = _conn()
        try:
            ids = _ids(conn)
            cfg = settingsm.get_settings(conn)
            price_eur, eurpln_rate = latest_price_and_rate(conn, ids)
            view = results_view(conn, cfg, ids, price_eur, eurpln_rate)
            return render_template(
                "results.html", active="wyniki", version=__version__,
                print_mode=request.args.get("print") == "1", **view)
        finally:
            conn.close()
