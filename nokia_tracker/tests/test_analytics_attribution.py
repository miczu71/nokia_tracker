"""Atrybucja zysku (krok 25, docs/PLAN_KROK_25_wyniki.md) — rozbicie
całkowitego zysku w PLN na pięć składników. Kryterium akceptacji jest
twarde: suma składników musi się równać `total_pln` z dokładnością do
grosza na KAŻDYM scenariuszu, inaczej rozbicie jest ozdobnikiem, nie liczbą."""
import pytest

from nokia_tracker.analytics import attribution


def _add_lot(conn, acquired_date, lot_type, quantity, price_eur, nbp_rate) -> int:
    cur = conn.execute(
        "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur, "
        "nbp_rate, qty_remaining) VALUES (?, ?, ?, ?, ?, ?)",
        (acquired_date, lot_type, quantity, price_eur, nbp_rate, quantity))
    conn.commit()
    return cur.lastrowid


def _assert_reconciles(result):
    components_sum = (
        result["price_change_pln"] + result["espp_match_pln"] + result["lti_pln"]
        + result["dividends_pln"] + result["fx_effect_pln"])
    assert components_sum == pytest.approx(result["total_pln"], abs=0.01)


def test_empty_portfolio_all_zero(conn):
    result = attribution.decompose(conn, current_price_eur=10.0, current_eurpln_rate=4.5)
    assert result["total_pln"] == 0.0
    for key in ("price_change_pln", "espp_match_pln", "lti_pln", "dividends_pln",
                "fx_effect_pln"):
        assert result[key] == 0.0
    _assert_reconciles(result)


def test_own_lot_price_up_fx_unchanged(conn):
    _add_lot(conn, "2024-01-01", "own", 10.0, price_eur=5.0, nbp_rate=4.5)

    result = attribution.decompose(conn, current_price_eur=6.0, current_eurpln_rate=4.5)

    assert result["price_change_pln"] == pytest.approx(10.0 * (6.0 - 5.0) * 4.5)
    assert result["fx_effect_pln"] == pytest.approx(0.0, abs=1e-9)
    assert result["espp_match_pln"] == 0.0
    assert result["lti_pln"] == 0.0
    assert result["dividends_pln"] == 0.0
    _assert_reconciles(result)


def test_own_lot_fx_moved_price_unchanged(conn):
    _add_lot(conn, "2024-01-01", "own", 10.0, price_eur=5.0, nbp_rate=4.5)

    result = attribution.decompose(conn, current_price_eur=5.0, current_eurpln_rate=5.0)

    assert result["price_change_pln"] == pytest.approx(0.0, abs=1e-9)
    assert result["fx_effect_pln"] == pytest.approx(10.0 * 5.0 * (5.0 - 4.5))
    _assert_reconciles(result)


def test_matched_lot_is_windfall_not_price_change(conn):
    _add_lot(conn, "2024-01-01", "matched", 5.0, price_eur=8.0, nbp_rate=4.0)

    # Cena i kurs bez zmian od dnia dopasowania - izoluje sam "windfall".
    result = attribution.decompose(conn, current_price_eur=8.0, current_eurpln_rate=4.0)

    assert result["espp_match_pln"] == pytest.approx(5.0 * 8.0 * 4.0)
    assert result["price_change_pln"] == pytest.approx(0.0, abs=1e-9)
    assert result["fx_effect_pln"] == pytest.approx(0.0, abs=1e-9)
    assert result["total_pln"] == pytest.approx(5.0 * 8.0 * 4.0)
    _assert_reconciles(result)


def test_lti_lot_is_windfall(conn):
    _add_lot(conn, "2024-01-01", "lti", 3.0, price_eur=7.0, nbp_rate=4.2)

    result = attribution.decompose(conn, current_price_eur=7.0, current_eurpln_rate=4.2)

    assert result["lti_pln"] == pytest.approx(3.0 * 7.0 * 4.2)
    _assert_reconciles(result)


def test_dividend_drip_lot_counts_as_dividend_windfall(conn):
    _add_lot(conn, "2024-01-01", "dividend_drip", 1.0, price_eur=9.0, nbp_rate=4.1)

    result = attribution.decompose(conn, current_price_eur=9.0, current_eurpln_rate=4.1)

    assert result["dividends_pln"] == pytest.approx(1.0 * 9.0 * 4.1)
    _assert_reconciles(result)


def test_cash_dividend_adds_to_dividends_component(conn):
    conn.execute(
        "INSERT INTO dividends (pay_date, gross_eur, net_received_eur, nbp_rate) "
        "VALUES ('2024-05-01', 10.0, 8.0, 4.3)")
    conn.commit()

    result = attribution.decompose(conn, current_price_eur=10.0, current_eurpln_rate=4.3)

    assert result["dividends_pln"] == pytest.approx(8.0 * 4.3)
    assert result["total_pln"] == pytest.approx(8.0 * 4.3)
    _assert_reconciles(result)


def test_reinvested_dividend_not_double_counted(conn):
    lot_id = _add_lot(conn, "2024-05-01", "dividend_drip", 1.0, price_eur=10.0, nbp_rate=4.3)
    conn.execute(
        "INSERT INTO dividends (pay_date, gross_eur, net_received_eur, nbp_rate, "
        "reinvested_lot_id) VALUES ('2024-05-01', 10.0, 8.0, 4.3, ?)", (lot_id,))
    conn.commit()

    result = attribution.decompose(conn, current_price_eur=10.0, current_eurpln_rate=4.3)

    # tylko wartość lotu DRIP (1*10*4.3=43), NIE plus jeszcze net_received_eur*rate
    assert result["dividends_pln"] == pytest.approx(43.0)
    _assert_reconciles(result)


def test_mixed_scenario_reconciles_to_the_penny(conn):
    _add_lot(conn, "2023-01-01", "own", 20.0, price_eur=4.0, nbp_rate=4.4)
    _add_lot(conn, "2023-06-01", "matched", 10.0, price_eur=4.5, nbp_rate=4.35)
    _add_lot(conn, "2024-01-01", "lti", 15.0, price_eur=5.0, nbp_rate=4.2)
    _add_lot(conn, "2024-03-01", "dividend_drip", 2.0, price_eur=5.5, nbp_rate=4.25)
    conn.execute(
        "INSERT INTO dividends (pay_date, gross_eur, net_received_eur, nbp_rate) "
        "VALUES ('2024-06-01', 15.0, 12.0, 4.15)")
    conn.commit()

    result = attribution.decompose(conn, current_price_eur=6.5, current_eurpln_rate=4.6)

    total_value_pln = (20.0 + 10.0 + 15.0 + 2.0) * 6.5 * 4.6
    own_cost_pln = 20.0 * 4.0 * 4.4
    cash_div_pln = 12.0 * 4.15
    expected_total = total_value_pln - own_cost_pln + cash_div_pln
    assert result["total_pln"] == pytest.approx(expected_total)
    _assert_reconciles(result)


def test_loss_scenario_still_reconciles(conn):
    _add_lot(conn, "2024-01-01", "own", 10.0, price_eur=10.0, nbp_rate=4.5)

    result = attribution.decompose(conn, current_price_eur=6.0, current_eurpln_rate=4.0)

    assert result["total_pln"] < 0
    _assert_reconciles(result)
