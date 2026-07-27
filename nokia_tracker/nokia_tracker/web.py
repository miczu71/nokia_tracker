"""Flask web UI — szkielet (krok 1). Pełny dashboard/portfel/importy w kroku 9.

create_app() zwraca gołą aplikację ze stroną statusu, żeby add-on miał coś
do zweryfikowania na ingressie od pierwszego kroku, zamiast pustego procesu.
"""
from __future__ import annotations

from flask import Flask, Response

from . import __version__


def create_app(db_path: str) -> Flask:
    app = Flask(__name__)

    @app.after_request
    def _no_cache(resp: Response) -> Response:
        # Cache-busting dla HTML/API — wymóg z CLAUDE.md (WebView Companion
        # cache'uje agresywnie i nie rewaliduje). Statyki dostaną
        # ?v=<wersja> + immutable dopiero w kroku 9, gdy powstaną.
        if resp.mimetype in ("text/html", "application/json"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/")
    def status() -> str:
        return (
            f"<!doctype html><html><head><title>Nokia Tracker</title></head>"
            f"<body><h1>Nokia Tracker {__version__}</h1>"
            f"<p>Szkielet add-onu — pełny dashboard w kroku 9.</p>"
            f"<p>DB: {db_path}</p></body></html>"
        )

    return app
