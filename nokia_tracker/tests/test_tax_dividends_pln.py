"""Sekcja G PIT-38 (krok 15): pl_tax_due_pln liczone w PLN na ZAMROŻONYM
kursie NBP (Record Date), nie na kursie bieżącym jak dotychczasowy
kalkulator orientacyjny compute_dividend_tax() z kroku 9 (test_tax.py).

Zamrożony jest kurs, NIE stawki procentowe — treaty/Belka z cfg stosowane
są w momencie przeliczenia, więc zmiana ustawień w UI ma się odzwierciedlić
natychmiast po ponownym backfillu, bez ponownego odpytywania NBP."""
from __future__ import annotations

import pytest

from nokia_tracker.tax import dividends as taxdiv

_CFG = {
    "treaty_withholding_pct": 15.0,
    "pl_capital_gains_tax_pct": 19.0,
    "finnish_withholding_pct": 35.0,
}


def test_compute_dividend_tax_pln_matches_blueprint_worked_example():
    # 100 PLN brutto (kurs 1 dla czytelności), 35% u źródła -> 65 netto,
    # zaliczenie 15, Belka 19 -> 4 PLN dopłaty, 20 PLN do odzysku z Vero.
    row = {"gross_pln": 100.0, "withholding_pct": 35.0}
    result = taxdiv.compute_dividend_tax_pln(row, _CFG)
    assert result["pl_tax_due_pln"] == pytest.approx(4.0)
    assert result["reclaimable_from_finland_pln"] == pytest.approx(20.0)


def test_compute_dividend_tax_pln_scales_with_frozen_rate():
    # Ten sam przykład, ale po kursie NBP 4.30 zamrożonym na Record Date.
    row = {"gross_pln": 100.0 * 4.30, "withholding_pct": 35.0}
    result = taxdiv.compute_dividend_tax_pln(row, _CFG)
    assert result["pl_tax_due_pln"] == pytest.approx(4.0 * 4.30)
    assert result["reclaimable_from_finland_pln"] == pytest.approx(20.0 * 4.30)


def test_compute_dividend_tax_pln_falls_back_to_cfg_withholding_when_row_missing_it():
    row = {"gross_pln": 100.0, "withholding_pct": None}
    result = taxdiv.compute_dividend_tax_pln(row, _CFG)
    assert result["pl_tax_due_pln"] == pytest.approx(4.0)


def test_compute_dividend_tax_pln_none_gross_pln_returns_none():
    row = {"gross_pln": None, "withholding_pct": 35.0}
    result = taxdiv.compute_dividend_tax_pln(row, _CFG)
    assert result["pl_tax_due_pln"] is None
    assert result["reclaimable_from_finland_pln"] is None


def test_compute_dividend_tax_pln_never_negative_when_withholding_below_treaty():
    # Rzadki przypadek: pobrano mniej niż stawka traktatowa (np. 10% < 15%
    # cap) — zaliczenie i tak nie może przekroczyć faktycznie pobranego.
    row = {"gross_pln": 100.0, "withholding_pct": 10.0}
    result = taxdiv.compute_dividend_tax_pln(row, _CFG)
    assert result["reclaimable_from_finland_pln"] == pytest.approx(0.0)
    # zaliczenie = min(10, 15) = 10 -> dopłata = 19 - 10 = 9
    assert result["pl_tax_due_pln"] == pytest.approx(9.0)


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


def _add_dividend(conn, **overrides):
    kwargs = dict(
        record_date="2026-01-30", purchase_date="2026-02-19",
        entitled_quantity=61.491555, gross_eur=100.0, taxes_eur=35.0, fees_eur=0.0,
        reinvested_eur=65.0, purchase_price_eur=6.3015, purchased_shares=0.19028)
    kwargs.update(overrides)
    return taxdiv.add_dividend(conn, **kwargs)


def test_backfill_pl_tax_due_fills_null_rows(conn):
    dividend_id = _add_dividend(conn)
    row = conn.execute("SELECT * FROM dividends WHERE id = ?", (dividend_id,)).fetchone()
    assert row["pl_tax_due_pln"] is None  # add_dividend samo w sobie zostawia NULL

    updated = taxdiv.backfill_pl_tax_due(conn, _CFG)
    assert updated == 1
    row = conn.execute("SELECT * FROM dividends WHERE id = ?", (dividend_id,)).fetchone()
    # gross_pln = 100 EUR * 4.0 (zamrożony kurs stub) = 400
    assert row["pl_tax_due_pln"] == pytest.approx(4.0 * 4.0)


def test_backfill_pl_tax_due_recomputes_on_settings_change_unlike_nbp_rate(conn):
    """Kurs NBP zamrożony na zawsze (jak w lots.py), ale stawki traktat/Belka
    NIE — backfill_pl_tax_due ma przeliczać KAŻDE wywołanie na aktualnym cfg,
    bo to tania arytmetyka (zero wywołań NBP), w odróżnieniu od
    backfill_missing_rates, które nigdy nie nadpisuje zamrożonego kursu."""
    dividend_id = _add_dividend(conn)
    taxdiv.backfill_pl_tax_due(conn, _CFG)
    row = conn.execute("SELECT * FROM dividends WHERE id = ?", (dividend_id,)).fetchone()
    first_value = row["pl_tax_due_pln"]

    changed_cfg = dict(_CFG, pl_capital_gains_tax_pct=0.0)
    taxdiv.backfill_pl_tax_due(conn, changed_cfg)
    row = conn.execute("SELECT * FROM dividends WHERE id = ?", (dividend_id,)).fetchone()
    assert row["pl_tax_due_pln"] == pytest.approx(0.0)
    assert row["pl_tax_due_pln"] != first_value

    # a kurs NBP samego lotu/dywidendy w międzyczasie pozostaje zamrożony
    assert row["nbp_rate"] == 4.0
    assert row["nbp_rate_date"] == "stub"


def test_add_dividend_without_drip_args_creates_no_lot(conn):
    # Krok 16: reinwestycja jest opcjonalna — dywidenda wypłacona gotówką
    # (formularz ręczny bez pól DRIP) nie tworzy lotu dividend_drip.
    dividend_id = taxdiv.add_dividend(
        conn, record_date="2026-01-30", entitled_quantity=61.491555,
        gross_eur=100.0, taxes_eur=35.0)
    row = conn.execute("SELECT * FROM dividends WHERE id = ?", (dividend_id,)).fetchone()
    assert row["reinvested_lot_id"] is None
    lots_count = conn.execute("SELECT COUNT(*) c FROM lots").fetchone()["c"]
    assert lots_count == 0


def test_add_dividend_with_drip_args_still_creates_lot(conn):
    # Ten sam formularz z wypełnionymi polami reinwestycji — zachowanie sprzed
    # kroku 16 (import PDF zawsze podaje te trzy pola razem).
    dividend_id = _add_dividend(conn)
    row = conn.execute("SELECT * FROM dividends WHERE id = ?", (dividend_id,)).fetchone()
    assert row["reinvested_lot_id"] is not None


def test_backfill_missing_dividend_rates_fills_null_rows(conn):
    # Symuluje dywidendę wpisaną przed ujednoliceniem formularza (surowy INSERT,
    # bez kursu NBP) — backfill ma ją dogonić tak jak tax/lots.py robi to dla lotów.
    conn.execute(
        "INSERT INTO dividends (pay_date, gross_eur, natural_key) "
        "VALUES ('2026-01-30', 100.0, 'manual:1')")
    conn.commit()
    filled = taxdiv.backfill_missing_dividend_rates(conn)
    assert filled == 1
    row = conn.execute(
        "SELECT nbp_rate, nbp_rate_date, gross_pln FROM dividends "
        "WHERE natural_key = 'manual:1'").fetchone()
    assert row["nbp_rate"] == 4.0
    assert row["gross_pln"] == pytest.approx(400.0)


def test_backfill_missing_dividend_rates_never_overwrites_frozen_rate(conn):
    dividend_id = _add_dividend(conn)
    row_before = conn.execute(
        "SELECT nbp_rate FROM dividends WHERE id = ?", (dividend_id,)).fetchone()
    filled = taxdiv.backfill_missing_dividend_rates(conn)
    assert filled == 0  # już zamrożone, nic do zrobienia
    row_after = conn.execute(
        "SELECT nbp_rate FROM dividends WHERE id = ?", (dividend_id,)).fetchone()
    assert row_after["nbp_rate"] == row_before["nbp_rate"]


def test_backfill_pl_tax_due_skips_rows_without_frozen_nbp_rate(conn):
    dividend_id = _add_dividend(conn, record_date="2026-01-30")
    conn.execute("UPDATE dividends SET gross_pln = NULL, nbp_rate = NULL WHERE id = ?",
                 (dividend_id,))
    conn.commit()
    updated = taxdiv.backfill_pl_tax_due(conn, _CFG)
    assert updated == 0
    row = conn.execute("SELECT pl_tax_due_pln FROM dividends WHERE id = ?",
                        (dividend_id,)).fetchone()
    assert row["pl_tax_due_pln"] is None
