"""Trasy AI: /analyze-now, /asystent (+API JSON), /api/preview/copilot.

Uwaga (reguła M — E3, docs/ROADMAP_V3.md): `analysis`, `ai_chat`,
`ai_copilot` są importowane jako MODUŁY i wołane przez atrybut
(`analysis.run_daily_analysis(...)`, `ai_chat.ask(...)`) — nigdy
`from ..analysis import run_daily_analysis`. Testy łatają te moduły przez
`monkeypatch.setattr(analysis, "run_daily_analysis", ...)` /
`monkeypatch.setattr(ai_chat, "ask", ...)`, co działa wyłącznie przy późnym
wiązaniu przez moduł."""
from __future__ import annotations

from flask import Flask, redirect, render_template, request, url_for

from ._context import AppContext
from ._helpers import _ai_keys
from .. import __version__
from .. import analysis
from .. import db as dbm
from .. import settings as settingsm
from ..ai import chat as ai_chat
from ..ai import copilot as ai_copilot
from ..ai import status as ai_status
from ..views.market_context import instrument_ids as _ids


def register_ai_routes(app: Flask, ctx: AppContext) -> None:
    _conn = ctx.conn

    def _assistant_ask_and_redirect(question: str):
        """Wspólna ścieżka dla POST /asystent i GET /asystent?q= (pole
        szybkiego pytania na pulpicie, krok 29.7) — OBIE kończą się
        przekierowaniem do CZYSTEGO /asystent, żeby odświeżenie strony po
        odpowiedzi nie powtórzyło pytania (to samo uzasadnienie co
        POST-redirect-GET gdzie indziej w aplikacji). Odpowiedź trafia do
        chat_log i pojawia się jako pierwszy wiersz historii na kolejnym GET."""
        conn = _conn()
        try:
            cfg = dict(settingsm.get_settings(conn), **_ai_keys())
            if question and cfg.get("ai_chat_enabled", 1):
                ai_chat.ask(conn, cfg, question)
            return redirect(url_for("assistant_get"))
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

    @app.get("/api/preview/copilot")
    def preview_copilot():
        """Krok 33 (docs/PLAN_KROK_33_copilot.md): podgląd co-pilota BEZ
        skutków ubocznych — nie woła AI, nie wysyła powiadomienia i nie
        zapisuje do alerts_log (nie konsumuje cooldownu), więc wolno go
        wołać na produkcji do weryfikacji. `?today=YYYY-MM-DD` pozwala
        sprawdzić, co odpaliłoby się innego dnia."""
        conn = _conn()
        try:
            today = request.args.get("today") or None
            cfg = settingsm.get_settings(conn)
            try:
                return ai_copilot.preview(conn, cfg, today=today)
            except ValueError:
                return {"ok": False, "error": "Niepoprawna data (oczekiwano RRRR-MM-DD)."}
        finally:
            conn.close()

    @app.get("/asystent")
    def assistant_get():
        q = request.args.get("q", "").strip()
        if q:
            return _assistant_ask_and_redirect(q)
        conn = _conn()
        try:
            cfg = dict(settingsm.get_settings(conn), **_ai_keys())
            return render_template(
                "assistant.html", active="assistant", version=__version__,
                chat_enabled=bool(cfg.get("ai_chat_enabled", 1)),
                history=ai_chat.history(conn, limit=20),
                ai_status=ai_status.snapshot(conn, cfg))
        finally:
            conn.close()

    @app.post("/asystent")
    def assistant_post():
        return _assistant_ask_and_redirect(request.form.get("question", "").strip())

    @app.route("/api/asystent", methods=["GET", "POST"])
    def assistant_api():
        """JSON dla ewentualnej przyszłej wersji z JS — CELOWO bez auto-fire
        na wpisywanie (initFormPreview() debounce'uje na 'input', co dla
        pytań w naturalnym języku wołałoby AI na każde naciśnięcie klawisza;
        ten endpoint wymaga jawnego wywołania, nie jest dziś podpięty pod
        żaden formularz z debounce)."""
        conn = _conn()
        try:
            cfg = dict(settingsm.get_settings(conn), **_ai_keys())
            if request.method == "POST":
                body = request.get_json(silent=True) or {}
                question = (body.get("question") or request.form.get("question", "")).strip()
            else:
                question = request.args.get("q", "").strip()
            if not cfg.get("ai_chat_enabled", 1):
                return {"ok": False, "intent": None, "params": {}, "title": None,
                       "lines": [], "detail_url": None,
                       "error": "Asystent jest wyłączony w Ustawieniach.", "answer_pl": None}
            return ai_chat.ask(conn, cfg, question)
        finally:
            conn.close()
