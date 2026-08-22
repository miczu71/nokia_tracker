"""Testy `cash.py` (krok E4, docs/ROADMAP_V3.md) — księga gotówki jako model
odczytu nad istniejącymi tabelami. Reguła modułu pod testem: gotówka != przychód
podatkowy — trzy testy niżej dowodzą trzech konkretnych rozjazdów (opłaty, kurs,
moment/DRIP), nie tylko sprawdzają, że funkcja się nie wywala."""
from datetime import date, timedelta

import pytest

from nokia_tracker import cash


def _add_lot(conn, acquired_date, quantity, price_eur, lot_type="own",
             nbp_rate=4.0, nbp_rate_date=None):
    conn.execute(
        "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur, fee_eur, "
        "nbp_rate, nbp_rate_date, cost_pln, qty_remaining) "
        "VALUES (?,?,?,?,0,?,?,?,?)",
        (acquired_date, lot_type, quantity, price_eur, nbp_rate,
         nbp_rate_date or acquired_date, quantity * price_eur * nbp_rate, quantity))
    conn.commit()


def _add_sale(conn, sale_date, quantity, price_eur, fee_eur=0.0, nbp_rate=4.0,
              revenue_pln=None, reported_revenue_pln=None):
    revenue_pln = revenue_pln if revenue_pln is not None else (
        quantity * price_eur - fee_eur) * nbp_rate
    cur = conn.execute(
        "INSERT INTO sales (sale_date, quantity, price_eur, fee_eur, nbp_rate, "
        "nbp_rate_date, revenue_pln, reported_revenue_pln) VALUES (?,?,?,?,?,?,?,?)",
        (sale_date, quantity, price_eur, fee_eur, nbp_rate, sale_date, revenue_pln,
         reported_revenue_pln))
    conn.commit()
    return cur.lastrowid


def _add_dividend(conn, pay_date, gross_eur, net_received_eur, reinvested_lot_id=None,
                   quantity=1.0):
    # quantity > 0 i notes puste to dokładnie kryterium "realnego" wiersza w
    # tax/dividends.py::payouts() (real_row_count) — testy tu odtwarzają
    # transakcyjny wiersz z wyciągu, nie odtworzony szacunek.
    conn.execute(
        "INSERT INTO dividends (pay_date, gross_eur, withholding_paid_eur, "
        "net_received_eur, reinvested_lot_id, quantity) VALUES (?,?,?,?,?,?)",
        (pay_date, gross_eur, gross_eur - net_received_eur, net_received_eur,
         reinvested_lot_id, quantity))
    conn.commit()


# --- sale_proceeds: gotówka != przychód podatkowy ---

def test_sale_proceeds_empty_when_no_sales(conn):
    result = cash.sale_proceeds(conn)
    assert result["total_eur"] == 0.0
    assert result["total_pln"] == 0.0
    assert result["by_year"] == {}


def test_sale_proceeds_uses_revenue_pln_over_nominal_price(conn):
    # Rozjazd 1 (kurs/override "Sale Proceeds"): revenue_pln w bazie odzwierciedla
    # realne "Sale Proceeds" z wyciągu, które bywa inne niż quantity*price_eur
    # (cena w PDF zaokrąglona do 2 miejsc — patrz tax/lots.py::record_sale
    # docstring). Tu quantity*price_eur - fee_eur = 800*5.0 - 10 = 3990, ale
    # realne "Sale Proceeds" (zapisane w revenue_pln po kursie 4.0) to 4000 EUR
    # brutto — różnica reprezentuje realny override, nie zaokrąglenie testu.
    _add_sale(conn, "2025-10-27", quantity=800.0, price_eur=5.0, fee_eur=10.0,
              nbp_rate=4.0, revenue_pln=16000.0)  # = 4000 EUR po kursie 4.0
    result = cash.sale_proceeds(conn)
    assert result["total_pln"] == pytest.approx(16000.0)
    assert result["total_eur"] == pytest.approx(4000.0)
    # NIE quantity*price_eur - fee_eur (= 3990), które ignoruje realne proceeds.
    assert result["total_eur"] != pytest.approx(800.0 * 5.0 - 10.0, rel=0.001)


def test_sale_proceeds_ignores_reported_revenue_pln_override(conn):
    # Rozjazd 2 (moment/deklaracja vs gotówka): reported_revenue_pln (v4) nadpisuje
    # PIT-38, gdy deklaracja już złożona z błędną liczbą arkusza — to poprawka
    # DEKLARACJI, nie tego, co faktycznie wpłynęło na konto. cash.py go ignoruje.
    _add_sale(conn, "2025-06-01", quantity=100.0, price_eur=5.0, fee_eur=0.0,
              nbp_rate=4.0, revenue_pln=2000.0, reported_revenue_pln=9999.0)
    result = cash.sale_proceeds(conn)
    assert result["total_pln"] == pytest.approx(2000.0)


def test_sale_proceeds_by_year_and_cumulative(conn):
    _add_sale(conn, "2024-03-01", 10.0, 5.0, revenue_pln=200.0)
    _add_sale(conn, "2025-03-01", 10.0, 6.0, revenue_pln=240.0)
    result = cash.sale_proceeds(conn)
    assert result["by_year"]["2024"]["pln"] == pytest.approx(200.0)
    assert result["by_year"]["2025"]["pln"] == pytest.approx(240.0)
    assert result["total_pln"] == pytest.approx(440.0)


def test_sale_proceeds_filters_by_year(conn):
    _add_sale(conn, "2024-03-01", 10.0, 5.0, revenue_pln=200.0)
    _add_sale(conn, "2025-03-01", 10.0, 6.0, revenue_pln=240.0)
    result = cash.sale_proceeds(conn, year=2024)
    assert result["total_pln"] == pytest.approx(200.0)


# --- dividend_flow: bezgotówkowe, moment != przychód gotówkowy ---

def test_dividend_flow_empty(conn):
    result = cash.dividend_flow(conn, {})
    assert result["payout_count"] == 0
    assert result["cash_contribution_eur"] == 0.0


def test_dividend_flow_drip_contributes_zero_cash(conn):
    # Rozjazd 3 (moment): dywidenda DRIP jest przychodem podatkowym (trafia do
    # PIT-38 sekcja G), ale zerowym przepływem gotówki — reinwestuje się
    # natychmiast w nowy lot, nigdy nie ląduje jako gotówka na koncie.
    _add_lot(conn, "2026-01-30", 0.19028, 6.3015, lot_type="dividend_drip")
    _add_dividend(conn, "2026-01-30", gross_eur=1.84, net_received_eur=1.2,
                  reinvested_lot_id=1)
    result = cash.dividend_flow(conn, {})
    assert result["payout_count"] == 1
    assert result["net_received_eur"] == pytest.approx(1.2)
    assert result["cash_contribution_eur"] == 0.0
    assert result["reinvested_shares"] == pytest.approx(0.19028)


def test_dividend_flow_non_drip_still_reported_but_not_double_counted(conn):
    # Dywidenda BEZ reinwestycji (reinwested_lot_id NULL) jest realną gotówką —
    # cash_contribution_eur dla takiego wiersza równa się net_received_eur, nie
    # zero. To odróżnia "bezgotówkowe z definicji" (DRIP) od "akurat nie ma
    # danych o reinwestycji".
    _add_dividend(conn, "2026-01-30", gross_eur=10.0, net_received_eur=6.5,
                  reinvested_lot_id=None)
    result = cash.dividend_flow(conn, {})
    assert result["cash_contribution_eur"] == pytest.approx(6.5)


def test_dividend_flow_groups_by_pay_date_like_payouts(conn):
    # Computershare drukuje osobny wiersz na koszyk planu tej samej wypłaty
    # (lekcja 0.17.2/0.17.3) — payout_count musi liczyć WYPŁATY, nie wiersze.
    _add_lot(conn, "2026-07-24", 2734.0, 0.02, lot_type="dividend_drip")
    _add_lot(conn, "2026-07-24", 154.663115, 0.02, lot_type="dividend_drip")
    _add_dividend(conn, "2026-07-24", gross_eur=6.18, net_received_eur=4.02,
                  reinvested_lot_id=1)
    _add_dividend(conn, "2026-07-24", gross_eur=109.36, net_received_eur=71.09,
                  reinvested_lot_id=2)
    result = cash.dividend_flow(conn, {})
    assert result["payout_count"] == 1
    assert result["net_received_eur"] == pytest.approx(4.02 + 71.09)
    assert result["cash_contribution_eur"] == 0.0


# --- tax_liability ---

def test_tax_liability_no_data_zero_due(conn):
    cfg = {"cost_basis_policy": "own_only", "pl_capital_gains_tax_pct": 19.0}
    result = cash.tax_liability(conn, cfg, 2026)
    assert result["due_pln"] == 0.0
    assert result["paid_pln"] == 0.0
    assert result["outstanding_pln"] == 0.0
    assert result["deadline"] == "2027-04-30"


def test_tax_liability_subtracts_payments(conn):
    cfg = {"cost_basis_policy": "own_only", "pl_capital_gains_tax_pct": 19.0}
    _add_sale(conn, "2025-06-01", 100.0, 5.0, revenue_pln=2000.0)
    _add_lot(conn, "2024-01-01", 100.0, 3.0)
    cash.add_tax_payment(conn, 2025, "2026-04-01", 100.0, "zaliczka")
    result = cash.tax_liability(conn, cfg, 2025)
    assert result["paid_pln"] == pytest.approx(100.0)
    assert result["outstanding_pln"] == pytest.approx(result["due_pln"] - 100.0)


def test_add_and_delete_tax_payment(conn):
    payment_id = cash.add_tax_payment(conn, 2025, "2026-04-01", 500.0, "test")
    assert cash.tax_liability(conn, {"cost_basis_policy": "own_only",
                                      "pl_capital_gains_tax_pct": 19.0}, 2025)["paid_pln"] == 500.0
    assert cash.delete_tax_payment(conn, payment_id) is True
    assert cash.tax_liability(conn, {"cost_basis_policy": "own_only",
                                      "pl_capital_gains_tax_pct": 19.0}, 2025)["paid_pln"] == 0.0


def test_delete_tax_payment_missing_id_returns_false(conn):
    assert cash.delete_tax_payment(conn, 9999) is False


# --- broker_balance / broker_history / record_broker_balance ---

def test_broker_balance_none_when_empty(conn):
    assert cash.broker_balance(conn) is None


def test_broker_balance_returns_latest_with_age(conn):
    today = date.today().isoformat()
    stale_date = (date.today() - timedelta(days=10)).isoformat()
    cash.record_broker_balance(conn, stale_date, 1000.0, "EUR")
    cash.record_broker_balance(conn, today, 1234.5, "EUR")
    result = cash.broker_balance(conn, today=today)
    assert result["amount"] == pytest.approx(1234.5)
    assert result["as_of_date"] == today
    assert result["age_days"] == 0
    assert result["is_stale"] is False


def test_broker_balance_is_stale_past_threshold(conn):
    today = date.today().isoformat()
    old_date = (date.today() - timedelta(days=45)).isoformat()
    cash.record_broker_balance(conn, old_date, 1000.0, "EUR")
    result = cash.broker_balance(conn, today=today)
    assert result["age_days"] == 45
    assert result["is_stale"] is True


def test_record_broker_balance_upserts_same_day(conn):
    cash.record_broker_balance(conn, "2026-08-01", 1000.0, "EUR")
    cash.record_broker_balance(conn, "2026-08-01", 1500.0, "EUR")
    history = cash.broker_history(conn)
    assert len(history) == 1
    assert history[0]["amount"] == pytest.approx(1500.0)


def test_broker_history_ordered_newest_first(conn):
    cash.record_broker_balance(conn, "2026-06-01", 500.0, "EUR")
    cash.record_broker_balance(conn, "2026-08-01", 700.0, "EUR")
    history = cash.broker_history(conn)
    assert [h["as_of_date"] for h in history] == ["2026-08-01", "2026-06-01"]


# --- ledger: spina wszystko ---

def test_ledger_shape(conn):
    cfg = {"cost_basis_policy": "own_only", "pl_capital_gains_tax_pct": 19.0}
    result = cash.ledger(conn, cfg, 2026)
    assert set(result) == {
        "sale_proceeds", "dividend_flow", "tax_liability", "broker_balance"}
