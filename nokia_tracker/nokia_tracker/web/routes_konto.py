"""Trasa Stanu konta: / (krok E5, docs/ROADMAP_V3.md). Zastąpiła dawny pulpit
rynkowy — ten przeniósł się na `/rynek` (`routes_rynek.py`). `/` odpowiada na
"jaki jest mój stan": akcje + gotówka + podatek + najbliższe zdarzenia,
złożone wyłącznie z istniejących klocków `views/account.py` (portfel,
`cash.ledger()` z E4, `account_events.py`)."""
from __future__ import annotations

from datetime import datetime

from flask import Flask, render_template, request

from ._context import AppContext
from .. import __version__
from .. import settings as settingsm
from ..views.account import account_view
from ..views.market_context import instrument_ids as _ids


def register_konto_routes(app: Flask, ctx: AppContext) -> None:
    _conn = ctx.conn

    @app.get("/")
    def account_get():
        conn = _conn()
        try:
            cfg = settingsm.get_settings(conn)
            # Ten sam wzorzec roku co `gotowka_get` (routes_podatki.py) —
            # selektor roku w pasku nawigacji działa więc identycznie tutaj.
            year = request.args.get("year", type=int) or cfg.get("tax_year") or datetime.now().year
            ids = _ids(conn)
            view = account_view(conn, cfg, ids, year)
            return render_template(
                "account.html", active="account", version=__version__,
                year=year, **view)
        finally:
            conn.close()
