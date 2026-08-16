"""Metryki ryzyka portfela (krok 32, docs/PLAN_KROK_32_ryzyko.md).

Czysty Python (bez numpy — ta sama zasada co `analytics/returns.py` z kroku 25,
powód: musl/armv7). `_TRADING_DAYS_PER_YEAR = 252` jest spójna z tym, co
faktycznie jest w `portfolio_history`: tabela jest budowana wyłącznie z dni,
dla których istnieje notowanie (`history.py::rebuild()` iteruje
`quotes.closes_in_range`), czyli już same dni sesyjne — nie osobne założenie.

Wszystkie funkcje liczą na `market_value_eur` (nie PLN), spójnie z
`returns.py::twr()`/`build_twr_cashflows()`, żeby efekt walutowy EUR/PLN
(pokazany osobno w atrybucji) nie zniekształcał miary ryzyka samej pozycji.
"""
from __future__ import annotations

import math
import statistics

_TRADING_DAYS_PER_YEAR = 252


def _daily_returns(daily_values: list[tuple[str, float]]) -> list[float]:
    """`daily_values` chronologicznie. Pomija dni, gdzie poprzednia wartość
    to 0 (portfel jeszcze pusty)."""
    returns = []
    for i in range(1, len(daily_values)):
        v_prev = daily_values[i - 1][1]
        v_cur = daily_values[i][1]
        if v_prev:
            returns.append((v_cur - v_prev) / v_prev)
    return returns


def max_drawdown(daily_values: list[tuple[str, float]]) -> float | None:
    """Największy spadek od bieżącego maksimum (peak-to-trough), ujemny
    ułamek (np. `-0.25` = -25%). `None` gdy pusta seria; `0.0` na 1 punkcie."""
    if not daily_values:
        return None
    peak = daily_values[0][1]
    max_dd = 0.0
    for _, v in daily_values:
        if v > peak:
            peak = v
        if peak:
            dd = (v - peak) / peak
            if dd < max_dd:
                max_dd = dd
    return max_dd


def volatility_annualized(daily_values: list[tuple[str, float]]) -> float | None:
    """Odchylenie standardowe próbki dziennych zwrotów * `sqrt(252)`. `None`
    przy < 2 obserwacjach zwrotu (< 3 punktów) — stdev próbki niezdefiniowany
    dla n=1."""
    returns = _daily_returns(daily_values)
    if len(returns) < 2:
        return None
    return statistics.stdev(returns) * math.sqrt(_TRADING_DAYS_PER_YEAR)


def sharpe_ratio(daily_values: list[tuple[str, float]],
                  risk_free_rate_pct: float) -> float | None:
    """`(annualizowany średni zwrot - stopa wolna od ryzyka) / annualizowana
    zmienność`. `None` przy < 3 punktach lub zerowej zmienności (dzielenie
    przez 0). `risk_free_rate_pct` jako procent (np. `3.0` = 3%)."""
    returns = _daily_returns(daily_values)
    if len(returns) < 2:
        return None
    vol = statistics.stdev(returns)
    if vol == 0:
        return None
    annual_return = statistics.mean(returns) * _TRADING_DAYS_PER_YEAR
    annual_vol = vol * math.sqrt(_TRADING_DAYS_PER_YEAR)
    return (annual_return - risk_free_rate_pct / 100) / annual_vol
