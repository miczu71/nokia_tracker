"""Wywołanie AI przez router freellmapi: jedyne ogniwo, dzienny limit, circuit
breaker, ai_primary='off' (BLUEPRINT §1; od 0.30.0 bez Gemini/Anthropic)."""
import pytest

from nokia_tracker import ratelimit
from nokia_tracker.ai import openai_compat, provider
from nokia_tracker.ai.errors import AIProviderError

SCHEMA = {"type": "object"}


def _cfg(**overrides):
    base = {
        "ai_primary": "local",
        "local_llm_base_url": "http://x/v1", "local_llm_api_key": "lkey",
        "local_llm_model": "gemini-3.5-flash",
        "ai_max_tokens": 4000, "ai_max_calls_per_day_local": 500,
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _reset_active():
    provider._active[0] = "off"
    ratelimit._consecutive_failures.clear()
    ratelimit._opened_at.clear()
    yield
    provider._active[0] = "off"
    ratelimit._consecutive_failures.clear()
    ratelimit._opened_at.clear()


def _fail(*a, **kw):
    raise AIProviderError("local down")


def test_analyze_uses_router_when_it_succeeds(conn, monkeypatch):
    monkeypatch.setattr(openai_compat, "call", lambda *a, **kw: ({"ok": True}, 10))
    result = provider.analyze(conn, _cfg(), "score_news", "prompt", SCHEMA, 2000)
    assert result == {"ok": True}
    assert provider.active_provider() == "local"


def test_analyze_raises_when_router_fails(conn, monkeypatch):
    monkeypatch.setattr(openai_compat, "call", _fail)
    with pytest.raises(AIProviderError):
        provider.analyze(conn, _cfg(), "score_news", "prompt", SCHEMA, 2000)
    assert provider.active_provider() == "off"


def test_analyze_off_makes_no_calls(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(openai_compat, "call",
                        lambda *a, **kw: (calls.append(1), ({"ok": True}, 1))[1])
    with pytest.raises(AIProviderError):
        provider.analyze(conn, _cfg(ai_primary="off"), "score_news", "prompt", SCHEMA, 2000)
    assert calls == []


@pytest.mark.parametrize("legacy", ["gemini", "anthropic"])
def test_analyze_legacy_primary_value_routes_to_router(conn, monkeypatch, legacy):
    # Baza sprzed 0.30.0 mogła mieć ai_primary='gemini'/'anthropic' — dodatek
    # nie zna już tych ogniw, więc każda wartość poza 'off' idzie do routera.
    monkeypatch.setattr(openai_compat, "call", lambda *a, **kw: ({"ok": True}, 1))
    assert provider.analyze(conn, _cfg(ai_primary=legacy), "score_news", "prompt",
                            SCHEMA, 2000) == {"ok": True}
    assert provider.active_provider() == "local"


def test_analyze_respects_daily_limit(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(openai_compat, "call",
                        lambda *a, **kw: (calls.append(1), ({"ok": True}, 1))[1])
    cfg = _cfg(ai_max_calls_per_day_local=1)
    provider.analyze(conn, cfg, "score_news", "prompt", SCHEMA, 2000)
    with pytest.raises(AIProviderError):
        provider.analyze(conn, cfg, "score_news", "prompt", SCHEMA, 2000)
    assert len(calls) == 1  # drugie wywołanie nie dotarło do routera (limit wyczerpany)


def test_analyze_zero_limit_means_unlimited(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(openai_compat, "call",
                        lambda *a, **kw: (calls.append(1), ({"ok": True}, 1))[1])
    cfg = _cfg(ai_max_calls_per_day_local=0)
    for _ in range(5):
        provider.analyze(conn, cfg, "score_news", "prompt", SCHEMA, 2000)
    assert len(calls) == 5


# --- circuit breaker (martwy router pomijany bez marnowania czasu na wywołanie) ---

def test_analyze_skips_router_with_open_circuit(conn, monkeypatch):
    for _ in range(3):
        ratelimit.record_failure("local")
    called = []
    monkeypatch.setattr(openai_compat, "call", lambda *a, **kw: (called.append(1), ({}, 1))[1])
    with pytest.raises(AIProviderError):
        provider.analyze(conn, _cfg(), "score_news", "prompt", SCHEMA, 2000)
    assert called == []  # obwód otwarty -> openai_compat.call nigdy nie wywołane


def test_analyze_skips_open_circuit_without_consuming_daily_budget(conn, monkeypatch):
    # Pominięcie przez otwarty obwód nie może wyglądać jak "wywołanie" w
    # liczniku ai_usage — inaczej po cooldownie ubyłby dzień limitu bez
    # ani jednego realnego zapytania.
    for _ in range(3):
        ratelimit.record_failure("local")
    with pytest.raises(AIProviderError):
        provider.analyze(conn, _cfg(ai_max_calls_per_day_local=1), "score_news",
                         "prompt", SCHEMA, 2000)
    from nokia_tracker.ai import usage
    assert usage.calls_today(conn, "local") == 0


def test_analyze_reopens_router_after_cooldown(conn, monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: t[0])
    for _ in range(3):
        ratelimit.record_failure("local")
    t[0] += ratelimit._CIRCUIT_COOLDOWN_SECONDS
    monkeypatch.setattr(openai_compat, "call", lambda *a, **kw: ({"ok": "local"}, 10))
    result = provider.analyze(conn, _cfg(), "score_news", "prompt", SCHEMA, 2000)
    assert result == {"ok": "local"}
    assert provider.active_provider() == "local"


def test_analyze_records_failure_and_success_in_breaker(conn, monkeypatch):
    monkeypatch.setattr(openai_compat, "call", _fail)
    with pytest.raises(AIProviderError):
        provider.analyze(conn, _cfg(), "score_news", "prompt", SCHEMA, 2000)
    assert ratelimit.provider_status("local") == "degraded"
    monkeypatch.setattr(openai_compat, "call", lambda *a, **kw: ({"ok": True}, 1))
    provider.analyze(conn, _cfg(), "score_news", "prompt", SCHEMA, 2000)
    assert ratelimit.provider_status("local") == "ok"
