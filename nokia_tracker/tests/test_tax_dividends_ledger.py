"""add_dividend (krok 13) - zapis dywidendy + auto-utworzenie lotu dividend_drip.
Osobno od test_tax.py, który pokrywa compute_dividend_tax() (kalkulator na BIEŻĄCYCH
ustawieniach, bez zmian od kroku 9)."""
from __future__ import annotations

import pytest

from nokia_tracker.tax import dividends as taxdiv


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


def test_add_dividend_computes_withholding_pct_from_real_taxes_gross(conn):
    dividend_id = taxdiv.add_dividend(
        conn, record_date="2026-01-30", purchase_date="2026-02-19",
        entitled_quantity=61.491555, gross_eur=1.84, taxes_eur=0.64, fees_eur=0.0,
        reinvested_eur=1.20, purchase_price_eur=6.3015, purchased_shares=0.19028)
    row = conn.execute("SELECT * FROM dividends WHERE id = ?", (dividend_id,)).fetchone()
    assert row["gross_eur"] == 1.84
    assert row["withholding_pct"] == pytest.approx(0.64 / 1.84 * 100)
    assert row["withholding_paid_eur"] == pytest.approx(0.64)
    assert row["net_received_eur"] == pytest.approx(1.20)


def test_add_dividend_freezes_nbp_rate_on_record_date(conn):
    dividend_id = taxdiv.add_dividend(
        conn, record_date="2026-01-30", purchase_date="2026-02-19",
        entitled_quantity=61.491555, gross_eur=1.84, taxes_eur=0.64, fees_eur=0.0,
        reinvested_eur=1.20, purchase_price_eur=6.3015, purchased_shares=0.19028)
    row = conn.execute("SELECT * FROM dividends WHERE id = ?", (dividend_id,)).fetchone()
    assert row["nbp_rate"] == 4.0
    assert row["nbp_rate_date"] == "stub"
    assert row["gross_pln"] == pytest.approx(1.84 * 4.0)


def test_add_dividend_creates_linked_dividend_drip_lot(conn):
    dividend_id = taxdiv.add_dividend(
        conn, record_date="2026-01-30", purchase_date="2026-02-19",
        entitled_quantity=61.491555, gross_eur=1.84, taxes_eur=0.64, fees_eur=0.0,
        reinvested_eur=1.20, purchase_price_eur=6.3015, purchased_shares=0.19028)
    row = conn.execute("SELECT * FROM dividends WHERE id = ?", (dividend_id,)).fetchone()
    assert row["reinvested_lot_id"] is not None
    lot = conn.execute(
        "SELECT * FROM lots WHERE id = ?", (row["reinvested_lot_id"],)).fetchone()
    assert lot["lot_type"] == "dividend_drip"
    assert lot["quantity"] == pytest.approx(0.19028)
    assert lot["price_eur"] == pytest.approx(6.3015)
    assert lot["acquired_date"] == "2026-02-19"


def test_add_dividend_idempotent_on_natural_key(conn):
    kwargs = dict(
        record_date="2026-01-30", purchase_date="2026-02-19",
        entitled_quantity=61.491555, gross_eur=1.84, taxes_eur=0.64, fees_eur=0.0,
        reinvested_eur=1.20, purchase_price_eur=6.3015, purchased_shares=0.19028)
    first = taxdiv.add_dividend(conn, **kwargs)
    second = taxdiv.add_dividend(conn, **kwargs)
    assert first == second
    assert conn.execute("SELECT COUNT(*) c FROM dividends").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM lots").fetchone()["c"] == 1


def test_add_dividend_pl_tax_due_left_null_for_krok14(conn):
    """Zaliczenie stawki traktatowej/Belki (pl_tax_due_pln) wymaga cfg (treaty/Belka) -
    to zakres kroku 14 (tax/dividends.py orkiestracja u źródła/zaliczenie/odzysk z Vero),
    nie samego zapisu do rejestru."""
    dividend_id = taxdiv.add_dividend(
        conn, record_date="2026-01-30", purchase_date="2026-02-19",
        entitled_quantity=61.491555, gross_eur=1.84, taxes_eur=0.64, fees_eur=0.0,
        reinvested_eur=1.20, purchase_price_eur=6.3015, purchased_shares=0.19028)
    row = conn.execute("SELECT * FROM dividends WHERE id = ?", (dividend_id,)).fetchone()
    assert row["pl_tax_due_pln"] is None
