"""Silnik PDF dla dokumentów dowodowych (E10, docs/PLAN_E10_dokumenty.md,
Etap 4) — cienka warstwa nad WeasyPrint. `weasyprint` importowany LENIWIE,
wewnątrz funkcji, NIGDY na poziomie modułu: brak biblioteki (dev box bez
`requirements-pdf.txt`, albo build na architekturze bez gotowego koła —
patrz `Dockerfile`) ma degradować trasy `.pdf` do 503, NIE wywalać startu
całego dodatku.

Awaria importu WeasyPrint na kontenerze bez `libpango` to `OSError` z
`dlopen` wewnątrz cffi (`weasyprint.text.ffi`), NIE `ImportError` — stąd
`except Exception` w `available()`, jedyne uzasadnione tak szerokie łapanie
w tym module (uzasadnienie w komentarzu przy nim, nie tylko tutaj)."""
from __future__ import annotations

import threading

_available: bool | None = None
_availability_lock = threading.Lock()
# WeasyPrint trzyma całe drzewo layoutu w pamięci; waitress serwuje 4 wątkami.
# Eksport dokumentu nie jest ścieżką wrażliwą na opóźnienie — jeden render
# naraz jest tańszy niż ryzyko OOM na małym sprzęcie HA przy dwóch naraz.
_render_semaphore = threading.Semaphore(1)


class PdfUnavailableError(RuntimeError):
    """Silnik PDF niedostępny w tym buildzie (brak `requirements-pdf.txt`
    przy instalacji albo brak bibliotek systemowych na tej architekturze) —
    trasa `.pdf` łapie to i zwraca 503 z odnośnikiem do `.html`, zamiast
    gołego 500."""


def available() -> bool:
    """Cache'owane — import WeasyPrint jest kosztowny (ładuje Pango/HarfBuzz
    przez cffi), pierwsze wywołanie próbuje raz, kolejne czytają wynik.
    Świadomie `except Exception`, nie `except ImportError`: brak `libpango`
    w kontenerze objawia się jako `OSError` z `dlopen` WEWNĄTRZ importu
    `weasyprint` (cffi ładuje biblioteki natywne przy imporcie modułu), nie
    jako brak pakietu Pythonowego — węższy `except` przepuściłby ten wyjątek
    i wywalił cały request (albo, gdyby ktoś kiedyś zaimportował na poziomie
    modułu zamiast leniwie tutaj, cały start dodatku)."""
    global _available
    if _available is None:
        with _availability_lock:
            if _available is None:
                try:
                    import weasyprint  # noqa: F401
                    _available = True
                except Exception:
                    _available = False
    return _available


def render_pdf(html: str) -> bytes:
    """Renderuje samodzielny HTML (`exports/documents.py::render_html`) do
    PDF. Woła `available()` first — podnosi `PdfUnavailableError` zamiast
    dać wyjątkowi z samego WeasyPrint wyciec jako gołe 500."""
    if not available():
        raise PdfUnavailableError("Silnik PDF niedostępny w tym buildzie dodatku.")
    import weasyprint
    with _render_semaphore:
        return weasyprint.HTML(string=html).write_pdf()
