"""Niezmienniki spójności danych (krok E2, docs/ROADMAP_V3.md) — te same
zapytania, które audyt E1 uruchomił ręcznie na eksporcie produkcyjnym,
tu jako kod: jedno źródło prawdy zamiast osobnego skryptu audytowego.

Audyt E1 na realnych danych (2026-08-22) znalazł jeden prawdziwy błąd: transzę
vestingu `pending` przeterminowaną o 386 dni bez odpowiadającego lotu — stąd
`_stale_pending_vest`, niezmiennik którego pierwotna lista z E1 nie miała
(sprawdzała tylko `status='vested'` bez lotu, nie przeterminowane `pending`)."""
from __future__ import annotations

import pytest

from nokia_tracker import integrity
from nokia_tracker.tax import grants, lots as taxlots


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    return None


def test_clean_db_has_no_findings(conn):
    assert integrity.check_all(conn) == []


# --- #1 qty_remaining ---

def test_qty_remaining_mismatch_detected(conn):
    lot_id = taxlots.add_lot(conn, "2025-01-10", "own", 10.0, 5.0, source="pdf_import")
    conn.execute("UPDATE lots SET qty_remaining = ? WHERE id = ?", (999.0, lot_id))
    conn.commit()

    findings = integrity.check_all(conn)

    kinds = [f.check for f in findings]
    assert "qty_remaining_mismatch" in kinds
    f = next(f for f in findings if f.check == "qty_remaining_mismatch")
    assert f.count == 1
    assert f.details[0]["lot_id"] == lot_id


# --- #2 sale_allocations sum vs sales.quantity ---

def test_sale_allocation_sum_mismatch_detected(conn):
    lot_id = taxlots.add_lot(conn, "2025-01-10", "own", 50.0, 5.0, source="pdf_import")
    cur = conn.execute(
        "INSERT INTO sales (sale_date, quantity, price_eur, revenue_pln) "
        "VALUES ('2025-06-01', 50.0, 6.0, 300.0)")
    sale_id = cur.lastrowid
    conn.execute(
        "INSERT INTO sale_allocations (sale_id, lot_id, quantity, cost_pln, revenue_pln) "
        "VALUES (?, ?, 10.0, 40.0, 60.0)", (sale_id, lot_id))
    conn.commit()

    findings = integrity.check_all(conn)

    f = next(f for f in findings if f.check == "sale_allocation_sum_mismatch")
    assert f.count == 1
    assert f.details[0]["sale_id"] == sale_id


def test_sale_allocation_float_noise_not_flagged(conn):
    lot_id = taxlots.add_lot(conn, "2025-01-10", "own", 50.0, 5.0, source="pdf_import")
    cur = conn.execute(
        "INSERT INTO sales (sale_date, quantity, price_eur, revenue_pln) "
        "VALUES ('2025-06-01', 10.0, 6.0, 60.0)")
    sale_id = cur.lastrowid
    conn.execute(
        "INSERT INTO sale_allocations (sale_id, lot_id, quantity, cost_pln, revenue_pln) "
        "VALUES (?, ?, 9.9999998, 40.0, 60.0)", (sale_id, lot_id))
    conn.commit()

    findings = integrity.check_all(conn)

    assert not any(f.check == "sale_allocation_sum_mismatch" for f in findings)


# --- #3 vests referential integrity ---

def test_vest_dangling_lot_id_detected(conn):
    # FK enforcement (db.get_conn) blokuje to w normalnej pracy aplikacji —
    # ten test symuluje anomalię z zewnątrz (ręczna edycja bazy, stary bug
    # sprzed dodania PRAGMA foreign_keys), którą niezmiennik ma wyłapać.
    grant_id = grants.add_grant(conn, "espp", "2025-01-01", 10.0, "g1")
    vest_id = grants.add_vest(conn, grant_id, "2025-06-01", 10.0, "v1", status="vested")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("UPDATE vests SET lot_id = 99999 WHERE id = ?", (vest_id,))
    conn.commit()

    findings = integrity.check_all(conn)

    f = next(f for f in findings if f.check == "vest_dangling_lot_id")
    assert f.count == 1
    assert f.details[0]["vest_id"] == vest_id


def test_vest_dangling_grant_id_detected(conn):
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO vests (grant_id, vest_date, quantity, status, natural_key) "
        "VALUES (99999, '2025-06-01', 10.0, 'pending', 'orphan')")
    conn.commit()

    findings = integrity.check_all(conn)

    f = next(f for f in findings if f.check == "vest_dangling_grant_id")
    assert f.count == 1


# --- #4 vested status without lot_id ---

def test_vested_without_lot_detected(conn):
    grant_id = grants.add_grant(conn, "espp", "2025-01-01", 10.0, "g1")
    vest_id = grants.add_vest(conn, grant_id, "2025-06-01", 10.0, "v1", status="vested")

    findings = integrity.check_all(conn)

    f = next(f for f in findings if f.check == "vested_without_lot")
    assert f.count == 1
    assert f.details[0]["vest_id"] == vest_id


# --- #5 orphaned sale_allocations ---

def test_orphaned_sale_allocation_detected(conn):
    lot_id = taxlots.add_lot(conn, "2025-01-10", "own", 50.0, 5.0, source="pdf_import")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO sale_allocations (sale_id, lot_id, quantity, cost_pln, revenue_pln) "
        "VALUES (99999, ?, 10.0, 40.0, 60.0)", (lot_id,))
    conn.commit()

    findings = integrity.check_all(conn)

    f = next(f for f in findings if f.check == "orphaned_sale_allocation")
    assert f.count == 1


# --- #6 unresolved import conflicts ---

def test_unresolved_import_conflict_detected(conn):
    conn.execute(
        "INSERT INTO imports (filename, file_sha256, imported_at) "
        "VALUES ('x.pdf', 'deadbeef', datetime('now'))")
    conn.execute(
        "INSERT INTO import_conflicts (import_id, entity_type, natural_key, "
        "existing_json, incoming_json, resolved) VALUES (1, 'balance', 'b:1', '{}', '{}', 0)")
    conn.commit()

    findings = integrity.check_all(conn)

    f = next(f for f in findings if f.check == "unresolved_import_conflict")
    assert f.count == 1


# --- #7 dividend arithmetic ---

def test_dividend_arithmetic_mismatch_detected(conn):
    conn.execute(
        "INSERT INTO dividends (pay_date, gross_eur, withholding_paid_eur, net_received_eur, "
        "natural_key) VALUES ('2025-08-01', 10.0, 2.0, 5.0, 'd1')")
    conn.commit()

    findings = integrity.check_all(conn)

    f = next(f for f in findings if f.check == "dividend_arithmetic_mismatch")
    assert f.count == 1


def test_dividend_arithmetic_within_grosz_not_flagged(conn):
    conn.execute(
        "INSERT INTO dividends (pay_date, gross_eur, withholding_paid_eur, net_received_eur, "
        "natural_key) VALUES ('2025-08-01', 10.0, 2.0, 8.005, 'd1')")
    conn.commit()

    findings = integrity.check_all(conn)

    assert not any(f.check == "dividend_arithmetic_mismatch" for f in findings)


# --- #8 missing/future NBP rate ---

def test_lot_missing_nbp_rate_detected(conn):
    conn.execute(
        "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur, fee_eur, "
        "qty_remaining, source) VALUES ('2025-01-10', 'own', 10.0, 5.0, 0.0, 10.0, 'manual')")
    conn.commit()

    findings = integrity.check_all(conn)

    f = next(f for f in findings if f.check == "missing_or_future_nbp_rate")
    assert any(d["table"] == "lots" for d in f.details)


def test_sale_future_nbp_rate_detected(conn):
    conn.execute(
        "INSERT INTO sales (sale_date, quantity, price_eur, nbp_rate, nbp_rate_date) "
        "VALUES ('2025-01-10', 10.0, 6.0, 4.0, '2025-02-01')")
    conn.commit()

    findings = integrity.check_all(conn)

    f = next(f for f in findings if f.check == "missing_or_future_nbp_rate")
    assert any(d["table"] == "sales" for d in f.details)


# --- #9 tax loss carryforward limits ---

def test_tax_loss_deductions_exceed_loss_detected(conn):
    cur = conn.execute(
        "INSERT INTO tax_loss_carryforward (origin_year, cost_basis_policy, loss_pln) "
        "VALUES (2023, 'own_only', 1000.0)")
    loss_id = cur.lastrowid
    conn.execute(
        "INSERT INTO tax_loss_deductions (loss_id, used_in_year, amount_pln) "
        "VALUES (?, 2024, 600.0)", (loss_id,))
    conn.execute(
        "INSERT INTO tax_loss_deductions (loss_id, used_in_year, amount_pln) "
        "VALUES (?, 2025, 600.0)", (loss_id,))
    conn.commit()

    findings = integrity.check_all(conn)

    f = next(f for f in findings if f.check == "tax_loss_deductions_exceed_loss")
    assert f.count == 1
    assert f.details[0]["loss_id"] == loss_id


def test_tax_loss_expired_unclaimed_detected(conn):
    conn.execute(
        "INSERT INTO tax_loss_carryforward (origin_year, cost_basis_policy, loss_pln) "
        "VALUES (2018, 'own_only', 500.0)")
    conn.commit()

    findings = integrity.check_all(conn, today="2026-08-22")

    f = next(f for f in findings if f.check == "tax_loss_expired_unclaimed")
    assert f.count == 1


# --- new: stale pending vest (found on production data in E1) ---

def test_stale_pending_vest_detected(conn):
    grant_id = grants.add_grant(conn, "espp", "2024-10-21", 24.42, "g1")
    vest_id = grants.add_vest(conn, grant_id, "2025-08-01", 24.42, "v1", status="pending")

    findings = integrity.check_all(conn, today="2026-08-22")

    f = next(f for f in findings if f.check == "stale_pending_vest")
    assert f.count == 1
    assert f.details[0]["vest_id"] == vest_id
    assert f.details[0]["days_overdue"] > 300


def test_recently_pending_vest_not_flagged(conn):
    grant_id = grants.add_grant(conn, "espp", "2026-07-27", 19.29, "g1")
    grants.add_vest(
        conn, grant_id, "2026-08-01", 19.29, "v1", status="pending",
        available_from="2026-08-27")

    findings = integrity.check_all(conn, today="2026-08-22")

    assert not any(f.check == "stale_pending_vest" for f in findings)


def test_pending_vest_uses_available_from_when_present(conn):
    """available_from (dostępność) różni się od vest_date (nabycie) o ~4 tyg. dla ESPP —
    próg przeterminowania liczony od späniejszej z dwóch dat, nie od samego vest_date."""
    grant_id = grants.add_grant(conn, "espp", "2026-01-01", 10.0, "g1")
    grants.add_vest(
        conn, grant_id, "2026-06-01", 10.0, "v1", status="pending",
        available_from="2026-06-28")

    # 70 dni po vest_date, ale tylko ~45 dni po available_from — wciąż w oknie 60 dni
    findings = integrity.check_all(conn, today="2026-08-11")

    assert not any(f.check == "stale_pending_vest" for f in findings)
