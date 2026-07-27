"""Finnhub: brak żywej weryfikacji (użytkownik nie dostarczył klucza) —
testy na udokumentowanym, stabilnym kształcie /quote (patrz docstring
providers/finnhub.py)."""
import json

import pytest

from nokia_tracker.providers import finnhub
from nokia_tracker.providers.base import QuoteProviderError


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.text = json.dumps(body)  # jak prawdziwe requests.Response.text — nie repr()
        self._body = body

    def json(self):
        return self._body


def test_fetch_quote_no_key_returns_none_without_network(conn, monkeypatch):
    calls = []
    monkeypatch.setattr("nokia_tracker.providers.finnhub.requests.get",
                        lambda *a, **kw: calls.append(1))
    assert finnhub.fetch_quote(conn, "NOK", "") is None
    assert len(calls) == 0


def test_fetch_quote_parses_documented_shape(conn, monkeypatch):
    body = {"c": 8.5, "h": 8.7, "l": 8.3, "o": 8.4, "pc": 8.3, "t": 1785000000}
    monkeypatch.setattr("nokia_tracker.providers.finnhub.requests.get",
                        lambda url, params=None, timeout=None: _FakeResponse(200, body))
    result = finnhub.fetch_quote(conn, "NOK", "fake-key")
    assert result == {"price": 8.5, "high": 8.7, "low": 8.3, "open": 8.4, "prev_close": 8.3}


def test_fetch_quote_zero_price_treated_as_no_data(conn, monkeypatch):
    body = {"c": 0, "h": 0, "l": 0, "o": 0, "pc": 0, "t": 0}
    monkeypatch.setattr("nokia_tracker.providers.finnhub.requests.get",
                        lambda url, params=None, timeout=None: _FakeResponse(200, body))
    assert finnhub.fetch_quote(conn, "NIEISTNIEJE", "fake-key") is None


def test_fetch_quote_invalid_key_returns_none(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.providers.finnhub.requests.get",
                        lambda url, params=None, timeout=None: _FakeResponse(401, {}))
    assert finnhub.fetch_quote(conn, "NOK", "bad-key") is None


def test_fetch_quote_other_error_raises(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.providers.finnhub.requests.get",
                        lambda url, params=None, timeout=None: _FakeResponse(500, {}))
    with pytest.raises(QuoteProviderError):
        finnhub.fetch_quote(conn, "NOK", "fake-key")


def test_fetch_quote_uses_cache_on_second_call(conn, monkeypatch):
    calls = []
    body = {"c": 8.5, "h": 8.7, "l": 8.3, "o": 8.4, "pc": 8.3, "t": 1785000000}

    def fake_get(url, params=None, timeout=None):
        calls.append(1)
        return _FakeResponse(200, body)

    monkeypatch.setattr("nokia_tracker.providers.finnhub.requests.get", fake_get)
    finnhub.fetch_quote(conn, "NOK", "fake-key")
    finnhub.fetch_quote(conn, "NOK", "fake-key")
    assert len(calls) == 1
