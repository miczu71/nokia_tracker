"""anthropic_api — testy na atrapie klienta SDK (NIE żywe Anthropic API:
użytkownik nie dostarczył klucza dla samego add-onu, patrz decyzja w sesji
implementacji). Weryfikuje kształt wywołania output_config.format i
parsowanie odpowiedzi/refusal/usage."""
import json

import pytest

from nokia_tracker.ai import anthropic_api
from nokia_tracker.ai.errors import AIProviderError

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}


class _Block:
    def __init__(self, type_, text=None):
        self.type = type_
        self.text = text


class _Usage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Message:
    def __init__(self, content, stop_reason="end_turn", input_tokens=10, output_tokens=5):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _Usage(input_tokens, output_tokens)


class _FakeMessages:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc:
            raise self._exc
        return self._response


class _FakeClient:
    def __init__(self, messages):
        self.messages = messages


def test_call_no_key_raises():
    with pytest.raises(AIProviderError):
        anthropic_api.call("prompt", SCHEMA, "result", 1000, "", "claude-haiku-4-5-20251001")


def test_call_parses_text_block_and_sums_usage(monkeypatch):
    fake_messages = _FakeMessages(_Message([_Block("text", json.dumps({"x": 1}))],
                                           input_tokens=100, output_tokens=20))
    monkeypatch.setattr("nokia_tracker.ai.anthropic_api.anthropic.Anthropic",
                        lambda api_key: _FakeClient(fake_messages))
    result, tokens = anthropic_api.call("prompt", SCHEMA, "result", 1000, "key",
                                        "claude-haiku-4-5-20251001")
    assert result == {"x": 1}
    assert tokens == 120


def test_call_sends_output_config_format(monkeypatch):
    fake_messages = _FakeMessages(_Message([_Block("text", json.dumps({"x": 1}))]))
    monkeypatch.setattr("nokia_tracker.ai.anthropic_api.anthropic.Anthropic",
                        lambda api_key: _FakeClient(fake_messages))
    anthropic_api.call("prompt", SCHEMA, "result", 1000, "key", "claude-haiku-4-5-20251001")
    sent = fake_messages.calls[0]
    assert sent["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}
    assert sent["model"] == "claude-haiku-4-5-20251001"
    assert sent["max_tokens"] == 1000


def test_call_raises_on_refusal(monkeypatch):
    fake_messages = _FakeMessages(_Message([], stop_reason="refusal"))
    monkeypatch.setattr("nokia_tracker.ai.anthropic_api.anthropic.Anthropic",
                        lambda api_key: _FakeClient(fake_messages))
    with pytest.raises(AIProviderError):
        anthropic_api.call("prompt", SCHEMA, "result", 1000, "key", "claude-haiku-4-5-20251001")


def test_call_raises_on_missing_text_block(monkeypatch):
    fake_messages = _FakeMessages(_Message([_Block("thinking", None)]))
    monkeypatch.setattr("nokia_tracker.ai.anthropic_api.anthropic.Anthropic",
                        lambda api_key: _FakeClient(fake_messages))
    with pytest.raises(AIProviderError):
        anthropic_api.call("prompt", SCHEMA, "result", 1000, "key", "claude-haiku-4-5-20251001")


def test_call_raises_on_malformed_json(monkeypatch):
    fake_messages = _FakeMessages(_Message([_Block("text", "not json")]))
    monkeypatch.setattr("nokia_tracker.ai.anthropic_api.anthropic.Anthropic",
                        lambda api_key: _FakeClient(fake_messages))
    with pytest.raises(AIProviderError):
        anthropic_api.call("prompt", SCHEMA, "result", 1000, "key", "claude-haiku-4-5-20251001")


def test_call_wraps_sdk_api_error(monkeypatch):
    import anthropic as anthropic_sdk

    class _FakeAPIError(anthropic_sdk.APIError):
        def __init__(self):
            pass

    fake_messages = _FakeMessages(exc=_FakeAPIError())
    monkeypatch.setattr("nokia_tracker.ai.anthropic_api.anthropic.Anthropic",
                        lambda api_key: _FakeClient(fake_messages))
    with pytest.raises(AIProviderError):
        anthropic_api.call("prompt", SCHEMA, "result", 1000, "key", "claude-haiku-4-5-20251001")
