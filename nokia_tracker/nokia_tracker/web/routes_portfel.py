"""Trasy portfela: /portfolio, /lots (+sprzedaż), /sales, /grants, /wyniki,
oraz ich podglądy JSON /api/preview/lot i /api/preview/sale (podgląd loty/
sprzedaż żyje tu, nie w routes_dywidendy.py, żeby zostać przy `/lots`/`/sales`,
z którymi dzielą `_LOT_TYPE_LABELS_PY` i `taxlots`)."""
from __future__ import annotations

from flask import Flask, redirect, render_template, request, url_for

from ._context import AppContext
from ._helpers import _is_future_date
from .. import __version__
from .. import db as dbm
from .. import portfolio as portfoliom
from .. import sensors
from .. import settings as settingsm
from ..providers import fx_nbp
from ..tax import grants as grantsm
from ..tax import lots as taxlots
from ..tax import policy as taxpolicy
from ..tax import trace as taxtrace
from ..tax import whatif as taxwhatif
from ..views.market_context import instrument_ids as _ids
from ..views.market_context import latest_price_and_rate
from ..views.results import results_view
from ..views.sales import sales_view

# Krok 18: kopia LOT_TYPE_LABELS z templates/_macros.html — ta strona żyje w
# Pythonie (JSON /api/preview/sale), tamta w Jinja (tabele HTML); nie da się
# ich zeszyć w jedno miejsce bez importowania Jinja env do zwykłej funkcji.
_LOT_TYPE_LABELS_PY = {"own": "własne", "matched": "podarowane", "lti": "LTI",
                       "dividend_drip": "dywidenda"}


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
                lots_position=lots_position,
                saved=request.args.get("saved") == "1")
        finally:
            conn.close()

    @app.post("/portfolio")
    def portfolio_post():
        conn = _conn()
        try:
            qty = float(request.form.get("position_qty") or 0)
            avg_cost = float(request.form.get("avg_cost_eur") or 0)
            with dbm.WRITE_LOCK:
                settingsm.set_settings(conn, {"position_qty": qty, "avg_cost_eur": avg_cost})
            return redirect(url_for("portfolio_get", saved="1"))
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
            return render_template(
                "lots.html", active="lots", version=__version__,
                lots=[dict(r) for r in rows], policies=policies, cfg=cfg,
                saved=request.args.get("saved") == "1",
                sold=request.args.get("sold") == "1",
                error=request.args.get("error"))
        finally:
            conn.close()

    @app.post("/lots")
    def lots_post():
        conn = _conn()
        try:
            acquired_date = request.form.get("acquired_date") or ""
            if _is_future_date(acquired_date):
                return redirect(url_for(
                    "lots_get", error="Data nabycia nie może być w przyszłości "
                                      "(NBP nie publikuje kursów na przyszłe daty)"))
            lot_type = request.form.get("lot_type") or "own"
            quantity = float(request.form.get("quantity") or 0)
            price_eur = float(request.form.get("price_eur") or 0)
            fee_eur = float(request.form.get("fee_eur") or 0)
            with dbm.WRITE_LOCK:
                taxlots.add_lot(conn, acquired_date, lot_type, quantity, price_eur,
                                fee_eur=fee_eur)
            return redirect(url_for("lots_get", saved="1"))
        finally:
            conn.close()

    @app.post("/lots/sell")
    def lots_sell_post():
        conn = _conn()
        try:
            sale_date = request.form.get("sale_date") or ""
            if _is_future_date(sale_date):
                return redirect(url_for(
                    "lots_get", error="Data sprzedaży nie może być w przyszłości "
                                      "(NBP nie publikuje kursów na przyszłe daty)"))
            quantity = float(request.form.get("sale_quantity") or 0)
            price_eur = float(request.form.get("sale_price_eur") or 0)
            fee_eur = float(request.form.get("sale_fee_eur") or 0)
            proceeds_raw = request.form.get("sale_proceeds_eur") or ""
            proceeds_eur = float(proceeds_raw) if proceeds_raw.strip() else None
            try:
                with dbm.WRITE_LOCK:
                    taxlots.record_sale(
                        conn, sale_date, quantity, price_eur, fee_eur=fee_eur,
                        proceeds_eur=proceeds_eur)
                return redirect(url_for("lots_get", sold="1"))
            except (taxlots.InsufficientLotsError, taxlots.CostBasisMissingError) as e:
                return redirect(url_for("lots_get", error=str(e)))
        finally:
            conn.close()

    # Krok 18: podgląd na żywo przed zapisem — dywidenda/lot/sprzedaż. Trzy
    # zasady dla wszystkich trzech endpointów: (1) tylko odczyt, zero zapisu do
    # `lots`/`sales`/`dividends`/`sale_allocations` (2) ten sam silnik co POST,
    # żeby podgląd nigdy nie rozjechał się z tym, co realnie zostanie zapisane
    # (3) błędy (brak pokrycia, brak kursu NBP, data w przyszłości) wracają jako
    # `{ok: false, error: ...}` z HTTP 200, nigdy 500 — formularz ma ostrzegać,
    # nie się wywalać. `rate_for_event`/`simulate_sale` mogą zrobić `INSERT OR
    # IGNORE` do `nbp_rates` (cache kursów publicznych) — nie do encji domenowych.

    @app.get("/api/preview/lot")
    def preview_lot():
        conn = _conn()
        try:
            acquired_date = request.args.get("acquired_date") or ""
            if not acquired_date:
                return {"ok": False, "error": "Podaj datę nabycia."}
            if _is_future_date(acquired_date):
                return {"ok": False, "error": "Data nabycia nie może być w przyszłości "
                                              "(NBP nie publikuje kursów na przyszłe daty)."}
            try:
                quantity = float(request.args.get("quantity") or 0)
                price_eur = float(request.args.get("price_eur") or 0)
                fee_eur = float(request.args.get("fee_eur") or 0)
            except ValueError:
                return {"ok": False, "error": "Niepoprawna liczba."}
            if quantity <= 0 or price_eur <= 0:
                return {"ok": False, "error": "Podaj ilość i cenę większe od zera."}

            rate = fx_nbp.rate_for_event(conn, acquired_date)
            if rate is None:
                return {"ok": False,
                        "error": f"Brak kursu NBP dla dnia {acquired_date} "
                                 "(spróbuj ponownie później)."}
            nbp_rate, nbp_rate_date = rate
            cost_eur = quantity * price_eur + fee_eur
            cost_pln = cost_eur * nbp_rate
            deriv = taxtrace.fx_derivation(conn, acquired_date, nbp_rate, nbp_rate_date, "nabycie")
            return {
                "ok": True,
                "nbp_rate": nbp_rate,
                "nbp_rate_date": nbp_rate_date,
                "explanation_pl": deriv["explanation_pl"],
                "table_urls": deriv.get("urls"),
                "lines": [
                    {"label": "Koszt", "value": round(cost_eur, 2), "unit": "EUR"},
                    {"label": "Koszt", "value": round(cost_pln, 2), "unit": "PLN",
                     "emphasis": True},
                ],
            }
        finally:
            conn.close()

    @app.get("/api/preview/sale")
    def preview_sale():
        conn = _conn()
        try:
            # sale_date opcjonalna — /pit38 „co jeśli sprzedam teraz" nie zbiera
            # daty (zawsze dziś, tak samo jak `simulate_sale(sale_date=None)`);
            # /lots „Zarejestruj sprzedaż" ją wysyła i wtedy jest walidowana.
            sale_date = request.args.get("sale_date") or None
            if sale_date and _is_future_date(sale_date):
                return {"ok": False, "error": "Data sprzedaży nie może być w przyszłości "
                                              "(NBP nie publikuje kursów na przyszłe daty)."}
            try:
                quantity = float(request.args.get("quantity") or 0)
                price_eur = float(request.args.get("price_eur") or 0)
                fee_eur = float(request.args.get("fee_eur") or 0)
            except ValueError:
                return {"ok": False, "error": "Niepoprawna liczba."}
            if quantity <= 0 or price_eur <= 0:
                return {"ok": False, "error": "Podaj ilość i cenę większe od zera."}

            cfg = settingsm.get_settings(conn)
            try:
                result = taxwhatif.simulate_sale(conn, cfg, quantity, price_eur, fee_eur, sale_date)
            except (taxlots.InsufficientLotsError, taxlots.CostBasisMissingError) as e:
                return {"ok": False, "error": str(e)}

            active = result["policies"][result["active_policy"]]
            sale_fx = (result["lots_consumed_detailed"] or {}).get("sale_fx") or {}
            lots_line = " · ".join(
                f"{_LOT_TYPE_LABELS_PY.get(a['lot_type'], a['lot_type'])} "
                f"{a['quantity']:.4f} @ {a['acquired_date']}"
                for a in result["lots_consumed"])
            return {
                "ok": True,
                "nbp_rate": result["nbp_rate"],
                "nbp_rate_date": result["nbp_rate_date"],
                "explanation_pl": sale_fx.get("explanation_pl"),
                "table_urls": sale_fx.get("urls"),
                "lines": [
                    {"label": "Loty (FIFO)", "value": lots_line or "—", "unit": None},
                    {"label": "Przychód", "value": result["revenue_pln"], "unit": "PLN"},
                    {"label": "Koszt", "value": active["cost_pln"], "unit": "PLN"},
                    {"label": "Podatek", "value": active["tax_pln"], "unit": "PLN"},
                    {"label": "Na rękę", "value": result["net_proceeds_pln"], "unit": "PLN",
                     "emphasis": True},
                ],
            }
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
