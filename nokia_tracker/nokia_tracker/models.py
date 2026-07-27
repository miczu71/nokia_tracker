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
