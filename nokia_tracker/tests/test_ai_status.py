"""ai/status.py — zrzut stanu łańcucha AI dla karty "Stan AI" (/ustawienia)
i paska nad czatem (/asystent), krok 29. Zero żywego HTTP w testach —
requests.get mockowany, jak w test_openai_compat.py."""
import json

import pytest

from nokia_tracker import ratelimit
from nokia_tracker.ai import openai_compat, status
from nokia_tracker.ai import usage as ai_usage


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def _cfg(**overrides):
    base = {
        "ai_primary": "local", "ai_fallback": "gemini",
        "local_llm_base_url": "http://192.168.0.106:3003/v1",
        "local_llm_api_key": "lkey", "local_llm_model": "gemini-3.1-flash-lite",
        "gemini_api_key": "gkey", "gemini_model": "gemini-3.1-flash-lite",
        "anthropic_api_key": "", "anthropic_model": "claude-haiku-4-5-20251001",
        "ai_max_calls_per_day": 40, "ai_max_calls_per_day_local": 500,
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _reset_ratelimit_and_cache():
    ratelimit._consecutive_failures.clear()
    ratelimit._opened_at.clear()
    ratelimit._last_error.clear()
    status._cache.clear()
    yield
    ratelimit._consecutive_failures.clear()
    ratelimit._opened_at.clear()
    ratelimit._last_error.clear()
    status._cache.clear()


def _no_models(monkeypatch):
    monkeypatch.setattr(openai_compat, "list_models", lambda *a, **kw: [])


def _no_router(monkeypatch):
    def _raise(*a, **kw):
        import requests
        raise requests.RequestException("boom")
    monkeypatch.setattr("nokia_tracker.ai.status.requests.get", _raise)


def test_snapshot_reports_calls_and_remaining_per_provider(conn, monkeypatch):
    _no_models(monkeypatch)
    _no_router(monkeypatch)
    ai_usage.record_call(conn, "local", "m", "score_news", 10)
    ai_usage.record_call(conn, "local", "m", "score_news", 10)

    snap = status.snapshot(conn, _cfg())

    local = next(p for p in snap["providers"] if p["name"] == "local")
    assert local["calls_today"] == 2
    assert local["max_per_day"] == 500
    assert local["remaining_today"] == 498
    assert local["key_present"] is True

    anthropic = next(p for p in snap["providers"] if p["name"] == "anthropic")
    assert anthropic["key_present"] is False
    assert anthropic["calls_today"] == 0


def test_snapshot_zero_limit_means_no_remaining_cap(conn, monkeypatch):
    _no_models(monkeypatch)
    _no_router(monkeypatch)
    snap = status.snapshot(conn, _cfg(ai_max_calls_per_day_local=0))
    local = next(p for p in snap["providers"] if p["name"] == "local")
    assert local["remaining_today"] is None  # bez limitu, nic do odliczenia


def test_snapshot_includes_circuit_status_and_last_error(conn, monkeypatch):
    _no_models(monkeypatch)
    _no_router(monkeypatch)
    for _ in range(3):
        ratelimit.record_failure("local", "freellmapi: HTTP 502")
    snap = status.snapshot(conn, _cfg())
    local = next(p for p in snap["providers"] if p["name"] == "local")
    assert local["circuit_status"] == "down"
    assert local["last_error"] == "freellmapi: HTTP 502"
    assert local["cooldown_remaining_seconds"] is not None


def test_snapshot_active_provider_reflects_last_successful_call(conn, monkeypatch):
    _no_models(monkeypatch)
    _no_router(monkeypatch)
    from nokia_tracker.ai import provider
    provider._active[0] = "gemini"
    try:
        snap = status.snapshot(conn, _cfg())
        assert snap["active_provider"] == "gemini"
    finally:
        provider._active[0] = "off"


def test_snapshot_router_unreachable_degrades_to_none(conn, monkeypatch):
    _no_models(monkeypatch)
    _no_router(monkeypatch)
    snap = status.snapshot(conn, _cfg())
    assert snap["router"]["reachable"] is False
    assert snap["router"]["admin_auth"] is False


def test_snapshot_router_admin_requires_auth_success(conn, monkeypatch):
    _no_models(monkeypatch)
    monkeypatch.setattr(
        "nokia_tracker.ai.status.requests.get",
        lambda url, headers=None, timeout=None: _FakeResponse(
            401, {"error": {"message": "Authentication required"}}))
    snap = status.snapshot(conn, _cfg())
    assert snap["router"]["reachable"] is True
    assert snap["router"]["admin_auth"] is False
    assert snap["router"]["health"] is None


def test_snapshot_router_admin_success_pulls_health_and_summary(conn, monkeypatch):
    _no_models(monkeypatch)
    calls = []

    def _get(url, headers=None, timeout=None):
        calls.append(url)
        if url.endswith("/api/health"):
            return _FakeResponse(200, {"status": "ok"})
        return _FakeResponse(200, {"requests_today": 12})

    monkeypatch.setattr("nokia_tracker.ai.status.requests.get", _get)
    snap = status.snapshot(conn, _cfg())
    assert snap["router"]["admin_auth"] is True
    assert snap["router"]["health"] == {"status": "ok"}
    assert snap["router"]["summary"] == {"requests_today": 12}
    assert any(u.endswith("/api/health") for u in calls)
    assert any(u.endswith("/api/analytics/summary") for u in calls)


def test_snapshot_reuses_precomputed_local_models_without_a_second_fetch(conn, monkeypatch):
    # /ustawienia już woła openai_compat.list_models() dla selecta modelu —
    # snapshot() nie może odpytać routera drugi raz o to samo.
    def _boom(*a, **kw):
        raise AssertionError("list_models() nie powinno być wywołane drugi raz")
    monkeypatch.setattr(openai_compat, "list_models", _boom)
    _no_router(monkeypatch)
    snap = status.snapshot(conn, _cfg(), local_models=[
        {"id": "gemini-3.1-flash-lite", "supports_schema": True}])
    assert snap["local_models_count"] == 1
    assert snap["local_model_supported"] is True


def test_snapshot_never_raises_on_missing_local_base_url(conn, monkeypatch):
    _no_models(monkeypatch)
    snap = status.snapshot(conn, _cfg(local_llm_base_url=""))
    assert snap["router"] is None
    assert snap["local_models_count"] == 0


def test_router_probe_is_cached_within_ttl(conn, monkeypatch):
    _no_models(monkeypatch)
    calls = []

    def _get(url, headers=None, timeout=None):
        calls.append(url)
        return _FakeResponse(200, {"status": "ok"})

    monkeypatch.setattr("nokia_tracker.ai.status.requests.get", _get)
    status.snapshot(conn, _cfg())
    status.snapshot(conn, _cfg())
    # Drugi snapshot w tej samej sekundzie nie powinien odpytać routera drugi raz.
    assert len(calls) == 2  # health+summary, RAZ (cache), nie 4
