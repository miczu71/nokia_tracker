"""Metryki ryzyka portfela (krok 32, docs/PLAN_KROK_32_ryzyko.md; poprawka
0.16.1 — netowanie cashflow, patrz niżej).

Czysty Python (bez numpy — ta sama zasada co `analytics/returns.py` z kroku 25,
powód: musl/armv7). `_TRADING_DAYS_PER_YEAR = 252` jest spójna z tym, co
faktycznie jest w `portfolio_history`: tabela jest budowana wyłącznie z dni,
dla których istnieje notowanie (`history.py::rebuild()` iteruje
`quotes.closes_in_range`), czyli już same dni sesyjne — nie osobne założenie.

Wszystkie funkcje liczą na `market_value_eur` (nie PLN), spójnie z
`returns.py::twr()`/`build_twr_cashflows()`, żeby efekt walutowy EUR/PLN
(pokazany osobno w atrybucji) nie zniekształcał miary ryzyka samej pozycji.

**Poprawka 0.16.1 — bug znaleziony przy weryfikacji na produkcji (0.16.0
pokazywało zmienność annualizowaną +1015%, matematycznie niemożliwe dla
realnej akcji):** dzienny zwrot liczony naiwnie z `market_value_eur`
(`(v_i - v_i-1) / v_i-1`) miesza REALNY ruch rynku z zmianą ilości akcji
(vesting LTI, dopasowanie ESPP, sprzedaże) — dzień dopłaty/vestingu wygląda
jak +1000% "zwrotu", chociaż cena się nie zmieniła. Dokładnie ten sam problem
`returns.py::twr()` już rozwiązuje, netując cashflow danego dnia — `cashflows`
(opcjonalny, ten sam kształt co `build_twr_cashflows()`) robi to samo tutaj.
Bez `cashflows` (domyślnie `None`) zachowanie jest identyczne jak przed 0.16.1
(zero cashflow = zero adjustycji, nie osobna gałąź kodu)."""
from __future__ import annotations

import math
import statistics

_TRADING_DAYS_PER_YEAR = 252


def _daily_returns(daily_values: list[tuple[str, float]],
                    cashflows: list[tuple[str, float]] | None = None) -> list[float]:
    """`daily_values` chronologicznie. Pomija dni, gdzie poprzednia wartość
    to 0 (portfel jeszcze pusty). `cashflows`: wartość dodana(+)/odjęta(-) tego
    dnia (wkład/wypłata), odejmowana przed policzeniem zwrotu — ta sama
    definicja co `returns.py::twr()`."""
    cf_by_date: dict[str, float] = {}
    for d, amt in (cashflows or []):
        cf_by_date[d] = cf_by_date.get(d, 0.0) + amt

    returns = []
    for i in range(1, len(daily_values)):
        v_prev = daily_values[i - 1][1]
        d_cur, v_cur = daily_values[i]
        if v_prev:
            cf = cf_by_date.get(d_cur, 0.0)
            returns.append((v_cur - v_prev - cf) / v_prev)
    return returns


def max_drawdown(daily_values: list[tuple[str, float]],
                  cashflows: list[tuple[str, float]] | None = None) -> float | None:
    """Największy spadek od bieżącego maksimum (peak-to-trough), ujemny
    ułamek (np. `-0.25` = -25%), liczony na indeksie zwrotów netto z wkładów
    (patrz `_daily_returns`) — kontrybucja/wypłata nie tworzy fałszywego
    szczytu/dołka. `None` gdy pusta seria; `0.0` na 1 punkcie."""
    if not daily_values:
        return None
    index = [1.0]
    for r in _daily_returns(daily_values, cashflows):
        index.append(index[-1] * (1 + r))

    peak = index[0]
    max_dd = 0.0
    for v in index:
        if v > peak:
            peak = v
        if peak:
            dd = (v - peak) / peak
            if dd < max_dd:
                max_dd = dd
    return max_dd


def volatility_annualized(daily_values: list[tuple[str, float]],
                           cashflows: list[tuple[str, float]] | None = None) -> float | None:
    """Odchylenie standardowe próbki dziennych zwrotów netto z wkładów
    * `sqrt(252)`. `None` przy < 2 obserwacjach zwrotu (< 3 punktów) — stdev
    próbki niezdefiniowany dla n=1."""
    returns = _daily_returns(daily_values, cashflows)
    if len(returns) < 2:
        return None
    return statistics.stdev(returns) * math.sqrt(_TRADING_DAYS_PER_YEAR)


def sharpe_ratio(daily_values: list[tuple[str, float]],
                  risk_free_rate_pct: float,
                  cashflows: list[tuple[str, float]] | None = None) -> float | None:
    """`(annualizowany średni zwrot netto z wkładów - stopa wolna od ryzyka)
    / annualizowana zmienność`. `None` przy < 3 punktach lub zerowej
    zmienności (dzielenie przez 0). `risk_free_rate_pct` jako procent
    (np. `3.0` = 3%)."""
    returns = _daily_returns(daily_values, cashflows)
    if len(returns) < 2:
        return None
    vol = statistics.stdev(returns)
    if vol == 0:
        return None
    annual_return = statistics.mean(returns) * _TRADING_DAYS_PER_YEAR
    annual_vol = vol * math.sqrt(_TRADING_DAYS_PER_YEAR)
    return (annual_return - risk_free_rate_pct / 100) / annual_vol
