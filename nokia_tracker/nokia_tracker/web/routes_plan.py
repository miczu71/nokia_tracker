"""Trasy doradcy planu: /plan i jego podglądy JSON /api/preview/espp,
/api/preview/sale-timing, /api/preview/exit-plan. Silniki scenariuszy
(`espp_scenario`/`timing_scenario`/`exit_scenario`) współdzielone z
`views/plan.py` — patrz jego docstring dla granicy dedupu (E3 §3b,
docs/ROADMAP_V3.md)."""
from __future__ import annotations

from flask import Flask, render_template, request

from ._context import AppContext
from .. import __version__
from .. import advisor as advisorm
from .. import settings as settingsm
from ..views.market_context import latest_eurpln_rate, latest_price_and_rate
from ..views.plan import espp_scenario, exit_scenario, timing_scenario


def register_plan_routes(app: Flask, ctx: AppContext) -> None:
    _conn = ctx.conn

    @app.get("/plan")
    def plan_get():
        """Krok 26 (docs/PLAN_KROK_26_doradca.md): cztery pytania, na które żadne
        narzędzie premium nie odpowiada — ile tracę sprzedając dziś, kiedy co wpada,
        ile da mi wpłacanie X EUR/mc, czy nie mam za dużo w jednym koszyku, który jest
        jednocześnie moim pracodawcą. Strona i sensor MQTT (`sensors.advisor_values`)
        liczą przez tę samą `advisor.overview()`, żeby nigdy nie pokazały dwóch różnych
        liczb dla tego samego faktu."""
        conn = _conn()
        try:
            price_eur, eurpln_rate = latest_price_and_rate(conn)

            cfg = settingsm.get_settings(conn)
            plan_overview = advisorm.overview(conn, cfg, price_eur, eurpln_rate)

            espp_result = None
            espp_error = None
            monthly_raw = request.args.get("espp_monthly")
            months_raw = request.args.get("espp_months")
            price_raw = request.args.get("espp_price")
            if monthly_raw and months_raw and price_raw:
                try:
                    monthly = float(monthly_raw)
                    months = int(float(months_raw))
                    price = float(price_raw)
                except ValueError as e:
                    espp_error = str(e)
                else:
                    espp_result, espp_error = espp_scenario(
                        cfg, eurpln_rate, monthly, months, price)

            espp_scenarios = None
            if price_eur:
                espp_scenarios = [
                    ("bieżąca", price_eur), ("−20%", price_eur * 0.8),
                    ("+20%", price_eur * 1.2)]

            timing_result = None
            timing_qty_raw = request.args.get("timing_qty")
            timing_price_raw = request.args.get("timing_price")
            if timing_qty_raw and timing_price_raw:
                timing_result = timing_scenario(
                    conn, cfg, eurpln_rate, float(timing_qty_raw), float(timing_price_raw))

            exit_result = None
            exit_error = None
            exit_qty_raw = request.args.get("exit_qty")
            exit_freq_raw = request.args.get("exit_freq")
            exit_periods_raw = request.args.get("exit_periods")
            if exit_qty_raw and exit_freq_raw and exit_periods_raw:
                try:
                    qty = float(exit_qty_raw)
                    periods = int(float(exit_periods_raw))
                except ValueError as e:
                    exit_error = str(e)
                else:
                    exit_result, exit_error = exit_scenario(
                        conn, cfg, eurpln_rate, price_eur, qty, exit_freq_raw, periods)

            return render_template(
                "plan.html", active="plan", version=__version__,
                overview=plan_overview, cfg=cfg, price_eur=price_eur,
                espp_result=espp_result, espp_error=espp_error,
                espp_monthly=monthly_raw, espp_months=months_raw, espp_price=price_raw,
                espp_scenarios=espp_scenarios,
                timing_result=timing_result, timing_qty=timing_qty_raw, timing_price=timing_price_raw,
                exit_result=exit_result, exit_error=exit_error,
                exit_qty=exit_qty_raw, exit_freq=exit_freq_raw, exit_periods=exit_periods_raw,
                has_restricted=bool(plan_overview["forfeit"]["items"]),
                has_timeline=bool(plan_overview["timeline"]["tranches"]),
                print_mode=request.args.get("print") == "1")
        finally:
            conn.close()

    @app.get("/api/preview/espp")
    def preview_espp():
        conn = _conn()
        try:
            try:
                monthly_eur = float(request.args.get("espp_monthly") or 0)
                months = int(float(request.args.get("espp_months") or 0))
                price_eur = float(request.args.get("espp_price") or 0)
            except ValueError:
                return {"ok": False, "error": "Niepoprawna liczba."}

            cfg = settingsm.get_settings(conn)
            eurpln_rate = latest_eurpln_rate(conn)

            result, error = espp_scenario(cfg, eurpln_rate, monthly_eur, months, price_eur)
            if error is not None:
                return {"ok": False, "error": error}

            lines = [
                {"label": "Akcje własne", "value": result["own_shares"], "unit": "szt."},
                {"label": "Akcje dopasowania", "value": result["matched_shares"], "unit": "szt."},
                {"label": "Razem", "value": result["total_shares"], "unit": "szt."},
            ]
            if result["tax_pln"] is not None:
                lines.append({"label": "Podatek", "value": result["tax_pln"], "unit": "PLN"})
                lines.append({"label": "Na rękę", "value": result["net_proceeds_pln"],
                              "unit": "PLN", "emphasis": True})
            return {"ok": True, "lines": lines}
        finally:
            conn.close()

    @app.get("/api/preview/sale-timing")
    def preview_sale_timing():
        conn = _conn()
        try:
            try:
                quantity = float(request.args.get("timing_qty") or 0)
                price_eur = float(request.args.get("timing_price") or 0)
            except ValueError:
                return {"ok": False, "error": "Niepoprawna liczba."}
            if quantity <= 0 or price_eur <= 0:
                return {"ok": False, "error": "Ilość i cena muszą być dodatnie."}

            cfg = settingsm.get_settings(conn)
            eurpln_rate = latest_eurpln_rate(conn)

            result = timing_scenario(conn, cfg, eurpln_rate, quantity, price_eur)

            if result["today"] is None or result["jan2_next_year"] is None:
                return {"ok": False, "error": "Brak pokrycia lotami dla jednego ze scenariuszy."}

            lines = [
                {"label": "Podatek dziś (po stracie)",
                 "value": result["today"]["tax_with_max_loss_pln"], "unit": "PLN"},
                {"label": "Podatek 2 stycznia (po stracie)",
                 "value": result["jan2_next_year"]["tax_with_max_loss_pln"], "unit": "PLN"},
                {"label": "Różnica netto (podatek + przepadek)",
                 "value": result["delta_total_pln"], "unit": "PLN", "emphasis": True},
            ]
            return {"ok": True, "lines": lines}
        finally:
            conn.close()

    @app.get("/api/preview/exit-plan")
    def preview_exit_plan():
        conn = _conn()
        try:
            try:
                shares_per_period = float(request.args.get("exit_qty") or 0)
                frequency = request.args.get("exit_freq") or ""
                num_periods = int(float(request.args.get("exit_periods") or 0))
            except ValueError:
                return {"ok": False, "error": "Niepoprawna liczba."}

            cfg = settingsm.get_settings(conn)
            price_eur, eurpln_rate = latest_price_and_rate(conn)

            result, error = exit_scenario(
                conn, cfg, eurpln_rate, price_eur, shares_per_period, frequency, num_periods)
            if error is not None:
                return {"ok": False, "error": error}

            lines = [
                {"label": "Łącznie sprzedanych akcji",
                 "value": result["totals"]["shares_sold"], "unit": "szt."},
            ]
            if result["totals"]["tax_pln"] is not None:
                lines.append({"label": "Podatek łącznie",
                              "value": result["totals"]["tax_pln"], "unit": "PLN"})
                lines.append({"label": "Na rękę", "value": result["totals"]["net_proceeds_pln"],
                              "unit": "PLN", "emphasis": True})
            return {"ok": True, "lines": lines}
        finally:
            conn.close()
