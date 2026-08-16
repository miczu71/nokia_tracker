"""Ogłoszony harmonogram dywidend — CRUD i dopasowanie do realnych wypłat
(krok 30, docs/PLAN_KROK_30_dywidendy.md, commit 4/8).

`reconcile_schedule()` jest wzorowane na `tax/grants.py::reconcile_vesting` —
ten sam kontrakt: dopasuj TYLKO gdy jednoznaczne, w przeciwnym razie zostaw
niedopasowaną. Nigdy zgadywania."""
from __future__ import annotations

import pytest

from nokia_tracker import dividend_outlook as outlook
from nokia_tracker.tax import dividends as taxdiv


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


# --- add_instalment(): UPSERT po (fiscal_year, instalment) ---

def test_add_instalment_inserts_new_row(conn):
    schedule_id = outlook.add_instalment(
        conn, fiscal_year=2026, instalment=1, record_date="2026-05-01",
        gross_per_share_eur=0.04)

    rows = outlook.list_schedule(conn)
    assert len(rows) == 1
    assert rows[0]["id"] == schedule_id
    assert rows[0]["dates_confirmed"] == 0


def test_add_instalment_upserts_same_fiscal_year_and_instalment(conn):
    id1 = outlook.add_instalment(
        conn, fiscal_year=2026, instalment=1, record_date="2026-05-01",
        gross_per_share_eur=0.04, dates_confirmed=False)
    id2 = outlook.add_instalment(
        conn, fiscal_year=2026, instalment=1, record_date="2026-05-04",
        gross_per_share_eur=0.045, dates_confirmed=True)

    assert id1 == id2
    rows = outlook.list_schedule(conn)
    assert len(rows) == 1
    assert rows[0]["record_date"] == "2026-05-04"
    assert rows[0]["dates_confirmed"] == 1


def test_add_instalment_different_instalments_stay_separate(conn):
    outlook.add_instalment(conn, fiscal_year=2026, instalment=1,
                           record_date="2026-02-01", gross_per_share_eur=0.04)
    outlook.add_instalment(conn, fiscal_year=2026, instalment=2,
                           record_date="2026-05-01", gross_per_share_eur=0.04)

    assert len(outlook.list_schedule(conn)) == 2


# --- delete_instalment() ---

def test_delete_instalment_removes_row(conn):
    schedule_id = outlook.add_instalment(
        conn, fiscal_year=2026, instalment=1, record_date="2026-05-01",
        gross_per_share_eur=0.04)

    result = outlook.delete_instalment(conn, schedule_id)

    assert result is True
    assert outlook.list_schedule(conn) == []


def test_delete_instalment_unknown_id_returns_false(conn):
    assert outlook.delete_instalment(conn, 9999) is False


# --- reconcile_schedule() ---

def test_reconcile_matches_exact_record_date(conn):
    outlook.add_instalment(conn, fiscal_year=2026, instalment=1,
                           record_date="2026-01-30", gross_per_share_eur=0.03)
    taxdiv.add_dividend(conn, record_date="2026-01-30", entitled_quantity=61.49,
                        gross_eur=1.84, taxes_eur=0.64, fees_eur=0.0)

    resolved = outlook.reconcile_schedule(conn, today="2026-02-01")

    assert resolved == 1
    row = outlook.list_schedule(conn)[0]
    assert row["matched_dividend_id"] is not None


def test_reconcile_matches_within_five_day_window_when_unambiguous(conn):
    outlook.add_instalment(conn, fiscal_year=2026, instalment=1,
                           record_date="2026-01-30", gross_per_share_eur=0.03)
    taxdiv.add_dividend(conn, record_date="2026-02-02", entitled_quantity=61.49,
                        gross_eur=1.84, taxes_eur=0.64, fees_eur=0.0)

    resolved = outlook.reconcile_schedule(conn, today="2026-02-05")

    assert resolved == 1


def test_reconcile_does_not_match_beyond_five_day_window(conn):
    outlook.add_instalment(conn, fiscal_year=2026, instalment=1,
                           record_date="2026-01-30", gross_per_share_eur=0.03)
    taxdiv.add_dividend(conn, record_date="2026-02-10", entitled_quantity=61.49,
                        gross_eur=1.84, taxes_eur=0.64, fees_eur=0.0)

    resolved = outlook.reconcile_schedule(conn, today="2026-02-15")

    assert resolved == 0
    assert outlook.list_schedule(conn)[0]["matched_dividend_id"] is None


def test_reconcile_leaves_ambiguous_match_unresolved(conn):
    outlook.add_instalment(conn, fiscal_year=2026, instalment=1,
                           record_date="2026-01-30", gross_per_share_eur=0.03)
    # dwóch kandydatów w oknie +-5 dni - niejednoznaczne, nie zgadujemy.
    taxdiv.add_dividend(conn, record_date="2026-01-28", entitled_quantity=61.49,
                        gross_eur=1.84, taxes_eur=0.64, fees_eur=0.0)
    taxdiv.add_dividend(conn, record_date="2026-02-02", entitled_quantity=61.49,
                        gross_eur=1.84, taxes_eur=0.64, fees_eur=0.0)

    resolved = outlook.reconcile_schedule(conn, today="2026-02-05")

    assert resolved == 0


def test_reconcile_is_idempotent(conn):
    outlook.add_instalment(conn, fiscal_year=2026, instalment=1,
                           record_date="2026-01-30", gross_per_share_eur=0.03)
    taxdiv.add_dividend(conn, record_date="2026-01-30", entitled_quantity=61.49,
                        gross_eur=1.84, taxes_eur=0.64, fees_eur=0.0)

    first = outlook.reconcile_schedule(conn, today="2026-02-01")
    second = outlook.reconcile_schedule(conn, today="2026-02-01")

    assert first == 1
    assert second == 0


def test_reconcile_never_matches_same_dividend_twice(conn):
    outlook.add_instalment(conn, fiscal_year=2026, instalment=1,
                           record_date="2026-01-30", gross_per_share_eur=0.03)
    outlook.add_instalment(conn, fiscal_year=2026, instalment=2,
                           record_date="2026-01-31", gross_per_share_eur=0.03)
    taxdiv.add_dividend(conn, record_date="2026-01-30", entitled_quantity=61.49,
                        gross_eur=1.84, taxes_eur=0.64, fees_eur=0.0)

    resolved = outlook.reconcile_schedule(conn, today="2026-02-05")

    rows = outlook.list_schedule(conn)
    matched = [r for r in rows if r["matched_dividend_id"] is not None]
    assert len(matched) == 1  # tylko jedna rata dostała tę samą dywidendę
    assert resolved == 1


def test_matched_row_reconciles_again_without_change(conn):
    outlook.add_instalment(conn, fiscal_year=2026, instalment=1,
                           record_date="2026-01-30", gross_per_share_eur=0.03)
    taxdiv.add_dividend(conn, record_date="2026-01-30", entitled_quantity=61.49,
                        gross_eur=1.84, taxes_eur=0.64, fees_eur=0.0)
    outlook.reconcile_schedule(conn, today="2026-02-01")

    # kolejny import/reconcile nie może wywalić się na już dopasowanym wierszu
    resolved_again = outlook.reconcile_schedule(conn, today="2026-06-01")

    assert resolved_again == 0
