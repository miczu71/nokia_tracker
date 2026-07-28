#!/bin/sh
set -e

CONFIG="/data/options.json"

# --- rynek ---
export POLL_INTERVAL_MINUTES=$(jq -r '.poll_interval_minutes // "10"' "$CONFIG")
export HISTORY_BACKFILL_YEARS=$(jq -r '.history_backfill_years // "5"' "$CONFIG")
export DISPLAY_CURRENCY_SECONDARY=$(jq -r '.display_currency_secondary // "PLN"' "$CONFIG")
export ALLOW_SCRAPE_FALLBACK=$(jq -r '.allow_scrape_fallback // "false"' "$CONFIG")
export AVANZA_LIVE_PRICE_ENABLED=$(jq -r '.avanza_live_price_enabled // "true"' "$CONFIG")

# --- klucze API (opcjonalne) ---
export FINNHUB_API_KEY=$(jq -r '.finnhub_api_key // ""' "$CONFIG")
export MARKETAUX_API_KEY=$(jq -r '.marketaux_api_key // ""' "$CONFIG")
export TWELVEDATA_API_KEY=$(jq -r '.twelvedata_api_key // ""' "$CONFIG")

# --- AI: łańcuch providerów, każdy z własnym kluczem (nigdy nie trafia do repo) ---
export AI_PRIMARY=$(jq -r '.ai_primary // "local"' "$CONFIG")
export AI_FALLBACK=$(jq -r '.ai_fallback // "gemini"' "$CONFIG")
export LOCAL_LLM_BASE_URL=$(jq -r '.local_llm_base_url // "http://192.168.0.106:3003/v1"' "$CONFIG")
export LOCAL_LLM_API_KEY=$(jq -r '.local_llm_api_key // ""' "$CONFIG")
export LOCAL_LLM_MODEL=$(jq -r '.local_llm_model // "gemini-3.5-flash"' "$CONFIG")
export GEMINI_API_KEY=$(jq -r '.gemini_api_key // ""' "$CONFIG")
export GEMINI_MODEL=$(jq -r '.gemini_model // "gemini-3.1-flash-lite"' "$CONFIG")
export ANTHROPIC_API_KEY=$(jq -r '.anthropic_api_key // ""' "$CONFIG")
export ANTHROPIC_MODEL=$(jq -r '.anthropic_model // "claude-haiku-4-5-20251001"' "$CONFIG")
export AI_MAX_TOKENS=$(jq -r '.ai_max_tokens // "4000"' "$CONFIG")
export AI_MAX_CALLS_PER_DAY=$(jq -r '.ai_max_calls_per_day // "40"' "$CONFIG")
export AI_NEWS_BATCH_SIZE=$(jq -r '.ai_news_batch_size // "15"' "$CONFIG")
export AI_RECOMMENDATIONS_ENABLED=$(jq -r '.ai_recommendations_enabled // "true"' "$CONFIG")
export ANALYSIS_TIME=$(jq -r '.analysis_time // "19:00"' "$CONFIG")

# --- alerty ---
export NOTIFY_SERVICE=$(jq -r '.notify_service // ""' "$CONFIG")
export ALERT_SENTIMENT_DROP=$(jq -r '.alert_sentiment_drop // "0.5"' "$CONFIG")
export ALERT_PRICE_MOVE_PCT=$(jq -r '.alert_price_move_pct // "3.0"' "$CONFIG")
export ALERT_ON_FORECAST_BREAK=$(jq -r '.alert_on_forecast_break // "true"' "$CONFIG")
export ALERT_MIN_INTERVAL_MINUTES=$(jq -r '.alert_min_interval_minutes // "120"' "$CONFIG")

# --- portfel i podatki (wartości startowe; baza ma pierwszeństwo po pierwszym starcie) ---
export POSITION_QTY=$(jq -r '.position_qty // "0"' "$CONFIG")
export AVG_COST_EUR=$(jq -r '.avg_cost_eur // "0"' "$CONFIG")
export BROKER_FEE_PCT=$(jq -r '.broker_fee_pct // "0"' "$CONFIG")
export COST_BASIS_POLICY=$(jq -r '.cost_basis_policy // "own_only"' "$CONFIG")
export ESPP_MATCH_PCT=$(jq -r '.espp_match_pct // "50"' "$CONFIG")
export FINNISH_WITHHOLDING_PCT=$(jq -r '.finnish_withholding_pct // "35"' "$CONFIG")
export TREATY_WITHHOLDING_PCT=$(jq -r '.treaty_withholding_pct // "15"' "$CONFIG")
export PL_CAPITAL_GAINS_TAX_PCT=$(jq -r '.pl_capital_gains_tax_pct // "19"' "$CONFIG")
export VEST_REMINDER_DAYS=$(jq -r '.vest_reminder_days // "7"' "$CONFIG")
export TAX_YEAR=$(jq -r '.tax_year // "0"' "$CONFIG")

# --- infra ---
export MQTT_HOST=$(jq -r '.mqtt_host // "core-mosquitto"' "$CONFIG")
export MQTT_PORT=$(jq -r '.mqtt_port // "1883"' "$CONFIG")
export MQTT_USER=$(jq -r '.mqtt_user // ""' "$CONFIG")
export MQTT_PASSWORD=$(jq -r '.mqtt_password // ""' "$CONFIG")
export LOG_LEVEL=$(jq -r '.log_level // "info"' "$CONFIG")
export BACKUP_SHARE=$(jq -r '.backup_share // "/share/nokia_tracker"' "$CONFIG")
export TZ=$(jq -r '.timezone // "Europe/Warsaw"' "$CONFIG")

export DB_PATH="/data/nokia_tracker.db"

exec python3 -m nokia_tracker.main
