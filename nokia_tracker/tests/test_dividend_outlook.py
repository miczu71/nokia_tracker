"""Kalendarz i prognoza dywidend (krok 30, docs/PLAN_KROK_30_dywidendy.md).

Ta część (commit 2/8) pokrywa fundament: stawkę na akcję wyprowadzoną z REALNYCH
wypłat (`per_share_history`) i bazę uprawnioną dziś + przyszłe przyrosty
(`entitled_base`/`qty_on`). `calendar()` (scalanie zdarzeń + łańcuch sekcji G) jest
w test_dividend_outlook_calendar.py (commit 3/8).

Najważniejszy test tego pliku: `test_per_share_excludes_estimated_rows` — wiersze
odtworzone z "Vested Dividend Shares" mają w `quantity` liczbę akcji KUPIONYCH z
reinwestycji (~0,19), nie bazę uprawnioną (~61), a `gross_eur` odtworzone jako
`quantity * cena_akcji`. Naiwne `gross_eur/quantity` na takim wierszu daje cenę akcji
(~kilka EUR), nie stawkę dywidendy (~0,04 EUR) — ~150x za dużo. Odróżnia je `notes`
niepuste (`taxdiv.is_estimated()`)."""
from __future__ import annotations

import pytest

from nokia_tracker import dividend_outlook as outlook
from nokia_tracker.tax import dividends as taxdiv
from nokia_tracker.tax import grants as grantsm


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


def _real_dividend(conn, pay_date, entitled_qty, per_share_eur, withholding_pct=35.0):
    gross_eur = entitled_qty * per_share_eur
    taxes_eur = gross_eur * withholding_pct / 100
    taxdiv.add_dividend(conn, record_date=pay_date, entitled_quantity=entitled_qty,
                        gross_eur=gross_eur, taxes_eur=taxes_eur, fees_eur=0.0)


def _estimated_dividend(conn, pay_date, drip_shares, share_price_eur):
    """Wiersz odtworzony z Vested Dividend Shares — kształt jak w
    importers/computershare_pdf.py:589-625: `entitled_quantity` = akcje KUPIONE z
    reinwestycji, `gross_eur` odtworzone z założenia 35% u źródła, `notes` niepuste."""
    reinvested_eur = drip_shares * share_price_eur
    gross_eur = reinvested_eur / (1 - 0.35)
    taxes_eur = gross_eur - reinvested_eur
    taxdiv.add_dividend(conn, record_date=pay_date, entitled_quantity=drip_shares,
                        gross_eur=gross_eur, taxes_eur=taxes_eur, fees_eur=0.0,
                        notes="SZACUNEK: brutto/podatek odtworzone z założenia 35%")


# --- per_share_history(): stawka na akcję z realnych wypłat ---

def test_per_share_excludes_estimated_rows(conn):
    # 4 realne wypłaty transakcyjne, stawka 0.04 EUR/akcję.
    for pay_date, qty in [("2025-05-01", 600.0), ("2025-07-25", 620.0),
                          ("2025-10-24", 780.0), ("2026-01-30", 61.0)]:
        _real_dividend(conn, pay_date, qty, 0.04)
    # Wiersz szacunkowy obok — gdyby nie był wykluczony, gross_eur/quantity dałoby
    # cenę akcji (~6 EUR), nie stawkę dywidendy.
    _estimated_dividend(conn, "2024-08-01", drip_shares=0.19, share_price_eur=6.3)

    result = outlook.per_share_history(conn)

    assert result["excluded_estimated_count"] == 1
    assert result["per_share_eur"] == pytest.approx(0.04, abs=0.001)
    assert result["sufficient"] is True


def test_per_share_uses_median_not_mean(conn):
    # Mediana z [0.04, 0.04, 0.03, 0.04] = 0.04, nie średnia (0.0375).
    for pay_date, rate in [("2025-05-01", 0.04), ("2025-07-25", 0.04),
                           ("2025-10-24", 0.03), ("2026-01-30", 0.04)]:
        _real_dividend(conn, pay_date, 100.0, rate)

    result = outlook.per_share_history(conn)

    assert result["per_share_eur"] == pytest.approx(0.04, abs=0.001)


def test_per_share_reports_low_high_band(conn):
    for pay_date, rate in [("2025-05-01", 0.03), ("2025-07-25", 0.04),
                           ("2025-10-24", 0.04), ("2026-01-30", 0.04)]:
        _real_dividend(conn, pay_date, 100.0, rate)

    result = outlook.per_share_history(conn)

    assert result["per_share_low_eur"] == pytest.approx(0.03, abs=0.001)
    assert result["per_share_high_eur"] == pytest.approx(0.04, abs=0.001)
    assert result["last_per_share_eur"] == pytest.approx(0.04, abs=0.001)


def test_cadence_derived_as_quarterly(conn):
    for pay_date in ["2025-02-20", "2025-05-15", "2025-08-14", "2025-11-13"]:
        _real_dividend(conn, pay_date, 100.0, 0.04)

    result = outlook.per_share_history(conn)

    assert result["payments_per_year"] == 4


def test_per_share_insufficient_history_reports_reason_and_none_rate(conn):
    # Tylko 2 realne wypłaty — poniżej wymaganych 4.
    _real_dividend(conn, "2025-10-24", 100.0, 0.04)
    _real_dividend(conn, "2026-01-30", 100.0, 0.04)

    result = outlook.per_share_history(conn)

    assert result["sufficient"] is False
    assert result["per_share_eur"] is None
    assert result["payments_per_year"] is None
    assert result["reason"]  # niepusty tekst, nie tylko None


def test_per_share_history_empty_db_is_insufficient_not_a_crash(conn):
    result = outlook.per_share_history(conn)

    assert result["sufficient"] is False
    assert result["per_share_eur"] is None
    assert result["excluded_estimated_count"] == 0
    assert result["reason"]


# --- entitled_base() / qty_on(): baza uprawniona dziś + przyszłe przyrosty ---

def test_entitled_base_includes_restricted_own_lots_excludes_pending_vests(conn):
    # Lot `own` JEST posiadany (nawet jeśli ma ograniczenie zbycia — to osobna reguła
    # w tax/grants.py, nie dotyczy tego, czy dywidenda się należy).
    from nokia_tracker.tax import lots as taxlots
    taxlots.add_lot(conn, "2026-01-10", "own", 60.0, 5.0)

    # Transza `pending` (zablokowana, jeszcze nie na koncie) NIE jest dziś posiadana.
    grant_id = grantsm.add_grant(conn, "espp", "2026-01-01", quantity=None,
                                 natural_key="g1")
    grantsm.add_vest(conn, grant_id, vest_date="2026-12-01", quantity=30.0,
                     natural_key="v1", status="pending")

    result = outlook.entitled_base(conn, today="2026-06-01")

    assert result["held_qty"] == pytest.approx(60.0)
    assert len(result["tranches"]) == 1
    assert result["tranches"][0]["quantity"] == pytest.approx(30.0)
    assert result["tranches"][0]["effective_date"] == "2026-12-01"


def test_entitled_base_excludes_overdue_pending_tranches(conn):
    grant_id = grantsm.add_grant(conn, "espp", "2024-01-01", quantity=None,
                                 natural_key="g1")
    # Transza z effective_date w przeszłości względem `today` — zaległa.
    grantsm.add_vest(conn, grant_id, vest_date="2025-01-01", quantity=10.0,
                     natural_key="v_overdue", status="pending")
    grantsm.add_vest(conn, grant_id, vest_date="2027-01-01", quantity=20.0,
                     natural_key="v_future", status="pending")

    result = outlook.entitled_base(conn, today="2026-06-01")

    assert result["overdue_excluded_qty"] == pytest.approx(10.0)
    assert len(result["tranches"]) == 1
    assert result["tranches"][0]["quantity"] == pytest.approx(20.0)


def test_entitled_base_empty_db_returns_zero_not_a_crash(conn):
    result = outlook.entitled_base(conn, today="2026-06-01")

    assert result["held_qty"] == 0.0
    assert result["tranches"] == []
    assert result["overdue_excluded_qty"] == 0.0


# --- qty_on(): CZYSTA ---

def test_qty_on_includes_tranches_up_to_and_including_record_date():
    base = {"held_qty": 100.0, "tranches": [
        {"effective_date": "2026-06-01", "quantity": 50.0},
        {"effective_date": "2027-01-01", "quantity": 20.0},
    ]}

    assert outlook.qty_on(base, "2026-01-01") == pytest.approx(100.0)
    assert outlook.qty_on(base, "2026-06-01") == pytest.approx(150.0)
    assert outlook.qty_on(base, "2026-12-31") == pytest.approx(150.0)
    assert outlook.qty_on(base, "2027-01-01") == pytest.approx(170.0)


def test_qty_on_with_no_tranches_is_just_held_qty():
    base = {"held_qty": 42.0, "tranches": []}

    assert outlook.qty_on(base, "2030-01-01") == pytest.approx(42.0)
