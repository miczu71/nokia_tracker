"""Punkt startowy add-onu (krok 1 — szkielet).

Migracja bazy, seed ustawień, pusty scheduler i minimalny serwer web (waitress),
żeby kontener miał żywy proces i coś do zweryfikowania na ingressie.
MQTT/publisher, providery cen/newsów i AI dochodzą w kolejnych krokach.
"""
from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from waitress import serve

from . import __version__, db as dbm
from . import settings as settingsm
from .web import create_app

logger = logging.getLogger("nokia_tracker")


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name, default)
    return default if v in ("", "null", "None") else v


def _setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, _env("LOG_LEVEL", "info").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def main() -> None:
    _setup_logging()
    logger.info("Nokia Tracker %s startuje", __version__)

    db_path = _env("DB_PATH", "/data/nokia_tracker.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)

    # Opcje Supervisora zasilają tylko brakujące klucze — baza ma
    # pierwszeństwo po pierwszym starcie (wzorzec fuel_tracker/settings.py).
    settingsm.seed_from_options(conn, {
        "poll_interval_minutes": _env("POLL_INTERVAL_MINUTES", "10"),
        "history_backfill_years": _env("HISTORY_BACKFILL_YEARS", "5"),
        "display_currency_secondary": _env("DISPLAY_CURRENCY_SECONDARY", "PLN"),
        "allow_scrape_fallback": "1" if _env("ALLOW_SCRAPE_FALLBACK") == "true" else "0",
        "ai_primary": _env("AI_PRIMARY", "local"),
        "ai_fallback": _env("AI_FALLBACK", "gemini"),
        "local_llm_base_url": _env("LOCAL_LLM_BASE_URL"),
        "local_llm_model": _env("LOCAL_LLM_MODEL"),
        "gemini_model": _env("GEMINI_MODEL"),
        "anthropic_model": _env("ANTHROPIC_MODEL"),
        "ai_max_tokens": _env("AI_MAX_TOKENS", "4000"),
        "ai_max_calls_per_day": _env("AI_MAX_CALLS_PER_DAY", "40"),
        "ai_news_batch_size": _env("AI_NEWS_BATCH_SIZE", "15"),
        "ai_recommendations_enabled": "1" if _env("AI_RECOMMENDATIONS_ENABLED") == "true" else "0",
        "analysis_time": _env("ANALYSIS_TIME", "19:00"),
        "notify_service": _env("NOTIFY_SERVICE"),
        "alert_sentiment_drop": _env("ALERT_SENTIMENT_DROP", "0.5"),
        "alert_price_move_pct": _env("ALERT_PRICE_MOVE_PCT", "3.0"),
        "alert_on_forecast_break": "1" if _env("ALERT_ON_FORECAST_BREAK") == "true" else "0",
        "alert_min_interval_minutes": _env("ALERT_MIN_INTERVAL_MINUTES", "120"),
        "position_qty": _env("POSITION_QTY", "0"),
        "avg_cost_eur": _env("AVG_COST_EUR", "0"),
        "broker_fee_pct": _env("BROKER_FEE_PCT", "0"),
        "cost_basis_policy": _env("COST_BASIS_POLICY", "own_only"),
        "espp_match_pct": _env("ESPP_MATCH_PCT", "50"),
        "finnish_withholding_pct": _env("FINNISH_WITHHOLDING_PCT", "35"),
        "treaty_withholding_pct": _env("TREATY_WITHHOLDING_PCT", "15"),
        "pl_capital_gains_tax_pct": _env("PL_CAPITAL_GAINS_TAX_PCT", "19"),
        "vest_reminder_days": _env("VEST_REMINDER_DAYS", "7"),
        "tax_year": _env("TAX_YEAR", "0"),
    })
    conn.close()

    # Scheduler pusty na razie — joby (poll cen, dzienna analiza AI, backup)
    # dochodzą w krokach 2/6/7/9. Trzymamy go żywym już teraz, żeby proces
    # add-onu nie kończył się od razu po starcie.
    scheduler = BackgroundScheduler(timezone=_env("TZ", "Europe/Warsaw"))
    scheduler.start()

    app = create_app(db_path=db_path)
    logger.info("Web UI nasłuchuje na :8100 (ingress)")
    serve(app, host="0.0.0.0", port=8100)


if __name__ == "__main__":
    main()
