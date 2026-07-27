"""Dzienna analiza AI: składa kontekst rynkowy + sentyment + pozycja
użytkownika, woła łańcuch AI (task 'daily_analysis') i zapisuje wynik jako
nowe prognozy (forecasts.py) + briefing/rekomendację (tabela briefings).
BLUEPRINT §1, krok 7.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone

from . import forecasts, quotes, sensors
from .ai import provider
from .ai.errors import AIProviderError
from .ai.prompts import DAILY_ANALYSIS_SCHEMA, daily_analysis_prompt

logger = logging.getLogger(__name__)

_TASK = "daily_analysis"
_HORIZON_DAYS = {"1w": 7, "1m": 30, "12m": 365}


def _build_context(conn: sqlite3.Connection, instrument_id: int, ericsson_id: int,
                   omxh25_id: int, eurpln_id: int, position_qty: float,
                   avg_cost_eur: float) -> dict:
    market = sensors.market_values(conn, instrument_id)
    bench = sensors.benchmark_values(conn, instrument_id, ericsson_id, omxh25_id, eurpln_id)
    ai = sensors.ai_values(conn)
    return {
        "price_eur": market["price_eur"],
        "change_pct_day": market["change_pct_day"],
        "sma_20": market["sma_20"],
        "sma_50": market["sma_50"],
        "rsi_14": market["rsi_14"],
        "volatility_30d_pct": market["volatility_30d_pct"],
        "trend": market["trend"],
        "rel_perf_1d_vs_omxh25": bench["rel_perf_1d_vs_omxh25"],
        "rel_perf_1m_vs_ericsson": bench["rel_perf_1m_vs_ericsson"],
        "beta_60d": bench["beta_60d"],
        "alpha_verdict": bench["alpha_verdict"],
        "sentiment_score": ai["sentiment_score"],
        "sentiment_label": ai["sentiment_label"],
        "news_count_24h": ai["news_count_24h"],
        "top_news": ai["top_news_attrs"]["items"][:5],
        "position_qty": position_qty,
        "avg_cost_eur": avg_cost_eur,
        "forecast_accuracy_pct": forecasts.accuracy_pct(conn),
    }


def run_daily_analysis(conn: sqlite3.Connection, cfg: dict, instrument_id: int,
                       ericsson_id: int, omxh25_id: int, eurpln_id: int) -> bool:
    """Zwraca True, jeśli analiza się powiodła i zapisała nowe prognozy +
    briefing. False przy braku danych wejściowych lub porażce całego
    łańcucha AI (wywołujący w main.py loguje wyjątek, nie przerywa reszty)."""
    latest = quotes.latest_quote(conn, instrument_id)
    if not latest:
        logger.warning("daily_analysis: brak aktualnego kursu, pomijam")
        return False
    price_eur = latest["close"]

    context = _build_context(conn, instrument_id, ericsson_id, omxh25_id, eurpln_id,
                             cfg["position_qty"], cfg["avg_cost_eur"])
    prompt = daily_analysis_prompt(context)

    try:
        result = provider.analyze(conn, cfg, _TASK, prompt, DAILY_ANALYSIS_SCHEMA,
                                  cfg["ai_max_tokens"])
    except AIProviderError:
        logger.exception("daily_analysis: łańcuch AI zawiódł")
        return False

    model = provider.active_provider()
    today = date.today()
    for horizon, days in _HORIZON_DAYS.items():
        f = result.get(f"forecast_{horizon}")
        if not f:
            continue
        forecasts.record_forecast(
            conn, horizon=horizon, target_date=(today + timedelta(days=days)).isoformat(),
            price_at_creation=price_eur, predicted_price=f["predicted_price"],
            ci_low=f["ci_low"], ci_high=f["ci_high"], confidence=f["confidence"], model=model)

    conn.execute(
        "INSERT INTO briefings (generated_at, text, tts_text, sentiment_avg, news_count, "
        "verdict, key_risks, recommendation, recommendation_reason_pl, "
        "recommendation_confidence, model) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), result["briefing_pl"], result["tts_text"],
         context["sentiment_score"], context["news_count_24h"],
         result["market_vs_company_verdict"],
         json.dumps(result["key_risks"], ensure_ascii=False),
         result["recommendation"], result["recommendation_reason_pl"],
         result["recommendation_confidence"], model))
    conn.commit()
    logger.info("daily_analysis: nowy briefing zapisany (provider=%s, rekomendacja=%s)",
               model, result["recommendation"])
    return True
