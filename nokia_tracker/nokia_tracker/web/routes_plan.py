"""Trasy doradcy planu: /plan i jego podglądy JSON /api/preview/espp,
/api/preview/sale-timing, /api/preview/exit-plan, oraz /wyplata (kalkulator
wypłaty, E6 — docs/PLAN_E6_wyplata.md) i /api/preview/wyplata. Silniki
scenariuszy (`espp_scenario`/`timing_scenario`/`exit_scenario`) współdzielone
z `views/plan.py` — patrz jego docstring dla granicy dedupu (E3 §3b,
docs/ROADMAP_V3.md). `/wyplata` żyje w tym samym pliku co `/plan` — rodzina
doradcy trzyma się razem."""
from __future__ import annotations

from datetime import datetime

from flask import Flask, render_template, request

from ._context import AppContext
from .. import __version__
from .. import advisor as advisorm
from .. import settings as settingsm
from ..views.market_context import latest_eurpln_rate, latest_price_and_rate
from ..views.plan import espp_scenario, exit_scenario, timing_scenario
from ..views.withdrawal import withdrawal_view


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
            timing_error = None
            timing_qty_raw = request.args.get("timing_qty")
            timing_price_raw = request.args.get("timing_price")
            if timing_qty_raw and timing_price_raw:
                timing_result, timing_error = timing_scenario(
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
                timing_result=timing_result, timing_error=timing_error,
                timing_qty=timing_qty_raw, timing_price=timing_price_raw,
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

            result, error = timing_scenario(conn, cfg, eurpln_rate, quantity, price_eur)
            if error is not None:
                return {"ok": False, "error": error}

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

    @app.get("/wyplata")
    def wyplata_get():
        """E6 (docs/PLAN_E6_wyplata.md): kalkulator wypłaty, dwukierunkowy.
        Kierunek `target` — 'potrzebuję X zł netto' (bisekcja, `solve_for_net`).
        Kierunek `quantity` — 'mam N akcji' (`annual_net_for_quantity`). Obie
        strony kończą w `views/withdrawal.py::withdrawal_view` — jeden kształt
        wyniku, jeden blok renderujący w szablonie."""
        conn = _conn()
        try:
            cfg = settingsm.get_settings(conn)
            default_price_eur, _default_eurpln_rate = latest_price_and_rate(conn)

            direction = request.args.get("direction") or "target"
            if direction not in ("target", "quantity"):
                direction = "target"

            price_raw = request.args.get("wyplata_price")
            fee_raw = request.args.get("wyplata_fee_pct")
            date_raw = request.args.get("wyplata_date")
            target_raw = request.args.get("wyplata_target")
            qty_raw = request.args.get("wyplata_qty")
            sale_date = date_raw or datetime.now().strftime("%Y-%m-%d")

            result = None
            error = None
            price_eur = default_price_eur
            fee_pct = cfg["broker_fee_pct"]

            has_input = (
                (direction == "target" and target_raw) or (direction == "quantity" and qty_raw))
            if has_input:
                try:
                    if price_raw:
                        price_eur = float(price_raw)
                    if fee_raw:
                        fee_pct = float(fee_raw)
                    if not price_eur or price_eur <= 0:
                        raise ValueError(
                            "Brak aktualnej ceny rynkowej — podaj cenę ręcznie.")
                    if direction == "target":
                        result, error = withdrawal_view(
                            conn, cfg, "target", price_eur, fee_pct, sale_date,
                            target_net_pln=float(target_raw))
                    else:
                        result, error = withdrawal_view(
                            conn, cfg, "quantity", price_eur, fee_pct, sale_date,
                            quantity=float(qty_raw))
                except ValueError as e:
                    error = str(e)

            return render_template(
                "withdrawal.html", active="wyplata", version=__version__,
                direction=direction, result=result, error=error,
                price_eur=price_eur, default_price_eur=default_price_eur, fee_pct=fee_pct,
                sale_date=sale_date, wyplata_target=target_raw, wyplata_qty=qty_raw,
                print_mode=request.args.get("print") == "1")
        finally:
            conn.close()

    @app.get("/api/preview/wyplata")
    def preview_wyplata():
        conn = _conn()
        try:
            direction = request.args.get("direction") or "target"
            if direction not in ("target", "quantity"):
                return {"ok": False, "error": "Nieznany kierunek."}

            try:
                price_eur = float(request.args.get("wyplata_price") or 0)
                fee_pct = float(request.args.get("wyplata_fee_pct") or 0)
            except ValueError:
                return {"ok": False, "error": "Niepoprawna liczba."}
            if price_eur <= 0:
                return {"ok": False, "error": "Cena musi być dodatnia."}

            sale_date = request.args.get("wyplata_date") or datetime.now().strftime("%Y-%m-%d")
            cfg = settingsm.get_settings(conn)

            try:
                if direction == "target":
                    target_net_pln = float(request.args.get("wyplata_target") or 0)
                    if target_net_pln <= 0:
                        return {"ok": False, "error": "Kwota docelowa musi być dodatnia."}
                    result, error = withdrawal_view(
                        conn, cfg, "target", price_eur, fee_pct, sale_date,
                        target_net_pln=target_net_pln)
                else:
                    quantity = float(request.args.get("wyplata_qty") or 0)
                    if quantity <= 0:
                        return {"ok": False, "error": "Ilość musi być dodatnia."}
                    result, error = withdrawal_view(
                        conn, cfg, "quantity", price_eur, fee_pct, sale_date,
                        quantity=quantity)
            except ValueError:
                return {"ok": False, "error": "Niepoprawna liczba."}

            if error is not None:
                return {"ok": False, "error": error}

            lines = [
                {"label": "Ilość akcji", "value": result["quantity"], "unit": "szt."},
                {"label": "Podatek", "value": result["tax_pln"], "unit": "PLN"},
                {"label": "Na rękę", "value": result["net_pln"], "unit": "PLN", "emphasis": True},
            ]
            return {"ok": True, "lines": lines}
        finally:
            conn.close()
