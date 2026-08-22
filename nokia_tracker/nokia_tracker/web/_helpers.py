"""Pomocnicy współdzieleni przez kilka modułów tras. Dawne funkcje modułowe
`web.py::_is_future_date`/`web.py::_ai_keys`, przeniesione bez zmiany treści
(E3 — docs/ROADMAP_V3.md)."""
from __future__ import annotations

import os
from datetime import date


def _is_future_date(date_str: str) -> bool:
    """Krok 16 (§8.2): NBP zwraca HTTP 400 dla dat przyszłych — bez tej
    walidacji `fx_nbp.rate_on_or_before` podnosi `QuoteProviderError`, który
    nie jest łapany w `tax/lots.py`/`tax/dividends.py`, więc formularz
    kończy się gołym 500 zamiast czytelnego komunikatu. Puste/niepoprawne
    daty NIE są tu odrzucane — to i tak zgłosi się inaczej (np. pusty
    `acquired_date`), walidujemy tylko to, co potrafimy jednoznacznie ocenić."""
    try:
        return date.fromisoformat(date_str) > date.today()
    except (TypeError, ValueError):
        return False


def _ai_keys() -> dict:
    """Klucze API z ENV — NIE z tabeli settings (patrz settings.py)."""
    return {
        "local_llm_api_key": os.environ.get("LOCAL_LLM_API_KEY", ""),
        "gemini_api_key": os.environ.get("GEMINI_API_KEY", ""),
        "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
    }
