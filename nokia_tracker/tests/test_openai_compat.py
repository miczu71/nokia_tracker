"""openai_compat (freellmapi) — testy na zapisanych kształtach odpowiedzi
(BLUEPRINT §5: zero żywego HTTP w testach), w tym przejście 502->fallback
po wyczerpaniu prób na tym samym modelu (zmierzone: awarie upstreamu to
HTTP 502, nie 429)."""
import json

import pytest

from nokia_tracker.ai import openai_compat
from nokia_tracker.ai.errors import AIProviderError


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.text = json.dumps(body)
        self._body = body

    def json(self):
        return self._body


SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}


def test_model_supports_json_schema_true(monkeypatch):
    body = {"data": [{"id": "gemini-3.5-flash", "supported_parameters": ["response_format"]}]}
    monkeypatch.setattr("nokia_tracker.ai.openai_compat.requests.get",
                        lambda url, headers=None, timeout=None: _FakeResponse(200, body))
    assert openai_compat.model_supports_json_schema(
        "http://x/v1", "key", "gemini-3.5-flash") is True


def test_model_supports_json_schema_false_for_auto(monkeypatch):
    body = {"data": [{"id": "auto", "supported_parameters": ["temperature"]}]}
    monkeypatch.setattr("nokia_tracker.ai.openai_compat.requests.get",
                        lambda url, headers=None, timeout=None: _FakeResponse(200, body))
    assert openai_compat.model_supports_json_schema("http://x/v1", "key", "auto") is False


def test_model_supports_json_schema_none_on_network_error(monkeypatch):
    def _raise(*a, **kw):
        import requests
        raise requests.RequestException("boom")
    monkeypatch.setattr("nokia_tracker.ai.openai_compat.requests.get", _raise)
    assert openai_compat.model_supports_json_schema("http://x/v1", "key", "m") is None


def test_call_parses_documented_shape(monkeypatch):
    body = {
        "choices": [{"message": {"content": json.dumps({"x": 1})}}],
        "usage": {"total_tokens": 250},
    }
    monkeypatch.setattr("nokia_tracker.ai.openai_compat.requests.post",
                        lambda url, headers=None, json=None, timeout=None: _FakeResponse(200, body))
    result, tokens = openai_compat.call("prompt", SCHEMA, "result", 2000,
                                        "http://x/v1", "key", "gemini-3.5-flash")
    assert result == {"x": 1}
    assert tokens == 250


def test_call_raises_on_http_error(monkeypatch):
    monkeypatch.setattr("nokia_tracker.ai.openai_compat.requests.post",
                        lambda url, headers=None, json=None, timeout=None: _FakeResponse(500, {}))
    with pytest.raises(AIProviderError):
        openai_compat.call("prompt", SCHEMA, "result", 2000, "http://x/v1", "key", "m")


def test_call_raises_on_malformed_content(monkeypatch):
    body = {"choices": [{"message": {"content": "not json"}}], "usage": {}}
    monkeypatch.setattr("nokia_tracker.ai.openai_compat.requests.post",
                        lambda url, headers=None, json=None, timeout=None: _FakeResponse(200, body))
    with pytest.raises(AIProviderError):
        openai_compat.call("prompt", SCHEMA, "result", 2000, "http://x/v1", "key", "m")


def test_call_raises_after_502_retries_exhausted(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(1)
        return _FakeResponse(502, {"error": "provider_error"})

    monkeypatch.setattr("nokia_tracker.ai.openai_compat.requests.post", fake_post)
    with pytest.raises(AIProviderError):
        openai_compat.call("prompt", SCHEMA, "result", 2000, "http://x/v1", "key", "m")
    assert len(calls) == 3  # domyślne max_attempts w ratelimit.backoff_retry


def test_call_raises_min_max_tokens_still_calls_with_floor(monkeypatch):
    """max_tokens<1500 nie blokuje wywołania — call() podnosi je na czas
    tego wywołania (zmierzone: poniżej progu router obcina JSON)."""
    captured = {}
    body = {"choices": [{"message": {"content": json.dumps({"x": 1})}}], "usage": {}}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["max_tokens"] = json["max_tokens"]
        return _FakeResponse(200, body)

    monkeypatch.setattr("nokia_tracker.ai.openai_compat.requests.post", fake_post)
    openai_compat.call("prompt", SCHEMA, "result", 300, "http://x/v1", "key", "m")
    assert captured["max_tokens"] == 1500
