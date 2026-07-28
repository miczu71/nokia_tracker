"""Test na realnej odpowiedzi GDELT pobranej 2026-07-27 (fixture, po
napotkaniu na żywo empirycznego limitu 1 zapytanie/5s)."""
import json
from pathlib import Path

import pytest

from nokia_tracker.providers import news_gdelt
from nokia_tracker.providers.base import QuoteProviderError

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def gdelt_json():
    return json.loads((FIXTURES / "gdelt_nokia.json").read_text())


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body) if isinstance(body, dict) else str(body)

    def json(self):
        return self._body


def test_fetch_parses_real_fixture(conn, monkeypatch, gdelt_json):
    monkeypatch.setattr("nokia_tracker.providers.news_gdelt.requests.get",
                        lambda url, params=None, timeout=None: _FakeResponse(200, gdelt_json))
    items = news_gdelt.fetch(conn, "Nokia Oyj")

    assert len(items) == 5
    first = items[0]
    assert first["url"].startswith("https://")
    assert first["published_at"] == "2026-06-25T02:45:00+00:00"  # z seendate 20260625T024500Z
    assert first["source_name"]  # domain


def test_fetch_uses_cache_on_second_call(conn, monkeypatch, gdelt_json):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(1)
        return _FakeResponse(200, gdelt_json)

    monkeypatch.setattr("nokia_tracker.providers.news_gdelt.requests.get", fake_get)
    news_gdelt.fetch(conn, "Nokia Oyj")
    news_gdelt.fetch(conn, "Nokia Oyj")
    assert len(calls) == 1


def test_fetch_429_rate_limit_raises_after_retries(conn, monkeypatch):
    # Zmierzone na żywo: GDELT odpowiada 429 tekstem, nie JSON-em.
    monkeypatch.setattr("nokia_tracker.providers.news_gdelt.requests.get",
                        lambda url, params=None, timeout=None: _FakeResponse(429, {}))
    monkeypatch.setattr("nokia_tracker.ratelimit.time.sleep", lambda s: None)
    with pytest.raises(QuoteProviderError):
        news_gdelt.fetch(conn, "Nokia Oyj")


def test_fetch_429_sets_cooldown_marker(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.providers.news_gdelt.requests.get",
                        lambda url, params=None, timeout=None: _FakeResponse(429, {}))
    monkeypatch.setattr("nokia_tracker.ratelimit.time.sleep", lambda s: None)
    with pytest.raises(QuoteProviderError):
        news_gdelt.fetch(conn, "Nokia Oyj")

    calls = []
    monkeypatch.setattr("nokia_tracker.providers.news_gdelt.requests.get",
                        lambda url, params=None, timeout=None: calls.append(1))
    # Kolejne wywołanie w cooldownie -> [] bez sięgania do sieci (kluczowe:
    # to samo źródło, które przed chwilą dostało 429, nie jest bombardowane
    # co 30 min, tylko raz na _COOLDOWN_SECONDS).
    items = news_gdelt.fetch(conn, "Nokia Oyj")
    assert items == []
    assert calls == []


def test_fetch_retries_after_cooldown_expires(conn, monkeypatch, gdelt_json):
    monkeypatch.setattr("nokia_tracker.providers.news_gdelt.requests.get",
                        lambda url, params=None, timeout=None: _FakeResponse(429, {}))
    # time.sleep patchowany globalnie (nokia_tracker.ratelimit.time to ten
    # sam obiekt modułu co wbudowany time), więc czasu backoffu NIE symulujemy
    # realnym sleep — cofamy zegar zapisanego cooldownu wprost w bazie.
    monkeypatch.setattr("nokia_tracker.ratelimit.time.sleep", lambda s: None)
    with pytest.raises(QuoteProviderError):
        news_gdelt.fetch(conn, "Nokia Oyj", cooldown_seconds=1)

    from datetime import datetime, timedelta, timezone
    stale = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    conn.execute("UPDATE http_cache SET fetched_at = ? WHERE url = ?",
                (stale, news_gdelt._COOLDOWN_KEY))
    conn.commit()

    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(1)
        return _FakeResponse(200, gdelt_json)

    monkeypatch.setattr("nokia_tracker.providers.news_gdelt.requests.get", fake_get)
    items = news_gdelt.fetch(conn, "Nokia Oyj", cooldown_seconds=1)
    assert len(calls) == 1
    assert len(items) == 5


def test_parse_skips_malformed_seendate():
    items = news_gdelt._parse({"articles": [
        {"title": "X", "url": "https://x.com", "seendate": "not-a-date", "domain": "x.com"},
    ]})
    assert items[0]["published_at"] is None
