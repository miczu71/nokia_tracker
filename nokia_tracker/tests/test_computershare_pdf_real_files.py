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
