"""Test na realnym XML-u ECB pobranym 2026-07-27 (fixture, zero żywego HTTP)."""
from pathlib import Path

import pytest

from nokia_tracker.providers import fx_ecb
from nokia_tracker.providers.base import QuoteProviderError

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def ecb_xml():
    return (FIXTURES / "ecb_eurofxref_daily.xml").read_text()


class _FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


def test_fetch_rate_parses_pln_from_real_fixture(conn, monkeypatch, ecb_xml):
    monkeypatch.setattr("nokia_tracker.providers.fx_ecb.requests.get",
                        lambda url, timeout=None: _FakeResponse(200, ecb_xml))
    result = fx_ecb.fetch_rate(conn, "PLN")
    assert result == pytest.approx((4.3155, "2026-07-24"))


def test_fetch_rate_uses_cache_on_second_call(conn, monkeypatch, ecb_xml):
    calls = []

    def fake_get(url, timeout=None):
        calls.append(1)
        return _FakeResponse(200, ecb_xml)

    monkeypatch.setattr("nokia_tracker.providers.fx_ecb.requests.get", fake_get)
    fx_ecb.fetch_rate(conn, "PLN")
    fx_ecb.fetch_rate(conn, "PLN")
    assert len(calls) == 1


def test_fetch_rate_unknown_currency_returns_none(conn, monkeypatch, ecb_xml):
    monkeypatch.setattr("nokia_tracker.providers.fx_ecb.requests.get",
                        lambda url, timeout=None: _FakeResponse(200, ecb_xml))
    assert fx_ecb.fetch_rate(conn, "XYZ") is None


def test_fetch_rate_http_error_returns_none(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.providers.fx_ecb.requests.get",
                        lambda url, timeout=None: _FakeResponse(500, ""))
    assert fx_ecb.fetch_rate(conn, "PLN") is None


def test_fetch_rate_malformed_xml_raises(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.providers.fx_ecb.requests.get",
                        lambda url, timeout=None: _FakeResponse(200, "<not-xml"))
    with pytest.raises(QuoteProviderError):
        fx_ecb.fetch_rate(conn, "PLN")
