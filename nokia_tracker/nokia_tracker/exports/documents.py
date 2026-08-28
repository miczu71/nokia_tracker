"""Renderowanie dokumentów dowodowych (`views/documents.py`) do samodzielnego
HTML — E10, docs/PLAN_E10_dokumenty.md. Środowisko Jinja WŁASNE, nie
`app.jinja_env` Flaska: dokument opuszcza aplikację (zapis na dysk, wydruk,
załącznik do e-maila), więc nie może zawierać `url_for()` (ingress dopisuje
prefiks sesji — bezużyteczny poza przeglądarką, patrz `_IngressPrefixMiddleware`
w `web/__init__.py`) ani polegać na `app.css` (ekranowe `@media
(prefers-color-scheme)`, `color-mix()`, i `.trace-body { no-print }` — dokładnie
odwrotność tego, czego potrzebuje wydruk). Osobny env sprawia, że sięgnięcie po
`url_for` w szablonie dokumentu jest błędem WYKRYWANYM w teście
(`UndefinedError`), nie cichym linkiem donikąd w pliku, który ktoś otworzy za
trzy lata na innym komputerze.

`autoescape` włączony JAWNIE (`select_autoescape` — goły `jinja2.Environment`
ma `autoescape=False` domyślnie, inaczej niż środowisko Flaska)."""
from __future__ import annotations

from pathlib import Path

import jinja2
from markupsafe import Markup

_PKG_ROOT = Path(__file__).resolve().parent.parent
_CSS_PATH = _PKG_ROOT / "static" / "doc.css"

_TEMPLATES = {
    "sale": "doc_sale.html",
    "simulation": "doc_simulation.html",
    "dossier": "doc_dossier.html",
}

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_PKG_ROOT / "templates")),
    autoescape=jinja2.select_autoescape(["html"]),
)


def doc_css() -> Markup:
    """Wczytane raz z dysku (moduł importowany raz per proces waitress) —
    świadomie NIE cache'owane w zmiennej globalnej, żeby edycja `doc.css` w
    trakcie developmentu (`python3 -m nokia_tracker.main` bez restartu)
    była widoczna od razu; w produkcji plik i tak nie zmienia się bez
    restartu procesu.

    `Markup(...)`, NIE goły `str`: `doc_base.html` wkleja to jako `<style>{{
    css }}</style>` w środowisku z `autoescape=True` (świadomie, dla reszty
    dokumentu — patrz docstring modułu). Jinja NIE wie, że wnętrze `<style>`
    to „surowy tekst" (HTML5 nie dekoduje tam encji) — bez `Markup` autoescape
    zamienia `"` w `&#34;` w KAŻDEJ regule z cudzysłowem (`font-family:
    "..."`, `content: "..."` w `@page`), po cichu psując parsowanie CSS.
    Przeglądarka po prostu pomija złamaną regułę (font spada na domyślny),
    WeasyPrint dodatkowo loguje ostrzeżenie — złapane w Etapie 4 dopiero przy
    realnym renderze PDF, nie w Etapie 3 na oko w przeglądarce. `Markup` tu, w
    JEDNYM miejscu z którego `css` zawsze pochodzi, zamiast `|safe` w każdym
    szablonie z osobna, który mógłby o tym zapomnieć."""
    return Markup(_CSS_PATH.read_text(encoding="utf-8"))


def render_html(kind: str, doc: dict) -> str:
    """`kind`: 'sale' | 'simulation' | 'dossier' — wybiera szablon treści
    (`doc_sale.html` itd.), który sam `{% extends "doc_base.html" %}`.
    `doc`: wynik `views/documents.py::sale_document`/`simulation_document`/
    `year_dossier` (zawiera `meta` + dane specyficzne dla `kind`)."""
    template_name = _TEMPLATES[kind]
    template = _env.get_template(template_name)
    return template.render(css=doc_css(), **doc)
