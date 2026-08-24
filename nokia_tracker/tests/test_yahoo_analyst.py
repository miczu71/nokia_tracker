"""Yahoo quoteSummary v10 — konsensus analityków dla NOKIA.HE
(docs/PLAN_0_26_0_konsensus.md). Fixture to prawdziwa odpowiedź zapisana na
żywo 2026-08-24 (tests/fixtures/yahoo_quotesummary_nokia_he.json).

Handshake cookie+crumb zweryfikowany na żywo osobno:
  1. GET fc.yahoo.com -> HTTP 404 (OCZEKIWANY — ustawia cookies)
  2. GET .../v1/test/getcrumb (z cookies) -> crumb
  3. GET quoteSummary?...&crumb=... (z TYMI SAMYMI cookies) -> 200
Sam crumb bez cookie -> 401 Invalid Crumb (zweryfikowane na żywo); tu
sprawdzamy reakcję na 401 przez atrapę requests.Session, nie żywą sieć."""
import json
from pathlib import Path

import pytest

from nokia_tracker.models import AnalystConsensus
from nokia_tracker.providers import yahoo_analyst
from nokia_tracker.providers.base import QuoteProviderError

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "yahoo_quotesummary_nokia_he.json").read_text())


class _FakeResponse:
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text if body is None else json.dumps(body)

    def json(self):
        return self._body


class _FakeSession:
    """Symuluje requests.Session(): 3 GET-y w kolejności per pełny handshake
    (fc.yahoo.com, getcrumb, quoteSummary), sterowane wspólną kolejką."""

    def __init__(self, script):
        self.headers = {}
        self._script = script  # dzielona kolejka między instancjami

    def get(self, url, params=None, timeout=None):
        return self._script.pop(0)


def _install_fake_sessions(monkeypatch, script):
    """`script`: płaska lista odpowiedzi, 3 na każdą sesję (fc/crumb/quoteSummary),
    zjadana w kolejności niezależnie ile razy kod stworzy nową Session()."""
    monkeypatch.setattr(
        "nokia_tracker.providers.yahoo_analyst.requests.Session",
        lambda: _FakeSession(script))


_OK_HANDSHAKE = [
    _FakeResponse(404, text="not found"),   # fc.yahoo.com — 404 oczekiwany
    _FakeResponse(200, text="abc123crumb"),  # getcrumb
]


def test_fetch_consensus_parses_documented_shape(conn, monkeypatch):
    _install_fake_sessions(monkeypatch, [*_OK_HANDSHAKE, _FakeResponse(200, _FIXTURE)])
    result = yahoo_analyst.fetch_consensus(conn, "NOKIA.HE")
    assert result == AnalystConsensus(
        low=4.65, mean=pytest.approx(10.32455), median=10.125, high=18.0,
        n_analysts=22, rating="hold", currency="EUR", source="yahoo",
        trend={"period": "0m", "strongBuy": 3, "buy": 9, "hold": 4,
               "sell": 4, "strongSell": 3},
    )


def test_fc_yahoo_404_is_not_treated_as_failure(conn, monkeypatch):
    """404 z fc.yahoo.com jest OCZEKIWANY (ustawia cookies) — nie powinien
    przerywać handshake'u ani podnosić wyjątku."""
    _install_fake_sessions(monkeypatch, [*_OK_HANDSHAKE, _FakeResponse(200, _FIXTURE)])
    assert yahoo_analyst.fetch_consensus(conn, "NOKIA.HE") is not None


def test_missing_target_mean_returns_none(conn, monkeypatch):
    empty = {"quoteSummary": {"result": [{"financialData": {}, "recommendationTrend": {}}]}}
    _install_fake_sessions(monkeypatch, [*_OK_HANDSHAKE, _FakeResponse(200, empty)])
    assert yahoo_analyst.fetch_consensus(conn, "NOKIA.HE") is None


def test_empty_result_returns_none(conn, monkeypatch):
    empty = {"quoteSummary": {"result": None}}
    _install_fake_sessions(monkeypatch, [*_OK_HANDSHAKE, _FakeResponse(200, empty)])
    assert yahoo_analyst.fetch_consensus(conn, "NOKIA.HE") is None


def test_persistent_http_error_raises(conn, monkeypatch):
    # 500 nie jest w retryable_statuses (429, 502) -> jedna próba, jeden handshake.
    _install_fake_sessions(monkeypatch, [*_OK_HANDSHAKE, _FakeResponse(500, {})])
    with pytest.raises(QuoteProviderError):
        yahoo_analyst.fetch_consensus(conn, "NOKIA.HE")


def test_401_triggers_one_full_handshake_retry(conn, monkeypatch):
    """Crumb wygasł w trakcie -> jeden ponowny PEŁNY handshake (nowe cookies +
    nowy crumb), nie zwykły retry na tym samym crumbie."""
    _install_fake_sessions(monkeypatch, [
        *_OK_HANDSHAKE, _FakeResponse(401, {}),      # pierwsza próba: crumb odrzucony
        *_OK_HANDSHAKE, _FakeResponse(200, _FIXTURE),  # druga: świeży handshake, sukces
    ])
    result = yahoo_analyst.fetch_consensus(conn, "NOKIA.HE")
    assert result is not None
    assert result.mean == pytest.approx(10.32455)


def test_401_twice_raises(conn, monkeypatch):
    _install_fake_sessions(monkeypatch, [
        *_OK_HANDSHAKE, _FakeResponse(401, {}),
        *_OK_HANDSHAKE, _FakeResponse(401, {}),
    ])
    with pytest.raises(QuoteProviderError):
        yahoo_analyst.fetch_consensus(conn, "NOKIA.HE")


def test_uses_cache_on_second_call(conn, monkeypatch):
    script = [*_OK_HANDSHAKE, _FakeResponse(200, _FIXTURE)]
    session_creations = {"n": 0}

    def fake_session_factory():
        session_creations["n"] += 1
        return _FakeSession(script)

    monkeypatch.setattr(
        "nokia_tracker.providers.yahoo_analyst.requests.Session", fake_session_factory)
    yahoo_analyst.fetch_consensus(conn, "NOKIA.HE")
    yahoo_analyst.fetch_consensus(conn, "NOKIA.HE")
    assert session_creations["n"] == 1  # drugie wywołanie trafia w cache.py
