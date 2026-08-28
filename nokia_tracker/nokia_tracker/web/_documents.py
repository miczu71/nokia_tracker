"""Wspólne dostarczanie dokumentów dowodowych (E10, docs/PLAN_E10_dokumenty.md)
— wołane z `routes_portfel.py`/`routes_plan.py`/`routes_podatki.py`. Świadomie
BEZ własnych tras (wzorzec `_helpers.py`) — reguła repo to podział wg domeny
nawigacji, nie wg rodzaju artefaktu, więc dokument sprzedaży zostaje w
`routes_portfel.py`, nie wędruje do osobnego `routes_dokumenty.py`.

Etap 3: HTML. Etap 4 (docs/PLAN_E10_dokumenty.md): PDF (WeasyPrint) jako
rozszerzenie tej samej funkcji (`fmt="pdf"`), nie osobna ścieżka."""
from __future__ import annotations

from flask import Response

from ..exports import documents as exports_documents
from ..exports import pdf as exports_pdf


def document_response(kind: str, doc: dict, *, fmt: str, filename_stem: str,
                      download: bool = False, html_fallback_url: str | None = None
                      ) -> Response:
    """`filename_stem`: nazwa pliku BEZ rozszerzenia, ASCII (Content-Disposition
    nie dostaje polskich znaków — to samo ograniczenie co istniejące eksporty
    w `exports/pit38.py`). `download=False` (domyślnie) -> `inline`: Android
    WebView aplikacji HA Companion nie zawsze obsługuje `attachment` dla
    `text/html` (link wygląda, jakby nic nie robił) — `inline` otwiera dokument
    w karcie, tak jak dzisiejszy `?print=1`. PDF jest ZAWSZE `attachment`
    (WebView otwiera PDF-y natywnym viewerem po pobraniu, `inline` nie ma tu
    sensu). `download=True` -> `attachment` również dla HTML.

    `Cache-Control: no-store` ustawiane TUTAJ jawnie — `web/__init__.py::
    _no_cache` reaguje tylko na `text/html`/`application/json`, PDF nic by
    nie dostał bez tego.

    `fmt="pdf"` bez działającego silnika (`exports/pdf.py::available() ==
    False`) zwraca 503 z krótką polską stroną i (gdy podane) odnośnikiem do
    `html_fallback_url` — degradacja, nie goły błąd serwera."""
    if fmt == "pdf":
        if not exports_pdf.available():
            link = (f'<p><a href="{html_fallback_url}">Otwórz dokument jako HTML</a></p>'
                    if html_fallback_url else "")
            body = (
                "<!doctype html><html lang=\"pl\"><head><meta charset=\"utf-8\">"
                "<title>PDF niedostępny — Nokia Tracker</title></head><body>"
                "<p>Silnik PDF nie jest dostępny w tym buildzie dodatku (brak "
                "bibliotek systemowych na tej architekturze) — dokument HTML "
                "działa zawsze.</p>" + link + "</body></html>")
            return Response(
                body, status=503, mimetype="text/html; charset=utf-8",
                headers={"Cache-Control": "no-store"})

        html = exports_documents.render_html(kind, doc)
        pdf_bytes = exports_pdf.render_pdf(html)
        return Response(
            pdf_bytes, mimetype="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_stem}.pdf"',
                "Cache-Control": "no-store",
            })

    html = exports_documents.render_html(kind, doc)
    disposition = "attachment" if download else "inline"
    return Response(
        html, mimetype="text/html; charset=utf-8",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename_stem}.html"',
            "Cache-Control": "no-store",
        })
