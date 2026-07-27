"""Klient prawdziwego Anthropic API (api.anthropic.com) — opcjonalne trzecie
ogniwo łańcucha AI, oficjalny SDK `anthropic` (BLUEPRINT §1 — zasada projektu:
oficjalny SDK zamiast surowego HTTP tam, gdzie istnieje).

Obsługuje WYŁĄCZNIE prawdziwe Anthropic API z kluczem użytkownika
(anthropic_api_key) — router freellmapi ma też endpoint /v1/messages
Anthropic-compatible, ale primary idzie przez ai/openai_compat.py
(/v1/chat/completions) dla architektonicznej spójności łańcucha (jeden
kształt klienta na dwa pierwsze ogniwa). Structured output przez
output_config.format (json_schema) — NIE tool-use forcing, jak zakładał
wcześniejszy szkic blueprintu (ustalone przez skill claude-api 2026-07-27,
bo output_config.format jest aktualnym, zalecanym mechanizmem).
"""
from __future__ import annotations

import json
import logging

import anthropic

from .errors import AIProviderError

logger = logging.getLogger(__name__)


def call(prompt: str, schema: dict, schema_name: str, max_tokens: int,
        api_key: str, model: str) -> tuple[dict, int]:
    """Zwraca (sparsowany_json, total_tokens). schema_name nieużywany
    (output_config.format nie nazywa schematu) — zachowany dla identycznej
    sygnatury z ai/openai_compat.py::call() i ai/gemini.py::call()."""
    if not api_key or not model:
        raise AIProviderError("anthropic_api: brak klucza lub modelu")

    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.APIError as exc:
        raise AIProviderError(f"anthropic_api: {exc}") from exc

    if response.stop_reason == "refusal":
        raise AIProviderError("anthropic_api: model odmówił odpowiedzi (stop_reason=refusal)")

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise AIProviderError("anthropic_api: brak bloku tekstowego w odpowiedzi")
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIProviderError(f"anthropic_api: nie udało się sparsować odpowiedzi — {exc}") from exc

    usage = response.usage
    total_tokens = (usage.input_tokens or 0) + (usage.output_tokens or 0)
    return result, total_tokens
