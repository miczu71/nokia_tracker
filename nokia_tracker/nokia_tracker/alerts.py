"""Smart alerty: progi + histereza + anty-spam, log w alerts_log, publikacja
na nokia_tracker/events/alert (NIE retained) + ha_client.notify (BLUEPRINT §2).

Histereza dla sentiment_drop porównuje BIEŻĄCY odczyt z POPRZEDNIM (w
pamięci procesu, wzorzec z ratelimit.py::_consecutive_failures — restart
daje świeży start zamiast dziedziczyć stan sprzed restartu). Pozostałe
rodzaje alertów to progi stanu bieżącego + anty-spam przez
alert_min_interval_minutes (minimalny odstęp między kolejnymi alertami
TEGO SAMEGO rodzaju, sprawdzany przez alerts_log — to jest właściwa
histereza chroniąca przed oscylacją wokół progu).

portfolio_threshold (P&L przebija zadany poziom) odłożone do kroku 9 —
zależy od portfolio.py, którego jeszcze nie ma.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from . import ha_client

logger = logging.getLogger(__name__)

_DIVERGENCE_THRESHOLD_PP = 5.0

# Ostatni znany sentyment — w pamięci procesu, per BLUEPRINT §1 (restart =
# świeży start, nie fałszywy alarm z odczytu sprzed restartu).
_last_sentiment: list[float | None] = [None]


def _check_sentiment_drop(cfg: dict, values: dict) -> dict | None:
    current = values.get("sentiment_score")
    previous = _last_sentiment[0]
    _last_sentiment[0] = current
    if current is None or previous is None:
        return None
    drop = previous - current
    if drop < cfg["alert_sentiment_drop"]:
        return None
    return {
        "kind": "sentiment_drop", "severity": "warning",
        "title": "Nagły spadek sentymentu Nokii",
        "message": f"Sentyment 24h spadł z {previous:+.2f} do {current:+.2f}.",
        "sentiment_before": previous, "sentiment_after": current,
    }


def _check_price_move(cfg: dict, values: dict) -> dict | None:
    change = values.get("change_pct_day")
    if change is None or abs(change) < cfg["alert_price_move_pct"]:
        return None
    direction = "wzrósł" if change > 0 else "spadł"
    return {
        "kind": "price_move_pct", "severity": "info",
        "title": f"Nokia {direction} o {abs(change):.1f}% dzisiaj",
        "message": f"Kurs {direction} o {change:+.2f}% w ciągu dnia.",
        "change_pct_day": change,
    }


def _check_forecast_break(conn: sqlite3.Connection, cfg: dict, values: dict) -> dict | None:
    if not cfg["alert_on_forecast_break"]:
        return None
    price = values.get("price_eur")
    if price is None:
        return None
    row = conn.execute(
        "SELECT ci_low, ci_high FROM forecasts WHERE horizon = '1w' "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not row or row["ci_low"] is None or row["ci_high"] is None:
        return None
    if row["ci_low"] <= price <= row["ci_high"]:
        return None
    return {
        "kind": "price_breaks_forecast", "severity": "warning",
        "title": "Kurs wyszedł poza przedział prognozy",
        "message": (f"Kurs {price} EUR poza przedziałem prognozy 1-tygodniowej "
                   f"[{row['ci_low']}, {row['ci_high']}] EUR."),
        "price_eur": price, "ci_low": row["ci_low"], "ci_high": row["ci_high"],
    }


def _check_divergence(values: dict) -> dict | None:
    rel = values.get("rel_perf_1d_vs_omxh25")
    if rel is None or abs(rel) < _DIVERGENCE_THRESHOLD_PP:
        return None
    return {
        "kind": "divergence", "severity": "info",
        "title": "Nokia mocno odstaje od OMXH25",
        "message": f"Różnica dzienna względem OMXH25: {rel:+.2f} pp.",
        "rel_perf_1d_vs_omxh25": rel,
    }


def _allow_fire(conn: sqlite3.Connection, kind: str, min_interval_minutes: int) -> bool:
    if min_interval_minutes <= 0:
        return True
    row = conn.execute(
        "SELECT fired_at FROM alerts_log WHERE kind = ? ORDER BY fired_at DESC LIMIT 1", (kind,)
    ).fetchone()
    if not row:
        return True
    last_fired = datetime.fromisoformat(row["fired_at"])
    if last_fired.tzinfo is None:
        last_fired = last_fired.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last_fired >= timedelta(minutes=min_interval_minutes)


def _fire(conn: sqlite3.Connection, alert: dict, mqtt_pub, notify_service: str) -> None:
    fired_at = datetime.now(timezone.utc).isoformat()
    extra = {k: v for k, v in alert.items() if k not in ("kind", "severity", "title", "message")}
    conn.execute(
        "INSERT INTO alerts_log (kind, severity, title, message, payload, fired_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (alert["kind"], alert["severity"], alert["title"], alert["message"],
         json.dumps(extra, ensure_ascii=False), fired_at))
    conn.commit()

    if mqtt_pub is not None:
        try:
            mqtt_pub.publish_alert(dict(alert, fired_at=fired_at))
        except Exception:
            logger.exception("Publikacja alertu MQTT nieudana (kind=%s)", alert["kind"])

    if notify_service:
        ha_client.notify(notify_service.replace(".", "/", 1), alert["title"], alert["message"])

    logger.info("Alert odpalony: %s — %s", alert["kind"], alert["title"])


def check_and_fire(conn: sqlite3.Connection, cfg: dict, values: dict, mqtt_pub=None) -> list[dict]:
    """Sprawdza wszystkie rodzaje alertów na bieżących wartościach sensorów
    (values = market_values+benchmark_values+ai_values scalone). Zwraca
    listę faktycznie odpalonych alertów (dla logów/testów)."""
    candidates = [
        _check_sentiment_drop(cfg, values),
        _check_price_move(cfg, values),
        _check_forecast_break(conn, cfg, values),
        _check_divergence(values),
    ]
    fired = []
    for alert in candidates:
        if alert is None:
            continue
        if _allow_fire(conn, alert["kind"], cfg["alert_min_interval_minutes"]):
            _fire(conn, alert, mqtt_pub, cfg.get("notify_service", ""))
            fired.append(alert)
    return fired
