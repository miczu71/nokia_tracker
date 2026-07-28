"""NBP tabela A (kurs średni) — jedyne źródło zgodne z art. 11a ustawy o PIT
(BLUEPRINT §3a): przychody i koszty w walucie obcej przelicza się kursem
średnim NBP z ostatniego dnia roboczego POPRZEDZAJĄCEGO zdarzenie.

Sprawdzone empirycznie 2026-07-27: NBP zwraca 404 dla dat bez publikacji
(weekend/święto), 400 dla dat przyszłych. Endpoint zakresowy
(/eur/{start}/{end}/) zwraca WSZYSTKIE publikacje w oknie jednym
wywołaniem — szukanie kursu 'na dzień X lub wcześniej' to jeden request
z oknem 10 dni wstecz, nie do 10 sekwencyjnych zapytań dzień-po-dniu.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta

import requests

from .. import ratelimit
from .base import QuoteProviderError

logger = logging.getLogger(__name__)

_BASE = "https://api.nbp.pl/api/exchangerates/rates/a/eur"
_LOOKBACK_DAYS = 10


def rate_on_or_before(conn: sqlite3.Connection, target_date: str) -> tuple[float, str] | None:
    """Kurs średni EUR/PLN z ostatniego dnia roboczego <= target_date.

    Zwraca (rate, effective_date) albo None, gdy w oknie _LOOKBACK_DAYS
    nie ma żadnej publikacji (praktycznie niemożliwe przy normalnym
    kalendarzu — NBP publikuje w każdy dzień roboczy).

    Wynik NIE jest cache'owany po URL-u (jak cache.py dla cen) — jest za
    to trwale zapisywany do tabeli nbp_rates przez wywołującego (tax/lots.py,
    krok 11+): raz przypisany do lotu kurs jest zamrożony na zawsze.
    """
    row = conn.execute(
        "SELECT rate, effective_date FROM nbp_rates WHERE date = ?", (target_date,)
    ).fetchone()
    if row:
        return row["rate"], row["effective_date"]

    end = date.fromisoformat(target_date)
    start = end - timedelta(days=_LOOKBACK_DAYS)
    url = f"{_BASE}/{start.isoformat()}/{end.isoformat()}/"

    def _do_request():
        return requests.get(url, params={"format": "json"}, timeout=15)

    resp = ratelimit.backoff_retry(_do_request, provider="nbp", retryable_statuses=(429, 502, 503))
    if resp is None:
        return None
    if resp.status_code == 404:
        logger.warning("NBP: brak publikacji w oknie %d dni przed %s", _LOOKBACK_DAYS, target_date)
        return None
    if resp.status_code != 200:
        raise QuoteProviderError(f"NBP {target_date}: HTTP {resp.status_code}")

    rates = resp.json().get("rates", [])
    if not rates:
        return None
    # Zakres kończy się na target_date, więc ostatni element (najpóźniejsza
    # effectiveDate <= target_date) to dokładnie to, czego szukamy.
    latest = rates[-1]
    rate = float(latest["mid"])
    effective_date = latest["effectiveDate"]
    conn.execute(
        "INSERT OR IGNORE INTO nbp_rates (date, rate, effective_date) VALUES (?, ?, ?)",
        (target_date, rate, effective_date))
    conn.commit()
    return rate, effective_date


def rate_for_event(conn: sqlite3.Connection, event_date: str) -> tuple[float, str] | None:
    """Kurs wg art. 11a ust. 1-2 ustawy o PIT: ostatni dzień roboczy
    POPRZEDZAJĄCY dzień zdarzenia (uzyskania przychodu / poniesienia kosztu).

    To jest funkcja, której powinny używać loty/sprzedaże/dywidendy (krok 12+)
    - `rate_on_or_before()` sama w sobie zwraca kurs 'na dzień X lub wcześniej',
    co dla X = dzień zdarzenia dawałoby o jeden dzień ZA PÓŹNO względem
    przepisu. Sprzedaż z 27.10.2025 (poniedziałek) musi użyć kursu z
    24.10.2025 (piątek), nie z 27.10 nawet gdyby NBP tego dnia publikował.
    """
    event = date.fromisoformat(event_date)
    day_before = (event - timedelta(days=1)).isoformat()
    return rate_on_or_before(conn, day_before)
