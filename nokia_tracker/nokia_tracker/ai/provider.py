"""Łańcuch providerów AI: local (freellmapi) → gemini → anthropic, wg
ai_primary/ai_fallback z ustawień (BLUEPRINT §1). Każde ogniwo, które rzuci
AIProviderError, oddaje głos następnemu — dopiero gdy WSZYSTKIE zawiodą,
analyze() rzuca dalej. anthropic jest zawsze dostępne jako trzecie,
opcjonalne ogniwo na końcu łańcucha, jeśli skonfigurowany klucz i nie jest
już jednym z dwóch pierwszych.
"""
from __future__ import annotations

import logging
import sqlite3

from . import anthropic_api, gemini, openai_compat, usage
from .errors import AIProviderError

logger = logging.getLogger(__name__)

# Ostatni provider, który faktycznie obsłużył wywołanie — w pamięci procesu
# (restart add-onu daje świeży start, jak ratelimit._consecutive_failures).
# Czytane przez sensors.py::ai_values() dla diagnostycznego ai_provider_active.
_active = ["off"]


def active_provider() -> str:
    return _active[0]


def _call(name: str, cfg: dict, prompt: str, schema: dict, schema_name: str,
         max_tokens: int) -> tuple[dict, int]:
    if name == "local":
        return openai_compat.call(prompt, schema, schema_name, max_tokens,
                                  cfg["local_llm_base_url"], cfg["local_llm_api_key"],
                                  cfg["local_llm_model"])
    if name == "gemini":
        return gemini.call(prompt, schema, schema_name, max_tokens,
                           cfg["gemini_api_key"], cfg["gemini_model"])
    if name == "anthropic":
        return anthropic_api.call(prompt, schema, schema_name, max_tokens,
                                  cfg["anthropic_api_key"], cfg["anthropic_model"])
    raise AIProviderError(f"nieznany provider AI: {name}")


def _model_for(name: str, cfg: dict) -> str:
    return {"local": cfg["local_llm_model"], "gemini": cfg["gemini_model"],
           "anthropic": cfg["anthropic_model"]}.get(name, name)


def analyze(conn: sqlite3.Connection, cfg: dict, task: str, prompt: str,
           schema: dict, max_tokens: int) -> dict:
    """cfg: settings.get_settings(conn) + klucze API z ENV (local_llm_api_key,
    gemini_api_key, anthropic_api_key) — te ostatnie NIE żyją w tabeli
    settings, patrz settings.py. Zwraca sparsowany JSON pierwszego ogniwa,
    które się powiedzie. Rzuca AIProviderError, gdy limit dzienny już
    wyczerpany albo WSZYSTKIE ogniwa zawiodą."""
    if not usage.allow(conn, cfg["ai_max_calls_per_day"]):
        _active[0] = "off"
        raise AIProviderError(
            f"ai: dzienny limit {cfg['ai_max_calls_per_day']} wywołań wyczerpany")

    chain = [cfg["ai_primary"]]
    if cfg["ai_fallback"] and cfg["ai_fallback"] != "none":
        chain.append(cfg["ai_fallback"])
    if "anthropic" not in chain and cfg.get("anthropic_api_key"):
        chain.append("anthropic")

    last_err: Exception | None = None
    for name in chain:
        if name == "off":
            continue
        try:
            parsed, total_tokens = _call(name, cfg, prompt, schema, task, max_tokens)
        except AIProviderError as exc:
            logger.warning("AI: ogniwo '%s' nieudane (%s), próbuję kolejne", name, exc)
            last_err = exc
            continue
        usage.record_call(conn, name, _model_for(name, cfg), task, total_tokens)
        _active[0] = name
        return parsed

    _active[0] = "off"
    raise last_err or AIProviderError("ai: wszystkie ogniwa łańcucha zawiodły")
