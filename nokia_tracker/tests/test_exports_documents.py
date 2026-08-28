"""E10 (docs/PLAN_E10_dokumenty.md), Etap 3 — dokumenty dowodowe HTML, bez
Flaska: `views/documents.py` (kompozycja) + `exports/documents.py`
(renderowanie samodzielnym środowiskiem Jinja). Sprawdza: brak `url_for` w
samodzielnym środowisku (mechanizm, nie tylko dzisiejsze szablony),
autoescape, odtwarzalność/czułość sumy kontrolnej, polskie znaki, i że
wszystkie trzy typy dokumentu renderują się bez wyjątku na danych z `seed`."""
from __future__ import annotations

import jinja2
import pytest

from nokia_tracker import settings as settingsm
from nokia_tracker.exports import documents as docexports
from nokia_tracker.views import documents as docviews


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "2026-07-27"))
    monkeypatch.setattr(
        "nokia_tracker.tax.whatif.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "2026-07-27"))


def _diacritics(html: str) -> bool:
    return any(ch in html for ch in "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")


# --- mechanizm: brak url_for w samodzielnym środowisku ---------------------

def test_standalone_env_has_no_url_for():
    # To jest sam MECHANIZM egzekwowania zakazu (docstring exports/documents.py)
    # — jeśli KTOKOLWIEK kiedyś doda url_for do szablonu doc_*.html, ten sam
    # błąd (UndefinedError) wybuchnie w teście render'ującym ten szablon,
    # zanim wybuchnie w pliku otwartym przez użytkownika za trzy lata.
    template = docexports._env.from_string("{{ url_for('sales_get') }}")
    with pytest.raises(jinja2.UndefinedError):
        template.render()


def test_standalone_env_autoescapes_html():
    template = docexports._env.from_string("{{ value }}")
    html = template.render(value="<script>alert(1)</script>")
    assert "&lt;script&gt;" in html
    assert "<script>alert" not in html


# --- sale_document -----------------------------------------------------

def test_sale_document_none_for_missing_sale(conn):
    cfg = settingsm.get_settings(conn)
    assert docviews.sale_document(conn, cfg, 999) is None


def test_sale_document_renders_self_contained_html(conn, seed):
    seed.lot("2024-01-05", 10, 5.0)
    seed.sale("2026-08-27", 4, 8.0, fee_eur=1.0)
    cfg = settingsm.get_settings(conn)
    sale_row = conn.execute("SELECT id FROM sales ORDER BY id DESC LIMIT 1").fetchone()

    doc = docviews.sale_document(conn, cfg, sale_row["id"])
    html = docexports.render_html("sale", doc)

    assert html.lower().startswith("<!doctype html>")
    assert "<style>" in html
    assert "url_for" not in html.lower()
    # Dopuszczalny <link> to WYŁĄCZNIE favicon jako data: URI (self-contained,
    # zero sieci) — link do arkusza stylów/skryptu na /static/ byłby martwy
    # poza aplikacją (patrz docstring exports/documents.py). Sprawdzamy
    # atrybuty href/src, NIE gołą podfrazę „/static/" — komentarz w doc.css
    # sam ją wspomina (wyjaśniając, czemu ten plik NIE jest tam serwowany).
    lower = html.lower()
    assert 'href="/static' not in lower
    assert 'src="/static' not in lower
    assert 'rel="stylesheet"' not in lower
    assert "hassio_ingress" not in html
    assert _diacritics(html)
    assert f"sha256:{doc['meta']['data_hash']}" in html


def test_sale_document_escapes_malicious_notes(conn, seed):
    seed.lot("2024-01-05", 10, 5.0)
    seed.sale("2026-08-27", 4, 8.0)
    cfg = settingsm.get_settings(conn)
    sale_row = conn.execute("SELECT id FROM sales ORDER BY id DESC LIMIT 1").fetchone()
    conn.execute("UPDATE sales SET notes = ? WHERE id = ?",
                 ("<script>alert(1)</script>", sale_row["id"]))
    conn.commit()

    doc = docviews.sale_document(conn, cfg, sale_row["id"])
    html = docexports.render_html("sale", doc)

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_sale_document_hash_stable_across_calls(conn, seed):
    seed.lot("2024-01-05", 10, 5.0)
    seed.sale("2026-08-27", 4, 8.0)
    cfg = settingsm.get_settings(conn)
    sale_id = conn.execute("SELECT id FROM sales ORDER BY id DESC LIMIT 1").fetchone()["id"]

    hash1 = docviews.sale_document(conn, cfg, sale_id)["meta"]["data_hash"]
    hash2 = docviews.sale_document(conn, cfg, sale_id)["meta"]["data_hash"]
    assert hash1 == hash2


def test_sale_document_hash_changes_with_override(conn, seed):
    seed.lot("2024-01-05", 10, 5.0)
    seed.sale("2026-08-27", 4, 8.0)
    cfg = settingsm.get_settings(conn)
    sale_id = conn.execute("SELECT id FROM sales ORDER BY id DESC LIMIT 1").fetchone()["id"]

    before = docviews.sale_document(conn, cfg, sale_id)["meta"]["data_hash"]
    conn.execute(
        "UPDATE sales SET reported_revenue_pln = 999.0, reported_cost_pln = 111.0 WHERE id = ?",
        (sale_id,))
    conn.commit()
    after = docviews.sale_document(conn, cfg, sale_id)["meta"]["data_hash"]
    assert before != after


def test_sale_document_shows_trace_failure_not_silent_omission(conn, seed, monkeypatch):
    # Kontrakt z planu (Etap 5 częściowo wyprzedzony tu, bo test jest tani):
    # rozjazd domykania ma być WIDOCZNY w dokumencie, nie po cichu pominięty.
    seed.lot("2024-01-05", 10, 5.0)
    seed.sale("2026-08-27", 4, 8.0)
    cfg = settingsm.get_settings(conn)
    sale_id = conn.execute("SELECT id FROM sales ORDER BY id DESC LIMIT 1").fetchone()["id"]

    from nokia_tracker import breakdown as bd
    fake_failure = bd.BreakdownNotClosedError("sprzedaz.tax", 50.0, 45.0)
    monkeypatch.setattr(
        "nokia_tracker.views.sales.bd.sale_traces",
        lambda conn, ctx, cfg, sale, detail: ({}, [fake_failure]))

    doc = docviews.sale_document(conn, cfg, sale_id)
    html = docexports.render_html("sale", doc)
    assert "nie domknął się do grosza" in html
    assert "sprzedaz.tax" in html


# --- simulation_document -------------------------------------------------

def _sim_params(**overrides):
    params = {
        "direction": "quantity", "sale_date": "2026-08-28",
        "price_eur": 8.0, "fee_pct": 0.0, "quantity": 5.0,
    }
    params.update(overrides)
    return params


def test_simulation_document_error_when_no_lots(conn):
    cfg = settingsm.get_settings(conn)
    doc, error = docviews.simulation_document(conn, cfg, _sim_params())
    assert doc is None
    assert error


def test_simulation_document_renders_self_contained_html(conn, seed):
    seed.lot("2024-01-05", 50, 3.0)
    cfg = settingsm.get_settings(conn)

    doc, error = docviews.simulation_document(conn, cfg, _sim_params())
    assert error is None
    html = docexports.render_html("simulation", doc)

    assert html.lower().startswith("<!doctype html>")
    assert "url_for" not in html.lower()
    assert _diacritics(html)
    assert "wyplata_qty=5.0" in doc["replay_query"]
    assert "wyplata_qty=5.0" in html


def test_simulation_document_target_direction(conn, seed):
    seed.lot("2024-01-05", 50, 3.0)
    cfg = settingsm.get_settings(conn)
    doc, error = docviews.simulation_document(
        conn, cfg, _sim_params(direction="target", target_net_pln=100.0, quantity=None))
    assert error is None
    assert "wyplata_target=100.0" in doc["replay_query"]
    html = docexports.render_html("simulation", doc)
    assert "projekcja" in html.lower()


# --- Etap 5: notka o nieznanym pliku wyciągu, zbiorcza nie per-wiersz ------

def test_doc_trace_consolidates_unknown_statement_footnote(conn, seed):
    # Dwa loty z importu PDF, natural_key spoza statement_index (świeży
    # seed nie ma statement_snapshots) -> provenance() da "Plik wyciągu
    # nieznany" DLA KAŻDEGO. Dokument ma to zwinąć do jednej notki z liczbą,
    # nie powtórzyć pełne zdanie w kolumnie Źródło per lot.
    seed.lot("2024-01-05", 5, 5.0, source="pdf_import", natural_key="nk-1")
    seed.lot("2024-02-05", 5, 5.0, source="pdf_import", natural_key="nk-2")
    seed.sale("2026-08-27", 10, 8.0)
    cfg = settingsm.get_settings(conn)
    sale_id = conn.execute("SELECT id FROM sales ORDER BY id DESC LIMIT 1").fetchone()["id"]

    doc = docviews.sale_document(conn, cfg, sale_id)
    html = docexports.render_html("sale", doc)

    # Surowe zdanie z provenance() w ogóle nie pojawia się per wiersz —
    # zastąpione kompaktowym markerem + jedną zbiorczą notką.
    assert "Plik wyciągu nieznany" not in html
    # 2 loty × 3 ślady z prowenniencją per lot (quantity, revenue_pln, cost)
    assert html.count("plik nieznany*") == 6
    assert "2 poz." in html


def test_doc_trace_no_footnote_when_all_sources_known(conn, seed):
    seed.lot("2024-01-05", 5, 5.0, source="manual")
    seed.sale("2026-08-27", 5, 8.0)
    cfg = settingsm.get_settings(conn)
    sale_id = conn.execute("SELECT id FROM sales ORDER BY id DESC LIMIT 1").fetchone()["id"]

    doc = docviews.sale_document(conn, cfg, sale_id)
    html = docexports.render_html("sale", doc)

    assert "Plik wyciągu nieznany" not in html
    assert "plik nieznany*" not in html


# --- year_dossier ----------------------------------------------------------

def test_year_dossier_renders_empty_year(conn):
    cfg = settingsm.get_settings(conn)
    doc = docviews.year_dossier(conn, cfg, 2019)
    html = docexports.render_html("dossier", doc)
    assert html.lower().startswith("<!doctype html>")
    assert "Brak sprzedaży" in html


def test_year_dossier_renders_with_sales_and_matches_totals(conn, seed):
    seed.lot("2024-01-05", 20, 3.0)
    seed.sale("2026-03-14", 5, 8.0)
    cfg = settingsm.get_settings(conn)

    doc = docviews.year_dossier(conn, cfg, 2026)
    html = docexports.render_html("dossier", doc)

    assert "url_for" not in html.lower()
    assert _diacritics(html)
    assert f"{doc['sales_totals']['net_pln']:.2f}" in html


# --- Etap 5: strażnik rozmiaru dla dużego roku ------------------------------

def test_year_dossier_includes_sale_traces_below_limit(conn, seed):
    seed.lot("2024-01-05", 20, 3.0)
    seed.sale("2026-03-14", 5, 8.0)
    cfg = settingsm.get_settings(conn)

    doc = docviews.year_dossier(conn, cfg, 2026)
    assert doc["include_sale_traces"] is True
    assert doc["sales"][0]["traces"]  # niepuste — pełny ślad obecny

    html = docexports.render_html("dossier", doc)
    assert "Pełny ślad FIFO per sprzedaż pominięty" not in html


def test_year_dossier_skips_sale_traces_above_limit(conn, seed, monkeypatch):
    monkeypatch.setattr("nokia_tracker.views.documents._DOSSIER_TRACE_ALLOCATION_LIMIT", 1)
    seed.lot("2024-01-05", 20, 3.0)
    seed.sale("2026-03-14", 5, 8.0)  # 1 alokacja
    seed.sale("2026-03-15", 5, 8.0)  # +1 alokacja = 2, przekracza limit=1
    cfg = settingsm.get_settings(conn)

    doc = docviews.year_dossier(conn, cfg, 2026)
    assert doc["include_sale_traces"] is False
    assert doc["allocation_count"] == 2
    assert all(not item["traces"] for item in doc["sales"])
    assert all(item["trace_failures"] == [] for item in doc["sales"])

    html = docexports.render_html("dossier", doc)
    assert "Pełny ślad FIFO per sprzedaż pominięty" in html
    assert "2 alokacji" in html
    # tabela zbiorcza (zawsze widoczna) zostaje nietknięta mimo strażnika
    assert "2026-03-14" in html
    assert "2026-03-15" in html
