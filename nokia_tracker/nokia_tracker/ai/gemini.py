"""Klient Gemini (Google Generative Language API) — drugie ogniwo łańcucha AI,
fallback po lokalnym freellmapi (BLUEPRINT §1).

REST bezpośrednio, nie SDK google-generativeai — jedno proste wywołanie nie
uzasadnia kolejnej zależności w requirements.txt (w przeciwieństwie do
ai/anthropic_api.py, gdzie oficjalny SDK jest wymagany przez zasadę projektu
dla prawdziwego Anthropic API).

Modele lite mają wysoki darmowy limit (reference_gemini_free_tier.md:
2.0-flash ma limit 0, 2.5-flash tylko 20/dzień) — call() próbuje najpierw
model z ustawień, potem znane fallbacki lite, żeby wyczerpana dzienna quota
jednego modelu nie zabijała całego ogniwa 'gemini' w łańcuchu.
"""
from __future__ import annotations

import json
import logging

import requests

from .errors import AIProviderError

logger = logging.getLogger(__name__)

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_KNOWN_FALLBACKS = ("gemini-3.1-flash-lite", "gemini-2.5-flash-lite")


def _strip_additional_properties(node):
    """Gemini responseSchema (podzbiór OpenAPI) odrzuca additionalProperties —
    nasze schematy (patrz prompts.py) mają je wszędzie dla strict JSON Schema."""
    if isinstance(node, dict):
        return {k: _strip_additional_properties(v) for k, v in node.items()
                if k != "additionalProperties"}
    if isinstance(node, list):
        return [_strip_additional_properties(v) for v in node]
    return node


def _call_one(prompt: str, schema: dict, max_tokens: int, api_key: str, model: str
             ) -> tuple[dict, int]:
    resp = requests.post(
        f"{_BASE_URL}/models/{model}:generateContent",
        params={"key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": _strip_additional_properties(schema),
                "max_output_tokens": max_tokens,
            },
        },
        timeout=90,
    )
    if resp.status_code != 200:
        raise AIProviderError(f"gemini ({model}): HTTP {resp.status_code} — {resp.text[:300]}")

    data = resp.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise AIProviderError(f"gemini ({model}): nie udało się sparsować odpowiedzi — {exc}") from exc

    total_tokens = (data.get("usageMetadata") or {}).get("totalTokenCount", 0)
    return result, total_tokens


def call(prompt: str, schema: dict, schema_name: str, max_tokens: int,
        api_key: str, model: str) -> tuple[dict, int]:
    """Zwraca (sparsowany_json, total_tokens). schema_name nieużywane (Gemini
    nie nazywa schematu jak OpenAI-compatible) — zachowane dla identycznej
    sygnatury z ai/openai_compat.py::call(), żeby provider.py wołał oba
    jednolicie. Próbuje model z ustawień, potem znane fallbacki lite; rzuca
    ostatni błąd dopiero gdy WSZYSTKIE zawiodą."""
    if not api_key:
        raise AIProviderError("gemini: brak klucza API")

    models_to_try = [model] + [m for m in _KNOWN_FALLBACKS if m != model]
    last_err: Exception | None = None
    for m in models_to_try:
        try:
            return _call_one(prompt, schema, max_tokens, api_key, m)
        except AIProviderError as exc:
            logger.warning("gemini: model %s nieudany, próbuję kolejny — %s", m, exc)
            last_err = exc
    raise last_err
