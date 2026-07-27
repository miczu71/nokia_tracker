"""Gemini — testy na udokumentowanym kształcie generateContent, w tym
przejście na znane fallbacki lite po wyczerpaniu quoty pierwszego modelu
(wzorzec z fuel_tracker/receipts.py, patrz ai/gemini.py)."""
import json

import pytest

from nokia_tracker.ai import gemini
from nokia_tracker.ai.errors import AIProviderError

SCHEMA = {"type": "object", "properties": {"x": {"type": "integer"}},
         "required": ["x"], "additionalProperties": False}


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.text = json.dumps(body)
        self._body = body

    def json(self):
        return self._body


def test_strip_additional_properties_removes_nested():
    stripped = gemini._strip_additional_properties(
        {"type": "object", "additionalProperties": False,
         "properties": {"a": {"type": "object", "additionalProperties": False}}})
    assert "additionalProperties" not in stripped
    assert "additionalProperties" not in stripped["properties"]["a"]


def test_call_no_key_raises():
    with pytest.raises(AIProviderError):
        gemini.call("prompt", SCHEMA, "result", 1000, "", "gemini-3.1-flash-lite")


def test_call_parses_documented_shape(monkeypatch):
    body = {
        "candidates": [{"content": {"parts": [{"text": json.dumps({"x": 1})}]}}],
        "usageMetadata": {"totalTokenCount": 120},
    }
    monkeypatch.setattr("nokia_tracker.ai.gemini.requests.post",
                        lambda url, params=None, json=None, timeout=None: _FakeResponse(200, body))
    result, tokens = gemini.call("prompt", SCHEMA, "result", 1000, "key", "gemini-3.1-flash-lite")
    assert result == {"x": 1}
    assert tokens == 120


def test_call_falls_back_to_next_model_on_quota_error(monkeypatch):
    calls = []

    def fake_post(url, params=None, json=None, timeout=None):
        calls.append(url)
        if "gemini-3.1-flash-lite" in url:
            return _FakeResponse(429, {"error": "quota exceeded"})
        body = {"candidates": [{"content": {"parts": [{"text": '{"x": 2}'}]}}],
               "usageMetadata": {"totalTokenCount": 50}}
        return _FakeResponse(200, body)

    monkeypatch.setattr("nokia_tracker.ai.gemini.requests.post", fake_post)
    result, tokens = gemini.call("prompt", SCHEMA, "result", 1000, "key", "gemini-3.1-flash-lite")
    assert result == {"x": 2}
    assert len(calls) == 2  # pierwszy model padł, drugi (2.5-flash-lite) się powiódł


def test_call_raises_when_all_models_fail(monkeypatch):
    monkeypatch.setattr("nokia_tracker.ai.gemini.requests.post",
                        lambda url, params=None, json=None, timeout=None: _FakeResponse(500, {}))
    with pytest.raises(AIProviderError):
        gemini.call("prompt", SCHEMA, "result", 1000, "key", "gemini-3.1-flash-lite")


def test_call_raises_on_malformed_response(monkeypatch):
    body = {"candidates": []}
    monkeypatch.setattr("nokia_tracker.ai.gemini.requests.post",
                        lambda url, params=None, json=None, timeout=None: _FakeResponse(200, body))
    with pytest.raises(AIProviderError):
        gemini.call("prompt", SCHEMA, "result", 1000, "key", "gemini-3.1-flash-lite")
