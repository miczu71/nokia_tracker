"""Brak żywej weryfikacji (brak klucza) — kształt z dokumentacji Finnhub
/company-news (patrz docstring providers/news_finnhub.py)."""
import json

import pytest

from nokia_tracker.providers import news_finnhub
from nokia_tracker.providers.base import QuoteProviderError


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


_DOCUMENTED_SHAPE = [
    {"category": "company", "datetime": 1785000000, "headline": "Nokia wins 5G deal",
     "id": 1, "image": "", "related": "NOK", "source": "Reuters",
     "summary": "Nokia signed...", "url": "https://reuters.com/x"},
]


def test_fetch_no_key_returns_none_without_network(conn, monkeypatch):
    calls = []
    monkeypatch.setattr("nokia_tracker.providers.news_finnhub.requests.get",
                        lambda *a, **kw: calls.append(1))
    assert news_finnhub.fetch(conn, "NOK", "") is None
    assert len(calls) == 0


def test_fetch_parses_documented_shape(conn, monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.providers.news_finnhub.requests.get",
        lambda url, params=None, timeout=None: _FakeResponse(200, _DOCUMENTED_SHAPE))
    items = news_finnhub.fetch(conn, "NOK", "fake-key")
    assert len(items) == 1
    assert items[0]["title"] == "Nokia wins 5G deal"
    assert items[0]["url"] == "https://reuters.com/x"
    assert items[0]["source_name"] == "Reuters"
    assert items[0]["published_at"] is not None


def test_fetch_invalid_key_returns_none(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.providers.news_finnhub.requests.get",
                        lambda url, params=None, timeout=None: _FakeResponse(401, {}))
    assert news_finnhub.fetch(conn, "NOK", "bad-key") is None


def test_fetch_other_error_raises(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.providers.news_finnhub.requests.get",
                        lambda url, params=None, timeout=None: _FakeResponse(500, []))
    with pytest.raises(QuoteProviderError):
        news_finnhub.fetch(conn, "NOK", "fake-key")
