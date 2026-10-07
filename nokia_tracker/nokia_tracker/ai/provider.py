"""Wywołanie AI przez lokalny router freellmapi (BLUEPRINT §1). Jedno ogniwo:
fallback między dostawcami (Gemini, Groq itd.) robi sam router, dodatek nie
trzyma już żadnych kluczy zewnętrznych dostawców (0.30.0). ai_primary='off'
wyłącza AI całkowicie.
"""
from __future__ import annotations

import logging
import sqlite3

from .. import ratelimit
from . import openai_compat, usage
from .errors import AIProviderError

logger = logging.getLogger(__name__)

# Ostatni provider, który faktycznie obsłużył wywołanie — w pamięci procesu
# (restart add-onu daje świeży start, jak ratelimit._consecutive_failures).
# Czytane przez sensors.py::ai_values() dla diagnostycznego ai_provider_active.
_active = ["off"]


def active_provider() -> str:
    return _active[0]


_NAME = "local"


def _max_calls(cfg: dict) -> int:
    """Dzienny limit wywołań routera (ai_max_calls_per_day_local, 0 = bez limitu)."""
    return cfg.get("ai_max_calls_per_day_local", 500)


def analyze(conn: sqlite3.Connection, cfg: dict, task: str, prompt: str,
           schema: dict, max_tokens: int) -> dict:
    """cfg: settings.get_settings(conn) + local_llm_api_key z ENV (klucz NIE
    żyje w tabeli settings, patrz settings.py). Zwraca sparsowany JSON z routera.
    Rzuca AIProviderError, gdy AI wyłączone, obwód otwarty, dzienny limit
    wyczerpany albo router zawiódł."""
    _active[0] = "off"
    # Każda wartość poza 'off' (także stara 'gemini'/'anthropic' w bazie sprzed
    # 0.30.0) oznacza router — jedyne ogniwo, jakie dodatek jeszcze zna.
    if cfg["ai_primary"] == "off":
        raise AIProviderError("ai: wyłączone (ai_primary=off)")
    if ratelimit.is_circuit_open(_NAME):
        logger.info("AI: router w cooldownie (obwód otwarty), pomijam")
        raise AIProviderError("ai: router w cooldownie po serii porażek")
    max_per_day = _max_calls(cfg)
    if not usage.allow(conn, _NAME, max_per_day):
        logger.info("AI: router wyczerpał dzienny limit (%d), pomijam", max_per_day)
        raise AIProviderError(f"ai: dzienny limit routera ({max_per_day}) wyczerpany")
    try:
        parsed, total_tokens = openai_compat.call(
            prompt, schema, task, max_tokens, cfg["local_llm_base_url"],
            cfg["local_llm_api_key"], cfg["local_llm_model"])
    except AIProviderError as exc:
        ratelimit.record_failure(_NAME, str(exc))
        logger.warning("AI: router nieudany (%s)", exc)
        raise
    ratelimit.record_success(_NAME)
    usage.record_call(conn, _NAME, cfg["local_llm_model"], task, total_tokens)
    _active[0] = _NAME
    return parsed
