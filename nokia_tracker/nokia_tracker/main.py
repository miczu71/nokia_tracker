"""Punkt startowy add-onu.

Migracja bazy, seed ustawień, MQTT discovery (rynek+technika, krok 3),
scheduler pollingu i serwer web (waitress). Newsy/AI/portfel dochodzą w
kolejnych krokach jako kolejne joby schedulera.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from waitress import serve

from . import __version__, alerts, analysis, db as dbm, forecasts, fx, ha_client
from . import news, portfolio, quotes, sensors
from . import settings as settingsm
from .ai import scoring as ai_scoring
from .providers import avanza as avanza_provider
from .providers import finnhub as finnhub_provider
from .providers.yahoo import YahooQuoteProvider
from .publisher import MQTTPublisher
from .web import create_app

_PRIMARY_SYMBOL = "NOKIA.HE"
_ERICSSON_SYMBOL = "ERIC-B.ST"
_OMXH25_SYMBOL = "^OMXH25"
_EURUSD_SYMBOL = "EURUSD=X"
_ADR_SYMBOL = "NOK"
# Orderbook Nokii na Avanzie — instrument z założenia hardcoded w tym
# dodatku (jak pozostałe symbole wyżej), patrz providers/avanza.py.
_AVANZA_ORDERBOOK_ID = "52784"

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
        "avanza_live_price_enabled": "1" if _env("AVANZA_LIVE_PRICE_ENABLED", "true") == "true" else "0",
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

    history_years = int(settingsm.get_settings(conn)["history_backfill_years"])
    instrument_id = quotes.ensure_instrument(
        conn, _PRIMARY_SYMBOL, "Nokia Oyj", "EUR", "primary")
    ericsson_id = quotes.ensure_instrument(
        conn, _ERICSSON_SYMBOL, "Ericsson", "SEK", "benchmark")
    omxh25_id = quotes.ensure_instrument(
        conn, _OMXH25_SYMBOL, "OMX Helsinki 25", "EUR", "benchmark")
    eurpln_id = quotes.ensure_instrument(
        conn, fx.EURPLN_SYMBOL, "EUR/PLN", "PLN", "fx")
    eurusd_id = quotes.ensure_instrument(
        conn, _EURUSD_SYMBOL, "EUR/USD", "USD", "fx")
    adr_id = quotes.ensure_instrument(
        conn, _ADR_SYMBOL, "Nokia ADR (NYSE)", "USD", "adr")
    news.seed_default_sources(conn)

    # Backfill przy pierwszym starcie — provider ze scope'em TEGO połączenia,
    # nie przetrwa conn.close() poniżej; publish_sensors() tworzy WŁASNY
    # provider na swoim własnym połączeniu (sqlite3 + wątki APScheduler).
    for iid, symbol, name in (
        (instrument_id, _PRIMARY_SYMBOL, "primary"),
        (ericsson_id, _ERICSSON_SYMBOL, "ericsson"),
        (omxh25_id, _OMXH25_SYMBOL, "omxh25"),
        (eurpln_id, fx.EURPLN_SYMBOL, "eurpln"),
        (eurusd_id, _EURUSD_SYMBOL, "eurusd"),
    ):
        if quotes.has_history(conn, iid):
            continue
        logger.info("Brak historii dla %s — pełny backfill %d lat", symbol, history_years)
        try:
            quotes.backfill(conn, iid, symbol, YahooQuoteProvider(conn), history_years)
        except Exception:
            logger.exception("Backfill %s nieudany — spróbuje ponownie przy najbliższym pollu", name)
    conn.close()

    mqtt_host = _env("MQTT_HOST", "core-mosquitto")
    mqtt_port = int(_env("MQTT_PORT", "1883") or 1883)
    mqtt_user = _env("MQTT_USER")
    mqtt_password = _env("MQTT_PASSWORD")
    if not mqtt_user:
        svc = ha_client.get_mqtt_service()
        if svc:
            mqtt_host = svc.get("host") or mqtt_host
            mqtt_port = int(svc.get("port") or mqtt_port)
            mqtt_user = svc.get("username") or ""
            mqtt_password = svc.get("password") or ""
            logger.info("MQTT: dane brokera z usługi Supervisora (%s)", mqtt_host)

    mqtt_pub = MQTTPublisher(host=mqtt_host, port=mqtt_port, user=mqtt_user,
                             password=mqtt_password, version=__version__)
    mqtt_pub.connect()

    finnhub_api_key = _env("FINNHUB_API_KEY")
    marketaux_api_key = _env("MARKETAUX_API_KEY")

    def publish_sensors() -> None:
        """Odświeża świeże świece + FX + ADR i publikuje komplet sensorów MQTT.

        Yahoo (primary/benchmark/fx) nie wymaga klucza, więc świadomie bez
        bramki is_session_open() na tych providerach — quota-świadomość
        dotyczy providerów z realnym limitem (Finnhub, ewentualnie
        Twelve Data w przyszłości). Finnhub (ADR) jest opcjonalny — bez
        klucza sensory ADR/spread po prostu zostają 'unknown', bez błędu.
        """
        with dbm.WRITE_LOCK:
            c = dbm.get_conn(db_path)
            try:
                cfg = settingsm.get_settings(c)

                provider = YahooQuoteProvider(c)
                quotes.refresh_recent_daily(c, instrument_id, _PRIMARY_SYMBOL, provider)
                quotes.refresh_recent_daily(c, ericsson_id, _ERICSSON_SYMBOL, provider)
                quotes.refresh_recent_daily(c, omxh25_id, _OMXH25_SYMBOL, provider)
                quotes.refresh_recent_daily(c, eurusd_id, _EURUSD_SYMBOL, provider)
                fx.refresh_eurpln(c, eurpln_id)

                if finnhub_api_key:
                    adr = finnhub_provider.fetch_quote(c, _ADR_SYMBOL, finnhub_api_key)
                    if adr:
                        quotes.store_single_price(c, adr_id, adr["price"], source="finnhub")

                if cfg["avanza_live_price_enabled"]:
                    # Dodatkowe, niezależne źródło żywej ceny (patrz
                    # providers/avanza.py) — owinięte we WŁASNY try/except,
                    # bo to nieoficjalne API i awaria nie może ubić reszty
                    # publikacji, która działa poprawnie na samym Yahoo.
                    try:
                        live = avanza_provider.fetch_quote(c, _AVANZA_ORDERBOOK_ID)
                        if live:
                            quotes.refresh_live_price(c, instrument_id, live["price"],
                                                     source="avanza")
                    except Exception:
                        logger.exception("Avanza live price nieudane (nie krytyczne)")

                values = sensors.market_values(c, instrument_id)
                values.update(sensors.benchmark_values(
                    c, instrument_id, ericsson_id, omxh25_id, eurpln_id, adr_id, eurusd_id))
                values.update(sensors.ai_values(c))
                values.update(sensors.forecast_values(c))

                cost_basis_eur = cfg["position_qty"] * cfg["avg_cost_eur"]
                dividends = sensors.dividends_values(c, cfg, cost_basis_eur)
                position = portfolio.position_values(
                    cfg["position_qty"], cfg["avg_cost_eur"], values.get("price_eur"),
                    values.get("eurpln_rate"),
                    dividends_net_total_eur=dividends["dividends_net_eur"])
                values.update(position)
                values.update(dividends)
                values.update(sensors.lots_values(c, cfg))

                mqtt_pub.publish(values)

                try:
                    alerts.check_and_fire(c, cfg, values, mqtt_pub)
                except Exception:
                    logger.exception("Sprawdzanie alertów nieudane")
            except Exception:
                logger.exception("Publikacja MQTT nieudana")
            finally:
                c.close()

    def _ai_cfg(c) -> dict:
        """Ustawienia AI z tabeli settings + klucze API z ENV — te ostatnie
        NIE żyją w tabeli settings (patrz settings.py), żeby nie dublować
        sekretów w dwóch miejscach."""
        cfg = dict(settingsm.get_settings(c))
        cfg["local_llm_api_key"] = _env("LOCAL_LLM_API_KEY")
        cfg["gemini_api_key"] = _env("GEMINI_API_KEY")
        cfg["anthropic_api_key"] = _env("ANTHROPIC_API_KEY")
        return cfg

    def fetch_news() -> None:
        """Newsy nie potrzebują świeżości co poll_interval_minutes — osobny,
        rzadszy interwał (30 min) niż ceny, głównie żeby nie napytać sobie
        biedy z ciasnym limitem GDELT (1 zapytanie/5s, zmierzone empirycznie).
        Ocena AI newsów leci od razu po agregacji, na tym samym połączeniu —
        batchuje wyłącznie nieocenione (ai/scoring.py), więc drugi przebieg
        bez nowych newsów to tani no-op."""
        with dbm.WRITE_LOCK:
            c = dbm.get_conn(db_path)
            try:
                news.aggregate(c, finnhub_api_key=finnhub_api_key,
                              marketaux_api_key=marketaux_api_key)
            except Exception:
                logger.exception("Agregacja newsów nieudana")
            try:
                ai_scoring.score_pending(c, _ai_cfg(c))
            except Exception:
                logger.exception("Ocena AI newsów nieudana")
            finally:
                c.close()

    def run_daily_analysis() -> None:
        """Codziennie o analysis_time: rozlicza dojrzałe prognozy (settle_due,
        do current_price) i — jeśli ai_recommendations_enabled — generuje
        nowe prognozy 1w/1m/12m + briefing + rekomendację (analysis.py)."""
        with dbm.WRITE_LOCK:
            c = dbm.get_conn(db_path)
            try:
                latest = quotes.latest_quote(c, instrument_id)
                if latest:
                    settled = forecasts.settle_due(c, latest["close"])
                    if settled:
                        logger.info("Rozliczono %d dojrzałych prognoz", settled)
                cfg = _ai_cfg(c)
                if cfg["ai_recommendations_enabled"]:
                    analysis.run_daily_analysis(
                        c, cfg, instrument_id, ericsson_id, omxh25_id, eurpln_id)
            except Exception:
                logger.exception("Dzienna analiza AI nieudana")
            finally:
                c.close()

    poll_minutes = int(_env("POLL_INTERVAL_MINUTES", "10") or 10)
    analysis_time = _env("ANALYSIS_TIME", "19:00")
    try:
        analysis_hour, analysis_minute = (int(x) for x in analysis_time.split(":", 1))
    except ValueError:
        logger.warning("ANALYSIS_TIME=%r nieprawidłowy (oczekiwano HH:MM) — używam 19:00",
                       analysis_time)
        analysis_hour, analysis_minute = 19, 0

    scheduler = BackgroundScheduler(timezone=_env("TZ", "Europe/Warsaw"))
    scheduler.add_job(publish_sensors, "interval", minutes=poll_minutes,
                      next_run_time=datetime.now())
    scheduler.add_job(fetch_news, "interval", minutes=30,
                      next_run_time=datetime.now())
    scheduler.add_job(run_daily_analysis, "cron", hour=analysis_hour, minute=analysis_minute)
    scheduler.start()

    app = create_app(db_path=db_path)
    logger.info("Web UI nasłuchuje na :8100 (ingress)")
    serve(app, host="0.0.0.0", port=8100)


if __name__ == "__main__":
    main()
