"""import_statement() - orkiestracja UPSERT z detekcją konfliktu (BLUEPRINT §3a, krok 13).

extract_layout_text() zamockowane na syntetyczny tekst (te same, zweryfikowane wcześniej
kształty linii co w test_computershare_pdf.py) - parse_*() i logika orkiestracji działają
NAPRAWDĘ, tylko krok "PDF bytes -> tekst" jest podstawiony, żeby nie potrzebować realnych
plików PDF w tym pliku testowym."""
from __future__ import annotations

import pytest

from nokia_tracker.importers import computershare_pdf as cp

_THIN = " "

_HEADER = "                1 Jan2026  - 26 Jul2026                     User  ID: 00000000\nas of 26 Jul2026\n"

_PURCHASE_LINE = (
    "  2 Feb  2026        24 Oct  2025         2 Feb 2026          4Feb  2026           "
    "105.39  EUR           0.00 EUR           0.00 EUR         0.00  EUR          "
    "5.48 EUR        19.21982           0.00 EUR\n"
)

_MATCHING_LINE = (
    f"Matching   Shares                                                      27 Oct 2025                      "
    f"1 Aug  2026                  27 Aug  2026                             29.24                     1{_THIN}036.84  PLN\n"
)

_RS_AWARD_LINE = (
    f"2025  RS AWARD    07-JUL-2025                                         7Jul 2025                     "
    f"5 Jul 2028                   5 Jul2028                          633.00                  14{_THIN}882.25  PLN\n"
)

_DIVIDEND_LINE = (
    "30 Jan 2026                       19 Feb  2026            61.491555                  1.84 EUR        "
    "0.64 EUR       0.00 EUR            1.20 EUR      6.3015  EUR          0.19028           0.00 EUR\n"
)

_WITHHOLD_A_LINE = (
    "9 Jul2026                                            Nokia  Share                            634                 "
    "10.22 EUR                 0.00 EUR                   0.00 EUR                     634\n"
)

_WITHHOLD_B_LINE = (
    "27 Oct 2025                                           784                               5.31 EUR              "
    "4 161.47 EUR                 0.00 EUR                    8.32 EUR           4 153.15  EUR\n"
)

_FULL_TEXT = (_HEADER + _PURCHASE_LINE + _MATCHING_LINE + _RS_AWARD_LINE + _DIVIDEND_LINE
             + _WITHHOLD_A_LINE + _WITHHOLD_B_LINE)


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


@pytest.fixture
def _fake_pdf(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: _FULL_TEXT)


def test_import_statement_inserts_all_row_types_first_time(conn, _fake_pdf):
    report = cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")

    assert conn.execute("SELECT COUNT(*) c FROM lots WHERE lot_type = 'own'").fetchone()["c"] == 1
    assert conn.execute(
        "SELECT COUNT(*) c FROM lots WHERE lot_type = 'dividend_drip'").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM grants").fetchone()["c"] == 2  # espp + lti
    assert conn.execute("SELECT COUNT(*) c FROM vests").fetchone()["c"] == 2
    assert conn.execute("SELECT COUNT(*) c FROM dividends").fetchone()["c"] == 1
    # Withhold Typ B -> zawsze do kolejki ręcznego potwierdzenia, nigdy nie księgowany
    conflicts = conn.execute(
        "SELECT * FROM import_conflicts WHERE entity_type = 'withhold_to_cover_sale'").fetchall()
    assert len(conflicts) == 1
    assert conflicts[0]["resolved"] == 0
    assert report["rows_inserted"] > 0
    assert report["import_id"] is not None


def test_import_statement_records_imports_audit_row(conn, _fake_pdf):
    report = cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")
    row = conn.execute(
        "SELECT * FROM imports WHERE id = ?", (report["import_id"],)).fetchone()
    assert row["filename"] == "test.pdf"
    assert row["period_start"] == "2026-01-01"
    assert row["period_end"] == "2026-07-26"
    assert row["as_of_date"] == "2026-07-26"
    assert row["rows_inserted"] == report["rows_inserted"]


def test_reimporting_same_file_gives_zero_inserted_all_unchanged(conn, _fake_pdf):
    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")
    lots_count_after_first = conn.execute("SELECT COUNT(*) c FROM lots").fetchone()["c"]

    report2 = cp.import_statement(conn, b"fake-pdf-bytes", "test2.pdf")

    assert report2["rows_inserted"] == 0
    assert report2["rows_unchanged"] > 0
    lots_count_after_second = conn.execute("SELECT COUNT(*) c FROM lots").fetchone()["c"]
    assert lots_count_after_second == lots_count_after_first  # zero duplikatów


def test_reimporting_withhold_type_b_does_not_duplicate_conflict_row(conn, _fake_pdf):
    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")
    cp.import_statement(conn, b"fake-pdf-bytes", "test2.pdf")
    conflicts = conn.execute(
        "SELECT * FROM import_conflicts WHERE entity_type = 'withhold_to_cover_sale'").fetchall()
    assert len(conflicts) == 1


def test_import_statement_modified_purchase_value_goes_to_conflicts_not_overwritten(conn, _fake_pdf, monkeypatch):
    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")
    original_lot = conn.execute(
        "SELECT * FROM lots WHERE lot_type = 'own'").fetchone()

    # Ten sam natural_key (contribution_date+trade_date+quantity), ale INNA cena - konflikt.
    modified_text = _FULL_TEXT.replace("5.48 EUR        19.21982", "9.99 EUR        19.21982")
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: modified_text)

    report = cp.import_statement(conn, b"different-bytes", "test3.pdf")

    assert report["rows_conflict"] >= 1
    unchanged_lot = conn.execute(
        "SELECT * FROM lots WHERE lot_type = 'own'").fetchone()
    assert unchanged_lot["price_eur"] == original_lot["price_eur"] == 5.48  # baza nietknięta
    conflict = conn.execute(
        "SELECT * FROM import_conflicts WHERE entity_type = 'lot'").fetchone()
    assert conflict is not None
    assert conflict["resolved"] == 0
