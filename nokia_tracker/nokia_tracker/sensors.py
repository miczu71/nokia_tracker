"""Jedno miejsce: składa wartości wszystkich sensorów z danych w bazie.

Czyste (bez sieci) — czyta wyłącznie z SQLite, żeby dało się testować bez
mockowania HTTP. Newsy/AI/portfel dochodzą w kolejnych krokach jako
kolejne funkcje _*_values() łączone tu.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from . import forecasts as forecastsm
from . import indicators as ind
from . import market, quotes
from . import tax as taxm
from .ai import provider as ai_provider
from .ai import usage as ai_usage
from .ai.prompts import DISCLAIMER

# Progi alpha_verdict: rel_perf_1d_vs_omxh25 w punktach procentowych.
# Świadome uproszczenie: |różnica| liczona wprost, bez normalizacji betą —
# to szybki, czytelny sygnał kierunkowy, nie precyzyjny rozkład ryzyka.
_ALPHA_MARKET_THRESHOLD = 0.5
_ALPHA_COMPANY_THRESHOLD = 2.0


def market_values(conn: sqlite3.Connection, instrument_id: int) -> dict:
    """Sensory grupy 'Rynek' + 'Technika' + binary_sensor market_open."""
    latest = quotes.latest_quote(conn, instrument_id)
    today = quotes.today_daily_candle(conn, instrument_id)
    prev_close = quotes.prev_daily_close(conn, instrument_id)
    week52_high, week52_low = quotes.week52_high_low(conn, instrument_id)
    closes = quotes.daily_closes(conn, instrument_id)

    price = latest["close"] if latest else None
    change_abs = (price - prev_close) if (price is not None and prev_close) else None
    change_pct = (change_abs / prev_close * 100) if (change_abs is not None and prev_close) else None

    is_open = market.is_session_open()

    return {
        "price_eur": price,
        "change_pct_day": change_pct,
        "change_abs_day": change_abs,
        "day_high": today["high"] if today else None,
        "day_low": today["low"] if today else None,
        "prev_close": prev_close,
        "volume": today["volume"] if today else None,
        "week52_high": week52_high,
        "week52_low": week52_low,
        "market_state": "sesja otwarta" if is_open else "sesja zamknięta",
        "last_quote_ts": latest["ts"] if latest else None,
        "sma_20": ind.sma(closes, 20),
        "sma_50": ind.sma(closes, 50),
        "rsi_14": ind.rsi(closes, 14),
        "volatility_30d_pct": ind.volatility_pct(closes, 30),
        "trend": ind.trend(closes),
        "market_open": is_open,
    }


def _pct_change(closes: list[float], sessions_back: int) -> float | None:
    if len(closes) <= sessions_back:
        return None
    a, b = closes[-1], closes[-1 - sessions_back]
    if b == 0:
        return None
    return (a - b) / b * 100


def benchmark_values(conn: sqlite3.Connection, primary_id: int, ericsson_id: int,
                     omxh25_id: int, eurpln_id: int, adr_id: int | None = None,
                     eurusd_id: int | None = None) -> dict:
    """Sensory grupy 'Benchmark' + bliźniaki PLN + ADR.

    Uwaga: serie Nokii/Ericssona/OMXH25 wyrównywane POZYCYJNIE (ostatnie N
    sesji), nie po dacie — Sztokholm i Helsinki mają niemal identyczny,
    ale nie w 100% tożsamy kalendarz świąt giełdowych. Świadome
    uproszczenie 0.1.0, ten sam kompromis co market.py (BLUEPRINT §1).
    """
    nokia_closes = quotes.daily_closes(conn, primary_id)
    ericsson_closes = quotes.daily_closes(conn, ericsson_id)
    omxh25_closes = quotes.daily_closes(conn, omxh25_id)

    ericsson_price = ericsson_closes[-1] if ericsson_closes else None
    omxh25_value = omxh25_closes[-1] if omxh25_closes else None

    nokia_1d = _pct_change(nokia_closes, 1)
    omxh25_1d = _pct_change(omxh25_closes, 1)
    rel_perf_1d = (nokia_1d - omxh25_1d) if (nokia_1d is not None and omxh25_1d is not None) else None

    nokia_1m = _pct_change(nokia_closes, 21)  # ~21 sesji = miesiąc handlowy
    ericsson_1m = _pct_change(ericsson_closes, 21)
    rel_perf_1m = ((nokia_1m - ericsson_1m)
                  if (nokia_1m is not None and ericsson_1m is not None) else None)

    nokia_returns = ind.daily_returns(nokia_closes)
    omxh25_returns = ind.daily_returns(omxh25_closes)
    beta_60d = ind.beta(nokia_returns[-60:], omxh25_returns[-60:])

    if rel_perf_1d is None:
        alpha_verdict = None
    elif abs(rel_perf_1d) < _ALPHA_MARKET_THRESHOLD:
        alpha_verdict = "trend rynkowy"
    elif abs(rel_perf_1d) > _ALPHA_COMPANY_THRESHOLD:
        alpha_verdict = "specyficzne dla spółki"
    else:
        alpha_verdict = "mieszane"

    eurpln_latest = quotes.latest_quote(conn, eurpln_id, granularity="daily")
    eurpln_rate = eurpln_latest["close"] if eurpln_latest else None
    price_eur = nokia_closes[-1] if nokia_closes else None
    price_pln = (price_eur * eurpln_rate) if (price_eur is not None and eurpln_rate) else None

    adr_price_usd = None
    spread_vs_adr = None
    if adr_id is not None:
        adr_latest = quotes.latest_quote(conn, adr_id, granularity="daily")
        adr_price_usd = adr_latest["close"] if adr_latest else None
        if adr_price_usd and price_eur and eurusd_id is not None:
            eurusd_latest = quotes.latest_quote(conn, eurusd_id, granularity="daily")
            eurusd_rate = eurusd_latest["close"] if eurusd_latest else None
            if eurusd_rate:
                # ADR (USD) -> implikowany kurs EUR na Helsinkach; różnica
                # względem realnego price_eur pokazuje rozjazd sesji
                # amerykańskiej z europejską (po zamknięciu Helsinek jedyny
                # żywy sygnał, patrz BLUEPRINT §1).
                implied_eur = adr_price_usd / eurusd_rate
                spread_vs_adr = (price_eur - implied_eur) / price_eur * 100

    return {
        "ericsson_price": ericsson_price,
        "omxh25_value": omxh25_value,
        "rel_perf_1d_vs_omxh25": rel_perf_1d,
        "rel_perf_1m_vs_ericsson": rel_perf_1m,
        "beta_60d": beta_60d,
        "alpha_verdict": alpha_verdict,
        "eurpln_rate": eurpln_rate,
        "price_pln": price_pln,
        "adr_price_usd": adr_price_usd,
        "spread_vs_adr": spread_vs_adr,
    }


def ai_values(conn: sqlite3.Connection) -> dict:
    """Sensory grupy 'AI': agregaty z news_scores dla newsów opublikowanych
    w ostatnich 24h + stan łańcucha providerów (diagnostyka)."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rows = conn.execute(
        "SELECT n.title, n.url_canonical, n.published_at, s.sentiment, s.impact, "
        "s.horizon, s.thesis_pl, s.tags "
        "FROM news n JOIN news_scores s ON s.news_id = n.id "
        "WHERE n.published_at >= ? ORDER BY s.impact DESC, n.published_at DESC",
        (since,),
    ).fetchall()

    sentiments = [r["sentiment"] for r in rows if r["sentiment"] is not None]
    impacts = [r["impact"] for r in rows if r["impact"] is not None]
    sentiment_score = sum(sentiments) / len(sentiments) if sentiments else None
    impact_score = sum(impacts) / len(impacts) if impacts else None

    if sentiment_score is None:
        sentiment_label = None
    elif sentiment_score > 0.2:
        sentiment_label = "pozytywny"
    elif sentiment_score < -0.2:
        sentiment_label = "negatywny"
    else:
        sentiment_label = "neutralny"

    top = rows[:10]
    top_items = [{
        "title": r["title"], "url": r["url_canonical"], "published_at": r["published_at"],
        "sentiment": r["sentiment"], "impact": r["impact"], "horizon": r["horizon"],
        "thesis_pl": r["thesis_pl"], "tags": json.loads(r["tags"]) if r["tags"] else [],
    } for r in top]

    return {
        "sentiment_score": sentiment_score,
        "sentiment_label": sentiment_label,
        "impact_score": impact_score,
        "news_count_24h": len(rows),
        "top_news": top[0]["title"][:250] if top else None,
        "top_news_attrs": {"items": top_items},
        "ai_provider_active": ai_provider.active_provider(),
        "ai_calls_today": ai_usage.calls_today(conn),
    }


def forecast_values(conn: sqlite3.Connection) -> dict:
    """Sensory grupy 'Prognozy i rekomendacja': najnowsza prognoza per
    horyzont (state=cena, attrs=przedział+pewność), historyczna trafność
    (MAPE-bazowana), najnowszy briefing dzienny i rekomendacja AI."""
    out: dict = {}
    for horizon in ("1w", "1m", "12m"):
        row = conn.execute(
            "SELECT predicted_price, ci_low, ci_high, confidence, model, created_at "
            "FROM forecasts WHERE horizon = ? ORDER BY created_at DESC LIMIT 1", (horizon,)
        ).fetchone()
        slug = f"forecast_{horizon}_eur"
        out[slug] = row["predicted_price"] if row else None
        out[f"{slug}_attrs"] = {
            "ci_low": row["ci_low"], "ci_high": row["ci_high"], "confidence": row["confidence"],
            "model": row["model"], "generated_at": row["created_at"],
        } if row else {}

    out["forecast_accuracy_pct"] = forecastsm.accuracy_pct(conn)

    briefing = conn.execute(
        "SELECT * FROM briefings ORDER BY generated_at DESC LIMIT 1"
    ).fetchone()
    if briefing:
        date_label = briefing["generated_at"][:10]
        out["daily_briefing"] = f"{date_label} · {briefing['verdict'] or ''}".rstrip(" ·")
        out["daily_briefing_attrs"] = {
            "text": briefing["text"], "tts_text": briefing["tts_text"],
            "sentiment_avg": briefing["sentiment_avg"], "news_count": briefing["news_count"],
            "verdict": briefing["verdict"],
            "key_risks": json.loads(briefing["key_risks"]) if briefing["key_risks"] else [],
            "model": briefing["model"], "generated_at": briefing["generated_at"],
        }
        out["ai_recommendation"] = briefing["recommendation"]
        out["ai_recommendation_attrs"] = {
            "reason_pl": briefing["recommendation_reason_pl"],
            "confidence": briefing["recommendation_confidence"],
            "disclaimer": DISCLAIMER,
        }
    else:
        out["daily_briefing"] = None
        out["daily_briefing_attrs"] = {}
        out["ai_recommendation"] = None
        out["ai_recommendation_attrs"] = {}
    return out


def dividends_values(conn: sqlite3.Connection, cfg: dict, cost_basis_eur: float | None) -> dict:
    """Sensory grupy 'Dywidendy i podatki' — suma po wszystkich zarejestrowanych
    dywidendach (formularz web UI, krok 9). Kalkulator ORIENTACYJNY na
    bieżących ustawieniach procentowych — przelicza tax.compute_dividend_tax()
    per wiersz z zapisanego gross_eur/withholding_pct (jak faktycznie pobrano
    u źródła) i AKTUALNYCH treaty/Belka z ustawień, nie z zamrożonego kursu
    NBP D-1 wymaganego przez pełne rozliczenie PIT-38 (dochodzi w kroku 11)."""
    rows = conn.execute(
        "SELECT gross_eur, withholding_pct FROM dividends ORDER BY pay_date"
    ).fetchall()
    gross = net = withheld = pl_due = reclaimable = 0.0
    for r in rows:
        withholding_pct = r["withholding_pct"]
        if withholding_pct is None:
            withholding_pct = cfg["finnish_withholding_pct"]
        t = taxm.compute_dividend_tax(
            r["gross_eur"], withholding_pct,
            cfg["treaty_withholding_pct"], cfg["pl_capital_gains_tax_pct"])
        gross += r["gross_eur"]
        net += t["net_received_eur"]
        withheld += t["withholding_paid_eur"]
        pl_due += t["pl_tax_due_eur"]
        reclaimable += t["reclaimable_from_finland_eur"]

    return {
        "dividends_gross_eur": gross,
        "dividends_net_eur": net,
        "withholding_paid_eur": withheld,
        "pl_tax_due_eur": pl_due,
        "reclaimable_from_finland_eur": reclaimable,
        "dividend_yield_on_cost_pct": (gross / cost_basis_eur * 100
                                       if cost_basis_eur else None),
    }
