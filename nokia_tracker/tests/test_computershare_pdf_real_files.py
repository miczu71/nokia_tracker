"""Weryfikacja parsera na 5 REALNYCH plikach użytkownika (BLUEPRINT §5, krok 13 DoD).

Ten plik NIGDY nie zawiera ani nie commituje danych z /config/akcje_temp/ — tylko czyta
je z dysku LOKALNIE, w tej jednej instalacji. Repo miczu71/nokia_tracker jest publiczne
na GitHubie, więc te testy są bramkowane istnieniem katalogu i pomijane wszędzie indziej
(świeży klon, CI) — patrz docs/PLAN_KROK_13.md, sekcja o prywatności."""
from __future__ import annotations

from pathlib import Path

import pytest

from nokia_tracker.importers import computershare_pdf as cp

_AKCJE_DIR = Path("/config/akcje_temp")

pytestmark = pytest.mark.skipif(
    not _AKCJE_DIR.is_dir(), reason="/config/akcje_temp/ niedostępny w tym środowisku")


def _pdf_files() -> list[Path]:
    return sorted(_AKCJE_DIR.glob("*.pdf"))


def test_five_real_files_present():
    assert len(_pdf_files()) == 5


@pytest.mark.parametrize("pdf_path", _pdf_files() if _AKCJE_DIR.is_dir() else [])
def test_parses_without_errors_and_meta_present(pdf_path):
    text = cp.extract_layout_text(pdf_path.read_bytes())
    meta = cp.parse_document_meta(text)
    assert meta["period_start"] and meta["period_end"] and meta["as_of_date"]
    # Każdy z 5 wyciągów ma co najmniej zakup lub grant ESPP - żaden nie jest pusty.
    purchases = cp.parse_purchases(text)
    matching = cp.parse_matching_shares(text)
    assert purchases or matching


def test_total_purchase_events_match_manual_count_across_all_five_files():
    # Policzone ręcznie z surowego tekstu layout-mode przy budowie parsera
    # (docs/PLAN_KROK_13.md) - zabezpieczenie przed regresją parsera.
    expected_by_period_end = {
        "2023-01-01": 1,
        "2024-01-01": 4,
        "2025-01-01": 4,
        "2026-01-01": 8,
    }
    seen = {}
    for pdf_path in _pdf_files():
        text = cp.extract_layout_text(pdf_path.read_bytes())
        meta = cp.parse_document_meta(text)
        seen[meta["period_end"]] = len(cp.parse_purchases(text))
    for period_end, expected_count in expected_by_period_end.items():
        assert seen.get(period_end) == expected_count, period_end


def test_withhold_to_cover_type_b_real_sale_detected_in_2025_statement():
    """Regresja dla odkrycia z fazy planowania: 784 akcje / 4153.15 EUR netto w pliku
    o period_end=2026-01-01 muszą trafić do type_b (nigdy do zero-efektowego type_a)."""
    for pdf_path in _pdf_files():
        text = cp.extract_layout_text(pdf_path.read_bytes())
        meta = cp.parse_document_meta(text)
        if meta["period_end"] != "2026-01-01":
            continue
        type_a, type_b = cp.parse_withhold_to_cover(text)
        assert len(type_b) == 1
        assert type_b[0]["quantity"] == 784.0
        assert type_b[0]["net_proceeds_eur"] == 4153.15
        return
    pytest.fail("Nie znaleziono pliku z period_end=2026-01-01")


def test_reimporting_same_file_is_idempotent_at_parse_level():
    """Parsowanie tego samego pliku dwa razy daje identyczne wyniki (deterministyczność
    parsera - warunek konieczny dla idempotencji importu w import_statement())."""
    pdf_path = _pdf_files()[0]
    data = pdf_path.read_bytes()
    text1 = cp.extract_layout_text(data)
    text2 = cp.extract_layout_text(data)
    assert cp.parse_purchases(text1) == cp.parse_purchases(text2)


def test_vested_matching_shares_unique_rows_across_all_five_files():
    """Krok 13.6 (docs/PLAN_KROK_13_6_vesting_gap.md): tabela "Vested Matching Shares" to
    powtarzający się snapshot - te same krotki (data, cena, ilość) pojawiają się w kolejnych
    wyciągach. Po redukcji po (vested_date, cost_basis_eur, quantity) - dokładnie 9 unikalnych
    wierszy, suma ok. 154.77 akcji (policzone ręcznie z surowego tekstu przy diagnozie)."""
    seen: set[tuple] = set()
    for pdf_path in _pdf_files():
        text = cp.extract_layout_text(pdf_path.read_bytes())
        for row in cp.parse_vested_matching_shares(text):
            seen.add((row["vested_date"], row["cost_basis_eur"], row["quantity"]))
    assert len(seen) == 9
    assert sum(qty for _, _, qty in seen) == pytest.approx(154.77, abs=0.01)


def test_withhold_type_a_classified_matched_when_same_day_as_vested_matching_shares():
    """Plik z period_end=2026-01-01 ma dokładnie 1 wiersz Typ A (101.396662 @ 28 Aug 2025),
    tego samego dnia co wiersz "Vested Matching Shares" (0.48 @ 28 Aug 2025) w TYM SAMYM
    pliku - wspólna kohorta dopasowań ESPP, klasyfikacja 'matched'."""
    for pdf_path in _pdf_files():
        text = cp.extract_layout_text(pdf_path.read_bytes())
        meta = cp.parse_document_meta(text)
        if meta["period_end"] != "2026-01-01":
            continue
        type_a, _ = cp.parse_withhold_to_cover(text)
        vested_dates = {row["vested_date"] for row in cp.parse_vested_matching_shares(text)}
        assert len(type_a) == 1
        assert type_a[0]["quantity"] == pytest.approx(101.396662)
        assert type_a[0]["execution_date"] in vested_dates
        return
    pytest.fail("Nie znaleziono pliku z period_end=2026-01-01")


def test_withhold_type_a_classified_lti_when_no_same_day_vested_matching_shares():
    """Plik z period_end=2026-07-26 ma dokładnie 2 wiersze Typ A (634 i 2100 @ 9 Jul 2026),
    żaden dzień nie pokrywa się z żadnym wierszem "Vested Matching Shares" w tym pliku ->
    klasyfikacja 'lti' dla obu (uwolnienie transz RS Award)."""
    for pdf_path in _pdf_files():
        text = cp.extract_layout_text(pdf_path.read_bytes())
        meta = cp.parse_document_meta(text)
        if meta["period_end"] != "2026-07-26":
            continue
        type_a, _ = cp.parse_withhold_to_cover(text)
        vested_dates = {row["vested_date"] for row in cp.parse_vested_matching_shares(text)}
        assert len(type_a) == 2
        assert {row["quantity"] for row in type_a} == {634.0, 2100.0}
        assert all(row["execution_date"] not in vested_dates for row in type_a)
        return
    pytest.fail("Nie znaleziono pliku z period_end=2026-07-26")


def test_import_all_five_real_files_covers_the_784_share_sale(conn, monkeypatch):
    """Regresja dla incydentu z tej sesji: import_statement.record_sale(784) na 27.10.2025
    rzucał InsufficientLotsError, bo zvestowane dopasowania ESPP/transze LTI nigdy nie
    stawały się lotami. Po dodaniu parse_vested_matching_shares + reklasyfikacji Withhold
    Typu A - pełny sekwencyjny import wszystkich 5 plików (najstarszy->najnowszy, jak
    realne użycie) musi dać wystarczające pokrycie."""
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.30, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.30, "stub"))

    total_conflicts = 0
    for pdf_path in _pdf_files():
        report = cp.import_statement(conn, pdf_path.read_bytes(), pdf_path.name)
        total_conflicts += report["rows_conflict"]
    # Dokładnie 1 konflikt oczekiwany: Withhold-to-Cover Typ B (sprzedaż 784 akcji) -
    # zawsze do ręcznego potwierdzenia, nigdy nie księgowany automatycznie (patrz
    # test_withhold_to_cover_type_b_real_sale_detected_in_2025_statement powyżej).
    assert total_conflicts == 1

    from nokia_tracker.tax import lots as taxlots
    total_available = sum(r["qty_remaining"] for r in taxlots.open_lots(conn))
    assert total_available >= 784.0

    sale_id = taxlots.record_sale(conn, "2025-10-27", 784.0, 5.31, fee_eur=8.32)
    assert sale_id is not None


def test_reconcile_vesting_resolves_exactly_the_provable_tranches(conn, monkeypatch):
    """Krok 14 (docs/PLAN_KROK_14_vesting_reconcile.md): po pełnym imporcie 5 realnych plików,
    reconcile_vesting() musi rozwiązać DOKŁADNIE te transze, dla których dopasowanie ilości
    jest jednoznaczne (7.33 i 33.36 z ESPP Matching Shares, 634 i 2100 z LTI RS Award) —
    24.42/29.24/28.99/17.37 (ESPP) i 633/633 (LTI 2027/2028) muszą zostać 'pending', bo albo
    nie mają dokładnego odpowiednika w saldzie (24.42), albo ich data jeszcze nie nadeszła."""
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.30, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.30, "stub"))

    for pdf_path in _pdf_files():
        cp.import_statement(conn, pdf_path.read_bytes(), pdf_path.name)

    from nokia_tracker.tax import grants as grantsm
    resolved = grantsm.reconcile_vesting(conn, today="2026-07-28")
    assert resolved == 4

    vested_qty = {
        r["quantity"] for r in conn.execute(
            "SELECT quantity FROM vests WHERE status = 'vested'").fetchall()
    }
    assert vested_qty == {7.33, 33.36, 634.0, 2100.0}

    pending_rows = conn.execute(
        "SELECT quantity FROM vests WHERE status = 'pending'").fetchall()
    still_pending_qty = [r["quantity"] for r in pending_rows]
    # 633.0 występuje DWA razy (transze LTI 2027 i 2028, obie wciąż w przyszłości)
    assert sorted(still_pending_qty) == sorted([24.42, 29.24, 28.99, 17.37, 633.0, 633.0])


def test_import_statement_full_pipeline_on_real_files_reimport_gives_zero_inserted(conn, monkeypatch):
    """Pełny pipeline (BLUEPRINT §5 DoD kroku 13): import_statement() na realnym pliku
    parsuje bez błędów i zapisuje do bazy; ponowny import TEGO SAMEGO pliku daje
    rows_inserted=0, rows_unchanged=N - zero duplikatów w lots/grants/vests/dividends."""
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.30, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.30, "stub"))

    pdf_path = _pdf_files()[-1]  # najnowszy, najbardziej złożony wyciąg (8 stron)
    data = pdf_path.read_bytes()

    report1 = cp.import_statement(conn, data, pdf_path.name)
    assert report1["rows_inserted"] > 0
    assert report1["rows_conflict"] == 0

    lots_after_first = conn.execute("SELECT COUNT(*) c FROM lots").fetchone()["c"]
    grants_after_first = conn.execute("SELECT COUNT(*) c FROM grants").fetchone()["c"]

    report2 = cp.import_statement(conn, data, pdf_path.name + "-again")

    # Niezmiennik idempotencji: ponowny import nie tworzy NIC nowego. Nie porównujemy
    # report2["rows_unchanged"] wprost z report1["rows_inserted"] - granty LTI z wieloma
    # transzami (ten sam natural_key dotykany kilka razy w JEDNYM imporcie) legalnie
    # rozkładają się inaczej na inserted/unchanged w pierwszym przebiegu niż w drugim,
    # mimo że łączna liczba dotkniętych faktów jest identyczna.
    assert report2["rows_inserted"] == 0
    assert report2["rows_conflict"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM lots").fetchone()["c"] == lots_after_first
    assert conn.execute("SELECT COUNT(*) c FROM grants").fetchone()["c"] == grants_after_first
