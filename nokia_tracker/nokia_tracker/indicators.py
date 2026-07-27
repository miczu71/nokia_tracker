"""Wskaźniki techniczne — czysty Python, bez Pandas/NumPy (patrz BLUEPRINT §1:
brak wheeli musl dla numpy na armv7/aarch64). Na serii 5 lat dziennych świec
(~1300 punktów) liczy się w milisekundach."""
from __future__ import annotations

import statistics


def sma(values: list[float], period: int) -> float | None:
    """Prosta średnia krocząca z ostatnich `period` wartości."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: list[float], period: int) -> float | None:
    """Wykładnicza średnia krocząca (seed = SMA pierwszych `period` wartości)."""
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def rsi(values: list[float], period: int = 14) -> float | None:
    """RSI metodą Wildera. Wymaga co najmniej period+1 wartości."""
    if len(values) < period + 1:
        return None
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def daily_returns(values: list[float]) -> list[float]:
    """Zwroty dzienne (ułamkowe, nie %): (close[i]-close[i-1])/close[i-1]."""
    return [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values))
        if values[i - 1] != 0
    ]


def volatility_pct(values: list[float], period: int = 30) -> float | None:
    """Odchylenie standardowe zwrotów dziennych z ostatnich `period` sesji, w %.
    Nie annualizowane — okno 30-dniowe samo w sobie jest jednostką odniesienia."""
    returns = daily_returns(values)
    if len(returns) < period:
        return None
    window = returns[-period:]
    if len(window) < 2:
        return None
    return statistics.stdev(window) * 100


def beta(returns_a: list[float], returns_b: list[float]) -> float | None:
    """Beta = kowariancja(a,b) / wariancja(b), na sparowanych, wyrównanych
    seriach zwrotów (ta sama liczba i kolejność okresów)."""
    n = min(len(returns_a), len(returns_b))
    if n < 2:
        return None
    a = returns_a[-n:]
    b = returns_b[-n:]
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n)) / n
    var_b = sum((x - mean_b) ** 2 for x in b) / n
    if var_b == 0:
        return None
    return cov / var_b


_TREND_LABELS = {
    "strong_up": "silny wzrost", "up": "wzrost", "flat": "bok",
    "down": "spadek", "strong_down": "silny spadek",
}


def trend(values: list[float], lookback: int = 10) -> str | None:
    """Etykieta trendu z % zmiany na przestrzeni ostatnich `lookback` sesji.
    Progi: >5% silny wzrost, >1% wzrost, <-5% silny spadek, <-1% spadek, inaczej bok."""
    if len(values) < lookback + 1:
        return None
    window = values[-(lookback + 1):]
    first, last = window[0], window[-1]
    if first == 0:
        return None
    pct = (last - first) / first * 100
    if pct > 5:
        key = "strong_up"
    elif pct > 1:
        key = "up"
    elif pct < -5:
        key = "strong_down"
    elif pct < -1:
        key = "down"
    else:
        key = "flat"
    return _TREND_LABELS[key]
