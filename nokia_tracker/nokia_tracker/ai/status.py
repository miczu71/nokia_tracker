"""Zrzut stanu łańcucha AI (krok 29): karta "Stan AI" na /ustawienia i pasek
nad czatem na /asystent. Domknięcie długu z roadmapy 0.8.1 — ratelimit.py
(circuit breaker) i ai/usage.py (liczniki) istniały od kroku 6/7, ale nie
miały żadnego konsumenta w UI, widoczne tylko przez jeden sensor MQTT.

Dodatkowo: osiągalność lokalnego routera freellmapi i, JEŚLI klucz Bearer
skonfigurowany dla `/v1/chat/completions` akurat otwiera też panel admina
routera (`/api/health`, `/api/analytics/summary` — niepewne, zmierzone 401
bez klucza w trakcie planowania tej fali, patrz docs/PLAN_KROK_29_asystent.md),
podstawowe statystyki stamtąd. Bez logowania e-mail+hasło do panelu — jeśli
klucz nie wystarcza, `router.admin_auth` zostaje False i sekcja jest po
prostu pusta. Każdy błąd sieci -> pola None, NIGDY wyjątek — /ustawienia i
/asystent muszą się wyrenderować nawet gdy router jest offline."""
from __future__ import annotations

import logging
import sqlite3
import time

import requests

from .. import ratelimit
from . import openai_compat, provider, usage

logger = logging.getLogger(__name__)

_TIMEOUT = 3
_CACHE_TTL_SECONDS = 60
# Cache w pamięci procesu, klucz = base_url — świadomie ulotny, jak
# ratelimit._consecutive_failures: restart daje świeży start, a 60s
# wystarcza żeby /ustawienia i /asystent (oba wołają snapshot()) nie
# podwoiły ruchu do routera przy odświeżeniu strony w tej samej minucie.
_cache: dict[str, tuple[float, dict]] = {}

_PROVIDERS = ("local", "gemini", "anthropic")
_KEY_FIELDS = {
    "local": "local_llm_api_key", "gemini": "gemini_api_key", "anthropic": "anthropic_api_key",
}


def _admin_root(base_url: str) -> str:
    """'http://host:3003/v1' -> 'http://host:3003' (panel admina routera
    siedzi pod /api/*, jeden poziom nad /v1/*, patrz nagłówek modułu)."""
    return base_url[: -len("/v1")] if base_url.endswith("/v1") else base_url


def _probe_router(base_url: str, api_key: str) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    root = _admin_root(base_url)
    result = {"reachable": False, "admin_auth": False, "health": None, "summary": None}
    try:
        resp = requests.get(f"{root}/api/health", headers=headers, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        logger.info("status: router %s nieosiągalny (%s)", root, exc)
        return result
    result["reachable"] = True
    if resp.status_code != 200:
        return result
    result["admin_auth"] = True
    try:
        result["health"] = resp.json()
    except ValueError:
        result["health"] = None
    try:
        summary_resp = requests.get(f"{root}/api/analytics/summary",
                                    headers=headers, timeout=_TIMEOUT)
        if summary_resp.status_code == 200:
            result["summary"] = summary_resp.json()
    except requests.RequestException as exc:
        logger.info("status: router %s/api/analytics/summary nieosiągalny (%s)", root, exc)
    return result


def _cached_router_probe(base_url: str, api_key: str) -> dict:
    now = time.monotonic()
    cached = _cache.get(base_url)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    result = _probe_router(base_url, api_key)
    _cache[base_url] = (now, result)
    return result


def _provider_snapshot(conn: sqlite3.Connection, name: str, cfg: dict) -> dict:
    calls = usage.calls_today(conn, name)
    max_per_day = provider._max_calls_for(name, cfg)
    remaining = None if max_per_day <= 0 else max(0, max_per_day - calls)
    return {
        "name": name,
        "key_present": bool(cfg.get(_KEY_FIELDS[name])),
        "calls_today": calls,
        "tokens_today": usage.tokens_today(conn, name),
        "max_per_day": max_per_day,
        "remaining_today": remaining,
        "circuit_status": ratelimit.provider_status(name),
        "cooldown_remaining_seconds": ratelimit.cooldown_remaining_seconds(name),
        "last_error": ratelimit.last_error(name),
    }


def snapshot(conn: sqlite3.Connection, cfg: dict,
            local_models: list[dict] | None = None) -> dict:
    """cfg: settings.get_settings(conn) + klucze API z ENV (web.py::_ai_keys —
    te ostatnie nie żyją w tabeli settings). Nigdy nie rzuca — każda sieciowa
    część degraduje do None/[] przy błędzie.

    `local_models`: przekaż listę z `openai_compat.list_models()`, jeśli
    wywołujący już ją pobrał (np. `/ustawienia` woła ją niezależnie dla
    selecta modelu) — inaczej ten sam request do routera leciałby dwa razy
    na jedno wejście na stronę. `None` = pobierz samodzielnie (np. pasek
    statusu na `/asystent`, który nie potrzebuje selecta)."""
    providers = [_provider_snapshot(conn, name, cfg) for name in _PROVIDERS]

    base_url = cfg.get("local_llm_base_url") or ""
    local_api_key = cfg.get("local_llm_api_key", "")
    if local_models is None:
        local_models = openai_compat.list_models(base_url, local_api_key) if base_url else []
    router = _cached_router_probe(base_url, local_api_key) if base_url else None

    return {
        "active_provider": provider.active_provider(),
        "providers": providers,
        "local_models_count": len(local_models),
        "local_model_supported": any(
            m.get("id") == cfg.get("local_llm_model") and m.get("supports_schema")
            for m in local_models),
        "router": router,
    }
