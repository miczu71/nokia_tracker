"""Rozwiązywalność nazw endpointów (E3 §4a — docs/ROADMAP_V3.md).

Nawigacja w `templates/base.html:34-46` trzyma nazwy endpointów jako GOŁE
STRINGI w słowniku `NAV_GROUPS` i rozwiązuje je dynamicznie przez
`url_for(endpoint)` (`base.html:59`) — żaden grep za `url_for('nazwa')` ich
nie znajdzie, a literówka ujawnia się dopiero przy RENDEROWANIU strony.
Cztery mechanizmy poniżej łapią wszystkie trzy sposoby, w jakie kod
referuje nazwę endpointu: literalny `url_for()` w szablonach, gołe stringi
w `NAV_GROUPS`, i literalny `url_for()` w Pythonie."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from nokia_tracker import db as dbm
from nokia_tracker.web import create_app

_PKG = Path(__file__).resolve().parents[1] / "nokia_tracker"


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("routing") / "test.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    conn.close()
    a = create_app(db_path)
    a.config["TESTING"] = True
    return a


# --- 1. literalne url_for('nazwa') w szablonach ---

_URL_FOR_RE = re.compile(r"""url_for\(\s*['"]([a-zA-Z0-9_]+)['"]""")


def _literal_template_endpoints() -> list[tuple[str, str]]:
    out = []
    for tpl in (_PKG / "templates").glob("*.html"):
        src = tpl.read_text(encoding="utf-8")
        for m in _URL_FOR_RE.finditer(src):
            out.append((tpl.name, m.group(1)))
    return out


def test_every_literal_url_for_in_templates_resolves(app):
    unknown = [f"{tpl}: url_for({ep!r})"
               for tpl, ep in _literal_template_endpoints()
               if ep not in app.view_functions]
    assert not unknown, "nieznane endpointy w szablonach: " + ", ".join(unknown)


def test_template_scan_is_not_vacuous():
    """Strażnik strażnika: gdyby regex przestał cokolwiek znajdować (zmiana
    składni szablonów, przeniesienie katalogu), test wyżej przechodziłby
    PUSTY i nic by nie sprawdzał."""
    assert len(_literal_template_endpoints()) >= 20


# --- 2. gołe stringi w NAV_GROUPS: łapane wyłącznie przez renderowanie ---

def _parameterless_get_paths(app) -> list[str]:
    return sorted(
        rule.rule for rule in app.url_map.iter_rules()
        if rule.endpoint not in ("static", "assistant_api")
        and not rule.arguments
        and "GET" in (rule.methods or set()))


def test_every_parameterless_get_route_renders(app):
    """Każda strona renderuje base.html, więc WYKONUJE pętlę po NAV_GROUPS —
    to jedyne miejsce, w którym gołe nazwy endpointów w tym słowniku są
    rozwiązywane. Lista tras pochodzi z `app.url_map`, nie z ręcznego
    wykazu — nowa trasa jest objęta testem automatycznie, w dniu dodania.
    `assistant_api` pominięty: bez `?q=` i tak woła żywe AI
    (`ai_chat.ask(conn, cfg, "")`)."""
    with app.test_client() as c:
        results = [(p, c.get(p).status_code) for p in _parameterless_get_paths(app)]
    broken = [(p, code) for p, code in results if code != 200]
    assert not broken, broken


def test_route_scan_is_not_vacuous(app):
    assert len(_parameterless_get_paths(app)) >= 20


# --- 3. url_for() po stronie Pythona (trasy web/) ---

def _python_url_for_sites() -> list[tuple[str, int, str]]:
    web_dir = _PKG / "web"
    out = []
    for py in sorted(web_dir.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), str(py))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "url_for" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                out.append((py.name, node.lineno, node.args[0].value))
    return out


def test_every_url_for_in_web_package_resolves(app):
    """Redirecty wskazują dziś endpointy z INNYCH plików niż przed
    podziałem — literówka na granicy modułu ujawniłaby się dopiero na
    produkcji, bo większość leży na ścieżkach błędu, których żaden test nie
    przechodzi (np. url_for('lots_get', error=...))."""
    bad = [f"{f}:{ln} url_for({ep!r})" for f, ln, ep in _python_url_for_sites()
           if ep not in app.view_functions]
    assert not bad, "nieznane endpointy w kodzie: " + ", ".join(bad)


def test_python_scan_is_not_vacuous():
    assert len(_python_url_for_sites()) >= 20


# --- 4. root_path: statyki naprawdę serwowane, nie tylko zlinkowane ---

def test_static_assets_are_actually_served(app):
    """`Flask(__name__)` w PAKIECIE `nokia_tracker.web` przestawia
    `root_path` na `nokia_tracker/web/` (dawniej, jako moduł, na
    `nokia_tracker/`). Szablony padają wtedy głośno, statyki NIE:
    `url_for('static', ...)` generuje poprawny URL także dla
    nieistniejącego katalogu — jedyny dotychczasowy strażnik
    (`test_base_template_versions_static_assets`) sprawdza sam string URL-a
    w HTML-u, nie czy plik faktycznie się serwuje."""
    with app.test_client() as c:
        for asset in ("app.css", "app.js", "chart.umd.min.js"):
            resp = c.get(f"/static/{asset}")
            assert resp.status_code == 200, asset


# --- przeniesione z dawnego test_web.py (E3): smoke + fallback nawigacji bez JS ---

# --- smoke: każda strona GET zwraca 200 i ma no-store ---

@pytest.mark.parametrize("path", ["/", "/portfolio", "/lots", "/sales", "/dividends", "/grants",
                                  "/imports", "/news", "/forecasts", "/settings", "/pit38",
                                  "/dane", "/wyniki", "/plan", "/pit38/kreator", "/asystent"])
def test_page_returns_200_with_no_store(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-store"


def test_base_template_versions_static_assets(client):
    resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert "/static/app.css?v=" in html
    assert "/static/app.js?v=" in html
    assert "/static/chart.umd.min.js?v=" in html


# --- krok 18: nawigacja w 5 sekcjach — fallback bez JS ---

def test_nav_groups_render_without_js(client):
    html = client.get("/").get_data(as_text=True)
    assert '<details class="nav-group' in html
    for label in ["Pulpit", "Portfel", "Loty", "Sprzedaże", "Granty", "Dywidendy",
                  "PIT-38", "Importy", "Newsy", "Prognozy", "Kopia zapasowa", "Ustawienia"]:
        assert label in html


def test_nav_contains_plan_link(client):
    html = client.get("/").get_data(as_text=True)
    assert 'href="/plan"' in html


def test_nav_contains_asystent_link(client):
    html = client.get("/").get_data(as_text=True)
    assert 'href="/asystent"' in html


