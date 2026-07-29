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


# ---- krok 16: numer tabeli NBP (link do konkretnej publikacji) ----

def test_rate_on_or_before_persists_table_no(conn, monkeypatch, nbp_range_fixture):
    monkeypatch.setattr(
        "nokia_tracker.providers.fx_nbp.requests.get",
        lambda url, params=None, timeout=None: _FakeResponse(200, nbp_range_fixture))
    fx_nbp.rate_on_or_before(conn, "2023-01-10")
    row = conn.execute("SELECT table_no FROM nbp_rates WHERE date = '2023-01-10'").fetchone()
    assert row["table_no"] == "006/A/NBP/2023"


def test_table_no_for_effective_date_looks_up_persisted_row(conn, monkeypatch, nbp_range_fixture):
    monkeypatch.setattr(
        "nokia_tracker.providers.fx_nbp.requests.get",
        lambda url, params=None, timeout=None: _FakeResponse(200, nbp_range_fixture))
    fx_nbp.rate_on_or_before(conn, "2023-01-10")
    assert fx_nbp.table_no_for_effective_date(conn, "2023-01-10") == "006/A/NBP/2023"


def test_table_no_for_effective_date_none_when_unknown(conn):
    assert fx_nbp.table_no_for_effective_date(conn, "1999-01-01") is None


def test_table_urls_builds_slug_from_table_no():
    urls = fx_nbp.table_urls("142/A/NBP/2026", "2026-07-24")
    assert urls["nbp"] == (
        "https://nbp.pl/archiwum-kursow/tabela-nr-142-a-nbp-2026-z-dnia-2026-07-24/")
    assert urls["api"] == (
        "https://api.nbp.pl/api/exchangerates/rates/a/eur/2026-07-24/?format=json")


def test_backfill_table_numbers_fills_null_rows_without_touching_rate(
        conn, monkeypatch, nbp_range_fixture):
    # Wiersz zapisany "przed krokiem 16" — rate/effective_date obecne, table_no NULL
    # (symuluje stan przed migracją v3, gdzie kolumna jeszcze nie istniała).
    conn.execute(
        "INSERT INTO nbp_rates (date, rate, effective_date) VALUES (?, ?, ?)",
        ("2023-01-10", 4.6981, "2023-01-10"))
    conn.commit()

    def fake_get(url, params=None, timeout=None):
        assert "2023-01-10" in url
        return _FakeResponse(200, {"rates": [{"no": "006/A/NBP/2023",
                                               "effectiveDate": "2023-01-10", "mid": 4.6981}]})

    monkeypatch.setattr("nokia_tracker.providers.fx_nbp.requests.get", fake_get)
    filled = fx_nbp.backfill_table_numbers(conn)
    assert filled == 1
    row = conn.execute("SELECT rate, table_no FROM nbp_rates WHERE date = '2023-01-10'").fetchone()
    assert row["rate"] == pytest.approx(4.6981)  # kurs nietknięty
    assert row["table_no"] == "006/A/NBP/2023"


def test_backfill_table_numbers_skips_rows_already_filled(conn, monkeypatch):
    conn.execute(
        "INSERT INTO nbp_rates (date, rate, effective_date, table_no) VALUES (?, ?, ?, ?)",
        ("2023-01-10", 4.6981, "2023-01-10", "006/A/NBP/2023"))
    conn.commit()

    def fake_get(url, params=None, timeout=None):
        raise AssertionError("nie powinno odpytywać NBP — table_no już uzupełniony")

    monkeypatch.setattr("nokia_tracker.providers.fx_nbp.requests.get", fake_get)
    assert fx_nbp.backfill_table_numbers(conn) == 0
