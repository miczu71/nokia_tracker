"""Krzywa wartości portfela (krok 25, docs/PLAN_KROK_25_wyniki.md) — rekonstrukcja
BEZ sieci z tego, co już jest w bazie: loty, alokacje sprzedaży, `quotes`, gęste
`nbp_rates` (z `fx_nbp.backfill_range`)."""
from nokia_tracker import quotes as quotesm
from nokia_tracker.analytics import history
from nokia_tracker.models import Candle


def _seed_candles(conn, closes: dict[str, float]) -> int:
    instrument_id = quotesm.ensure_instrument(conn, "NOKIA.HE", "Nokia", "EUR", role="primary")
    candles = [Candle(ts=f"{d}T00:00:00+00:00", close=c) for d, c in closes.items()]
    quotesm.upsert_candles(conn, instrument_id, "daily", candles, source="yahoo")
    return instrument_id


def _add_lot(conn, acquired_date, quantity, lot_type="own", price_eur=1.0) -> int:
    cur = conn.execute(
        "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur, qty_remaining) "
        "VALUES (?, ?, ?, ?, ?)", (acquired_date, lot_type, quantity, price_eur, quantity))
    conn.commit()
    return cur.lastrowid


def _add_sale_allocation(conn, sale_date, lot_id, quantity) -> None:
    cur = conn.execute(
        "INSERT INTO sales (sale_date, quantity, price_eur) VALUES (?, ?, 1.0)",
        (sale_date, quantity))
    sale_id = cur.lastrowid
    conn.execute(
        "INSERT INTO sale_allocations (sale_id, lot_id, quantity, cost_pln, revenue_pln) "
        "VALUES (?, ?, ?, 0, 0)", (sale_id, lot_id, quantity))
    conn.commit()


def test_rebuild_returns_zero_when_no_lots(conn):
    instrument_id = _seed_candles(conn, {"2024-01-01": 10.0})
    assert history.rebuild(conn, instrument_id) == 0
    assert conn.execute("SELECT COUNT(*) FROM portfolio_history").fetchone()[0] == 0


def test_rebuild_builds_rows_only_from_first_lot_date_onward(conn):
    instrument_id = _seed_candles(conn, {
        "2024-01-01": 10.0, "2024-01-02": 11.0, "2024-01-03": 12.0,
    })
    _add_lot(conn, "2024-01-02", 5.0, price_eur=10.0)

    n = history.rebuild(conn, instrument_id)

    assert n == 2
    rows = conn.execute(
        "SELECT date, position_qty, price_eur, market_value_eur "
        "FROM portfolio_history ORDER BY date").fetchall()
    assert [r["date"] for r in rows] == ["2024-01-02", "2024-01-03"]
    assert rows[0]["position_qty"] == 5.0
    assert rows[0]["price_eur"] == 11.0
    assert rows[0]["market_value_eur"] == 55.0
    assert rows[1]["market_value_eur"] == 60.0


def test_rebuild_reduces_qty_after_sale(conn):
    instrument_id = _seed_candles(conn, {
        "2024-01-01": 10.0, "2024-01-02": 11.0, "2024-01-03": 12.0,
        "2024-01-04": 13.0, "2024-01-05": 14.0,
    })
    lot_id = _add_lot(conn, "2024-01-01", 10.0)
    _add_sale_allocation(conn, "2024-01-04", lot_id, 4.0)

    history.rebuild(conn, instrument_id)

    rows = {r["date"]: r["position_qty"] for r in
            conn.execute("SELECT date, position_qty FROM portfolio_history").fetchall()}
    assert rows["2024-01-03"] == 10.0
    assert rows["2024-01-04"] == 6.0
    assert rows["2024-01-05"] == 6.0


def test_rebuild_uses_nbp_rate_as_of_day(conn):
    instrument_id = _seed_candles(conn, {"2024-01-01": 10.0, "2024-01-02": 11.0})
    _add_lot(conn, "2024-01-01", 1.0)
    conn.execute(
        "INSERT INTO nbp_rates (date, rate, effective_date) VALUES (?, ?, ?)",
        ("2023-12-29", 4.5, "2023-12-29"))
    conn.commit()

    history.rebuild(conn, instrument_id)

    rows = {r["date"]: r["market_value_pln"] for r in
            conn.execute("SELECT date, market_value_pln FROM portfolio_history").fetchall()}
    assert rows["2024-01-01"] == 45.0
    assert rows["2024-01-02"] == 49.5


def test_rebuild_market_value_pln_none_without_nbp_rate(conn):
    instrument_id = _seed_candles(conn, {"2024-01-01": 10.0})
    _add_lot(conn, "2024-01-01", 1.0)

    history.rebuild(conn, instrument_id)

    row = conn.execute("SELECT market_value_pln, market_value_eur FROM portfolio_history"
                       ).fetchone()
    assert row["market_value_pln"] is None
    assert row["market_value_eur"] == 10.0


def test_rebuild_recomputes_from_scratch_on_second_call(conn):
    instrument_id = _seed_candles(conn, {"2024-01-01": 10.0, "2024-01-02": 11.0})
    _add_lot(conn, "2024-01-02", 5.0)
    history.rebuild(conn, instrument_id)

    _add_lot(conn, "2024-01-02", 3.0)  # drugi lot tego samego dnia
    n = history.rebuild(conn, instrument_id)

    assert n == 1  # nadal jeden wiersz na 2024-01-02, nie zdublowany
    row = conn.execute("SELECT position_qty FROM portfolio_history").fetchone()
    assert row["position_qty"] == 8.0


def test_rebuild_counts_all_lot_types_not_only_own(conn):
    instrument_id = _seed_candles(conn, {"2024-01-01": 10.0})
    _add_lot(conn, "2024-01-01", 2.0, lot_type="own")
    _add_lot(conn, "2024-01-01", 1.0, lot_type="matched")

    history.rebuild(conn, instrument_id)

    row = conn.execute("SELECT position_qty FROM portfolio_history").fetchone()
    assert row["position_qty"] == 3.0
