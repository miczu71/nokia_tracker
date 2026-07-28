"""Avanza: kształt odpowiedzi zweryfikowany na żywo 2026-07-28 dla orderbookId
Nokii (52784), patrz tests/fixtures/avanza_stock_nokia.json (prawdziwa
odpowiedź) i providers/avanza.py."""
import json
from pathlib import Path

import pytest

from nokia_tracker.providers import avanza

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.text = json.dumps(body)
        self._body = body

    def json(self):
        return self._body


@pytest.fixture
def fixture_ok():
    return json.loads((FIXTURES / "avanza_stock_nokia.json").read_text())


def test_fetch_quote_no_orderbook_id_returns_none_without_network(conn, monkeypatch):
    calls = []
    monkeypatch.setattr("nokia_tracker.providers.avanza.requests.get",
                        lambda *a, **kw: calls.append(1))
    assert avanza.fetch_quote(conn, "") is None
    assert len(calls) == 0


def test_fetch_quote_parses_real_fixture(conn, monkeypatch, fixture_ok):
    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse(200, fixture_ok)

    monkeypatch.setattr("nokia_tracker.providers.avanza.requests.get", fake_get)
    result = avanza.fetch_quote(conn, "52784")

    assert result["price"] == 7.93
    assert result["high"] == 8.336
    assert result["low"] == 7.838
    assert result["prev_close"] == pytest.approx(8.222)
    assert result["updated_ms"] is not None


def test_fetch_quote_uses_cache_on_second_call(conn, monkeypatch, fixture_ok):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(1)
        return _FakeResponse(200, fixture_ok)

    monkeypatch.setattr("nokia_tracker.providers.avanza.requests.get", fake_get)
    avanza.fetch_quote(conn, "52784")
    avanza.fetch_quote(conn, "52784")

    assert len(calls) == 1


def test_fetch_quote_missing_last_price_returns_none(conn, monkeypatch):
    body = {"quote": {"highest": 8.3, "lowest": 7.8}}  # brak "last"

    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse(200, body)

    monkeypatch.setattr("nokia_tracker.providers.avanza.requests.get", fake_get)
    assert avanza.fetch_quote(conn, "52784") is None


def test_fetch_quote_error_status_returns_none_not_raises(conn, monkeypatch):
    # Świadome odstępstwo od providers/finnhub.py (który przy nieoczekiwanym
    # kodzie HTTP podnosi QuoteProviderError): Avanza to API nieoficjalne i
    # dodatkowe/opcjonalne, awaria nie może przerwać publish_sensors().
    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse(500, {})

    monkeypatch.setattr("nokia_tracker.providers.avanza.requests.get", fake_get)
    assert avanza.fetch_quote(conn, "52784") is None
