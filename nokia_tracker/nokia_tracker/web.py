"""Flask web UI — dashboard, portfel, dywidendy, newsy, prognozy, ustawienia
(krok 9, BLUEPRINT §3/§9). Cache-busting: no-store na HTML/API, statyki
?v=<wersja>, badge wersji w nav (CLAUDE.md — WebView Companion cache'uje
HTML agresywnie i nie rewaliduje).
"""
from __future__ import annotations

import json
import logging
import os

from flask import Flask, Response, redirect, render_template, request, url_for

from . import __version__, analysis, db as dbm, fx
from . import portfolio as portfoliom
from . import quotes, sensors
from . import settings as settingsm
from . import tax as taxm
from .tax import lots as taxlots
from .tax import policy as taxpolicy
from .ai import openai_compat

logger = logging.getLogger(__name__)

_PRIMARY_SYMBOL = "NOKIA.HE"
_ERICSSON_SYMBOL = "ERIC-B.ST"
_OMXH25_SYMBOL = "^OMXH25"
_EURUSD_SYMBOL = "EURUSD=X"
_ADR_SYMBOL = "NOK"


def _ids(conn) -> dict:
    """Get-or-create instrumentów — niezależne od main.py (web.py może
    obsłużyć request zanim scheduler w main.py przejdzie backfill)."""
    return {
        "primary": quotes.ensure_instrument(conn, _PRIMARY_SYMBOL, "Nokia Oyj", "EUR", "primary"),
        "ericsson": quotes.ensure_instrument(conn, _ERICSSON_SYMBOL, "Ericsson", "SEK", "benchmark"),
        "omxh25": quotes.ensure_instrument(
            conn, _OMXH25_SYMBOL, "OMX Helsinki 25", "EUR", "benchmark"),
        "eurpln": quotes.ensure_instrument(conn, fx.EURPLN_SYMBOL, "EUR/PLN", "PLN", "fx"),
        "eurusd": quotes.ensure_instrument(conn, _EURUSD_SYMBOL, "EUR/USD", "USD", "fx"),
        "adr": quotes.ensure_instrument(conn, _ADR_SYMBOL, "Nokia ADR (NYSE)", "USD", "adr"),
    }


def _ai_keys() -> dict:
    """Klucze API z ENV — NIE z tabeli settings (patrz settings.py)."""
    return {
        "local_llm_api_key": os.environ.get("LOCAL_LLM_API_KEY", ""),
        "gemini_api_key": os.environ.get("GEMINI_API_KEY", ""),
        "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
    }


class _IngressPrefixMiddleware:
    """HA ingress zdejmuje prefiks ścieżki przed przekazaniem requestu do
    kontenera (Flask widzi czyste '/', '/portfolio' itd.), ale oryginalny
    prefiks leci w nagłówku X-Ingress-Path. Bez ustawienia SCRIPT_NAME
    url_for()/redirect() generują URL-e bez prefiksu, które w przeglądarce
    rozwiązują się pod domeną główną HA zamiast pod ingressem (złapane na
    żywo Playwrightem: /static/app.css -> 404, bo poszło do
    homeassistant.local:8123/static/... zamiast pod ingress prefix)."""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        prefix = environ.get("HTTP_X_INGRESS_PATH", "")
        if prefix:
            environ["SCRIPT_NAME"] = prefix
        return self.wsgi_app(environ, start_response)


def create_app(db_path: str) -> Flask:
    app = Flask(__name__)
    app.wsgi_app = _IngressPrefixMiddleware(app.wsgi_app)

    @app.after_request
    def _no_cache(resp: Response) -> Response:
        if resp.mimetype in ("text/html", "application/json"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def _conn():
        return dbm.get_conn(db_path)

    @app.get("/")
    def dashboard():
        conn = _conn()
        try:
            ids = _ids(conn)
            values = sensors.market_values(conn, ids["primary"])
            values.update(sensors.benchmark_values(
                conn, ids["primary"], ids["ericsson"], ids["omxh25"], ids["eurpln"],
                ids["adr"], ids["eurusd"]))
            values.update(sensors.ai_values(conn))
            values.update(sensors.forecast_values(conn))

            cfg = settingsm.get_settings(conn)
            cost_basis_eur = cfg["position_qty"] * cfg["avg_cost_eur"]
            dividends = sensors.dividends_values(conn, cfg, cost_basis_eur)
            position = portfoliom.position_values(
                cfg["position_qty"], cfg["avg_cost_eur"], values.get("price_eur"),
                values.get("eurpln_rate"), dividends_net_total_eur=dividends["dividends_net_eur"])

            closes = quotes.daily_closes(conn, ids["primary"])[-90:]
            recent_alerts = conn.execute(
                "SELECT * FROM alerts_log ORDER BY fired_at DESC LIMIT 5").fetchall()

            return render_template(
                "dashboard.html", active="dashboard", version=__version__,
                values=values, position=position, dividends=dividends,
                chart_closes_json=json.dumps(closes),
                alerts=[dict(r) for r in recent_alerts],
            )
        finally:
            conn.close()

    @app.post("/analyze-now")
    def analyze_now():
        conn = _conn()
        try:
            with dbm.WRITE_LOCK:
                ids = _ids(conn)
                cfg = dict(settingsm.get_settings(conn), **_ai_keys())
                ok = analysis.run_daily_analysis(
                    conn, cfg, ids["primary"], ids["ericsson"], ids["omxh25"], ids["eurpln"])
            return redirect(url_for("dashboard", analyzed="1" if ok else "0"))
        finally:
            conn.close()

    @app.get("/portfolio")
    def portfolio_get():
        conn = _conn()
        try:
            cfg = settingsm.get_settings(conn)
            return render_template(
                "portfolio.html", active="portfolio", version=__version__, cfg=cfg,
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

    @app.get("/dividends")
    def dividends_get():
        conn = _conn()
        try:
            cfg = settingsm.get_settings(conn)
            rows = conn.execute("SELECT * FROM dividends ORDER BY pay_date DESC").fetchall()
            items = []
            for r in rows:
                withholding_pct = (r["withholding_pct"] if r["withholding_pct"] is not None
                                   else cfg["finnish_withholding_pct"])
                t = taxm.compute_dividend_tax(
                    r["gross_eur"], withholding_pct,
                    cfg["treaty_withholding_pct"], cfg["pl_capital_gains_tax_pct"])
                items.append({**dict(r), **t})
            cost_basis_eur = cfg["position_qty"] * cfg["avg_cost_eur"]
            totals = sensors.dividends_values(conn, cfg, cost_basis_eur)
            return render_template(
                "dividends.html", active="dividends", version=__version__,
                items=items, totals=totals, cfg=cfg, saved=request.args.get("saved") == "1")
        finally:
            conn.close()

    @app.post("/dividends")
    def dividends_post():
        conn = _conn()
        try:
            cfg = settingsm.get_settings(conn)
            pay_date = request.form.get("pay_date") or ""
            gross_eur = float(request.form.get("gross_eur") or 0)
            quantity = float(request.form.get("quantity") or 0) or None
            gross_per_share = float(request.form.get("gross_per_share_eur") or 0) or None
            withholding_raw = request.form.get("withholding_pct")
            withholding_pct = (float(withholding_raw) if withholding_raw
                               else cfg["finnish_withholding_pct"])
            t = taxm.compute_dividend_tax(
                gross_eur, withholding_pct,
                cfg["treaty_withholding_pct"], cfg["pl_capital_gains_tax_pct"])
            with dbm.WRITE_LOCK:
                conn.execute(
                    "INSERT INTO dividends (pay_date, gross_per_share_eur, quantity, gross_eur, "
                    "withholding_pct, withholding_paid_eur, net_received_eur) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (pay_date, gross_per_share, quantity, gross_eur, withholding_pct,
                     t["withholding_paid_eur"], t["net_received_eur"]))
                conn.commit()
            return redirect(url_for("dividends_get", saved="1"))
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
            quantity = float(request.form.get("sale_quantity") or 0)
            price_eur = float(request.form.get("sale_price_eur") or 0)
            fee_eur = float(request.form.get("sale_fee_eur") or 0)
            try:
                with dbm.WRITE_LOCK:
                    taxlots.record_sale(conn, sale_date, quantity, price_eur, fee_eur=fee_eur)
                return redirect(url_for("lots_get", sold="1"))
            except (taxlots.InsufficientLotsError, taxlots.CostBasisMissingError) as e:
                return redirect(url_for("lots_get", error=str(e)))
        finally:
            conn.close()

    @app.get("/news")
    def news_page():
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT n.*, s.sentiment, s.impact, s.horizon, s.thesis_pl, s.tags "
                "FROM news n LEFT JOIN news_scores s ON s.news_id = n.id "
                "ORDER BY n.published_at DESC LIMIT 50"
            ).fetchall()
            items = []
            for r in rows:
                d = dict(r)
                d["tags"] = json.loads(d["tags"]) if d.get("tags") else []
                items.append(d)
            return render_template("news.html", active="news", version=__version__, items=items)
        finally:
            conn.close()

    @app.get("/forecasts")
    def forecasts_page():
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT * FROM forecasts ORDER BY created_at DESC LIMIT 60"
            ).fetchall()
            return render_template(
                "forecasts.html", active="forecasts", version=__version__,
                items=[dict(r) for r in rows])
        finally:
            conn.close()

    @app.get("/settings")
    def settings_get():
        conn = _conn()
        try:
            cfg = settingsm.get_settings(conn)
            keys = _ai_keys()
            local_models = (openai_compat.list_models(cfg["local_llm_base_url"],
                                                       keys["local_llm_api_key"])
                            if cfg["local_llm_base_url"] else [])
            return render_template(
                "settings.html", active="settings", version=__version__, cfg=cfg,
                local_models=local_models, saved=request.args.get("saved") == "1")
        finally:
            conn.close()

    @app.post("/settings")
    def settings_post():
        conn = _conn()
        try:
            updates = {
                "ai_primary": request.form.get("ai_primary", "local"),
                "ai_fallback": request.form.get("ai_fallback", "gemini"),
                "local_llm_model": request.form.get("local_llm_model", ""),
                "gemini_model": request.form.get("gemini_model", ""),
                "anthropic_model": request.form.get("anthropic_model", ""),
                "ai_recommendations_enabled": 1 if request.form.get("ai_recommendations_enabled") else 0,
                "alert_sentiment_drop": float(request.form.get("alert_sentiment_drop") or 0.5),
                "alert_price_move_pct": float(request.form.get("alert_price_move_pct") or 3.0),
                "alert_on_forecast_break": 1 if request.form.get("alert_on_forecast_break") else 0,
                "alert_min_interval_minutes": int(
                    request.form.get("alert_min_interval_minutes") or 120),
                "notify_service": request.form.get("notify_service", ""),
                "cost_basis_policy": request.form.get("cost_basis_policy", "own_only"),
            }
            with dbm.WRITE_LOCK:
                settingsm.set_settings(conn, updates)
            return redirect(url_for("settings_get", saved="1"))
        finally:
            conn.close()

    return app
