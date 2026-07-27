"""Orkiestracja łańcucha AI: kolejność ogniw, fallback po błędzie, dzienny
limit, automatyczne trzecie ogniwo anthropic (BLUEPRINT §1, krok 6)."""
import pytest

from nokia_tracker.ai import anthropic_api, gemini, openai_compat, provider
from nokia_tracker.ai.errors import AIProviderError

SCHEMA = {"type": "object"}


def _cfg(**overrides):
    base = {
        "ai_primary": "local", "ai_fallback": "gemini",
        "local_llm_base_url": "http://x/v1", "local_llm_api_key": "lkey",
        "local_llm_model": "gemini-3.5-flash",
        "gemini_api_key": "gkey", "gemini_model": "gemini-3.1-flash-lite",
        "anthropic_api_key": "", "anthropic_model": "claude-haiku-4-5-20251001",
        "ai_max_tokens": 4000, "ai_max_calls_per_day": 40,
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _reset_active():
    provider._active[0] = "off"
    yield
    provider._active[0] = "off"


def test_analyze_uses_primary_when_it_succeeds(conn, monkeypatch):
    monkeypatch.setattr(openai_compat, "call", lambda *a, **kw: ({"ok": True}, 10))
    result = provider.analyze(conn, _cfg(), "score_news", "prompt", SCHEMA, 2000)
    assert result == {"ok": True}
    assert provider.active_provider() == "local"


def test_analyze_falls_back_to_gemini_on_local_failure(conn, monkeypatch):
    monkeypatch.setattr(openai_compat, "call",
                        lambda *a, **kw: (_ for _ in ()).throw(AIProviderError("local down")))
    monkeypatch.setattr(gemini, "call", lambda *a, **kw: ({"ok": "gemini"}, 5))
    result = provider.analyze(conn, _cfg(), "score_news", "prompt", SCHEMA, 2000)
    assert result == {"ok": "gemini"}
    assert provider.active_provider() == "gemini"


def test_analyze_appends_anthropic_as_third_link_when_key_present(conn, monkeypatch):
    monkeypatch.setattr(openai_compat, "call",
                        lambda *a, **kw: (_ for _ in ()).throw(AIProviderError("local down")))
    monkeypatch.setattr(gemini, "call",
                        lambda *a, **kw: (_ for _ in ()).throw(AIProviderError("gemini down")))
    monkeypatch.setattr(anthropic_api, "call", lambda *a, **kw: ({"ok": "anthropic"}, 3))
    result = provider.analyze(conn, _cfg(anthropic_api_key="akey"), "score_news",
                              "prompt", SCHEMA, 2000)
    assert result == {"ok": "anthropic"}
    assert provider.active_provider() == "anthropic"


def test_analyze_raises_when_all_links_fail(conn, monkeypatch):
    monkeypatch.setattr(openai_compat, "call",
                        lambda *a, **kw: (_ for _ in ()).throw(AIProviderError("local down")))
    monkeypatch.setattr(gemini, "call",
                        lambda *a, **kw: (_ for _ in ()).throw(AIProviderError("gemini down")))
    with pytest.raises(AIProviderError):
        provider.analyze(conn, _cfg(), "score_news", "prompt", SCHEMA, 2000)
    assert provider.active_provider() == "off"


def test_analyze_respects_daily_limit(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(openai_compat, "call",
                        lambda *a, **kw: (calls.append(1), ({"ok": True}, 1))[1])
    cfg = _cfg(ai_max_calls_per_day=1)
    provider.analyze(conn, cfg, "score_news", "prompt", SCHEMA, 2000)
    with pytest.raises(AIProviderError):
        provider.analyze(conn, cfg, "score_news", "prompt", SCHEMA, 2000)
    assert len(calls) == 1  # drugie wywołanie nie dotarło do żadnego providera


def test_analyze_skips_fallback_none(conn, monkeypatch):
    monkeypatch.setattr(openai_compat, "call",
                        lambda *a, **kw: (_ for _ in ()).throw(AIProviderError("local down")))
    called = []
    monkeypatch.setattr(gemini, "call", lambda *a, **kw: (called.append(1), ({}, 1))[1])
    with pytest.raises(AIProviderError):
        provider.analyze(conn, _cfg(ai_fallback="none"), "score_news", "prompt", SCHEMA, 2000)
    assert called == []  # 'none' oznacza brak fallbacku, gemini nigdy nie wywołany
