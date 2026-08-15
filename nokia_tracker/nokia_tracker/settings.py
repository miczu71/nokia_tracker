"""Ustawienia edytowalne w UI — typed KV store w tabeli settings.

Opcje Supervisora zostają jako wartość startowa: seed_from_options() zasila
tylko brakujące klucze (INSERT OR IGNORE), więc baza ma pierwszeństwo nad
opcjami po pierwszym uruchomieniu (wzorzec 1:1 z fuel_tracker/settings.py).
"""
from __future__ import annotations

import sqlite3

SETTINGS_TYPES: dict[str, type] = {
    # --- rynek ---
    "poll_interval_minutes": int,
    "history_backfill_years": int,
    "display_currency_secondary": str,
    "allow_scrape_fallback": int,
    "avanza_live_price_enabled": int,
    # --- AI ---
    "ai_primary": str,
    "ai_fallback": str,
    "local_llm_base_url": str,
    "local_llm_model": str,
    "gemini_model": str,
    "anthropic_model": str,
    "ai_max_tokens": int,
    "ai_max_calls_per_day": int,
    "ai_news_batch_size": int,
    "ai_recommendations_enabled": int,
    "analysis_time": str,
    # --- alerty ---
    "notify_service": str,
    "alert_sentiment_drop": float,
    "alert_price_move_pct": float,
    "alert_on_forecast_break": int,
    "alert_min_interval_minutes": int,
    # --- powiadomienia (krok 22: newsy push + dzienny digest) ---
    "notify_news_enabled": int,
    "notify_news_min_impact": int,
    "notify_digest_enabled": int,
    "digest_time": str,
    # --- portfel (0.1.0: stan posiadania) ---
    "position_qty": float,
    "avg_cost_eur": float,
    "broker_fee_pct": float,
    # --- podatki (schemat gotowy pod 0.2.0) ---
    "cost_basis_policy": str,
    "espp_match_pct": float,
    "finnish_withholding_pct": float,
    "treaty_withholding_pct": float,
    "pl_capital_gains_tax_pct": float,
    "vest_reminder_days": int,
    "tax_year": int,
    # --- doradca planu pracowniczego (krok 26, 0.10.0) ---
    "other_net_worth_pln": float,
    "concentration_alert_pct": float,
}

# Klucze API i hasła MQTT NIE żyją w tabeli settings — zostają wyłącznie w
# ENV z /data/options.json (patrz run.sh), żeby nie duplikować sekretów
# w dwóch miejscach i nie ryzykować ich ujawnienia przez API ustawień w UI.

DEFAULTS: dict[str, object] = {
    "poll_interval_minutes": 10,
    "history_backfill_years": 5,
    "display_currency_secondary": "PLN",
    "allow_scrape_fallback": 0,
    "avanza_live_price_enabled": 1,
    "ai_primary": "local",
    "ai_fallback": "gemini",
    "local_llm_base_url": "http://192.168.0.106:3003/v1",
    "local_llm_model": "gemini-3.1-flash-lite",
    "gemini_model": "gemini-3.1-flash-lite",
    "anthropic_model": "claude-haiku-4-5-20251001",
    "ai_max_tokens": 4000,
    "ai_max_calls_per_day": 40,
    "ai_news_batch_size": 15,
    "ai_recommendations_enabled": 1,
    "analysis_time": "19:00",
    "notify_service": "",
    "alert_sentiment_drop": 0.5,
    "alert_price_move_pct": 3.0,
    "alert_on_forecast_break": 1,
    "alert_min_interval_minutes": 120,
    "notify_news_enabled": 1,
    "notify_news_min_impact": 1,
    "notify_digest_enabled": 1,
    "digest_time": "20:10",
    "position_qty": 0.0,
    "avg_cost_eur": 0.0,
    "broker_fee_pct": 0.0,
    "cost_basis_policy": "own_only",
    "espp_match_pct": 50.0,
    "finnish_withholding_pct": 35.0,
    "treaty_withholding_pct": 15.0,
    "pl_capital_gains_tax_pct": 19.0,
    "vest_reminder_days": 7,
    "tax_year": 0,
    "other_net_worth_pln": 0.0,
    "concentration_alert_pct": 25.0,
}


def normalize_notify_service(service: str) -> str:
    """Ujednolica zapis do formatu kropkowego: 'notify/x' -> 'notify.x'."""
    service = (service or "").strip()
    if "/" in service and "." not in service:
        service = service.replace("/", ".", 1)
    return service


def get_settings(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    stored = {r["key"]: r["value"] for r in rows}
    result = {}
    for key, typ in SETTINGS_TYPES.items():
        raw = stored.get(key)
        result[key] = typ(raw) if raw is not None else DEFAULTS[key]
    result["notify_service"] = normalize_notify_service(result["notify_service"])
    return result


def set_settings(conn: sqlite3.Connection, updates: dict) -> None:
    for key, value in updates.items():
        if key not in SETTINGS_TYPES:
            continue
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)))
    conn.commit()


def seed_from_options(conn: sqlite3.Connection, options: dict) -> None:
    for key, value in options.items():
        if key not in SETTINGS_TYPES or value is None:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value)))
    conn.commit()
