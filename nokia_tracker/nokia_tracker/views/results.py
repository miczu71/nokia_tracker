"""Dane dla /wyniki (krok 25/28.1/32: XIRR, TWR, atrybucja, ryzyko,
kontrfaktyczny benchmark OMXH25). Wyodrębnione z `web.py::wyniki_get`
(E3 — docs/ROADMAP_V3.md), zero zmiany liczb: identyczne zapytania,
identyczna kolejność, te same warunki brzegowe."""
from __future__ import annotations

from datetime import date

from ..analytics import attribution as analytics_attribution
from ..analytics import benchmark as analytics_benchmark
from ..analytics import returns as analytics_returns
from ..analytics import risk as analytics_risk
from ..tax import lots as taxlots


def results_view(conn, cfg: dict, ids: dict, price_eur: float | None,
                 eurpln_rate: float | None) -> dict:
    history_rows = conn.execute(
        "SELECT date, market_value_eur, market_value_pln FROM portfolio_history "
        "ORDER BY date").fetchall()
    # Krok 28.1: kurs PLN/EUR danego dnia wyprowadzony z JUŻ POBRANEGO wiersza
    # `portfolio_history` (ten sam `rate`, którego użył `history.py::rebuild()`
    # do policzenia `market_value_pln`) — zero dodatkowych zapytań do NBP,
    # zero rozjazdu metodologii między krzywą portfela a krzywą benchmarku.
    rate_by_date = {
        r["date"]: r["market_value_pln"] / r["market_value_eur"]
        for r in history_rows
        if r["market_value_eur"] not in (None, 0) and r["market_value_pln"] is not None}

    xirr_pct = twr_pct = attribution = None
    benchmark_today_eur = benchmark_today_pln = None
    sharpe = volatility_pct = None
    xirr_flows: list[tuple[str, float]] = []

    # Krok 32: `daily_values`/`twr_flows` budowane bezwarunkowo (nie
    # potrzebują dzisiejszej ceny/kursu) — `max_drawdown()` działa
    # nawet, gdy dzisiejszy poll jeszcze się nie wykonał, tak jak
    # krzywa wartości i tabela rok-po-roku niżej w tej samej funkcji.
    # `twr_flows` (0.16.1): netuje kontrybucje/wypłaty (vesting, ESPP,
    # sprzedaże) z dziennych zwrotów — bez tego jeden dzień vestingu
    # wygląda jak +1000% "zwrotu" (bug znaleziony na produkcji 0.16.0).
    daily_values = [(r["date"], r["market_value_eur"]) for r in history_rows
                    if r["market_value_eur"] is not None]
    twr_flows = analytics_returns.build_twr_cashflows(conn)
    max_dd_result = analytics_risk.max_drawdown(daily_values, twr_flows)
    max_dd_pct = max_dd_result * 100 if max_dd_result is not None else None

    # Krzywa wartości i tabela rok-po-roku wyłącznie z `portfolio_history`
    # (przeliczana nocnym jobem) — NIEZALEŻNE od dzisiejszej ceny/kursu,
    # więc renderują się nawet gdy dzisiejszy poll jeszcze nie zdążył.
    chart_points = [
        {"date": r["date"], "value_pln": r["market_value_pln"],
         "value_eur": r["market_value_eur"],
         "benchmark_pln": None, "benchmark_eur": None}
        for r in history_rows]
    years: dict[str, dict] = {}
    for r in history_rows:
        y = r["date"][:4]
        years.setdefault(y, {"start_pln": r["market_value_pln"],
                             "start_eur": r["market_value_eur"]})
        years[y]["end_pln"] = r["market_value_pln"]
        years[y]["end_eur"] = r["market_value_eur"]
    yearly_returns = []
    for y in sorted(years):
        start, end = years[y]["start_pln"], years[y]["end_pln"]
        pct = ((end - start) / start * 100) if start else None
        yearly_returns.append({
            "year": y, "start_pln": start, "end_pln": end,
            "start_eur": years[y]["start_eur"], "end_eur": years[y]["end_eur"],
            "pct": pct})

    if price_eur and eurpln_rate:
        today = date.today().isoformat()
        current_qty = sum(r["qty_remaining"] for r in taxlots.open_lots(conn))
        xirr_flows = analytics_returns.build_xirr_cashflows(
            conn, today, current_qty, price_eur)
        xirr_result = analytics_returns.xirr(xirr_flows)
        xirr_pct = xirr_result * 100 if xirr_result is not None else None

        twr_result = analytics_returns.twr(daily_values, twr_flows)
        twr_pct = twr_result * 100 if twr_result is not None else None

        sharpe = analytics_risk.sharpe_ratio(
            daily_values, cfg["risk_free_rate_pct"], cashflows=twr_flows)
        volatility_result = analytics_risk.volatility_annualized(
            daily_values, cashflows=twr_flows)
        volatility_pct = volatility_result * 100 if volatility_result is not None else None

        attribution = analytics_attribution.decompose(conn, price_eur, eurpln_rate)

        # Krok 28.1 — poprawka błędu: `xirr_flows` to gotówka w EUR
        # (`build_xirr_cashflows` używa `price_eur`/`net_received_eur`), a
        # OMXH25 jest zarejestrowany z `currency="EUR"` (main.py, ensure_instrument)
        # — `counterfactual()` zawsze liczył w EUR. Wcześniej wynik trafiał
        # do zmiennej `benchmark_today_pln` i renderował się z jednostką „zł"
        # bez żadnej konwersji: liczba w EUR pokazywana jako PLN (różnica
        # rzędu kursu EUR/PLN, ~4x zawyżenie/zaniżenie). Poprawka: liczyć
        # jawnie EUR, dokładać PLN przez ten sam kurs co reszta strony.
        benchmark_today_eur = analytics_benchmark.counterfactual(
            conn, xirr_flows, ids["omxh25"])
        benchmark_today_pln = (
            benchmark_today_eur * eurpln_rate if benchmark_today_eur is not None else None)

        bench_series_eur = dict(analytics_benchmark.counterfactual_series(
            conn, xirr_flows, ids["omxh25"], [r["date"] for r in history_rows]))
        for point in chart_points:
            b_eur = bench_series_eur.get(point["date"])
            point["benchmark_eur"] = b_eur
            rate = rate_by_date.get(point["date"])
            point["benchmark_pln"] = (
                b_eur * rate if b_eur is not None and rate is not None else None)

    return {
        "xirr_pct": xirr_pct, "twr_pct": twr_pct, "attribution": attribution,
        "benchmark_today_pln": benchmark_today_pln, "benchmark_today_eur": benchmark_today_eur,
        "sharpe": sharpe, "volatility_pct": volatility_pct, "max_dd_pct": max_dd_pct,
        "chart_points": chart_points, "yearly_returns": yearly_returns,
        "has_history": bool(history_rows),
    }
