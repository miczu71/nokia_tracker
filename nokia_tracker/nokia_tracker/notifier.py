"""Push na telefon: nowe ocenione newsy + dzienny digest AI (krok 22,
docs/PLAN_KROK_22_notify.md). Newsy: SELECT WHERE notified_at IS NULL +
próg impact, limit na przebieg, żeby restart/dłuższa awaria nie zalały
telefonu wsteczną kolejką. Digest: najnowszy wiersz `briefings` + `values`
policzone tym samym łańcuchem co `publish_sensors` w main.py, ale BEZ
odpytywania providerów cen — digest tylko czyta stan.
"""
from __future__ import annotations

import json
import logging
import sqlite3

from . import ha_client

logger = logging.getLogger(__name__)

# Twardy limit na przebieg (fetch_news leci co 30 min) — chroni telefon
# przed lawiną, gdyby np. długa awaria HA zostawiła dziesiątki newsów
# z notified_at=NULL naraz. Reszta poczeka do kolejnego cyklu.
_MAX_PER_RUN = 5
# Newsy starsze niż to nigdy nie są "świeże" na telefon — patrz _mark_skipped.
_STALE_AFTER = "-2 days"

_IMPACT_LABELS = {0: "brak", 1: "niski", 2: "średni", 3: "wysoki"}
_HORIZON_LABELS = {"immediate": "natychmiastowy", "weeks": "tygodnie", "quarters": "kwartały"}

_GROUP = "nokia-news"
_CHANNEL = "Nokia"


def _sentiment_emoji(sentiment: float | None) -> str:
    # Te same progi (±0.2), co sensors.py::ai_values dla sentiment_label —
    # spójna interpretacja "pozytywny/negatywny/neutralny" w całym add-onie.
    if sentiment is None:
        return "📰"
    if sentiment >= 0.2:
        return "📈"
    if sentiment <= -0.2:
        return "📉"
    return "➖"


def _pending_news(conn: sqlite3.Connection, min_impact: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT n.id, n.title, n.url_canonical, s.sentiment, s.impact, s.horizon, "
        "s.thesis_pl FROM news n JOIN news_scores s ON s.news_id = n.id "
        "WHERE n.notified_at IS NULL AND s.impact >= ? "
        "AND n.published_at >= datetime('now', ?) "
        "ORDER BY n.published_at ASC LIMIT ?",
        (min_impact, _STALE_AFTER, _MAX_PER_RUN),
    ).fetchall()


def _mark_skipped(conn: sqlite3.Connection, min_impact: int) -> None:
    """Domyka pozycje, które nigdy nie zostaną wysłane: ocenione poniżej progu
    impact (wymaga JOIN news_scores — nieocenione jeszcze newsy zostają
    NULL, mogą dojrzeć do wysyłki w kolejnym cyklu po score_pending) albo po
    prostu za stare. Bez tego wisiałyby w kolejce `notified_at IS NULL` na
    zawsze i byłyby przeliczane w każdym cyklu za darmo."""
    conn.execute(
        "UPDATE news SET notified_at = 'skipped' WHERE notified_at IS NULL AND ("
        "published_at < datetime('now', ?) "
        "OR id IN (SELECT news_id FROM news_scores WHERE impact < ?))",
        (_STALE_AFTER, min_impact),
    )
    conn.commit()


def notify_new_news(conn: sqlite3.Connection, cfg: dict) -> int:
    """Wysyła push za każdy nowo oceniony news z impact >= notify_news_min_impact.
    Zwraca liczbę faktycznie wysłanych (main.py loguje). `notified_at`
    ustawiany TYLKO po udanej wysyłce — niedostępność HA daje retry przy
    kolejnym cyklu fetch_news (30 min), nie utratę newsa."""
    notify_service = cfg.get("notify_service", "")
    if not notify_service:
        return 0
    service = notify_service.replace(".", "/", 1)

    rows = _pending_news(conn, cfg["notify_news_min_impact"])
    sent = 0
    for r in rows:
        title = f"{_sentiment_emoji(r['sentiment'])} Nokia: {(r['title'] or '')[:60]}"
        sentiment_txt = f"{r['sentiment']:+.2f}" if r["sentiment"] is not None else "?"
        impact_txt = _IMPACT_LABELS.get(r["impact"], "?")
        horizon_txt = _HORIZON_LABELS.get(r["horizon"], r["horizon"] or "?")
        message = (f"Sentyment {sentiment_txt} · wpływ {impact_txt} · {horizon_txt}\n"
                   f"{r['thesis_pl'] or ''}")
        data = {
            "url": r["url_canonical"], "clickAction": r["url_canonical"],
            "tag": f"nokia-news-{r['id']}", "group": _GROUP, "channel": _CHANNEL,
        }
        if ha_client.notify(service, title, message, data):
            conn.execute(
                "UPDATE news SET notified_at = datetime('now') WHERE id = ?", (r["id"],))
            conn.commit()
            sent += 1

    _mark_skipped(conn, cfg["notify_news_min_impact"])
    if rows:
        logger.info("Powiadomienia o newsach: wysłano %d/%d", sent, len(rows))
    return sent


def _format_digest(values: dict, briefing: sqlite3.Row | None) -> str:
    lines = []

    price = values.get("price_eur")
    if price is not None:
        change_pct = values.get("change_pct_day")
        change_abs = values.get("change_abs_day")
        chg = (f" ({change_pct:+.2f}% / {change_abs:+.2f} EUR)"
              if change_pct is not None and change_abs is not None else "")
        lines.append(f"Nokia Oyj: {price:.3f} EUR{chg}")

    mv_eur = values.get("market_value_eur")
    if mv_eur is not None:
        mv_pln = values.get("market_value_pln")
        pln_part = f" / {mv_pln:,.0f} PLN".replace(",", " ") if mv_pln is not None else ""
        lines.append(f"\nPortfel: {mv_eur:,.0f} EUR{pln_part}".replace(",", " "))
        pnl_eur = values.get("unrealized_pnl_eur")
        pnl_pct = values.get("total_return_pct")
        if pnl_eur is not None:
            pct_part = f" ({pnl_pct:+.1f}%)" if pnl_pct is not None else ""
            lines.append(f"Wynik: {pnl_eur:+,.0f} EUR{pct_part}".replace(",", " "))

    if briefing:
        lines.append(f"\n{briefing['text']}")
        if briefing["recommendation"]:
            conf = briefing["recommendation_confidence"]
            conf_txt = f" (pewność {conf:.1f})" if conf is not None else ""
            lines.append(f"\nRekomendacja: {briefing['recommendation']}{conf_txt}")
        risks = json.loads(briefing["key_risks"]) if briefing["key_risks"] else []
        if risks:
            lines.append("Ryzyka: " + "; ".join(risks[:3]))
    else:
        lines.append("\n(Brak dzisiejszej analizy AI — sprawdź pulpit.)")

    return "\n".join(lines)


def send_daily_digest(conn: sqlite3.Connection, cfg: dict, values: dict) -> bool:
    """Zastępuje dawną automatyzację 'Nokia Stock Telegram'. Zawsze wysyła
    sekcję kursu/portfela nawet gdy briefing AI nie istnieje (analiza
    wyłączona albo łańcuch AI zawiódł tego dnia) — digest nie ma prawa
    milczeć z powodu awarii AI, tylko być uboższy."""
    notify_service = cfg.get("notify_service", "")
    if not notify_service:
        return False
    service = notify_service.replace(".", "/", 1)

    briefing = conn.execute(
        "SELECT * FROM briefings ORDER BY generated_at DESC LIMIT 1"
    ).fetchone()
    if not briefing:
        logger.warning(
            "Dzienny digest: brak briefingu AI, wysyłam samą sekcję rynku/portfela")

    message = _format_digest(values, briefing)
    data = {"tag": "nokia-digest", "group": _GROUP, "channel": _CHANNEL}
    ok = ha_client.notify(service, "Nokia — podsumowanie dnia", message, data)
    if not ok:
        logger.warning("Dzienny digest: wysyłka nieudana")
    return ok
