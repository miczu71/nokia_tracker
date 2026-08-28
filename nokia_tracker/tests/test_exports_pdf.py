"""E10 (docs/PLAN_E10_dokumenty.md), Etap 4 — `exports/pdf.py`: cienka
warstwa nad WeasyPrint. Sprawdza: renderowanie realnego PDF (bajty magiczne,
zaokrąglenie polskich znaków diakrytycznych przez `pypdf`), i strategię
degradacji (`available() == False` -> `PdfUnavailableError`, nie goły wyjątek
z WeasyPrint), bez zakładania, że WeasyPrint jest zainstalowany w środowisku
uruchamiającym testy — testy renderu pomijane, gdy `available()` mówi
`False`; test degradacji działa ZAWSZE (monkeypatch, nie zależy od instalacji)."""
from __future__ import annotations

import pytest

from nokia_tracker.exports import pdf as exports_pdf

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture(autouse=True)
def _reset_availability_cache():
    """`available()` cache'uje wynik w zmiennej modułowej — testy manipulujące
    tym cache muszą go zresetować, inaczej kolejność testów w tym samym
    procesie wpływa na wynik."""
    exports_pdf._available = None
    yield
    exports_pdf._available = None


def test_available_is_a_cached_bool():
    result1 = exports_pdf.available()
    assert isinstance(result1, bool)
    result2 = exports_pdf.available()
    assert result1 is result2  # ten sam obiekt bool -> naprawdę cache'owane


def test_render_pdf_raises_pdf_unavailable_when_engine_absent(monkeypatch):
    monkeypatch.setattr(exports_pdf, "_available", False)
    monkeypatch.setattr(exports_pdf, "available", lambda: False)
    with pytest.raises(exports_pdf.PdfUnavailableError):
        exports_pdf.render_pdf("<html><body>x</body></html>")


def test_available_false_when_import_raises(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "weasyprint":
            raise OSError("simulated: libpango not found")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    assert exports_pdf.available() is False


@pytest.mark.skipif(not exports_pdf.available(), reason="WeasyPrint niedostępny w tym środowisku")
def test_render_pdf_produces_valid_pdf_bytes():
    html = "<!doctype html><html lang=\"pl\"><body><p>test</p></body></html>"
    pdf_bytes = exports_pdf.render_pdf(html)
    assert pdf_bytes[:5] == b"%PDF-"
    assert len(pdf_bytes) > 100


@pytest.mark.skipif(not exports_pdf.available(), reason="WeasyPrint niedostępny w tym środowisku")
def test_render_pdf_preserves_polish_diacritics():
    pypdf = pytest.importorskip("pypdf")
    diacritics = "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"
    html = (
        "<!doctype html><html lang=\"pl\"><head><meta charset=\"utf-8\">"
        f"<style>body{{font-family:'DejaVu Sans'}}</style></head>"
        f"<body><p>{diacritics} zł</p></body></html>")
    pdf_bytes = exports_pdf.render_pdf(html)

    import io
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    text = reader.pages[0].extract_text()
    assert all(ch in text for ch in diacritics)
