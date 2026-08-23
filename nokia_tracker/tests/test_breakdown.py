"""E8 (docs/PLAN_E8_slad.md) — rdzeń `breakdown.py`: dataklasy, domykacze
(`close_sum`/`close_formula`), `BreakdownCtx`, `provenance()`. Testy dla
budowniczych 20 konkretnych śladów żyją w `test_breakdown_account.py` i
`test_breakdown_withdrawal.py` — tu wyłącznie prymitywy."""
from __future__ import annotations

import json

import pytest

from nokia_tracker import breakdown as bd
from nokia_tracker.tax import lots as taxlots


def test_close_sum_exact_match_closes_without_rounding_component():
    components = (bd.Component("a", 10.0), bd.Component("b", 5.0))
    result = bd.close_sum("k", "Etykieta", 15.0, "zł", "a + b", components)
    assert result.amount == 15.0
    assert result.recomputed == 15.0
    assert result.components == components


def test_close_sum_within_one_grosz_appends_rounding_component():
    components = (bd.Component("a", 10.0), bd.Component("b", 5.001))
    result = bd.close_sum("k", "Etykieta", 15.01, "zł", "a + b", components)
    assert len(result.components) == 3
    assert result.components[-1].label.startswith("zaokrąglenie")
    assert result.recomputed == pytest.approx(15.01, abs=0.001)


def test_close_sum_beyond_one_grosz_raises():
    components = (bd.Component("a", 10.0), bd.Component("b", 5.0))
    with pytest.raises(bd.BreakdownNotClosedError):
        bd.close_sum("k", "Etykieta", 15.05, "zł", "a + b", components)


def test_close_sum_ignores_none_valued_components_in_recompute():
    components = (bd.Component("a", 10.0), bd.Component("info", None, detail="nota"))
    result = bd.close_sum("k", "Etykieta", 10.0, "zł", "a", components)
    assert result.recomputed == 10.0
    assert result.components == components


def test_close_formula_compares_recomputed_to_shown():
    components = (bd.Component("ilość", 3.0), bd.Component("cena", 4.0))
    result = bd.close_formula("k", "Etykieta", 12.0, "EUR", "ilość × cena",
                              components, recomputed=3.0 * 4.0)
    assert result.amount == 12.0
    assert result.recomputed == 12.0


def test_close_formula_beyond_tolerance_raises():
    with pytest.raises(bd.BreakdownNotClosedError):
        bd.close_formula("k", "Etykieta", 12.0, "EUR", "formuła", (), recomputed=11.0)


def test_breakdown_not_closed_error_carries_key_and_amounts():
    with pytest.raises(bd.BreakdownNotClosedError) as exc_info:
        bd.close_formula("moja.kwota", "Etykieta", 12.0, "EUR", "f", (), recomputed=11.0)
    err = exc_info.value
    assert err.key == "moja.kwota"
    assert err.shown == 12.0
    assert err.recomputed == 11.0


def test_ctx_caches_lots_by_id(conn):
    lot_id = taxlots.add_lot(conn, "2020-01-01", "own", 10.0, 3.0, source="manual")
    ctx = bd.build_ctx(conn)
    assert ctx.lots_by_id[lot_id]["lot_type"] == "own"


def test_ctx_statement_index_empty_without_snapshots(conn):
    ctx = bd.build_ctx(conn)
    assert ctx.statement_index == {}


def test_ctx_statement_index_matches_natural_key_to_filename(conn):
    cur = conn.execute(
        "INSERT INTO imports (filename, file_sha256, rows_inserted) "
        "VALUES ('wyciag_2026-08-19.pdf', 'abc', 1)")
    import_id = cur.lastrowid
    snapshot = {
        "period_start": "2026-01-01", "period_end": "2026-08-19",
        "as_of_date": "2026-08-19",
        "pending_tranches": [{"natural_key": "espp_vest:2024-03-15:2025-03-15:12.0"}],
        "dividends": [], "purchases": [],
    }
    conn.execute(
        "INSERT INTO statement_snapshots (import_id, as_of_date, period_start, "
        "period_end, snapshot_json) VALUES (?,?,?,?,?)",
        (import_id, "2026-08-19", "2026-01-01", "2026-08-19", json.dumps(snapshot)))
    conn.commit()

    ctx = bd.build_ctx(conn)
    meta = ctx.statement_index["espp_vest:2024-03-15:2025-03-15:12.0"]
    assert meta["filename"] == "wyciag_2026-08-19.pdf"
    assert meta["as_of_date"] == "2026-08-19"


def test_provenance_manual_row():
    ctx = bd.BreakdownCtx(lots_by_id={}, statement_index={})
    sources = bd.provenance(ctx, {"source": "manual", "natural_key": None})
    assert len(sources) == 1
    assert sources[0].kind == "manual"


def test_provenance_pdf_import_without_snapshot_match_is_explicit():
    ctx = bd.BreakdownCtx(lots_by_id={}, statement_index={})
    sources = bd.provenance(ctx, {"source": "pdf_import", "natural_key": "purchase:2024-03-15:2024-03-20:12.0"})
    assert sources[0].kind == "pdf_import"
    assert sources[0].ref == "purchase:2024-03-15:2024-03-20:12.0"
    assert sources[1].kind == "statement"
    assert "nieznany" in sources[1].label


def test_provenance_pdf_import_with_snapshot_match_shows_filename():
    nk = "purchase:2024-03-15:2024-03-20:12.0"
    ctx = bd.BreakdownCtx(
        lots_by_id={},
        statement_index={nk: {"filename": "wyciag.pdf", "as_of_date": "2026-08-19",
                              "period_start": "2026-01-01", "period_end": "2026-08-19"}})
    sources = bd.provenance(ctx, {"source": "pdf_import", "natural_key": nk})
    assert sources[1].kind == "statement"
    assert "wyciag.pdf" in sources[1].label
