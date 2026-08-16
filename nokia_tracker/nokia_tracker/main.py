"""Punkt startowy add-onu.

Migracja bazy, seed ustawień, MQTT discovery (rynek+technika, krok 3),
scheduler pollingu i serwer web (waitress). Newsy/AI/portfel dochodzą w
kolejnych krokach jako kolejne joby schedulera.
"""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from waitress import serve

from . import __version__, alerts, analysis, backup as backupm, db as dbm, forecasts, fx, ha_client
from . import news, notifier, portfolio, quotes, sensors
from . import settings as settingsm
from .analytics import history as analytics_history
from .importers import computershare_pdf
from .tax import dividends as taxdiv
from .tax import grants as grantsm
from .tax import losses as taxlosses
from .tax import lots as taxlots
from .ai import scoring as ai_scoring
from .providers import avanza as avanza_provider
from .providers import finnhub as finnhub_provider
from .providers import fx_nbp
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
    backup_share = _env("BACKUP_SHARE", "/share/nokia_tracker")
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
        "ai_chat_enabled": "1" if _env("AI_CHAT_ENABLED", "true") == "true" else "0",
        "ai_chat_narration_enabled": "1" if _env("AI_CHAT_NARRATION_ENABLED", "true") == "true" else "0",
        "ai_max_calls_per_day_local": _env("AI_MAX_CALLS_PER_DAY_LOCAL", "500"),
        "notify_service": _env("NOTIFY_SERVICE"),
        "alert_sentiment_drop": _env("ALERT_SENTIMENT_DROP", "0.5"),
        "alert_price_move_pct": _env("ALERT_PRICE_MOVE_PCT", "3.0"),
        "alert_on_forecast_break": "1" if _env("ALERT_ON_FORECAST_BREAK") == "true" else "0",
        "alert_min_interval_minutes": _env("ALERT_MIN_INTERVAL_MINUTES", "120"),
        "notify_news_enabled": "1" if _env("NOTIFY_NEWS_ENABLED", "true") == "true" else "0",
        "notify_news_min_impact": _env("NOTIFY_NEWS_MIN_IMPACT", "1"),
        "notify_digest_enabled": "1" if _env("NOTIFY_DIGEST_ENABLED", "true") == "true" else "0",
        "digest_time": _env("DIGEST_TIME", "20:10"),
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
        # --- doradca planu pracowniczego (krok 26, 0.10.0) ---
        "other_net_worth_pln": _env("OTHER_NET_WORTH_PLN", "0"),
        "concentration_alert_pct": _env("CONCENTRATION_ALERT_PCT", "25"),
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
                position = portfolio.position_values_auto(
                    c, cfg, values.get("price_eur"), values.get("eurpln_rate"),
                    dividends_net_total_eur=dividends["dividends_net_eur"])
                values.update(position)
                values.update(dividends)
                values.update(sensors.lots_values(c, cfg))
                values.update(sensors.grants_values(c))
                values.update(sensors.pit38_values(c, cfg))
                values.update(sensors.whatif_values(c, cfg, values.get("price_eur")))
                values.update(sensors.results_values(
                    c, values.get("price_eur"), values.get("eurpln_rate"), omxh25_id))
                # Krok 26 (docs/PLAN_KROK_26_doradca.md): świadomie NIE dopisane do
                # łańcucha digestu w run_daily_analysis() poniżej — notifier tych kluczy
                # nie konsumuje, a ten łańcuch jest tam celowo krótszy.
                values.update(sensors.advisor_values(
                    c, cfg, values.get("price_eur"), values.get("eurpln_rate")))
                values.update(sensors.losses_values(c, cfg))

                mqtt_pub.publish(values)

                try:
                    alerts.check_and_fire(c, cfg, values, mqtt_pub)
                except Exception:
                    logger.exception("Sprawdzanie alertów nieudane")
            except Exception:
                logger.exception("Publikacja MQTT nieudana")
            finally:
                c.close()

    def refresh_intraday_job() -> None:
        """Krok 16: dogrywa historię śróddzienną (5-minutową) instrumentu
        głównego, żeby zakres „1D" na wykresie pulpitu (`/api/chart`) miał z
        czego rysować — bez tego joba `quotes.refresh_intraday()` istnieje w
        kodzie, ale nikt go nie woła i w bazie jest tylko jedna świeca dzienna.
        Własny try/except, tak samo jak Avanza w `publish_sensors` — awaria
        Yahoo na tym providerze nie może ubić reszty pollingu."""
        with dbm.WRITE_LOCK:
            c = dbm.get_conn(db_path)
            try:
                provider = YahooQuoteProvider(c)
                quotes.refresh_intraday(c, instrument_id, _PRIMARY_SYMBOL, provider)
            except Exception:
                logger.exception("Odświeżenie intraday nieudane (nie krytyczne)")
            finally:
                c.close()

    def prune_intraday_job() -> None:
        """Codziennie: retencja świec intraday (krok 16) — zakres „1D" nigdy
        nie patrzy dalej niż jeden dzień wstecz, więc starsze świece 5-minutowe
        są tylko martwym balastem w bazie."""
        with dbm.WRITE_LOCK:
            c = dbm.get_conn(db_path)
            try:
                removed = quotes.prune_intraday(c)
                if removed:
                    logger.info("Retencja intraday: usunięto %d świec", removed)
            except Exception:
                logger.exception("Retencja intraday nieudana")
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
            try:
                cfg = settingsm.get_settings(c)
                if cfg["notify_news_enabled"] and cfg["notify_service"]:
                    notifier.notify_new_news(c, cfg)
            except Exception:
                logger.exception("Powiadomienia o newsach nieudane")
            finally:
                c.close()

    def auto_import_pdf_share() -> None:
        """Skanuje <backup_share>/import/*.pdf, importuje przez
        computershare_pdf.import_statement() i przenosi do
        <backup_share>/imported/<timestamp>_<nazwa> (port
        fuel_tracker/main.py::auto_import_share — try/except per plik, żeby jeden
        zepsuty PDF nie zablokował reszty)."""
        import_dir = Path(backup_share) / "import"
        if not import_dir.is_dir():
            return
        done_dir = Path(backup_share) / "imported"
        with dbm.WRITE_LOCK:
            c = dbm.get_conn(db_path)
            try:
                cfg = settingsm.get_settings(c)
                for pdf_file in sorted(import_dir.glob("*.pdf")):
                    try:
                        report = computershare_pdf.import_statement(
                            c, pdf_file.read_bytes(), pdf_file.name, cfg)
                        grantsm.reconcile_vesting(c)
                        logger.info("Auto-import %s: %s", pdf_file.name, report)
                        done_dir.mkdir(parents=True, exist_ok=True)
                        shutil.move(
                            str(pdf_file),
                            done_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{pdf_file.name}")
                    except Exception:
                        logger.exception("Auto-import %s nieudany", pdf_file.name)
            finally:
                c.close()

    def nightly_backup_job() -> None:
        """Krok 24 (docs/PLAN_KROK_24_backup.md): kopia zapasowa na `/share`,
        żeby dane podatkowe przeżyły cykl przeinstalowania add-onu (znane
        wyczyszczenie /data, patrz reference_supervisor_git_addon_rebuild w
        pamięci projektu). `export_zip()` czyta bazę przez `Connection.backup()`
        — bezpieczne równolegle z resztą schedulera dzięki WAL, bez WRITE_LOCK."""
        snapshot_dir = Path(backup_share) / "backup"
        try:
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            name = f"nokia_{datetime.now():%Y-%m-%d}.zip"
            (snapshot_dir / name).write_bytes(backupm.export_zip(db_path))
            logger.info("Nocna kopia zapasowa zapisana: %s", name)

            keep = 14
            snapshots = sorted(snapshot_dir.glob("nokia_*.zip"))
            for stale in snapshots[:-keep]:
                stale.unlink(missing_ok=True)
        except Exception:
            logger.exception("Nocna kopia zapasowa nieudana")

    def backfill_nbp_rates() -> None:
        """Codziennie: uzupełnia kursy NBP lotom zapisanym, gdy NBP było
        chwilowo niedostępne (tax/lots.py::add_lot nie blokuje zapisu na
        brak kursu — patrz krok 12). GET /lots też to robi przy każdej
        wizycie, ten job jest siatką bezpieczeństwa, gdyby nikt strony
        nie odwiedził. Przy okazji (krok 15): przelicza sekcję G
        (`dividends.pl_tax_due_pln`) na AKTUALNYCH stawkach traktat/Belka —
        w odróżnieniu od kursu NBP to nie jest jednorazowe uzupełnienie
        braku, tylko przeliczenie za każdym razem (patrz docstring
        `taxdiv.backfill_pl_tax_due`), więc zmiana ustawień w UI dociera
        tu bez czekania na ręczną wizytę na `/pit38`. Krok 16: dociąga też
        numer tabeli NBP (`table_no`) dla wierszy zapisanych przed tym
        krokiem oraz zamraża kurs dywidendom wpisanym ręcznie przed
        ujednoliceniem formularza na `add_dividend()`."""
        with dbm.WRITE_LOCK:
            c = dbm.get_conn(db_path)
            try:
                filled = taxlots.backfill_missing_rates(c)
                if filled:
                    logger.info("Backfill kursów NBP: uzupełniono %d lotów", filled)
                filled_div_rates = taxdiv.backfill_missing_dividend_rates(c)
                if filled_div_rates:
                    logger.info(
                        "Backfill kursów NBP: uzupełniono %d dywidend", filled_div_rates)
                filled_tables = fx_nbp.backfill_table_numbers(c)
                if filled_tables:
                    logger.info(
                        "Backfill numerów tabel NBP: uzupełniono %d publikacji", filled_tables)
                cfg = settingsm.get_settings(c)
                updated = taxdiv.backfill_pl_tax_due(c, cfg)
                if updated:
                    logger.info(
                        "Sekcja G: przeliczono pl_tax_due_pln dla %d dywidend", updated)
            except Exception:
                logger.exception("Backfill kursów NBP nieudany")
            finally:
                c.close()

    def backfill_nbp_range_job() -> None:
        """Krok 25 (docs/PLAN_KROK_25_wyniki.md): gęsta seria kursów NBP dla
        `analytics/history.py` — pełny backfill 5 lat wstecz przy pierwszym
        uruchomieniu (`nbp_rates` puste), potem codziennie tylko domyka lukę
        od ostatniej znanej `effective_date` do dziś (kilka dni, nie cały
        zakres — inaczej codzienny job powtarzałby te same ~5 zapytań w
        nieskończoność, `fx_nbp.backfill_range()` sam nie wie, co już ma)."""
        with dbm.WRITE_LOCK:
            c = dbm.get_conn(db_path)
            try:
                row = c.execute("SELECT MAX(effective_date) AS d FROM nbp_rates").fetchone()
                years = settingsm.get_settings(c).get("history_backfill_years", 5)
                since = row["d"] if row and row["d"] else (
                    (datetime.now() - timedelta(days=365 * years)).date().isoformat())
                today = datetime.now().date().isoformat()
                inserted = fx_nbp.backfill_range(c, since, today)
                if inserted:
                    logger.info(
                        "Backfill zakresu NBP: dodano %d publikacji (%s..%s)",
                        inserted, since, today)
            except Exception:
                logger.exception("Backfill zakresu NBP nieudany")
            finally:
                c.close()

    def rebuild_portfolio_history_job() -> None:
        """Krok 25: przelicza `portfolio_history` od zera
        (`analytics/history.py::rebuild()`) — materializowana krzywa wartości
        portfela pod TWR (`sensors.results_values`) i wykres `/wyniki`.
        Uruchamiane PO `backfill_nbp_range_job`, żeby kursy PLN na dzisiejszy
        dzień były już dociągnięte przed przeliczeniem."""
        with dbm.WRITE_LOCK:
            c = dbm.get_conn(db_path)
            try:
                n = analytics_history.rebuild(c, instrument_id)
                logger.info("Krzywa wartości portfela przeliczona: %d dni", n)
            except Exception:
                logger.exception("Przeliczenie krzywej wartości portfela nieudane")
            finally:
                c.close()

    def rebuild_tax_losses_job() -> None:
        """Krok 27: przelicza tax_loss_carryforward od zera
        (tax/losses.py::rebuild()) - żeby korekty importu/danych (nowa
        sprzedaż, poprawiona cena) odzwierciedliły się w dostępnej stracie
        bez czekania na ręczne wejście na /pit38/kreator."""
        with dbm.WRITE_LOCK:
            c = dbm.get_conn(db_path)
            try:
                cfg = settingsm.get_settings(c)
                result = taxlosses.rebuild(c, cfg)
                if result["conflicts"]:
                    logger.warning(
                        "Przeliczenie strat z lat ubiegłych: %d konfliktów "
                        "(korekta danych vs już wykorzystane odliczenia) - "
                        "sprawdź /pit38/kreator", len(result["conflicts"]))
                logger.info(
                    "Straty z lat ubiegłych przeliczone: %d wierszy", len(result["upserted"]))
            except Exception:
                logger.exception("Przeliczenie strat z lat ubiegłych nieudane")
            finally:
                c.close()

    def check_vest_reminders() -> None:
        """Codziennie: reconciliation (loty -> transze, patrz reconcile_vesting) + przypomnienie
        o nadchodzącym vestingu (cfg['vest_reminder_days'] przed datą, krok 14). Reconciliation
        też wywoływane od razu po każdym imporcie (web.py/auto_import_pdf_share) — ten job to
        siatka bezpieczeństwa, ten sam wzorzec co backfill_nbp_rates."""
        with dbm.WRITE_LOCK:
            c = dbm.get_conn(db_path)
            try:
                resolved = grantsm.reconcile_vesting(c)
                if resolved:
                    logger.info("Reconciliation vestingu: rozwiązano %d transz", resolved)
                cfg = settingsm.get_settings(c)
                notify_service = cfg.get("notify_service", "")
                if notify_service:
                    for vest in grantsm.due_for_reminder(c, cfg["vest_reminder_days"]):
                        label = (f"LTI ({vest['participation_description']})"
                                if vest["program"] == "lti" else "ESPP (dopasowanie)")
                        ha_client.notify(
                            notify_service.replace(".", "/", 1),
                            "Zbliża się vesting akcji Nokia",
                            f"{vest['quantity']:.4f} akcji {label} — "
                            f"data vestingu {vest['vest_date']}.")
                        grantsm.mark_reminder_sent(c, vest["id"])
            except Exception:
                logger.exception("Reconciliation/przypomnienia vestingu nieudane")
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

    def daily_digest_job() -> None:
        """Zastępuje dawną automatyzację HA 'Nokia Stock Telegram' (20:00,
        tylko dni robocze — tu odtworzone przez day_of_week zamiast
        binary_sensor.workday). Składa `values` tym samym łańcuchem co
        publish_sensors, ale BEZ odpytywania providerów — digest tylko
        czyta aktualny stan bazy, nie pobiera nowych cen."""
        with dbm.WRITE_LOCK:
            c = dbm.get_conn(db_path)
            try:
                cfg = settingsm.get_settings(c)
                if not (cfg["notify_digest_enabled"] and cfg["notify_service"]):
                    return
                values = sensors.market_values(c, instrument_id)
                values.update(sensors.benchmark_values(
                    c, instrument_id, ericsson_id, omxh25_id, eurpln_id))
                values.update(sensors.ai_values(c))
                values.update(sensors.forecast_values(c))
                dividends = sensors.dividends_values(
                    c, cfg, cfg["position_qty"] * cfg["avg_cost_eur"])
                values.update(portfolio.position_values_auto(
                    c, cfg, values.get("price_eur"), values.get("eurpln_rate"),
                    dividends_net_total_eur=dividends["dividends_net_eur"]))
                values.update(dividends)
                notifier.send_daily_digest(c, cfg, values)
            except Exception:
                logger.exception("Dzienny digest nieudany")
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

    digest_time = _env("DIGEST_TIME", "20:10")
    try:
        digest_hour, digest_minute = (int(x) for x in digest_time.split(":", 1))
    except ValueError:
        logger.warning("DIGEST_TIME=%r nieprawidłowy (oczekiwano HH:MM) — używam 20:10",
                       digest_time)
        digest_hour, digest_minute = 20, 10

    scheduler = BackgroundScheduler(timezone=_env("TZ", "Europe/Warsaw"))
    scheduler.add_job(publish_sensors, "interval", minutes=poll_minutes,
                      next_run_time=datetime.now())
    scheduler.add_job(refresh_intraday_job, "interval", minutes=poll_minutes,
                      next_run_time=datetime.now())
    scheduler.add_job(fetch_news, "interval", minutes=30,
                      next_run_time=datetime.now())
    scheduler.add_job(run_daily_analysis, "cron", hour=analysis_hour, minute=analysis_minute)
    # day_of_week odtwarza warunek binary_sensor.workday z dawnej automatyzacji
    # HA "Nokia Stock Telegram" bez sięgania po encję HA (digest_time > 19:00
    # analysis_time, więc zawsze widzi świeży briefing z tego samego dnia).
    scheduler.add_job(daily_digest_job, "cron", hour=digest_hour, minute=digest_minute,
                      day_of_week="mon-fri")
    scheduler.add_job(backfill_nbp_rates, "cron", hour=6, minute=15)
    scheduler.add_job(check_vest_reminders, "cron", hour=6, minute=30)
    scheduler.add_job(prune_intraday_job, "cron", hour=3, minute=0)
    scheduler.add_job(auto_import_pdf_share, "interval", minutes=30)
    scheduler.add_job(nightly_backup_job, "cron", hour=4, minute=0)
    scheduler.add_job(backfill_nbp_range_job, "cron", hour=5, minute=0)
    scheduler.add_job(rebuild_portfolio_history_job, "cron", hour=5, minute=30)
    scheduler.add_job(rebuild_tax_losses_job, "cron", hour=5, minute=45)
    scheduler.start()

    app = create_app(db_path=db_path)
    logger.info("Web UI nasłuchuje na :8100 (ingress)")
    serve(app, host="0.0.0.0", port=8100)


if __name__ == "__main__":
    main()
