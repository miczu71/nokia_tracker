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

_VESTED_MATCHING_LINE = (
    "Vested  Matching   Shares                                             28 Aug  2025"
    "                        3.71 EUR                      4.51 EUR                               "
    "0.48                       16.98 PLN\n"
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


def test_import_statement_creates_matched_lot_from_vested_matching_shares_row(conn, monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: _HEADER + _VESTED_MATCHING_LINE)

    report = cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")

    lot = conn.execute(
        "SELECT * FROM lots WHERE lot_type = 'matched'").fetchone()
    assert lot is not None
    assert lot["acquired_date"] == "2025-08-28"
    assert lot["quantity"] == 0.48
    assert lot["price_eur"] == 3.71
    assert lot["source"] == "pdf_import"
    assert report["rows_inserted"] >= 1


def test_reimporting_vested_matching_shares_is_idempotent(conn, monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: _HEADER + _VESTED_MATCHING_LINE)

    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")
    count_after_first = conn.execute(
        "SELECT COUNT(*) c FROM lots WHERE lot_type = 'matched'").fetchone()["c"]

    report2 = cp.import_statement(conn, b"fake-pdf-bytes", "test2.pdf")

    assert count_after_first == 1
    count_after_second = conn.execute(
        "SELECT COUNT(*) c FROM lots WHERE lot_type = 'matched'").fetchone()["c"]
    assert count_after_second == 1
    assert report2["rows_inserted"] == 0
    assert report2["rows_unchanged"] >= 1


def test_import_statement_withhold_type_a_creates_lti_lot_when_no_vested_matching_same_date(
        conn, _fake_pdf):
    # _FULL_TEXT ma _WITHHOLD_A_LINE (2026-07-09) bez żadnego wiersza "Vested Matching
    # Shares" tego samego dnia -> klasyfikacja 'lti' (uwolnienie RS Award/LTI).
    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")

    lot = conn.execute("SELECT * FROM lots WHERE lot_type = 'lti'").fetchone()
    assert lot is not None
    assert lot["acquired_date"] == "2026-07-09"
    assert lot["quantity"] == 634.0
    assert lot["price_eur"] == 10.22
    assert lot["source"] == "pdf_import"


def test_import_statement_withhold_type_a_creates_matched_lot_when_date_coincides_with_vested_matching(
        conn, monkeypatch):
    # Withhold Typ A z 28 Aug 2025 + "Vested Matching Shares" TEGO SAMEGO dnia w tym samym
    # wyciągu -> wspólna kohorta dopasowań ESPP, klasyfikacja 'matched', nie 'lti'.
    same_day_withhold_a = (
        "28 Aug 2025                                            Nokia  Share                            "
        "101.396662                 3.71 EUR                 0.00 EUR                   0.00 EUR             "
        "101.396662\n"
    )
    text = _HEADER + _VESTED_MATCHING_LINE + same_day_withhold_a
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: text)

    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")

    lot = conn.execute(
        "SELECT * FROM lots WHERE quantity = 101.396662").fetchone()
    assert lot is not None
    assert lot["lot_type"] == "matched"
    assert lot["acquired_date"] == "2025-08-28"


def test_reimporting_withhold_type_a_lot_is_idempotent(conn, _fake_pdf):
    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")
    count_after_first = conn.execute(
        "SELECT COUNT(*) c FROM lots WHERE lot_type = 'lti'").fetchone()["c"]

    report2 = cp.import_statement(conn, b"fake-pdf-bytes", "test2.pdf")

    assert count_after_first == 1
    count_after_second = conn.execute(
        "SELECT COUNT(*) c FROM lots WHERE lot_type = 'lti'").fetchone()["c"]
    assert count_after_second == 1
    assert report2["rows_inserted"] == 0


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
