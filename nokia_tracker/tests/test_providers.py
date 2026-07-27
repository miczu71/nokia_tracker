"""Testy providera Yahoo — zero żywego HTTP, wyłącznie fixture'y z realnych
odpowiedzi (zweryfikowane na żywym NOKIA.HE 2026-07-27, patrz BLUEPRINT §1)."""
import json
from pathlib import Path

import pytest

from nokia_tracker.providers.base import QuoteProviderError
from nokia_tracker.providers.yahoo import YahooQuoteProvider, _pick_daily_range

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture
def fixture_ok():
    return json.loads((FIXTURES / "yahoo_chart_nokia_5d.json").read_text())


@pytest.fixture
def fixture_404():
    return json.loads((FIXTURES / "yahoo_chart_error_404.json").read_text())


def test_fetch_parses_real_fixture(conn, monkeypatch, fixture_ok):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params))
        return _FakeResponse(200, fixture_ok)

    monkeypatch.setattr("nokia_tracker.providers.yahoo.requests.get", fake_get)
    provider = YahooQuoteProvider(conn)
    candles = provider.fetch("NOKIA.HE", "daily")

    assert len(calls) == 1
    assert len(candles) == 5
    # Ostatnia świeca fixture'a: close 8.262..., zgodnie z realną odpowiedzią.
    assert candles[-1].close == pytest.approx(8.26200008392334)
    assert candles[0].close == pytest.approx(9.38599967956543)
    # ts musi być poprawnym ISO8601 (epoch -> UTC)
    assert candles[0].ts.startswith("20")
    assert "+00:00" in candles[0].ts or candles[0].ts.endswith("Z")


def test_fetch_uses_cache_on_second_call(conn, monkeypatch, fixture_ok):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return _FakeResponse(200, fixture_ok)

    monkeypatch.setattr("nokia_tracker.providers.yahoo.requests.get", fake_get)
    provider = YahooQuoteProvider(conn, cache_ttl_seconds=300)
    provider.fetch("NOKIA.HE", "daily")
    provider.fetch("NOKIA.HE", "daily")

    assert len(calls) == 1  # drugie wywołanie trafia w cache, zero HTTP


def test_fetch_raises_on_error_response(conn, monkeypatch, fixture_404):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse(404, fixture_404)

    monkeypatch.setattr("nokia_tracker.providers.yahoo.requests.get", fake_get)
    provider = YahooQuoteProvider(conn)
    with pytest.raises(QuoteProviderError):
        provider.fetch("NIEISTNIEJE.XX", "daily")


def test_fetch_intraday_uses_5m_interval(conn, monkeypatch, fixture_ok):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(params)
        return _FakeResponse(200, fixture_ok)

    monkeypatch.setattr("nokia_tracker.providers.yahoo.requests.get", fake_get)
    provider = YahooQuoteProvider(conn)
    provider.fetch("NOKIA.HE", "intraday")

    assert seen["interval"] == "5m"
    assert seen["range"] == "1d"


def test_pick_daily_range_none_since_defaults_5y():
    assert _pick_daily_range(None) == "5y"


def test_pick_daily_range_recent_since_picks_short_range():
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=3)).date().isoformat()
    assert _pick_daily_range(since) == "5d"


def test_candle_skips_null_close(conn, monkeypatch, fixture_ok):
    # Wstrzykujemy dziurę (close=None) na jednej pozycji — świeca musi
    # zniknąć z wyniku, nie wywalić się na float(None).
    broken = json.loads(json.dumps(fixture_ok))
    broken["chart"]["result"][0]["indicators"]["quote"][0]["close"][2] = None

    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse(200, broken)

    monkeypatch.setattr("nokia_tracker.providers.yahoo.requests.get", fake_get)
    provider = YahooQuoteProvider(conn)
    candles = provider.fetch("NOKIA.HE", "daily")

    assert len(candles) == 4  # 5 - 1 dziura
