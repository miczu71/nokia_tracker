"""Kontrakt providera kursów — zamiana źródła danych = jedna klasa, zero
zmian w resztę systemu (patrz docs/BLUEPRINT.md sekcja 1)."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Candle


class QuoteProviderError(Exception):
    """Błąd providera zrozumiały dla warstwy wyżej (quotes.py loguje i idzie dalej)."""


class QuoteProvider(ABC):
    name: str = "base"

    @abstractmethod
    def fetch(self, symbol: str, granularity: str, since: str | None = None
              ) -> list[Candle]:
        """Zwraca świece dla symbolu.

        granularity: 'daily' | 'intraday'.
        since: ISO8601, tylko podpowiedź dla providera (np. do wyboru range) —
        wywołujący i tak filtruje/upsertuje po unikalnym (instrument, ts, granularity).
        """
        raise NotImplementedError
