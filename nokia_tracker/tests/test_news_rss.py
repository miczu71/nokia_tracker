"""Test na realnym Google News RSS pobranym 2026-07-27 (fixture, zero
żywego HTTP)."""
from pathlib import Path

import pytest

from nokia_tracker.providers import news_rss
from nokia_tracker.providers.base import QuoteProviderError

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def rss_xml():
    return (FIXTURES / "google_news_rss_nokia.xml").read_text()


class _FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


def test_fetch_parses_real_fixture(conn, monkeypatch, rss_xml):
    monkeypatch.setattr("nokia_tracker.providers.news_rss.requests.get",
                        lambda url, headers=None, timeout=None: _FakeResponse(200, rss_xml))
    items = news_rss.fetch(conn, "https://news.google.com/rss/search?q=Nokia+Oyj")

    assert len(items) == 13
    first = items[0]
    assert "Nokia" in first["title"]
    assert first["url"].startswith("https://news.google.com/")
    assert first["published_at"] is not None
    assert first["source_name"]  # Google News RSS zawsze podaje <source>


def test_fetch_uses_cache_on_second_call(conn, monkeypatch, rss_xml):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(1)
        return _FakeResponse(200, rss_xml)

    monkeypatch.setattr("nokia_tracker.providers.news_rss.requests.get", fake_get)
    news_rss.fetch(conn, "https://news.google.com/rss/search?q=Nokia+Oyj")
    news_rss.fetch(conn, "https://news.google.com/rss/search?q=Nokia+Oyj")
    assert len(calls) == 1


def test_fetch_http_error_raises(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.providers.news_rss.requests.get",
                        lambda url, headers=None, timeout=None: _FakeResponse(500, ""))
    with pytest.raises(QuoteProviderError):
        news_rss.fetch(conn, "https://broken.example.com/rss")


def test_fetch_empty_feed_returns_empty_list(conn, monkeypatch):
    empty_rss = '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
    monkeypatch.setattr("nokia_tracker.providers.news_rss.requests.get",
                        lambda url, headers=None, timeout=None: _FakeResponse(200, empty_rss))
    assert news_rss.fetch(conn, "https://empty.example.com/rss") == []
