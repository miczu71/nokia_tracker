"""XIRR i TWR portfela (krok 25, docs/PLAN_KROK_25_wyniki.md).

Dwie różne definicje przepływu — `xirr()` na gotówce realnie wydanej (`own`),
`twr()` na wartości rynkowej dodanej/odjętej niezależnie od źródła (fair value
KAŻDEGO typu lotu, patrz docstring modułu analytics/returns.py)."""
import pytest

from nokia_tracker.analytics import returns


# ---- xirr(): czysta matematyka, znane wzorce z arkuszy kalkulacyjnych ----

def test_xirr_simple_one_year_ten_percent():
    result = returns.xirr([("2023-01-01", -1000.0), ("2024-01-01", 1100.0)])
    assert result == pytest.approx(0.10, abs=0.001)


def test_xirr_matches_known_excel_example():
    # Klasyczny przykład dokumentacji XIRR (Excel/Sheets) — wynik referencyjny 37.3%.
    cashflows = [
        ("2008-01-01", -10000.0),
        ("2008-03-01", 2750.0),
        ("2008-10-30", 4250.0),
        ("2009-02-15", 3250.0),
        ("2009-04-01", 2750.0),
    ]
    result = returns.xirr(cashflows)
    assert result == pytest.approx(0.373, abs=0.005)


def test_xirr_none_when_all_positive():
    assert returns.xirr([("2023-01-01", 100.0), ("2023-06-01", 50.0)]) is None


def test_xirr_none_when_all_negative():
    assert returns.xirr([("2023-01-01", -100.0), ("2023-06-01", -50.0)]) is None


def test_xirr_none_with_fewer_than_two_flows():
    assert returns.xirr([("2023-01-01", -100.0)]) is None
    assert returns.xirr([]) is None


def test_xirr_handles_many_small_contributions():
    # Wpłaty miesięczne przez rok + wypłata na koniec, wyższa niż suma wpłat.
    cashflows = [(f"2023-{m:02d}-01", -100.0) for m in range(1, 13)]
    cashflows.append(("2024-01-01", 1300.0))
    result = returns.xirr(cashflows)
    assert result is not None
    assert result > 0  # zarobił


# ---- twr(): czysta matematyka, łańcuchowanie dziennych stóp ----

def test_twr_no_cashflow_simple_growth():
    result = returns.twr(
        [("2024-01-01", 100.0), ("2024-01-02", 110.0)], [])
    assert result == pytest.approx(0.10, abs=1e-9)


def test_twr_neutralizes_deposit_timing():
    # Dzień 2: wartość skacze ze 100 do 210 (100 z zysku + 100 wpłaty).
    # Dzień 3: 210 -> 220 (organiczny zysk).
    daily = [("2024-01-01", 100.0), ("2024-01-02", 210.0), ("2024-01-03", 220.0)]
    cashflows = [("2024-01-02", 100.0)]
    result = returns.twr(daily, cashflows)
    expected = (1 + 0.10) * (1 + 10 / 210) - 1
    assert result == pytest.approx(expected, abs=1e-9)


def test_twr_neutralizes_withdrawal():
    # Dzień 3: wypłata 40 - spadek wartości ze 100 do 60 to CAŁKOWICIE
    # wyjaśnione wypłatą, zwrot rynkowy tego dnia powinien wyjść 0%.
    daily = [("2024-01-01", 100.0), ("2024-01-02", 100.0), ("2024-01-03", 60.0)]
    cashflows = [("2024-01-03", -40.0)]
    result = returns.twr(daily, cashflows)
    assert result == pytest.approx(0.0, abs=1e-9)


def test_twr_none_with_fewer_than_two_points():
    assert returns.twr([("2024-01-01", 100.0)], []) is None
    assert returns.twr([], []) is None


# ---- build_xirr_cashflows(): przepływy z bazy (loty/sprzedaże/dywidendy) ----

def test_build_xirr_cashflows_own_lot_is_negative_cash(conn):
    conn.execute(
        "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur, fee_eur) "
        "VALUES ('2024-01-01', 'own', 10.0, 5.0, 1.0)")
    conn.commit()

    flows = returns.build_xirr_cashflows(conn, "2024-06-01", 10.0, 6.0)

    assert ("2024-01-01", -51.0) in flows  # -(10*5 + 1)
    assert ("2024-06-01", 60.0) in flows  # wartość końcowa: 10 * 6.0


def test_build_xirr_cashflows_matched_lot_has_no_cash_flow(conn):
    conn.execute(
        "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur) "
        "VALUES ('2024-01-01', 'matched', 5.0, 5.0)")
    conn.commit()

    flows = returns.build_xirr_cashflows(conn, "2024-06-01", 5.0, 6.0)

    dates = [d for d, _ in flows]
    assert "2024-01-01" not in dates  # darmowe akcje - brak przepływu gotówki
    assert ("2024-06-01", 30.0) in flows


def test_build_xirr_cashflows_sale_is_positive_cash(conn):
    conn.execute(
        "INSERT INTO sales (sale_date, quantity, price_eur, fee_eur) "
        "VALUES ('2024-03-01', 4.0, 7.0, 0.5)")
    conn.commit()

    flows = returns.build_xirr_cashflows(conn, "2024-06-01", 0.0, 6.0)

    assert ("2024-03-01", 27.5) in flows  # 4*7 - 0.5


def test_build_xirr_cashflows_dividend_reinvested_excluded(conn):
    cur = conn.execute(
        "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur) "
        "VALUES ('2024-02-01', 'dividend_drip', 1.0, 8.0)")
    drip_lot_id = cur.lastrowid
    conn.execute(
        "INSERT INTO dividends (pay_date, gross_eur, net_received_eur, reinvested_lot_id) "
        "VALUES ('2024-02-01', 10.0, 8.0, ?)", (drip_lot_id,))
    conn.execute(
        "INSERT INTO dividends (pay_date, gross_eur, net_received_eur) "
        "VALUES ('2024-02-15', 10.0, 8.0)")
    conn.commit()

    flows = returns.build_xirr_cashflows(conn, "2024-06-01", 0.0, 6.0)

    assert ("2024-02-01", 8.0) not in flows  # reinwestowana - nie opuściła portfela
    assert ("2024-02-15", 8.0) in flows  # gotówkowa - realny wpływ


# ---- build_twr_cashflows(): wartość rynkowa dodana/odjęta (fair value) ----

def test_build_twr_cashflows_any_lot_type_uses_fair_value(conn):
    conn.execute(
        "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur) "
        "VALUES ('2024-01-01', 'matched', 5.0, 5.0)")
    conn.commit()

    flows = returns.build_twr_cashflows(conn)

    assert ("2024-01-01", 25.0) in flows  # 5*5, mimo że to darmowe akcje


def test_build_twr_cashflows_sale_is_negative_value(conn):
    conn.execute(
        "INSERT INTO sales (sale_date, quantity, price_eur, fee_eur) "
        "VALUES ('2024-03-01', 4.0, 7.0, 0.5)")
    conn.commit()

    flows = returns.build_twr_cashflows(conn)

    assert ("2024-03-01", -27.5) in flows
