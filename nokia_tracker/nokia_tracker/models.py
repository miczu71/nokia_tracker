"""Modele danych współdzielone między providerami a warstwą bazy."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candle:
    """Jedna świeca OHLCV. `ts` to ISO8601 UTC (np. '2026-07-24T00:00:00+00:00')."""
    ts: str
    close: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None


@dataclass(frozen=True)
class AnalystConsensus:
    """Konsensus cen docelowych analityków, jeden snapshot (docs/PLAN_0_26_0_konsensus.md).
    `trend` to najnowszy wpis Yahoo `recommendationTrend` (liczba głosów
    strongBuy/buy/hold/sell/strongSell) albo None — stockanalysis.com (fallback)
    tego rozkładu nie dostarcza."""
    low: float | None
    mean: float | None
    median: float | None
    high: float | None
    n_analysts: int | None
    rating: str | None
    currency: str
    source: str
    trend: dict | None = None
