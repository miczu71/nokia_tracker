"""Dzienna analiza AI: składanie kontekstu + zapis prognoz/briefingu z
odpowiedzi łańcucha (BLUEPRINT §1, krok 7)."""
import json
from datetime import date, timedelta

import pytest

from nokia_tracker import analysis, quotes
from nokia_tracker.ai import provider
from nokia_tracker.ai.errors import AIProviderError
from nokia_tracker.models import Candle


@pytest.fixture
def ids(conn):
    primary = quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia", "EUR", "primary")
    ericsson = quotes.ensure_instrument(conn, "ERIC-B.ST", "Ericsson", "SEK", "benchmark")
    omxh25 = quotes.ensure_instrument(conn, "^OMXH25", "OMXH25", "EUR", "benchmark")
    eurpln = quotes.ensure_instrument(conn, "EURPLN=X", "EUR/PLN", "PLN", "fx")
    quotes.upsert_candles(conn, primary, "daily",
                          [Candle(ts="2026-01-01T00:00:00+00:00", close=9.0)])
    return primary, ericsson, omxh25, eurpln


_CFG = {"ai_primary": "local", "ai_fallback": "gemini", "ai_max_tokens": 4000,
       "ai_max_calls_per_day": 40, "position_qty": 100.0, "avg_cost_eur": 8.5,
       "local_llm_base_url": "http://x/v1", "local_llm_api_key": "k",
       "local_llm_model": "m", "gemini_api_key": "", "gemini_model": "m2",
       "anthropic_api_key": "", "anthropic_model": "m3"}

_FAKE_RESULT = {
    "forecast_1w": {"predicted_price": 9.2, "ci_low": 8.8, "ci_high": 9.6, "confidence": 0.6},
    "forecast_1m": {"predicted_price": 9.5, "ci_low": 8.5, "ci_high": 10.5, "confidence": 0.5},
    "forecast_12m": {"predicted_price": 11.0, "ci_low": 8.0, "ci_high": 14.0, "confidence": 0.3},
    "briefing_pl": "Nokia stabilna, lekki wzrost.",
    "tts_text": "Nokia stabilna, lekki wzrost.",
    "key_risks": ["presja marżowa"],
    "market_vs_company_verdict": "trend rynkowy",
    "recommendation": "trzymaj",
    "recommendation_reason_pl": "Kurs blisko Twojej średniej ceny.",
    "recommendation_confidence": 0.55,
}


@pytest.fixture(autouse=True)
def _reset_active():
    provider._active[0] = "off"
    yield
    provider._active[0] = "off"


def test_run_daily_analysis_no_quote_returns_false(conn):
    primary = quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia", "EUR", "primary")
    ericsson = quotes.ensure_instrument(conn, "ERIC-B.ST", "Ericsson", "SEK", "benchmark")
    omxh25 = quotes.ensure_instrument(conn, "^OMXH25", "OMXH25", "EUR", "benchmark")
    eurpln = quotes.ensure_instrument(conn, "EURPLN=X", "EUR/PLN", "PLN", "fx")
    assert analysis.run_daily_analysis(conn, _CFG, primary, ericsson, omxh25, eurpln) is False


def test_run_daily_analysis_records_forecasts_and_briefing(conn, ids, monkeypatch):
    primary, ericsson, omxh25, eurpln = ids
    monkeypatch.setattr(provider, "analyze", lambda *a, **kw: _FAKE_RESULT)
    monkeypatch.setattr(provider, "active_provider", lambda: "local")

    ok = analysis.run_daily_analysis(conn, _CFG, primary, ericsson, omxh25, eurpln)
    assert ok is True

    rows = {r["horizon"]: r for r in conn.execute("SELECT * FROM forecasts").fetchall()}
    assert set(rows) == {"1w", "1m", "12m"}
    assert rows["1w"]["predicted_price"] == 9.2
    assert rows["1w"]["price_at_creation"] == 9.0
    assert rows["1w"]["model"] == "local"

    briefing = conn.execute("SELECT * FROM briefings").fetchone()
    assert briefing["text"] == "Nokia stabilna, lekki wzrost."
    assert briefing["recommendation"] == "trzymaj"
    assert briefing["recommendation_confidence"] == 0.55
    assert json.loads(briefing["key_risks"]) == ["presja marżowa"]


def test_run_daily_analysis_chain_failure_returns_false(conn, ids, monkeypatch):
    primary, ericsson, omxh25, eurpln = ids
    monkeypatch.setattr(provider, "analyze",
                        lambda *a, **kw: (_ for _ in ()).throw(AIProviderError("chain down")))
    ok = analysis.run_daily_analysis(conn, _CFG, primary, ericsson, omxh25, eurpln)
    assert ok is False
    assert conn.execute("SELECT COUNT(*) c FROM forecasts").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM briefings").fetchone()["c"] == 0


def test_run_daily_analysis_target_dates_offset_correctly(conn, ids, monkeypatch):
    primary, ericsson, omxh25, eurpln = ids
    monkeypatch.setattr(provider, "analyze", lambda *a, **kw: _FAKE_RESULT)
    monkeypatch.setattr(provider, "active_provider", lambda: "local")
    analysis.run_daily_analysis(conn, _CFG, primary, ericsson, omxh25, eurpln)

    rows = {r["horizon"]: r for r in conn.execute("SELECT * FROM forecasts").fetchall()}
    today = date.today()
    assert rows["1w"]["target_date"] == (today + timedelta(days=7)).isoformat()
    assert rows["1m"]["target_date"] == (today + timedelta(days=30)).isoformat()
    assert rows["12m"]["target_date"] == (today + timedelta(days=365)).isoformat()
