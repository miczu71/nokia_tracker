"""Testy na realnej odpowiedzi NBP (fixture zakresu 2023-01-01..2023-01-10,
pobrana 2026-07-27) — potwierdza empirycznie zmierzone zachowanie: 404 dla
dat bez publikacji, jedno wywołanie z oknem zamiast sekwencyjnego cofania."""
import json
from pathlib import Path

import pytest

from nokia_tracker.providers import fx_nbp
from nokia_tracker.providers.base import QuoteProviderError

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def nbp_range_fixture():
    return json.loads((FIXTURES / "nbp_eur_range.json").read_text())


@pytest.fixture
def nbp_range_fixture_oct2025():
    return json.loads((FIXTURES / "nbp_eur_range_2025_10.json").read_text())


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def test_rate_on_or_before_picks_last_entry_in_window(conn, monkeypatch, nbp_range_fixture):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        return _FakeResponse(200, nbp_range_fixture)

    monkeypatch.setattr("nokia_tracker.providers.fx_nbp.requests.get", fake_get)
    result = fx_nbp.rate_on_or_before(conn, "2023-01-10")
    assert result == (4.6981, "2023-01-10")
    assert len(calls) == 1  # jedno wywołanie, nie sekwencyjne cofanie dzień-po-dniu


def test_rate_on_or_before_url_has_10_day_window(conn, monkeypatch, nbp_range_fixture):
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen["url"] = url
        return _FakeResponse(200, nbp_range_fixture)

    monkeypatch.setattr("nokia_tracker.providers.fx_nbp.requests.get", fake_get)
    fx_nbp.rate_on_or_before(conn, "2026-07-27")
    assert "2026-07-17" in seen["url"]  # start = target - 10 dni
    assert "2026-07-27" in seen["url"]  # end = target


def test_rate_on_or_before_persists_to_nbp_rates_table(conn, monkeypatch, nbp_range_fixture):
    monkeypatch.setattr("nokia_tracker.providers.fx_nbp.requests.get",
                        lambda url, params=None, timeout=None: _FakeResponse(200, nbp_range_fixture))
    fx_nbp.rate_on_or_before(conn, "2023-01-10")
    row = conn.execute("SELECT * FROM nbp_rates WHERE date = '2023-01-10'").fetchone()
    assert row["rate"] == pytest.approx(4.6981)
    assert row["effective_date"] == "2023-01-10"


def test_rate_on_or_before_second_call_hits_cache_not_network(conn, monkeypatch, nbp_range_fixture):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(1)
        return _FakeResponse(200, nbp_range_fixture)

    monkeypatch.setattr("nokia_tracker.providers.fx_nbp.requests.get", fake_get)
    fx_nbp.rate_on_or_before(conn, "2023-01-10")
    fx_nbp.rate_on_or_before(conn, "2023-01-10")
    assert len(calls) == 1  # kurs raz zapisany do nbp_rates nigdy nie jest przeliczany


def test_rate_on_or_before_404_returns_none(conn, monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _FakeResponse(404, {})

    monkeypatch.setattr("nokia_tracker.providers.fx_nbp.requests.get", fake_get)
    assert fx_nbp.rate_on_or_before(conn, "2026-08-01") is None


def test_rate_on_or_before_other_error_raises(conn, monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _FakeResponse(500, {})

    monkeypatch.setattr("nokia_tracker.providers.fx_nbp.requests.get", fake_get)
    with pytest.raises(QuoteProviderError):
        fx_nbp.rate_on_or_before(conn, "2026-07-27")


def test_rate_for_event_uses_last_business_day_before_event(conn, monkeypatch, nbp_range_fixture_oct2025):
    """Zdarzenie w poniedziałek 27.10.2025 -> kurs z piątku 24.10.2025
    (art. 11a: ostatni dzień roboczy POPRZEDZAJĄCY zdarzenie), nie z 27.10
    nawet gdyby NBP tego dnia publikował."""
    monkeypatch.setattr(
        "nokia_tracker.providers.fx_nbp.requests.get",
        lambda url, params=None, timeout=None: _FakeResponse(200, nbp_range_fixture_oct2025))
    result = fx_nbp.rate_for_event(conn, "2025-10-27")
    assert result == (4.2353, "2025-10-24")


def test_rate_for_event_window_ends_day_before_event_not_on_it(conn, monkeypatch, nbp_range_fixture_oct2025):
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen["url"] = url
        return _FakeResponse(200, nbp_range_fixture_oct2025)

    monkeypatch.setattr("nokia_tracker.providers.fx_nbp.requests.get", fake_get)
    fx_nbp.rate_for_event(conn, "2025-10-27")
    assert "2025-10-26" in seen["url"]  # okno kończy się na dniu PRZED zdarzeniem
    assert "2025-10-27" not in seen["url"]
