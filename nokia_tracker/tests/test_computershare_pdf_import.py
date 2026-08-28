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

_VESTED_DIVIDEND_LINE = (
    "Vested  Dividend  Shares                                            20 Feb  2023"
    "                       4.48 EUR                     -1.43 EUR                             "
    "0.04                        0.56 PLN\n"
)


def _shares_summary_block(total_qty: float) -> str:
    # own (19.21982) + dividend_drip (0.19028) z _FULL_TEXT sumują się do 19.41010 -
    # wywołujący podaje total_qty jawnie, żeby testować zarówno zgodność jak i rozjazd.
    return f" {total_qty}                                                           1.0\n Shares                                      1.00 PLN            Share  inSuccess  Plan 2019-2026             1.00 PLN\n"

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


# --- krok 0.17.2: linia w kształcie wiersza dywidendy, która nie dopasowuje _DIVIDEND_RE
# (kolejna, jeszcze nieznana odmiana formatu) musi trafić do import_conflicts zamiast cicho
# zniknąć - dokładnie to zniknęło bez śladu w regresji naprawionej w 0.17.1, zanim ten
# guard w ogóle istniał.

# Wyprowadzona z _DIVIDEND_LINE (nie retypowana ręcznie) - popsuta kolumna Entitled
# Quantity (litera zamiast liczby) automatycznie dziedziczy realny układ kolumn, więc nie
# może pozostać nieaktualna, gdy _DIVIDEND_LINE zacznie śledzić inny realny wyciąg.
_BROKEN_DIVIDEND_LINE = _DIVIDEND_LINE.replace("61.491555", "X")

# Ta sama linia co _BROKEN_DIVIDEND_LINE, ale z innym paddingiem kolumn (pdfplumber nie
# gwarantuje identycznych szerokości między wyciągami) - klucz naturalny (krok 0.17.3:
# dwie daty, nie hash całej linii) musi wciąż zdeduplikować, mimo że bajty linii różnią się.
_BROKEN_DIVIDEND_LINE_DIFFERENT_PADDING = _BROKEN_DIVIDEND_LINE.replace(
    "X                  1.84", "X          1.84")


def test_import_statement_flags_unparsed_dividend_shaped_line(conn, monkeypatch):
    text = _HEADER + _BROKEN_DIVIDEND_LINE
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: text)

    report = cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")

    assert conn.execute("SELECT COUNT(*) c FROM dividends").fetchone()["c"] == 0
    conflicts = conn.execute(
        "SELECT * FROM import_conflicts WHERE entity_type = 'dividend_unparsed'").fetchall()
    assert len(conflicts) == 1
    assert report["rows_conflict"] >= 1


def test_reimporting_unparsed_dividend_line_does_not_duplicate_conflict_row(conn, monkeypatch):
    text = _HEADER + _BROKEN_DIVIDEND_LINE
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: text)

    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")
    cp.import_statement(conn, b"fake-pdf-bytes", "test2.pdf")

    conflicts = conn.execute(
        "SELECT * FROM import_conflicts WHERE entity_type = 'dividend_unparsed'").fetchall()
    assert len(conflicts) == 1


def test_reimporting_unparsed_dividend_line_with_different_padding_does_not_duplicate(
        conn, monkeypatch):
    # krok 0.17.3: klucz naturalny jest z pól (dwie daty), nie z hasha całej linii -
    # pdfplumber nie gwarantuje identycznego paddingu kolumn między wyciągami, więc
    # dedupe musi przetrwać różnicę w samych odstępach, nie tylko bajt-w-bajt identyczny
    # tekst (co poprzedni hash-owy klucz po cichu zakładał).
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: _HEADER + _BROKEN_DIVIDEND_LINE)
    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")

    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: _HEADER + _BROKEN_DIVIDEND_LINE_DIFFERENT_PADDING)
    cp.import_statement(conn, b"fake-pdf-bytes", "test2.pdf")

    conflicts = conn.execute(
        "SELECT * FROM import_conflicts WHERE entity_type = 'dividend_unparsed'").fetchall()
    assert len(conflicts) == 1


def test_import_statement_no_dividend_unparsed_conflict_for_well_formed_line(conn, _fake_pdf):
    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")

    conflicts = conn.execute(
        "SELECT * FROM import_conflicts WHERE entity_type = 'dividend_unparsed'").fetchall()
    assert len(conflicts) == 0


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
    # 0.27.0: "Vested Matching Shares" (0.48 szt.) i Withhold Typ A (101.396662 szt.)
    # opisują TO SAMO uwolnienie — musi powstać JEDEN lot 'matched', nie dwa (do 0.26.0
    # oba tworzyły osobne loty, podwójne księgowanie naprawione w tym kroku).
    assert conn.execute(
        "SELECT COUNT(*) c FROM lots WHERE lot_type = 'matched'").fetchone()["c"] == 1


def test_vested_matching_shares_row_without_same_day_withhold_still_creates_own_lot(
        conn, monkeypatch):
    # Kontrola negatywna dla powyższego: gdy WTC Typ A tego dnia NIE istnieje w wyciągu
    # (historyczne dopasowania sprzed wzorca "jeden zbiorczy wiersz" - np. 2023/2024 w
    # danych produkcyjnych), "Vested Matching Shares" zostaje jedynym źródłem i lot
    # musi powstać tak jak dziś (test_import_statement_creates_matched_lot_from_
    # vested_matching_shares_row musi przejść bez zmian - to jest ten sam scenariusz).
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: _HEADER + _VESTED_MATCHING_LINE)

    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")

    lot = conn.execute("SELECT * FROM lots WHERE lot_type = 'matched'").fetchone()
    assert lot is not None
    assert lot["quantity"] == 0.48


def test_multiple_vested_matching_rows_same_day_all_deduped_against_one_pool_lot(
        conn, monkeypatch):
    # Realny wzorzec produkcyjny (27.08.2026): CZTERY wiersze "Vested Matching Shares"
    # tego samego dnia (jedna transza każdy, kwoty zaokrąglone do 2 m.d.) + JEDEN wiersz
    # Withhold Typ A będący ich sumą (pełna precyzja) - wszystkie cztery muszą zostać
    # pominięte, powstaje wyłącznie lot puli.
    rows = [(19.29, 170.15), (29.24, 257.99), (17.37, 153.28), (28.99, 255.79)]
    matching_lines = "".join(
        f"Vested  Matching   Shares                                             27 Aug  2026"
        f"                        8.82 EUR                      1.40 EUR                               "
        f"{qty}                       {val} PLN\n"
        for qty, val in rows)
    pool_line = (
        "27 Aug2026                                            Nokia  Share                            "
        "94.89909                 8.82 EUR                 0.00 EUR                   0.00 EUR             "
        "94.89909\n"
    )
    text = _HEADER + matching_lines + pool_line
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: text)

    report = cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")

    matched_lots = conn.execute("SELECT * FROM lots WHERE lot_type = 'matched'").fetchall()
    assert len(matched_lots) == 1
    assert matched_lots[0]["quantity"] == 94.89909
    assert report["rows_conflict"] == 0


def test_reimporting_older_statement_without_withhold_row_skips_when_pool_lot_exists(
        conn, monkeypatch):
    # Re-import scenariusza: NAJPIERW wyciąg z pełnym WTC (tworzy lot puli), POTEM inny
    # (np. wcześniej wygenerowany) wyciąg, którego okno transakcji nie obejmuje tej daty,
    # więc pokazuje WYŁĄCZNIE snapshot "Vested Matching Shares" bez wiersza WTC - nie może
    # dołożyć drugiego lotu na to samo zdarzenie.
    same_day_withhold_a = (
        "28 Aug 2025                                            Nokia  Share                            "
        "101.396662                 3.71 EUR                 0.00 EUR                   0.00 EUR             "
        "101.396662\n"
    )
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: _HEADER + _VESTED_MATCHING_LINE + same_day_withhold_a)
    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")
    count_after_first = conn.execute(
        "SELECT COUNT(*) c FROM lots WHERE lot_type = 'matched'").fetchone()["c"]

    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: _HEADER + _VESTED_MATCHING_LINE)
    report2 = cp.import_statement(conn, b"fake-pdf-bytes", "test2.pdf")

    assert count_after_first == 1
    count_after_second = conn.execute(
        "SELECT COUNT(*) c FROM lots WHERE lot_type = 'matched'").fetchone()["c"]
    assert count_after_second == 1
    assert report2["rows_inserted"] == 0


def test_vested_matching_sum_exceeding_pool_records_conflict_instead_of_silent_skip(
        conn, monkeypatch):
    # Założenie "Vested Matching Shares to podzbiór puli WTC" złamane - Σ wierszy (100.00)
    # przekracza pulę (94.89909) o więcej niż tolerancja zaokrąglenia druku - to nie jest
    # duplikat do cichego pominięcia, tylko rozjazd wart zgłoszenia.
    oversized_matching_line = (
        "Vested  Matching   Shares                                             27 Aug  2026"
        "                        8.82 EUR                      1.40 EUR                               "
        "100.00                       882.00 PLN\n"
    )
    pool_line = (
        "27 Aug2026                                            Nokia  Share                            "
        "94.89909                 8.82 EUR                 0.00 EUR                   0.00 EUR             "
        "94.89909\n"
    )
    text = _HEADER + oversized_matching_line + pool_line
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: text)

    report = cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")

    assert report["rows_conflict"] >= 1
    conflicts = conn.execute(
        "SELECT * FROM import_conflicts WHERE entity_type = 'lot' AND resolved = 0").fetchall()
    assert any("vested_matching:2026-08-27" in c["natural_key"] for c in conflicts)
    # Lot puli mimo to powstaje normalnie - konflikt dotyczy tylko snapshotu, nie
    # blokuje zaksięgowania transakcji WTC samej w sobie.
    assert conn.execute(
        "SELECT COUNT(*) c FROM lots WHERE lot_type = 'matched'").fetchone()["c"] == 1


def test_withhold_type_a_matched_release_links_pending_espp_vests_via_pooled_lot_id(
        conn, monkeypatch):
    # Integracja z tax/grants.py::link_pooled_release - transze ESPP z ta samą
    # available_from co data uwolnienia muszą dostać pooled_lot_id, inaczej wracają
    # 'pending' na zawsze i integrity.py::_stale_pending_vest zgłosi fałszywy błąd (E9).
    matching_line = (
        "Vested  Matching   Shares                                             27 Aug  2026"
        "                        8.82 EUR                      1.40 EUR                               "
        "19.29                       170.15 PLN\n"
    )
    pool_line = (
        "27 Aug2026                                            Nokia  Share                            "
        "19.29                 8.82 EUR                 0.00 EUR                   0.00 EUR             "
        "19.29\n"
    )
    # Grant/transza ESPP z available_from == 27 Aug 2026 (kształt "Matching Shares" - jak
    # dziś w harmonogramie, przed uwolnieniem) - dodana w EARLIER (osobnym) imporcie, tak
    # jak w produkcji (transza była widoczna w harmonogramie zanim zvestowała).
    schedule_line = (
        f"Matching   Shares                                                      27 Jul 2026                      "
        f"1 Aug  2026                  27 Aug  2026                             19.29                     742.69  PLN\n"
    )
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: _HEADER + schedule_line)
    cp.import_statement(conn, b"fake-pdf-bytes", "test0.pdf")

    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: _HEADER + matching_line + pool_line)
    cp.import_statement(conn, b"fake-pdf-bytes", "test1.pdf")

    vest = conn.execute(
        "SELECT v.* FROM vests v JOIN grants g ON g.id = v.grant_id "
        "WHERE g.natural_key = 'espp_grant:2026-07-27:19.29'").fetchone()
    assert vest["status"] == "vested"
    assert vest["lot_id"] is None
    pooled_lot = conn.execute(
        "SELECT * FROM lots WHERE natural_key = 'vested_release:2026-08-27:8.82:19.29'"
    ).fetchone()
    assert vest["pooled_lot_id"] == pooled_lot["id"]


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


# ---- krok 19: Vested Dividend Shares jako źródło zapasowe (wyciągi 2022-2024 bez
# sekcji "Dividend (Reinvested)" transakcyjnej) ----

def test_import_statement_creates_drip_lot_from_vested_dividend_shares_when_no_transactions_section(
        conn, monkeypatch):
    text = _HEADER + _VESTED_DIVIDEND_LINE  # brak _DIVIDEND_LINE - jak realny wyciąg 2023/2024
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: text)

    report = cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")

    lot = conn.execute("SELECT * FROM lots WHERE lot_type = 'dividend_drip'").fetchone()
    assert lot is not None
    assert lot["acquired_date"] == "2023-02-20"
    assert lot["quantity"] == 0.04
    assert lot["price_eur"] == 4.48
    assert lot["source"] == "holdings_snapshot"
    assert report["rows_inserted"] >= 1


def test_reimporting_vested_dividend_shares_fallback_is_idempotent(conn, monkeypatch):
    text = _HEADER + _VESTED_DIVIDEND_LINE
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: text)

    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")
    count_after_first = conn.execute(
        "SELECT COUNT(*) c FROM lots WHERE lot_type = 'dividend_drip'").fetchone()["c"]

    report2 = cp.import_statement(conn, b"fake-pdf-bytes", "test2.pdf")

    assert count_after_first == 1
    count_after_second = conn.execute(
        "SELECT COUNT(*) c FROM lots WHERE lot_type = 'dividend_drip'").fetchone()["c"]
    assert count_after_second == 1
    assert report2["rows_inserted"] == 0
    assert report2["rows_unchanged"] >= 1


def test_import_statement_creates_estimated_dividend_row_for_vested_dividend_fallback(
        conn, monkeypatch):
    # krok 20: sekcja G 2022-2024 - "Vested Dividend Shares" nie ma Gross/Taxes/Fees,
    # więc odtwarzamy je zakładając 35% u źródła (jak w latach z pełnymi danymi).
    text = _HEADER + _VESTED_DIVIDEND_LINE
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: text)

    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf", cfg={"finnish_withholding_pct": 35.0})

    lot = conn.execute("SELECT * FROM lots WHERE lot_type = 'dividend_drip'").fetchone()
    div = conn.execute("SELECT * FROM dividends").fetchone()
    assert div is not None
    assert div["reinvested_lot_id"] == lot["id"]
    reinvested_eur = 0.04 * 4.48  # quantity * cost_basis_eur
    expected_gross = reinvested_eur / (1 - 0.35)
    assert div["gross_eur"] == pytest.approx(expected_gross)
    assert div["withholding_pct"] == pytest.approx(35.0)
    assert div["notes"] is not None
    assert "SZACUNEK" in div["notes"]
    # nie zdublowany lot
    assert conn.execute(
        "SELECT COUNT(*) c FROM lots WHERE lot_type = 'dividend_drip'").fetchone()["c"] == 1


def test_reimporting_estimated_dividend_row_is_idempotent(conn, monkeypatch):
    text = _HEADER + _VESTED_DIVIDEND_LINE
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: text)

    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")
    count_after_first = conn.execute("SELECT COUNT(*) c FROM dividends").fetchone()["c"]

    cp.import_statement(conn, b"fake-pdf-bytes", "test2.pdf")
    count_after_second = conn.execute("SELECT COUNT(*) c FROM dividends").fetchone()["c"]

    assert count_after_first == 1
    assert count_after_second == 1


def test_import_statement_skips_vested_dividend_fallback_when_transactions_section_present(
        conn, monkeypatch):
    # _FULL_TEXT ma _DIVIDEND_LINE (sekcja Dividend (Reinvested) obecna, jak w wyciągu 2025) -
    # dołączony wiersz Vested Dividend Shares NIE może utworzyć drugiego, zdublowanego lotu.
    text = _FULL_TEXT + _VESTED_DIVIDEND_LINE
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: text)

    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")

    drip_count = conn.execute(
        "SELECT COUNT(*) c FROM lots WHERE lot_type = 'dividend_drip'").fetchone()["c"]
    assert drip_count == 1  # tylko z Dividend (Reinvested); fallback pominięty


# ---- krok 19: kontrola krzyżowa salda (BLUEPRINT §3a) ----

def test_import_statement_auto_resolves_type_b_conflict_when_sale_already_booked(
        conn, monkeypatch):
    # krok 20: sprzedaż z 2025-10-27 zaksięgowana ręcznie w kroku 13.6, zanim przycisk
    # "Zatwierdź jako sprzedaż" istniał — konflikt Typu B z PIERWSZEGO importu (przed tą
    # poprawką) wisiał nierozstrzygnięty wiecznie. Symulujemy dokładnie ten stan
    # produkcyjny: stary nierozstrzygnięty konflikt już w bazie + sprzedaż już
    # zaksięgowana → PONOWNY import musi go oznaczyć rozwiązanym.
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    from nokia_tracker.tax import lots as taxlots
    import json as _json

    taxlots.add_lot(conn, "2020-01-01", "own", 1000.0, 1.0)
    taxlots.record_sale(conn, "2025-10-27", 784.0, 5.31, fee_eur=8.32)

    cur = conn.execute(
        "INSERT INTO imports (filename, file_sha256, period_start, period_end, as_of_date) "
        "VALUES ('old.pdf','xyz','2025-01-01','2026-01-01','2025-12-31')")
    old_import_id = cur.lastrowid
    incoming = {"execution_date": "2025-10-27", "quantity": 784.0,
                "sale_price_eur": 5.31, "net_proceeds_eur": 4153.15}
    conn.execute(
        "INSERT INTO import_conflicts (import_id, entity_type, natural_key, existing_json, "
        "incoming_json) VALUES (?, 'withhold_to_cover_sale', "
        "'wtc:2025-10-27:784.0:4153.15', '{}', ?)",
        (old_import_id, _json.dumps(incoming)))
    conn.commit()

    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: _FULL_TEXT)
    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")

    conflict = conn.execute(
        "SELECT * FROM import_conflicts WHERE entity_type = 'withhold_to_cover_sale'"
    ).fetchone()
    assert conflict is not None
    assert conflict["resolved"] == 1
    assert "już zaksięgowana" in conflict["resolution"]


def test_import_statement_type_b_still_flagged_when_sale_not_yet_booked(conn, _fake_pdf):
    # Regresja: gdy sprzedaż NIE jest jeszcze zaksięgowana, konflikt musi nadal
    # trafić do kolejki jako nierozstrzygnięty (zachowanie sprzed kroku 20).
    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")
    conflict = conn.execute(
        "SELECT * FROM import_conflicts WHERE entity_type = 'withhold_to_cover_sale'"
    ).fetchone()
    assert conflict is not None
    assert conflict["resolved"] == 0


def test_import_statement_no_conflict_when_balance_matches(conn, monkeypatch):
    # own (19.21982) + dividend_drip (0.19028) = 19.41010, zgodne z "Shares" w PDF.
    text = _HEADER + _shares_summary_block(19.41010) + _PURCHASE_LINE + _DIVIDEND_LINE
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: text)

    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")

    balance_conflicts = conn.execute(
        "SELECT COUNT(*) c FROM import_conflicts WHERE entity_type = 'balance'").fetchone()["c"]
    assert balance_conflicts == 0


def test_import_statement_flags_balance_mismatch(conn, monkeypatch):
    # "Shares" w PDF = 50, a baza po imporcie ma tylko 19.41010 - rozjazd musi trafić
    # do kolejki konfliktów, nie zniknąć po cichu.
    text = _HEADER + _shares_summary_block(50.0) + _PURCHASE_LINE + _DIVIDEND_LINE
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: text)

    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")

    conflict = conn.execute(
        "SELECT * FROM import_conflicts WHERE entity_type = 'balance'").fetchone()
    assert conflict is not None
    assert conflict["resolved"] == 0
    incoming = __import__("json").loads(conflict["incoming_json"])
    assert incoming["shares_total_from_pdf"] == 50.0


def test_reconcile_holdings_does_not_double_subtract_an_already_booked_sale(
        conn, monkeypatch):
    # krok 20: nawet gdyby konflikt Typu B jakimś trybem zostałby nierozstrzygnięty
    # mimo że sprzedaż już istnieje w `sales`, reconcile_holdings nie może odjąć jej
    # ilości drugi raz (qty_remaining już ją odzwierciedla).
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    from nokia_tracker.tax import lots as taxlots
    import json as _json

    taxlots.add_lot(conn, "2020-01-01", "own", 19.41010, 1.0)
    taxlots.record_sale(conn, "2025-10-27", 15.0, 5.31, fee_eur=8.32)
    # qty_remaining po sprzedaży = 19.41010 - 15 = 4.41010

    cur = conn.execute(
        "INSERT INTO imports (filename, file_sha256, period_start, period_end, as_of_date) "
        "VALUES ('x.pdf','abc','2025-01-01','2026-01-01','2026-01-01')")
    import_id = cur.lastrowid
    incoming = {"execution_date": "2025-10-27", "quantity": 15.0}
    conn.execute(
        "INSERT INTO import_conflicts (import_id, entity_type, natural_key, existing_json, "
        "incoming_json) VALUES (?, 'withhold_to_cover_sale', 'wtc:x', '{}', ?)",
        (import_id, _json.dumps(incoming)))
    conn.commit()

    text = (
        " 4.41010                                                           1.0\n"
        " Shares                                      1.00 PLN            Share  inSuccess  Plan 2019-2026             1.00 PLN\n"
    )
    cp.reconcile_holdings(conn, text, "2026-01-01", import_id)

    balance_conflicts = conn.execute(
        "SELECT COUNT(*) c FROM import_conflicts WHERE entity_type = 'balance'").fetchone()["c"]
    assert balance_conflicts == 0  # 4.41010 == 4.41010, bez podwójnego odjęcia 15


def test_reconcile_holdings_auto_resolves_stale_balance_conflict_once_reconciled(
        conn, monkeypatch):
    # krok 20 (fix): rozjazd salda z WCZEŚNIEJSZEGO, błędnego przebiegu (np. przed
    # naprawą podwójnego odejmowania) zostawiał wiecznie nierozstrzygnięty konflikt
    # w kolejce, nawet gdy kolejny import wykazał, że saldo się teraz zgadza -
    # znalezione na realnych danych po ponownym imporcie 5 plików pod 0.5.2.
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    from nokia_tracker.tax import lots as taxlots
    import json as _json

    taxlots.add_lot(conn, "2020-01-01", "own", 19.41010, 1.0)

    cur = conn.execute(
        "INSERT INTO imports (filename, file_sha256, period_start, period_end, as_of_date) "
        "VALUES ('old.pdf','xyz','2025-01-01','2026-01-01','2025-12-31')")
    old_import_id = cur.lastrowid
    conn.execute(
        "INSERT INTO import_conflicts (import_id, entity_type, natural_key, existing_json, "
        "incoming_json) VALUES (?, 'balance', 'balance:2025-12-31', '{}', '{}')",
        (old_import_id,))
    conn.commit()

    cur2 = conn.execute(
        "INSERT INTO imports (filename, file_sha256, period_start, period_end, as_of_date) "
        "VALUES ('new.pdf','abc','2025-01-01','2026-01-01','2026-01-01')")
    new_import_id = cur2.lastrowid
    conn.commit()

    text = (
        " 19.41010                                                           1.0\n"
        " Shares                                      1.00 PLN            Share  inSuccess  Plan 2019-2026             1.00 PLN\n"
    )
    result = cp.reconcile_holdings(conn, text, "2026-01-01", new_import_id)

    assert result is False  # nie zapisano NOWEGO konfliktu - saldo się zgadza
    stale = conn.execute(
        "SELECT resolved FROM import_conflicts WHERE natural_key = 'balance:2025-12-31'"
    ).fetchone()
    assert stale["resolved"] == 1


# --- E7: reconcile_holdings() zdelegowane do reconcile.reconcile() - konflikt powstaje
# przy rozjeździe KTÓREJKOLWIEK pozycji, nie tylko liczby akcji, i niesie pełną tabelę.

def test_reconcile_holdings_flags_conflict_on_restricted_units_mismatch_alone(
        conn, monkeypatch):
    # Liczba akcji się zgadza (0 == 0), ale RSU nie - to musi wystarczyć do zapisania
    # konfliktu (zmiana zachowania względem sprzed E7, ROADMAP_V3.md §E7).
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    from nokia_tracker.tax import grants as grantsm
    grant_id = grantsm.add_grant(conn, "espp", "2025-01-01", None, "grant:x")
    grantsm.add_vest(conn, grant_id, "2027-01-01", 40.0, "vest:x")  # daleko w przyszłości

    cur = conn.execute(
        "INSERT INTO imports (filename, file_sha256, as_of_date) VALUES ('x','x','2026-06-01')")
    import_id = cur.lastrowid
    conn.commit()

    text = (
        " 0.0                                                           1 266\n"
        " Shares                                      1.00 PLN            Share  inSuccess  Plan 2019-2026             1.00 PLN\n"
        " 999.0                                                         1 266\n"
        " Restricted stock units                     1.00 PLN            Restricted "
        "Shares                           1.00 PLN\n"
    )
    result = cp.reconcile_holdings(conn, text, "2026-06-01", import_id)

    assert result is True
    conflict = conn.execute(
        "SELECT * FROM import_conflicts WHERE entity_type = 'balance'").fetchone()
    assert conflict is not None
    existing = __import__("json").loads(conflict["existing_json"])
    positions = {p["key"]: p for p in existing["positions"]}
    assert positions["restricted_units"]["status"] == "mismatch"
    assert positions["shares"]["status"] == "ok"


# ---- krok 21: available_from zapisywane i uzupełniane przy re-imporcie ----

def test_import_statement_stores_available_from_on_espp_vest(conn, _fake_pdf):
    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")
    row = conn.execute(
        "SELECT v.available_from FROM vests v JOIN grants g ON g.id = v.grant_id "
        "WHERE g.program = 'espp'").fetchone()
    assert row["available_from"] == "2026-08-27"  # z _MATCHING_LINE


def test_import_statement_stores_available_from_on_lti_vest(conn, _fake_pdf):
    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")
    row = conn.execute(
        "SELECT v.available_from FROM vests v JOIN grants g ON g.id = v.grant_id "
        "WHERE g.program = 'lti'").fetchone()
    assert row["available_from"] == "2028-07-05"  # z _RS_AWARD_LINE


def test_reimporting_backfills_available_from_on_existing_vest(conn, monkeypatch):
    # Symuluje realny incydent z audytu: transza dodana przed krokiem 21 (add_vest
    # wywołane bez available_from, kolumna NULL), potem wgrywamy wyciąg ponownie -
    # add_vest zwraca wcześnie na istniejącym natural_key i NIC nie aktualizuje,
    # więc import_statement musi jawnie wywołać backfill_available_from.
    from nokia_tracker.tax import grants as grantsm
    grant_id = grantsm.add_grant(conn, "espp", "2025-10-27", 29.24, "espp_grant:2025-10-27:29.24")
    grantsm.add_vest(
        conn, grant_id, "2026-08-01", 29.24, "espp_vest:2025-10-27:2026-08-01:29.24")
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: _HEADER + _MATCHING_LINE)

    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")

    row = conn.execute(
        "SELECT available_from FROM vests WHERE natural_key = "
        "'espp_vest:2025-10-27:2026-08-01:29.24'").fetchone()
    assert row["available_from"] == "2026-08-27"


def test_import_statement_balance_check_skipped_when_importing_an_older_backfill(
        conn, monkeypatch):
    # Najpierw wgrywamy wyciąg 2026 (nowszy as_of_date) - staje się "najnowszym znanym
    # stanem". Potem wgrywamy wyciąg 2023 (starszy, celowo z rozjeżdżającym się saldem,
    # np. backfill danych historycznych) - kontrola NIE powinna się uruchomić, bo
    # porównanie starego zdjęcia salda z pełną, dzisiejszą bazą byłoby mylące.
    text_2026 = _HEADER + _shares_summary_block(19.41010) + _PURCHASE_LINE + _DIVIDEND_LINE
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: text_2026)
    cp.import_statement(conn, b"fake-pdf-bytes", "test-2026.pdf")

    older_header = "                1 Jan2023  - 1 Jan2024                     User  ID: 00000000\nas of 1 Jan2024\n"
    text_2023 = older_header + _shares_summary_block(999.0)  # rażąco błędne, celowo
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: text_2023)
    cp.import_statement(conn, b"different-bytes", "test-2023.pdf")

    balance_conflicts = conn.execute(
        "SELECT COUNT(*) c FROM import_conflicts WHERE entity_type = 'balance'").fetchone()["c"]
    assert balance_conflicts == 0


# --- E7: statement_snapshots — snapshot wyciągu zapisany przy każdym imporcie, pod
# uzgodnienie liczone na żądanie (reconcile.py) bez ponownego wgrywania PDF.

def test_import_statement_saves_a_statement_snapshot_row(conn, _fake_pdf):
    report = cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")
    row = conn.execute(
        "SELECT * FROM statement_snapshots WHERE import_id = ?",
        (report["import_id"],)).fetchone()
    assert row is not None
    assert row["as_of_date"] == "2026-07-26"
    payload = __import__("json").loads(row["snapshot_json"])
    assert payload["as_of_date"] == "2026-07-26"
    assert len(payload["dividends"]) == 1


def test_reimporting_same_file_upserts_snapshot_instead_of_duplicating(conn, _fake_pdf):
    cp.import_statement(conn, b"fake-pdf-bytes", "test.pdf")
    cp.import_statement(conn, b"fake-pdf-bytes", "test2.pdf")
    rows = conn.execute(
        "SELECT COUNT(*) c FROM statement_snapshots WHERE as_of_date = '2026-07-26'"
    ).fetchone()
    assert rows["c"] == 1


def test_import_statement_snapshot_is_never_written_when_as_of_date_is_missing(conn, monkeypatch):
    # Wyciąg bez rozpoznawalnej daty "as of" (format się nie dopasował) - snapshot bez
    # as_of_date byłby bezużyteczny dla reconcile() i złamałby UNIQUE(as_of_date) przy
    # drugim takim pliku - lepiej pominąć zapis niż zapisać śmieciowy wiersz.
    monkeypatch.setattr(
        "nokia_tracker.importers.computershare_pdf.extract_layout_text",
        lambda pdf_bytes: "tekst bez rozpoznawalnego nagłówka")
    cp.import_statement(conn, b"weird-bytes", "weird.pdf")
    assert conn.execute("SELECT COUNT(*) c FROM statement_snapshots").fetchone()["c"] == 0
