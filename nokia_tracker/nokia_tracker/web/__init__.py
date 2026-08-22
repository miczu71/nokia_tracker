"""Flask web UI — montaż aplikacji (krok 9, BLUEPRINT §3/§9). Cache-busting:
no-store na HTML/API, statyki ?v=<wersja>, badge wersji w nav (CLAUDE.md —
WebView Companion cache'uje HTML agresywnie i nie rewaliduje).

Trasy żyją w routes_*.py (ten pakiet), obliczenia w `nokia_tracker/views/`.
Ten plik trzyma WYŁĄCZNIE to, co wspólne dla całej aplikacji: middleware
ingressu, filtry Jinja, nagłówek no-store i kontekst szablonów (rok
podatkowy w nawigacji). Zero tras — każda trasa żyje w dokładnie jednym
routes_*.py, zarejestrowanym tutaj przez register_*_routes(app, ctx)
(E3 — docs/ROADMAP_V3.md).

Celowo NIE Blueprinty: nawigacja (templates/base.html:34-46) trzyma nazwy
endpointów jako gołe stringi w NAV_GROUPS i rozwiązuje je dynamicznie przez
url_for(endpoint) (base.html:59) — Flask prefiksowałby je nazwą blueprintu
(`dashboard` -> `portfel.dashboard`), co po cichu wywaliłoby nawigację.
register_*_routes(app, ctx) daje ten sam podział na pliki bez tego ryzyka.
"""
from __future__ import annotations

from pathlib import Path

from flask import Flask, Response, request

from ._context import AppContext
from .routes_ai import register_ai_routes
from .routes_dane import register_dane_routes
from .routes_dywidendy import register_dywidendy_routes
from .routes_plan import register_plan_routes
from .routes_podatki import register_podatki_routes
from .routes_portfel import register_portfel_routes
from .routes_rynek import register_rynek_routes
from .routes_ustawienia import register_ustawienia_routes
from .. import format as fmt
from ..tax import pit38 as taxpit38

# `Flask(__name__)` z PAKIETU `nokia_tracker.web` liczy root_path na
# nokia_tracker/web/, więc domyślne "templates"/"static" wskazywałyby na
# nieistniejące katalogi wewnątrz web/ — dopóki web.py był MODUŁEM, root_path
# wypadał na nokia_tracker/ i domyślki działały. Szablony padłyby przez to
# głośno (każda strona 500), ale static NIE: url_for('static', ...) generuje
# poprawny URL także dla nieistniejącego katalogu (patrz
# tests/test_web_routing.py::test_static_assets_are_actually_served).
_PKG_ROOT = Path(__file__).resolve().parent.parent


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
    app = Flask(
        __name__,
        template_folder=str(_PKG_ROOT / "templates"),
        static_folder=str(_PKG_ROOT / "static"),
    )
    app.wsgi_app = _IngressPrefixMiddleware(app.wsgi_app)

    # Krok 23 (docs/PLAN_KROK_23_portfel_kafelki.md): formatowanie po polsku
    # (separator tysięcy, przecinek dziesiętny) dla karty „Portfel" na pulpicie.
    # Celowo NIE używane na stronach podatkowych (/lots, /pit38, ...) — te muszą
    # zostać bajtowo zestawialne z wyciągiem/NBP w dotychczasowym formacie.
    app.jinja_env.filters["money"] = fmt.money
    app.jinja_env.filters["qty"] = fmt.qty
    app.jinja_env.filters["pct"] = fmt.pct

    @app.after_request
    def _no_cache(resp: Response) -> Response:
        if resp.mimetype in ("text/html", "application/json"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    ctx = AppContext(db_path=db_path)

    # Zostaje TUTAJ, nie w routes_podatki.py, mimo że czyta `taxpit38`: to
    # context_processor CAŁEJ aplikacji — base.html konsumuje `nav_tax_years`/
    # `nav_selected_year` na KAŻDEJ stronie (base.html:70-80), nie tylko na
    # podatkowych.
    @app.context_processor
    def _inject_nav_tax_year():
        """Krok 28.3 (docs/PLAN_KROK_28_ux_mobile.md §3): jeden selektor roku w
        nagłówku (base.html) zamiast trzech osobnych kopii (`pit38.html`,
        `sales.html`, `wizard.html`). Submituje `?year=` na BIEŻĄCĄ stronę
        (`request.path`) — trasy, które już czytają `request.args.get("year")`
        (pit38/sales/wizard), dostają go bez zmiany własnej logiki; trasy, które
        nie znają tego parametru, po prostu go ignorują (Flask nie waliduje
        nieznanych query stringów) — zero ryzyka regresji na stronach, które
        dziś selektora nie mają. Pusty wybór ("wszystkie") wysyła `year=` — dla
        `request.args.get("year", type=int)` to `None`, czyli dokładnie ten sam
        fallback co brak parametru w ogóle (sales: wszystkie lata; pit38/wizard:
        `cfg['tax_year']` albo rok bieżący)."""
        conn = ctx.conn()
        try:
            return {"nav_tax_years": taxpit38.years_with_data(conn),
                    "nav_selected_year": request.args.get("year", type=int)}
        finally:
            conn.close()

    register_rynek_routes(app, ctx)
    register_portfel_routes(app, ctx)
    register_dywidendy_routes(app, ctx)
    register_podatki_routes(app, ctx)
    register_plan_routes(app, ctx)
    register_dane_routes(app, ctx)
    register_ai_routes(app, ctx)
    register_ustawienia_routes(app, ctx)

    return app
