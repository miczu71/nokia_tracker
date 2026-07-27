"""Brak żywej weryfikacji (brak klucza) — kształt z dokumentacji MarketAux
(patrz docstring providers/news_marketaux.py)."""
import json

import pytest

from nokia_tracker.providers import news_marketaux
from nokia_tracker.providers.base import QuoteProviderError


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


_DOCUMENTED_SHAPE = {
    "meta": {"found": 1, "returned": 1, "limit": 20, "page": 1},
    "data": [
        {"uuid": "abc", "title": "Nokia beats estimates", "description": "...",
         "keywords": "", "snippet": "Nokia reported...",
         "url": "https://example.com/news/1", "image_url": "",
         "language": "en", "published_at": "2026-07-27T10:00:00.000000Z",
         "source": "example.com", "relevance_score": None, "entities": []},
    ],
}


def test_fetch_no_key_returns_none_without_network(conn, monkeypatch):
    calls = []
    monkeypatch.setattr("nokia_tracker.providers.news_marketaux.requests.get",
                        lambda *a, **kw: calls.append(1))
    assert news_marketaux.fetch(conn, "NOK", "") is None
    assert len(calls) == 0


def test_fetch_parses_documented_shape(conn, monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.providers.news_marketaux.requests.get",
        lambda url, params=None, timeout=None: _FakeResponse(200, _DOCUMENTED_SHAPE))
    items = news_marketaux.fetch(conn, "NOK", "fake-key")
    assert len(items) == 1
    assert items[0]["title"] == "Nokia beats estimates"
    assert items[0]["published_at"] == "2026-07-27T10:00:00.000000Z"
    assert items[0]["source_name"] == "example.com"


def test_fetch_forbidden_key_returns_none(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.providers.news_marketaux.requests.get",
                        lambda url, params=None, timeout=None: _FakeResponse(403, {}))
    assert news_marketaux.fetch(conn, "NOK", "bad-key") is None


def test_fetch_other_error_raises(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.providers.news_marketaux.requests.get",
                        lambda url, params=None, timeout=None: _FakeResponse(500, {}))
    with pytest.raises(QuoteProviderError):
        news_marketaux.fetch(conn, "NOK", "fake-key")
