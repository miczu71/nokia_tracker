"""E10 (docs/PLAN_E10_dokumenty.md), Etapy 3-4 — trasy HTTP dla dokumentów
dowodowych: `/sales/<id>/dokument.{html,pdf}`, `/wyplata/dokument.{html,pdf}`,
`/pit38/dokumentacja.{html,pdf}`. Sprawdza dostarczanie (status, mimetype,
Content-Disposition, Cache-Control), nie samą treść dokumentu (pokryte przez
`test_exports_documents.py`/`test_exports_pdf.py`). Testy `.pdf` renderujące
realny plik pomijane, gdy WeasyPrint niedostępny w środowisku uruchamiającym
testy — ścieżka degradacji (503) ma OSOBNY test, który działa zawsze
(monkeypatch, nie zależy od instalacji)."""
from __future__ import annotations

import pytest

from nokia_tracker.exports import pdf as exports_pdf

_PDF_AVAILABLE = exports_pdf.available()
_requires_pdf = pytest.mark.skipif(
    not _PDF_AVAILABLE, reason="WeasyPrint niedostępny w tym środowisku")


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "2026-07-27"))
    monkeypatch.setattr(
        "nokia_tracker.tax.whatif.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "2026-07-27"))


# --- /sales/<id>/dokument.html ---------------------------------------------

def test_sale_document_html_returns_self_contained_page(client, seed, _fake_nbp_rate):
    seed.lot("2024-01-05", 10, 5.0)
    seed.sale("2026-08-27", 4, 8.0)
    sale_id = seed.conn.execute(
        "SELECT id FROM sales ORDER BY id DESC LIMIT 1").fetchone()["id"]

    resp = client.get(f"/sales/{sale_id}/dokument.html")

    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    assert resp.headers["Cache-Control"] == "no-store"
    assert 'inline; filename="sprzedaz_2026-08-27_id' in resp.headers["Content-Disposition"]
    body = resp.get_data(as_text=True)
    assert body.lower().startswith("<!doctype html>")


def test_sale_document_html_download_query_param(client, seed, _fake_nbp_rate):
    seed.lot("2024-01-05", 10, 5.0)
    seed.sale("2026-08-27", 4, 8.0)
    sale_id = seed.conn.execute(
        "SELECT id FROM sales ORDER BY id DESC LIMIT 1").fetchone()["id"]

    resp = client.get(f"/sales/{sale_id}/dokument.html?pobierz=1")
    assert resp.headers["Content-Disposition"].startswith("attachment")


def test_sale_document_html_404_for_missing_sale(client):
    resp = client.get("/sales/999999/dokument.html")
    assert resp.status_code == 404


# --- /wyplata/dokument.html -------------------------------------------------

def test_wyplata_document_html_400_without_input(client):
    resp = client.get("/wyplata/dokument.html")
    assert resp.status_code == 400


def test_wyplata_document_html_renders_result(client, seed, _fake_nbp_rate):
    seed.lot("2024-01-05", 50, 3.0)

    resp = client.get(
        "/wyplata/dokument.html",
        query_string={"direction": "quantity", "wyplata_qty": "5", "wyplata_price": "8.0"})

    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    body = resp.get_data(as_text=True)
    assert body.lower().startswith("<!doctype html>")
    assert "Symulacja sprzedaży" in body


def test_wyplata_document_html_400_when_engine_errors(client, seed, _fake_nbp_rate):
    # za mało lotów -> withdrawal_view zwraca błąd, dokument NIE ma się
    # renderować z pustymi/nonsensownymi danymi
    seed.lot("2024-01-05", 1, 3.0)

    resp = client.get(
        "/wyplata/dokument.html",
        query_string={"direction": "quantity", "wyplata_qty": "500", "wyplata_price": "8.0"})

    assert resp.status_code == 400


# --- /pit38/dokumentacja.html -----------------------------------------------

def test_pit38_dossier_html_renders_empty_year(client):
    resp = client.get("/pit38/dokumentacja.html?year=2019")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    assert 'filename="pit38_dokumentacja_2019.html"' in resp.headers["Content-Disposition"]


def test_pit38_dossier_html_renders_with_sale(client, seed, _fake_nbp_rate):
    seed.lot("2024-01-05", 20, 3.0)
    seed.sale("2026-03-14", 5, 8.0)

    resp = client.get("/pit38/dokumentacja.html?year=2026")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "2026-03-14" in body


# --- .pdf: dostarczanie realnego pliku ---------------------------------------

@_requires_pdf
def test_sale_document_pdf_returns_valid_pdf(client, seed, _fake_nbp_rate):
    seed.lot("2024-01-05", 10, 5.0)
    seed.sale("2026-08-27", 4, 8.0)
    sale_id = seed.conn.execute(
        "SELECT id FROM sales ORDER BY id DESC LIMIT 1").fetchone()["id"]

    resp = client.get(f"/sales/{sale_id}/dokument.pdf")

    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.headers["Content-Disposition"].startswith("attachment")
    assert f'filename="sprzedaz_2026-08-27_id{sale_id}.pdf"' in resp.headers["Content-Disposition"]
    assert resp.data[:5] == b"%PDF-"


@_requires_pdf
def test_sale_document_pdf_404_for_missing_sale(client):
    resp = client.get("/sales/999999/dokument.pdf")
    assert resp.status_code == 404


@_requires_pdf
def test_wyplata_document_pdf_returns_valid_pdf(client, seed, _fake_nbp_rate):
    seed.lot("2024-01-05", 50, 3.0)

    resp = client.get(
        "/wyplata/dokument.pdf",
        query_string={"direction": "quantity", "wyplata_qty": "5", "wyplata_price": "8.0"})

    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.data[:5] == b"%PDF-"


@_requires_pdf
def test_wyplata_document_pdf_400_without_input(client):
    resp = client.get("/wyplata/dokument.pdf")
    assert resp.status_code == 400


@_requires_pdf
def test_pit38_dossier_pdf_returns_valid_pdf(client):
    resp = client.get("/pit38/dokumentacja.pdf?year=2019")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert 'filename="pit38_dokumentacja_2019.pdf"' in resp.headers["Content-Disposition"]
    assert resp.data[:5] == b"%PDF-"


# --- .pdf: degradacja, gdy silnik niedostępny (działa ZAWSZE, bez WeasyPrint) -

def test_sale_document_pdf_degrades_to_503_when_engine_unavailable(
        client, seed, _fake_nbp_rate, monkeypatch):
    seed.lot("2024-01-05", 10, 5.0)
    seed.sale("2026-08-27", 4, 8.0)
    sale_id = seed.conn.execute(
        "SELECT id FROM sales ORDER BY id DESC LIMIT 1").fetchone()["id"]
    monkeypatch.setattr("nokia_tracker.web._documents.exports_pdf.available", lambda: False)

    resp = client.get(f"/sales/{sale_id}/dokument.pdf")

    assert resp.status_code == 503
    assert resp.mimetype == "text/html"
    assert resp.headers["Cache-Control"] == "no-store"
    body = resp.get_data(as_text=True)
    assert "PDF" in body
    assert f"/sales/{sale_id}/dokument.html" in body


def test_pit38_dossier_pdf_degrades_to_503_when_engine_unavailable(client, monkeypatch):
    monkeypatch.setattr("nokia_tracker.web._documents.exports_pdf.available", lambda: False)
    resp = client.get("/pit38/dokumentacja.pdf?year=2019")
    assert resp.status_code == 503
    assert "/pit38/dokumentacja.html?year=2019" in resp.get_data(as_text=True)
