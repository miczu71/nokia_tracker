"""Klient OpenAI-compatible — freellmapi (lokalny router) jako primary.

Wszystko poniżej to ZMIERZONE zachowanie na żywym routerze użytkownika
(192.168.0.106:3003), nie założenia — patrz docs/BLUEPRINT.md §1:
1. json_schema DZIAŁA, ale tylko na NAZWANYCH modelach — 'auto'/'fusion'
   nie mają 'response_format' w supported_parameters (sprawdzane przez
   /v1/models, patrz model_supports_json_schema()).
2. max_tokens MUSI być >=1500 — przy 300 router zwrócił HTTP 502
   'truncated JSON (finish_reason=length)': tokeny reasoningu (modele typu
   gpt-oss) liczą się do budżetu PRZED treścią odpowiedzi.
3. Awarie upstreamu to HTTP 502 'provider_error', NIE 429 — stąd
   ratelimit.backoff_retry z domyślnym retryable_statuses=(429,502).
4. usage.total_tokens bywa większe niż prompt+completion (tokeny myślenia)
   — czytane wprost, nie sumowane z komponentów.
"""
from __future__ import annotations

import json
import logging

import requests

from .. import ratelimit
from .errors import AIProviderError

logger = logging.getLogger(__name__)

_MIN_MAX_TOKENS = 1500


def model_supports_json_schema(base_url: str, api_key: str, model: str,
                               timeout: int = 15) -> bool | None:
    """None, gdy nie da się ustalić (błąd sieci/klucza) — provider.py loguje
    ostrzeżenie, ale i tak próbuje (fail-open, nie fail-closed: brak
    pewności nie powinien blokować jedynego skonfigurowanego providera)."""
    try:
        resp = requests.get(f"{base_url}/models",
                            headers=_headers(api_key), timeout=timeout)
        if resp.status_code != 200:
            return None
        for m in resp.json().get("data", []):
            if m.get("id") == model:
                return "response_format" in (m.get("supported_parameters") or [])
        return None
    except requests.RequestException:
        return None


def _headers(api_key: str) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def call(prompt: str, schema: dict, schema_name: str, max_tokens: int,
        base_url: str, api_key: str, model: str) -> tuple[dict, int]:
    """Zwraca (sparsowany_json, total_tokens). Rzuca AIProviderError na
    każdą porażkę — provider.py decyduje co dalej (fallback)."""
    if max_tokens < _MIN_MAX_TOKENS:
        logger.warning("ai_max_tokens=%d poniżej bezpiecznego minimum %d — "
                       "podnoszę na czas tego wywołania (zmierzone: mniej "
                       "obcina JSON w połowie u modeli reasoningowych)",
                       max_tokens, _MIN_MAX_TOKENS)
        max_tokens = _MIN_MAX_TOKENS

    url = f"{base_url}/chat/completions"
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
    }

    def _do_request():
        return requests.post(url, headers=_headers(api_key), json=body, timeout=90)

    resp = ratelimit.backoff_retry(_do_request, provider="freellmapi")
    if resp is None:
        raise AIProviderError("freellmapi: brak odpowiedzi (sieć)")
    if resp.status_code != 200:
        raise AIProviderError(f"freellmapi: HTTP {resp.status_code} — {resp.text[:300]}")

    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
        result = json.loads(content)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise AIProviderError(f"freellmapi: nie udało się sparsować odpowiedzi — {exc}") from exc

    total_tokens = (data.get("usage") or {}).get("total_tokens", 0)
    return result, total_tokens
