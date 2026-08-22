"""Trasy pulpitu i wiadomości rynkowych: /, /api/chart, /news, /forecasts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Flask, render_template, request, url_for

from ._context import AppContext
from .. import __version__
from .. import quotes
from .. import sensors
from ..views.dashboard import dashboard_view
from ..views.market_context import instrument_ids as _ids

# Zakresy wykresu pulpitu (krok 16): (granularity, dni_wstecz). `None` dni = bez
# filtra dolnego (całość dostępnej historii). "1d" jedyny na intraday — reszta na
# świecach dziennych, których backfill/refresh i tak już istnieje (quotes.py).
_CHART_RANGES: dict[str, tuple[str, int | None]] = {
    "1d": ("intraday", None),
    "1w": ("daily", 7),
    "1m": ("daily", 31),
    "3m": ("daily", 93),
    "6m": ("daily", 186),
    "1y": ("daily", 366),
    "3y": ("daily", 3 * 366),
    "5y": ("daily", 5 * 366),
    "max": ("daily", None),
}
_DEFAULT_CHART_RANGE = "3m"


def register_rynek_routes(app: Flask, ctx: AppContext) -> None:
    # Alias, żeby ciała tras zostały bajtowo bliskie temu, co było w web.py —
    # `git diff -M` czyta się wtedy jak przeniesienie, nie przepisanie.
    _conn = ctx.conn

    @app.get("/")
    def dashboard():
        conn = _conn()
        try:
            ids = _ids(conn)
            view = dashboard_view(conn, ids)
            return render_template(
                "dashboard.html", active="dashboard", version=__version__,
                chart_ranges=list(_CHART_RANGES), default_chart_range=_DEFAULT_CHART_RANGE,
                chart_api_url=url_for("chart_api"),
                **view,
            )
        finally:
            conn.close()

    @app.get("/api/chart")
    def chart_api():
        """Krok 16: zasila konfigurowalny wykres pulpitu — `?range=` z
        `_CHART_RANGES` (1d na intraday, reszta na dziennych). `no-store`
        łapie się automatycznie (`_no_cache` obsługuje `application/json`),
        więc WebView Companion nie zserwuje kiedyś złapanego zakresu na
        stałe (patrz CLAUDE.md — cache mobilny)."""
        conn = _conn()
        try:
            range_key = request.args.get("range", _DEFAULT_CHART_RANGE)
            granularity, days = _CHART_RANGES.get(range_key, _CHART_RANGES[_DEFAULT_CHART_RANGE])
            since = (
                (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
                if days is not None else None)
            ids = _ids(conn)
            points = quotes.closes_in_range(conn, ids["primary"], granularity, since)
            return {"range": range_key, "granularity": granularity, "points": points}
        finally:
            conn.close()

    @app.get("/news")
    def news_page():
        conn = _conn()
        try:
            # Krok 18: `s.horizon`/`s.tags` odpytywane tu wcześniej, ale szablon
            # nigdy ich nie renderował — martwa robota na każde żądanie, usunięte.
            # `ns.kind` (rss/gdelt/finnhub/marketaux) dołożone jako kolumna „Źródło" —
            # dotąd niewidoczne w UI mimo że news.py śledzi providera per wpis.
            rows = conn.execute(
                "SELECT n.*, s.sentiment, s.impact, s.thesis_pl, ns.kind AS source_kind "
                "FROM news n LEFT JOIN news_scores s ON s.news_id = n.id "
                "LEFT JOIN news_sources ns ON ns.id = n.source_id "
                # Krok 28.6: 50 -> 200 — limit istniał wyłącznie żeby nie
                # renderować bez końca; teraz "Pokaż więcej" (news.html/app.js)
                # ujawnia po 20 client-side, więc wyższy limit ma sens dopiero
                # z paginacją po stronie serwera. 200 to wciąż jedno zapytanie
                # SQLite po indeksowanej kolumnie, bez odczuwalnego kosztu.
                "ORDER BY n.published_at DESC LIMIT 200"
            ).fetchall()
            items = [dict(r) for r in rows]
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
            # Krok 18: jedyna liczba, po którą się tu przychodzi ("czy prognozy się
            # sprawdzają") żyła dotąd tylko na pulpicie (`sensors.forecast_values`);
            # ta strona pokazywała samą historię bez podsumowania.
            accuracy_pct = sensors.forecast_values(conn).get("forecast_accuracy_pct")
            return render_template(
                "forecasts.html", active="forecasts", version=__version__,
                items=[dict(r) for r in rows], accuracy_pct=accuracy_pct)
        finally:
            conn.close()
