"""Kontrfaktyczny benchmark (krok 25, docs/PLAN_KROK_25_wyniki.md) — "gdyby te
same wpłaty własne poszły w OMXH25/Ericsson zamiast Nokii", te same przepływy
co `returns.build_xirr_cashflows()`."""
from nokia_tracker import quotes as quotesm
from nokia_tracker.analytics import benchmark
from nokia_tracker.models import Candle


def _seed_quotes(conn, symbol: str, closes: dict[str, float]) -> int:
    instrument_id = quotesm.ensure_instrument(conn, symbol, symbol, "EUR", role="benchmark")
    candles = [Candle(ts=f"{d}T00:00:00+00:00", close=c) for d, c in closes.items()]
    quotesm.upsert_candles(conn, instrument_id, "daily", candles, source="yahoo")
    return instrument_id


def test_counterfactual_single_contribution(conn):
    bench_id = _seed_quotes(conn, "^OMXH25", {"2024-01-01": 10.0, "2024-06-01": 12.0})
    cashflows = [("2024-01-01", -100.0), ("2024-06-01", 130.0)]  # dodatnia = wartość dziś, ignorowana

    result = benchmark.counterfactual(conn, cashflows, bench_id)

    assert result == 120.0  # 10 jednostek * 12.0


def test_counterfactual_multiple_contributions(conn):
    bench_id = _seed_quotes(conn, "^OMXH25", {
        "2024-01-01": 10.0, "2024-03-01": 20.0, "2024-06-01": 15.0,
    })
    cashflows = [("2024-01-01", -100.0), ("2024-03-01", -200.0), ("2024-06-01", 999.0)]

    result = benchmark.counterfactual(conn, cashflows, bench_id)

    # 10 jednostek (100/10) + 10 jednostek (200/20) = 20 jednostek * 15.0
    assert result == 300.0


def test_counterfactual_uses_price_on_or_before_when_exact_date_missing(conn):
    bench_id = _seed_quotes(conn, "^OMXH25", {"2024-01-01": 10.0, "2024-06-01": 11.0})
    cashflows = [("2024-01-03", -50.0)]  # brak notowania dokładnie na tę datę

    result = benchmark.counterfactual(conn, cashflows, bench_id)

    assert result == 55.0  # 5 jednostek (50/10, ostatnia znana cena <= 01-03) * 11.0


def test_counterfactual_none_without_any_quotes(conn):
    bench_id = quotesm.ensure_instrument(conn, "^OMXH25", "OMXH25", "EUR", role="benchmark")
    result = benchmark.counterfactual(conn, [("2024-01-01", -100.0)], bench_id)
    assert result is None


def test_counterfactual_none_without_own_contributions(conn):
    bench_id = _seed_quotes(conn, "^OMXH25", {"2024-01-01": 10.0})
    result = benchmark.counterfactual(conn, [("2024-01-01", 100.0)], bench_id)
    assert result is None


# ---- counterfactual_series(): krzywa dla wykresu /wyniki ----

def test_counterfactual_series_none_before_first_contribution(conn):
    bench_id = _seed_quotes(conn, "^OMXH25", {
        "2024-01-01": 10.0, "2024-01-02": 10.0, "2024-01-03": 11.0,
    })
    cashflows = [("2024-01-02", -100.0)]

    series = benchmark.counterfactual_series(
        conn, cashflows, bench_id, ["2024-01-01", "2024-01-02", "2024-01-03"])

    assert series == [
        ("2024-01-01", None),
        ("2024-01-02", 100.0),   # 10 jednostek * 10.0
        ("2024-01-03", 110.0),   # 10 jednostek * 11.0
    ]


def test_counterfactual_series_accumulates_multiple_contributions(conn):
    bench_id = _seed_quotes(conn, "^OMXH25", {
        "2024-01-01": 10.0, "2024-02-01": 20.0, "2024-03-01": 25.0,
    })
    cashflows = [("2024-01-01", -100.0), ("2024-02-01", -200.0)]

    series = benchmark.counterfactual_series(
        conn, cashflows, bench_id, ["2024-01-01", "2024-02-01", "2024-03-01"])

    assert series[0] == ("2024-01-01", 100.0)  # 10 jednostek * 10.0
    assert series[1] == ("2024-02-01", 400.0)  # (10+10) jednostek * 20.0
    assert series[2] == ("2024-03-01", 500.0)  # 20 jednostek * 25.0
