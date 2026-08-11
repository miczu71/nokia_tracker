"""Rozbicie alokacji FIFO do numeru tabeli NBP (krok 16,
docs/PLAN_KROK_16_transparentnosc.md). Zero żywego HTTP — `fx_derivation`
i `enrich_allocations` czytają wyłącznie już zamrożone kolumny/lokalną
tabelę `nbp_rates`, nigdy nie wołają `fx_nbp.rate_for_event`."""
from __future__ import annotations

import pytest

from nokia_tracker.tax import lots as taxlots
from nokia_tracker.tax import trace as taxtrace

_CFG = {"cost_basis_policy": "own_only", "pl_capital_gains_tax_pct": 19.0}


def _seed_table_no(conn, effective_date: str, table_no: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO nbp_rates (date, rate, effective_date, table_no) "
        "VALUES (?, 4.0, ?, ?)", (effective_date, effective_date, table_no))
    conn.commit()


# ---- fx_derivation ----

def test_fx_derivation_ordinary_business_day(conn):
    # Sprzedaż we wtorek -> D-1 poniedziałek, publikacja dokładnie na D-1.
    _seed_table_no(conn, "2026-07-27", "143/A/NBP/2026")
    result = taxtrace.fx_derivation(conn, "2026-07-28", 4.3139, "2026-07-27", "sprzedaż")
    assert result["event_weekday"] == "wt"
    assert result["d_minus_1"] == "2026-07-27"
    assert result["d_minus_1_weekday"] == "pon"
    assert result["effective_date"] == "2026-07-27"
    assert result["table_no"] == "143/A/NBP/2026"
    assert result["urls"]["api"] == (
        "https://api.nbp.pl/api/exchangerates/rates/a/eur/2026-07-27/?format=json")
    assert "143/A/NBP/2026" in result["explanation_pl"]
    assert "brak publikacji" not in result["explanation_pl"]


def test_fx_derivation_monday_event_falls_back_to_friday(conn):
    # Sprzedaż w poniedziałek 27.10.2025 -> D-1 = niedziela (brak publikacji)
    # -> ostatnia opublikowana: piątek 24.10.2025 (dokładnie przykład z lots.py).
    _seed_table_no(conn, "2025-10-24", "207/A/NBP/2025")
    result = taxtrace.fx_derivation(conn, "2025-10-27", 4.2353, "2025-10-24", "sprzedaż")
    assert result["event_weekday"] == "pon"
    assert result["d_minus_1"] == "2025-10-26"
    assert result["d_minus_1_weekday"] == "nd"
    assert result["effective_date"] == "2025-10-24"
    assert result["effective_weekday"] == "pt"
    assert "brak publikacji" in result["explanation_pl"]
    assert "207/A/NBP/2025" in result["explanation_pl"]


def test_fx_derivation_missing_rate_explains_not_frozen_yet(conn):
    result = taxtrace.fx_derivation(conn, "2026-07-28", None, None, "nabycie")
    assert result["rate"] is None
    assert result["table_no"] is None
    assert "nie jest jeszcze zamrożony" in result["explanation_pl"]


def test_fx_derivation_missing_table_no_still_gives_rate_and_explanation(conn):
    # table_no nieznany (np. wiersz sprzed kroku 16, backfill jeszcze nie przeszedł)
    # — wyprowadzenie ma się nie wywalić, tylko pominąć numer tabeli.
    result = taxtrace.fx_derivation(conn, "2026-07-28", 4.3139, "2026-07-27", "sprzedaż")
    assert result["table_no"] is None
    assert result["urls"] is None
    assert "kurs 4,3139" in result["explanation_pl"]


# ---- enrich_allocations ----

def test_enrich_allocations_cost_eur_derived_from_frozen_pln(conn, monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event", lambda conn, d: (4.0, d))
    taxlots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    lot = taxlots.open_lots(conn)[0]

    allocations = [{"lot_id": lot["id"], "quantity": 4, "cost_pln": 80.0, "revenue_pln": 160.0}]
    sale_ctx = {"sale_date": "2026-07-28", "price_eur": 8.0, "fee_eur": 0.0,
                "quantity": 4, "nbp_rate": 4.0, "nbp_rate_date": "2026-07-27"}

    result = taxtrace.enrich_allocations(conn, allocations, sale_ctx, _CFG)
    detail = result["allocations"][0]
    # cost_eur * lot_nbp_rate musi dać z powrotem dokładnie cost_pln (spójność).
    assert detail["cost_eur"] * 4.0 == pytest.approx(80.0)
    assert detail["revenue_eur"] == pytest.approx(4 * 8.0)
    assert detail["lot_type"] == "own"
    assert "own_only" in detail["counted_in"]
    assert "all_at_acquisition" in detail["counted_in"]


def test_enrich_allocations_sums_match_sale_totals(conn, monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event", lambda conn, d: (4.0, d))
    taxlots.add_lot(conn, "2024-01-10", "own", 5, 5.0)
    taxlots.add_lot(conn, "2024-03-01", "lti", 5, 3.0)
    sale_id = taxlots.record_sale(conn, "2026-07-28", 8, 8.0)

    rows = conn.execute(
        "SELECT lot_id, quantity, cost_pln, revenue_pln FROM sale_allocations "
        "WHERE sale_id = ?", (sale_id,)).fetchall()
    allocations = [dict(r) for r in rows]
    sale_row = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()

    sale_ctx = {"sale_date": sale_row["sale_date"], "price_eur": sale_row["price_eur"],
                "fee_eur": sale_row["fee_eur"], "quantity": sale_row["quantity"],
                "nbp_rate": sale_row["nbp_rate"], "nbp_rate_date": sale_row["nbp_rate_date"]}
    result = taxtrace.enrich_allocations(conn, allocations, sale_ctx, _CFG)

    assert result["revenue_pln"] == pytest.approx(sale_row["revenue_pln"])
    assert sum(d["quantity"] for d in allocations) == pytest.approx(8)
    # own_only uznaje tylko lot 'own' (5 z 8) — koszt niższy niż all_at_acquisition,
    # które uznaje też 'lti'.
    assert (result["policies"]["own_only"]["cost_pln"]
            < result["policies"]["all_at_acquisition"]["cost_pln"])


def test_enrich_allocations_net_pln_uses_active_policy_tax(conn, monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event", lambda conn, d: (4.0, d))
    taxlots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    lot = taxlots.open_lots(conn)[0]
    allocations = [{"lot_id": lot["id"], "quantity": 10, "cost_pln": 200.0,
                    "revenue_pln": 320.0}]
    sale_ctx = {"sale_date": "2026-07-28", "price_eur": 8.0, "fee_eur": 0.0,
                "quantity": 10, "nbp_rate": 4.0, "nbp_rate_date": "2026-07-27"}
    result = taxtrace.enrich_allocations(conn, allocations, sale_ctx, _CFG)
    active_tax = result["policies"]["own_only"]["tax_pln"]
    assert result["net_pln"] == pytest.approx(result["revenue_pln"] - active_tax)
    assert result["net_eur"] == pytest.approx(result["revenue_eur"] - active_tax / 4.0)


# ---- krok 20: zgłoszona wartość nadpisuje totale, ale nie ślad per-lot ----

def test_enrich_allocations_reported_override_changes_totals_not_per_lot_trace(conn):
    taxlots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    lot = taxlots.open_lots(conn)[0]
    allocations = [{"lot_id": lot["id"], "quantity": 10, "cost_pln": 200.0,
                    "revenue_pln": 320.0}]
    sale_ctx = {"sale_date": "2026-07-28", "price_eur": 8.0, "fee_eur": 0.0,
                "quantity": 10, "nbp_rate": 4.0, "nbp_rate_date": "2026-07-27"}

    result = taxtrace.enrich_allocations(
        conn, allocations, sale_ctx, _CFG,
        reported={"reported_revenue_pln": 999.0, "reported_cost_pln": 111.0})

    assert result["revenue_pln"] == pytest.approx(999.0)
    assert result["revenue_pln_engine"] == pytest.approx(320.0)
    assert result["policies"]["own_only"]["cost_pln"] == pytest.approx(111.0)
    assert result["policies"]["own_only"]["cost_pln_engine"] == pytest.approx(200.0)
    assert result["is_reported_override"] is True
    # ślad per lot (co realnie wzięto) pozostaje niezmieniony
    assert result["allocations"][0]["cost_pln"] == pytest.approx(200.0)
    assert result["allocations"][0]["revenue_pln"] == pytest.approx(320.0)


def test_enrich_allocations_no_reported_override_is_unaffected(conn):
    taxlots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    lot = taxlots.open_lots(conn)[0]
    allocations = [{"lot_id": lot["id"], "quantity": 10, "cost_pln": 200.0,
                    "revenue_pln": 320.0}]
    sale_ctx = {"sale_date": "2026-07-28", "price_eur": 8.0, "fee_eur": 0.0,
                "quantity": 10, "nbp_rate": 4.0, "nbp_rate_date": "2026-07-27"}
    result = taxtrace.enrich_allocations(conn, allocations, sale_ctx, _CFG)
    assert result["is_reported_override"] is False
    assert result["revenue_pln"] == pytest.approx(result["revenue_pln_engine"])
