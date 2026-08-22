"""Trasy /settings."""
from __future__ import annotations

from flask import Flask, redirect, render_template, request, url_for

from ._context import AppContext
from ._helpers import _ai_keys
from .. import __version__
from .. import db as dbm
from .. import settings as settingsm
from ..ai import openai_compat
from ..ai import status as ai_status


def register_ustawienia_routes(app: Flask, ctx: AppContext) -> None:
    _conn = ctx.conn

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
                local_models=local_models, saved=request.args.get("saved") == "1",
                ai_status=ai_status.snapshot(conn, dict(cfg, **_ai_keys()), local_models))
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
                "ai_chat_enabled": 1 if request.form.get("ai_chat_enabled") else 0,
                "ai_chat_narration_enabled": 1 if request.form.get("ai_chat_narration_enabled") else 0,
                "ai_max_calls_per_day_local": int(
                    request.form.get("ai_max_calls_per_day_local") or 500),
                "alert_sentiment_drop": float(request.form.get("alert_sentiment_drop") or 0.5),
                "alert_price_move_pct": float(request.form.get("alert_price_move_pct") or 3.0),
                "alert_on_forecast_break": 1 if request.form.get("alert_on_forecast_break") else 0,
                "alert_min_interval_minutes": int(
                    request.form.get("alert_min_interval_minutes") or 120),
                "notify_service": request.form.get("notify_service", ""),
                "notify_news_enabled": 1 if request.form.get("notify_news_enabled") else 0,
                "notify_news_min_impact": int(
                    request.form.get("notify_news_min_impact") or 1),
                "notify_digest_enabled": 1 if request.form.get("notify_digest_enabled") else 0,
                "digest_time": request.form.get("digest_time", "20:10"),
                "cost_basis_policy": request.form.get("cost_basis_policy", "own_only"),
                # Krok 18: dawniej tylko odczytywalne (domyślne 35% pokazywane jako
                # tekst na /dywidendy bez możliwości zmiany) — te cztery stawki
                # wpływają na każdą kwotę PLN w aplikacji, więc trafiają do UI.
                "finnish_withholding_pct": float(
                    request.form.get("finnish_withholding_pct") or 35.0),
                "treaty_withholding_pct": float(
                    request.form.get("treaty_withholding_pct") or 15.0),
                "pl_capital_gains_tax_pct": float(
                    request.form.get("pl_capital_gains_tax_pct") or 19.0),
                "tax_year": int(request.form.get("tax_year") or 0),
                # Krok 26: doradca planu pracowniczego.
                "other_net_worth_pln": float(
                    request.form.get("other_net_worth_pln") or 0.0),
                "concentration_alert_pct": float(
                    request.form.get("concentration_alert_pct") or 25.0),
            }
            with dbm.WRITE_LOCK:
                settingsm.set_settings(conn, updates)
            return redirect(url_for("settings_get", saved="1"))
        finally:
            conn.close()
