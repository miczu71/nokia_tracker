"""ai/prompts.py — daily_analysis_prompt nie crashuje na typowym/pustym
kontekście i osadza kluczowe dane (BLUEPRINT §1, krok 7)."""
from nokia_tracker.ai.prompts import DAILY_ANALYSIS_SCHEMA, daily_analysis_prompt

_CONTEXT = {
    "price_eur": 9.0, "change_pct_day": -1.2, "sma_20": 9.1, "sma_50": 9.3, "rsi_14": 45.0,
    "volatility_30d_pct": 3.5, "trend": "bok",
    "rel_perf_1d_vs_omxh25": 0.4, "rel_perf_1m_vs_ericsson": -2.1, "beta_60d": 1.1,
    "alpha_verdict": "mieszane",
    "sentiment_score": 0.2, "sentiment_label": "neutralny", "news_count_24h": 3,
    "top_news": [{"title": "Nokia wins contract", "sentiment": 0.7, "impact": 2,
                 "thesis_pl": "Zwiększa backlog."}],
    "position_qty": 100.0, "avg_cost_eur": 8.5, "forecast_accuracy_pct": 82.5,
}


def test_daily_analysis_prompt_includes_price_and_position():
    prompt = daily_analysis_prompt(_CONTEXT)
    assert "9.0 EUR" in prompt
    assert "100.0 akcji" in prompt
    assert "8.5 EUR" in prompt
    assert "Nokia wins contract" in prompt


def test_daily_analysis_prompt_handles_no_position():
    ctx = dict(_CONTEXT, position_qty=0.0, avg_cost_eur=0.0)
    prompt = daily_analysis_prompt(ctx)
    assert "brak pozycji" in prompt


def test_daily_analysis_prompt_handles_no_news_and_no_accuracy_history():
    ctx = dict(_CONTEXT, top_news=[], forecast_accuracy_pct=None)
    prompt = daily_analysis_prompt(ctx)
    assert "brak newsów" in prompt
    assert "Brak jeszcze rozliczonych prognoz" in prompt


def test_daily_analysis_schema_has_required_top_level_keys():
    assert set(DAILY_ANALYSIS_SCHEMA["required"]) == {
        "forecast_1w", "forecast_1m", "forecast_12m", "briefing_pl", "tts_text",
        "key_risks", "market_vs_company_verdict", "recommendation",
        "recommendation_reason_pl", "recommendation_confidence",
    }
