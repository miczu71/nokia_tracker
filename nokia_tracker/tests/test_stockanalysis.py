"""stockanalysis.com — fallback konsensusu analityków, gdy Yahoo pada i
`allow_scrape_fallback` jest włączony (docs/PLAN_0_26_0_konsensus.md §2).
Fixture to prawdziwa strona zapisana na żywo 2026-08-24
(tests/fixtures/stockanalysis_nokia_forecast.html) — jej liczby zgadzają
się co do centa z fixture'em Yahoo z tego samego dnia (4,65 / 10,32 / 10,13 / 18,00).

Zasada „parser milknie, nie rzuca" (plan §2, ryzyko „scraping łamie się przy
redesignie"): zmiana STRUKTURY strony -> None + log warning, nigdy wyjątek.
Awaria transportowa (HTTP) -> QuoteProviderError, jak w innych providerach."""
from pathlib import Path

import pytest

from nokia_tracker.providers import stockanalysis
from nokia_tracker.providers.base import QuoteProviderError

_HTML = (Path(__file__).parent / "fixtures" / "stockanalysis_nokia_forecast.html").read_text()


class _FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def test_fetch_consensus_parses_real_page(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.providers.stockanalysis.requests.get",
                        lambda *a, **kw: _FakeResponse(200, _HTML))
    result = stockanalysis.fetch_consensus(conn, "NOKIA.HE")
    assert result.low == pytest.approx(4.65)
    assert result.mean == pytest.approx(10.32)
    assert result.median == pytest.approx(10.13)
    assert result.high == pytest.approx(18.0)
    assert result.n_analysts == 23
    assert result.rating == "Hold"
    assert result.currency == "EUR"
    assert result.source == "stockanalysis"
    assert result.trend is None  # nie dostarcza rozkładu strongBuy/buy/...


def test_unknown_symbol_returns_none_without_network(conn, monkeypatch):
    calls = []
    monkeypatch.setattr("nokia_tracker.providers.stockanalysis.requests.get",
                        lambda *a, **kw: calls.append(1))
    assert stockanalysis.fetch_consensus(conn, "AAPL") is None
    assert len(calls) == 0


def test_page_structure_change_returns_none_not_raises(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.providers.stockanalysis.requests.get",
                        lambda *a, **kw: _FakeResponse(200, "<html>redesigned page</html>"))
    assert stockanalysis.fetch_consensus(conn, "NOKIA.HE") is None


def test_http_error_raises(conn, monkeypatch):
    monkeypatch.setattr("nokia_tracker.providers.stockanalysis.requests.get",
                        lambda *a, **kw: _FakeResponse(500, ""))
    with pytest.raises(QuoteProviderError):
        stockanalysis.fetch_consensus(conn, "NOKIA.HE")


def test_uses_cache_on_second_call(conn, monkeypatch):
    calls = []

    def fake_get(*a, **kw):
        calls.append(1)
        return _FakeResponse(200, _HTML)

    monkeypatch.setattr("nokia_tracker.providers.stockanalysis.requests.get", fake_get)
    stockanalysis.fetch_consensus(conn, "NOKIA.HE")
    stockanalysis.fetch_consensus(conn, "NOKIA.HE")
    assert len(calls) == 1
