"""ai/prompts.py — daily_analysis_prompt nie crashuje na typowym/pustym
kontekście i osadza kluczowe dane (BLUEPRINT §1, krok 7). Sekcja czatu
(krok 29) dodatkowo pilnuje dwóch rzeczy strukturalnie ważnych dla
architektury 3-stopniowej: prompt intencji NIE dostaje żadnych liczb z
portfela (rozpoznanie intencji to nie zadanie tego wywołania — silnik
liczy PO rozpoznaniu), a prompt narracji jawnie zabrania zmieniania liczb."""
from nokia_tracker.ai.prompts import (
    CHAT_INTENT_SCHEMA,
    CHAT_NARRATION_SCHEMA,
    DAILY_ANALYSIS_SCHEMA,
    chat_intent_prompt,
    chat_narration_prompt,
    daily_analysis_prompt,
)

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


# --- czat (krok 29) ---

_CHAT_INTENTS = {
    "podatek_ze_sprzedazy", "ile_moge_sprzedac", "kiedy_vesting", "ile_zarobilem",
    "dywidendy_w_roku", "koszt_sprzedazy_teraz", "porownanie_z_benchmarkiem",
    "pit_za_rok", "straty_z_lat_ubieglych", "koncentracja_majatku", "kiedy_sprzedac",
    "inne",
}


def test_chat_intent_schema_enumerates_all_eleven_intents_plus_inne():
    assert set(CHAT_INTENT_SCHEMA["properties"]["intent"]["enum"]) == _CHAT_INTENTS


def test_chat_intent_schema_top_level_required_and_strict():
    assert set(CHAT_INTENT_SCHEMA["required"]) == {"intent", "params", "confidence"}
    assert CHAT_INTENT_SCHEMA["additionalProperties"] is False
    assert CHAT_INTENT_SCHEMA["properties"]["params"]["additionalProperties"] is False


def test_chat_intent_prompt_includes_question_and_years():
    prompt = chat_intent_prompt("Ile zapłacę podatku sprzedając 500 akcji?", {
        "today": "2026-08-16", "years_with_data": [2025, 2023], "cost_basis_policy": "own_only",
    })
    assert "Ile zapłacę podatku sprzedając 500 akcji?" in prompt
    assert "2026-08-16" in prompt
    assert "2025" in prompt and "2023" in prompt


def test_chat_intent_prompt_handles_no_years_with_data():
    prompt = chat_intent_prompt("Kiedy mam najbliższy vesting?", {
        "today": "2026-08-16", "years_with_data": [], "cost_basis_policy": "own_only",
    })
    assert "brak" in prompt.lower()


def test_chat_intent_prompt_carries_no_portfolio_numbers():
    # Architektura 3-stopniowa (docs/PLAN_KROK_29_asystent.md): rozpoznanie intencji
    # nie ma dostępu do liczb portfela — funkcja przyjmuje TYLKO
    # today/years_with_data/cost_basis_policy, więc structurally nie może ich osadzić.
    import inspect
    params = inspect.signature(chat_intent_prompt).parameters
    assert set(params) == {"question", "context"}


def test_chat_narration_schema_requires_answer_pl_only():
    assert CHAT_NARRATION_SCHEMA["required"] == ["answer_pl"]
    assert CHAT_NARRATION_SCHEMA["additionalProperties"] is False


def test_chat_narration_prompt_forbids_changing_numbers():
    prompt = chat_narration_prompt(
        "Ile zapłacę podatku?", "Podatek ze sprzedaży",
        [{"label": "Podatek", "value": 1234.56, "unit": "PLN"}])
    assert "nie zmieniaj" in prompt.lower()
    assert "1234.56" in prompt
    assert "PLN" in prompt


def test_chat_narration_prompt_includes_all_lines():
    prompt = chat_narration_prompt(
        "Ile zarobiłem?", "Wynik", [
            {"label": "Wartość rynkowa", "value": 10000.0, "unit": "PLN"},
            {"label": "Zysk", "value": 500.0, "unit": "PLN", "emphasis": True},
        ])
    assert "Wartość rynkowa" in prompt
    assert "10000.0" in prompt
    assert "Zysk" in prompt
    assert "500.0" in prompt


def test_chat_narration_prompt_handles_empty_lines_without_crash():
    prompt = chat_narration_prompt("Coś tam?", "Temat", [])
    assert "brak" in prompt.lower()
