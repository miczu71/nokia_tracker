"""Testy na seriach o ręcznie policzonym, jednoznacznym wyniku — bez Pandas
(BLUEPRINT §1) więc każdy wzór jest tu w 100% czystym Pythonem i musi dać
dokładnie te same liczby, co ręczne wyliczenie."""
import statistics

import pytest

from nokia_tracker import indicators as ind


# --- SMA ---

def test_sma_basic():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert ind.sma(values, 5) == pytest.approx(8.0)  # mean(6..10)


def test_sma_insufficient_data_returns_none():
    assert ind.sma([1, 2, 3], 5) is None


# --- EMA ---

def test_ema_hand_computed():
    # seed = mean(pierwsze 5) = 10; k = 2/6; e = 20*k + 10*(1-k) = 13.3333...
    values = [10, 10, 10, 10, 10, 20]
    assert ind.ema(values, 5) == pytest.approx(13.3333, abs=1e-3)


def test_ema_insufficient_data_returns_none():
    assert ind.ema([1, 2], 5) is None


# --- RSI (Wilder) ---

def test_rsi_monotonic_increasing_is_100():
    values = list(range(1, 17))  # 16 wartości, same wzrosty o 1
    assert ind.rsi(values, 14) == pytest.approx(100.0)


def test_rsi_monotonic_decreasing_is_0():
    values = list(range(16, 0, -1))  # 16 wartości, same spadki o 1
    assert ind.rsi(values, 14) == pytest.approx(0.0)


def test_rsi_insufficient_data_returns_none():
    assert ind.rsi([1] * 10, 14) is None


# --- volatility ---

def test_volatility_pct_matches_stdev_of_returns():
    closes = [100, 101, 99, 102, 98, 103, 97, 104, 96, 105, 95, 106, 94, 107, 93,
              108, 92, 109, 91, 110, 90, 111, 89, 112, 88, 113, 87, 114, 86, 115, 85]
    returns = ind.daily_returns(closes)
    expected = statistics.stdev(returns[-30:]) * 100
    assert ind.volatility_pct(closes, 30) == pytest.approx(expected)


def test_volatility_pct_insufficient_data_returns_none():
    assert ind.volatility_pct([100, 101, 102], 30) is None


# --- beta ---

def test_beta_exact_linear_relationship():
    # a = 2*b dokładnie, zero szumu -> beta MUSI wyjść dokładnie 2.0
    b = [1, 2, 3, 4, 5]
    a = [2, 4, 6, 8, 10]
    assert ind.beta(a, b) == pytest.approx(2.0)


def test_beta_zero_variance_benchmark_returns_none():
    assert ind.beta([1, 2, 3], [5, 5, 5]) is None


def test_beta_insufficient_data_returns_none():
    assert ind.beta([1], [1]) is None


# --- trend ---

def test_trend_strong_up():
    values = [100] + [100] * 9 + [120]  # +20% na przestrzeni 10 sesji
    assert ind.trend(values, lookback=10) == "silny wzrost"


def test_trend_flat():
    values = [100] * 11
    assert ind.trend(values, lookback=10) == "bok"


def test_trend_strong_down():
    values = [100] * 10 + [80]  # -20%
    assert ind.trend(values, lookback=10) == "silny spadek"


def test_trend_boundary_exactly_5pct_is_not_strong():
    values = [100] * 10 + [105]  # dokładnie +5% -> próg ">5" NIE łapie
    assert ind.trend(values, lookback=10) == "wzrost"


def test_trend_insufficient_data_returns_none():
    assert ind.trend([100, 101], lookback=10) is None
