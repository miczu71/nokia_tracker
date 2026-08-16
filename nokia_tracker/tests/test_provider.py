"""Orkiestracja łańcucha AI: kolejność ogniw, fallback po błędzie, dzienny
limit, automatyczne trzecie ogniwo anthropic (BLUEPRINT §1, krok 6)."""
import pytest

from nokia_tracker import ratelimit
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
    ratelimit._consecutive_failures.clear()
    ratelimit._opened_at.clear()
    yield
    provider._active[0] = "off"
    ratelimit._consecutive_failures.clear()
    ratelimit._opened_at.clear()


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


def test_analyze_respects_daily_limit_scoped_to_one_provider(conn, monkeypatch):
    # Krok 29: limit dzienny sprawdzany PER OGNIWO, nie raz globalnie przed
    # łańcuchem — 'none' fallback izoluje test do samego 'local', żeby
    # sprawdzić że jego własny limit działa niezależnie od pozostałych.
    calls = []
    monkeypatch.setattr(openai_compat, "call",
                        lambda *a, **kw: (calls.append(1), ({"ok": True}, 1))[1])
    cfg = _cfg(ai_fallback="none", ai_max_calls_per_day_local=1)
    provider.analyze(conn, cfg, "score_news", "prompt", SCHEMA, 2000)
    with pytest.raises(AIProviderError):
        provider.analyze(conn, cfg, "score_news", "prompt", SCHEMA, 2000)
    assert len(calls) == 1  # drugie wywołanie nie dotarło do 'local' (limit wyczerpany)


def test_analyze_falls_through_when_one_provider_exhausts_its_own_limit(conn, monkeypatch):
    # Krok 29: naprawa realnego błędu — wyczerpanie limitu PŁATNEGO ogniwa
    # (albo tu: darmowego 'local') nie może już blokować DRUGIEGO ogniwa w
    # łańcuchu. Przed tą zmianą usage.allow() był sprawdzany raz, globalnie,
    # przed całą pętlą — drugie wywołanie analyze() rzucałoby tu wyjątek bez
    # nawet próby gemini.
    local_calls = []
    monkeypatch.setattr(openai_compat, "call",
                        lambda *a, **kw: (local_calls.append(1), ({"ok": "local"}, 1))[1])
    monkeypatch.setattr(gemini, "call", lambda *a, **kw: ({"ok": "gemini"}, 5))
    cfg = _cfg(ai_max_calls_per_day_local=1)  # ai_fallback zostaje domyślne: gemini
    first = provider.analyze(conn, cfg, "score_news", "prompt", SCHEMA, 2000)
    assert first == {"ok": "local"}
    second = provider.analyze(conn, cfg, "score_news", "prompt", SCHEMA, 2000)
    assert second == {"ok": "gemini"}
    assert len(local_calls) == 1  # local nie wywołane drugi raz, budżet wyczerpany
    assert provider.active_provider() == "gemini"


def test_analyze_raises_when_all_providers_daily_limits_exhausted(conn, monkeypatch):
    monkeypatch.setattr(openai_compat, "call", lambda *a, **kw: ({"ok": "local"}, 1))
    monkeypatch.setattr(gemini, "call", lambda *a, **kw: ({"ok": "gemini"}, 1))
    cfg = _cfg(ai_max_calls_per_day_local=1, ai_max_calls_per_day=1)
    provider.analyze(conn, cfg, "score_news", "prompt", SCHEMA, 2000)  # local zużywa swój limit
    provider.analyze(conn, cfg, "score_news", "prompt", SCHEMA, 2000)  # gemini zużywa swój limit
    with pytest.raises(AIProviderError):
        provider.analyze(conn, cfg, "score_news", "prompt", SCHEMA, 2000)


def test_analyze_zero_local_limit_means_unlimited(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(openai_compat, "call",
                        lambda *a, **kw: (calls.append(1), ({"ok": True}, 1))[1])
    cfg = _cfg(ai_fallback="none", ai_max_calls_per_day_local=0)
    for _ in range(5):
        provider.analyze(conn, cfg, "score_news", "prompt", SCHEMA, 2000)
    assert len(calls) == 5


def test_analyze_local_limit_falls_back_to_shared_limit_when_unset(conn, monkeypatch):
    # cfg bez ai_max_calls_per_day_local (np. stary settings.get_settings()
    # przed migracją opcji) — 'local' musi nadal respektować JAKIŚ limit,
    # nie zachowywać się jak nieograniczony.
    calls = []
    monkeypatch.setattr(openai_compat, "call",
                        lambda *a, **kw: (calls.append(1), ({"ok": True}, 1))[1])
    cfg = _cfg(ai_fallback="none")
    del cfg["ai_max_calls_per_day"]
    cfg["ai_max_calls_per_day"] = 1
    provider.analyze(conn, cfg, "score_news", "prompt", SCHEMA, 2000)
    with pytest.raises(AIProviderError):
        provider.analyze(conn, cfg, "score_news", "prompt", SCHEMA, 2000)
    assert len(calls) == 1


def test_analyze_skips_fallback_none(conn, monkeypatch):
    monkeypatch.setattr(openai_compat, "call",
                        lambda *a, **kw: (_ for _ in ()).throw(AIProviderError("local down")))
    called = []
    monkeypatch.setattr(gemini, "call", lambda *a, **kw: (called.append(1), ({}, 1))[1])
    with pytest.raises(AIProviderError):
        provider.analyze(conn, _cfg(ai_fallback="none"), "score_news", "prompt", SCHEMA, 2000)
    assert called == []  # 'none' oznacza brak fallbacku, gemini nigdy nie wywołany


# --- circuit breaker (martwe ogniwo pomijane bez marnowania czasu na wywołanie) ---

def test_analyze_skips_link_with_open_circuit(conn, monkeypatch):
    for _ in range(3):
        ratelimit.record_failure("local")
    called = []
    monkeypatch.setattr(openai_compat, "call", lambda *a, **kw: (called.append(1), ({}, 1))[1])
    monkeypatch.setattr(gemini, "call", lambda *a, **kw: ({"ok": "gemini"}, 5))
    result = provider.analyze(conn, _cfg(), "score_news", "prompt", SCHEMA, 2000)
    assert result == {"ok": "gemini"}
    assert called == []  # obwód 'local' otwarty -> openai_compat.call nigdy nie wywołane


def test_analyze_skips_open_circuit_without_consuming_its_daily_budget(conn, monkeypatch):
    # Pominięcie ogniwa przez otwarty obwód nie może wyglądać jak "wywołanie" w
    # liczniku ai_usage — inaczej providerowi wracającemu z cooldownu zostałby
    # ubyty dzień limitu bez ani jednego realnego zapytania.
    for _ in range(3):
        ratelimit.record_failure("local")
    monkeypatch.setattr(gemini, "call", lambda *a, **kw: ({"ok": "gemini"}, 5))
    provider.analyze(conn, _cfg(ai_max_calls_per_day_local=1), "score_news",
                     "prompt", SCHEMA, 2000)
    from nokia_tracker.ai import usage
    assert usage.calls_today(conn, "local") == 0


def test_analyze_reopens_link_after_cooldown(conn, monkeypatch):
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
    monkeypatch.setattr(openai_compat, "call",
                        lambda *a, **kw: (_ for _ in ()).throw(AIProviderError("local down")))
    monkeypatch.setattr(gemini, "call", lambda *a, **kw: ({"ok": "gemini"}, 5))
    provider.analyze(conn, _cfg(), "score_news", "prompt", SCHEMA, 2000)
    assert ratelimit.provider_status("local") == "degraded"
    assert ratelimit.provider_status("gemini") == "ok"
