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
    table_no = latest.get("no")
    conn.execute(
        "INSERT OR IGNORE INTO nbp_rates (date, rate, effective_date, table_no) "
        "VALUES (?, ?, ?, ?)",
        (target_date, rate, effective_date, table_no))
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


def table_no_for_effective_date(conn: sqlite3.Connection, effective_date: str) -> str | None:
    """Numer tabeli NBP (np. '142/A/NBP/2026') dla danej `effective_date`
    (dzień faktycznej publikacji, NIE dzień zdarzenia/lookup) — krok 16.

    Świadomie osobna funkcja od `rate_for_event`/`rate_on_or_before` zamiast
    rozszerzania ich krotki wyniku: `effective_date` jest już zamrożone w
    `lots.nbp_rate_date`/`sales.nbp_rate_date`/`dividends.nbp_rate_date`, więc
    UI może dociągnąć numer tabeli osobnym zapytaniem do `nbp_rates` bez
    zmiany sygnatury funkcji używanej w kilkunastu miejscach (lots/dividends/
    whatif) i bez psucia ich testów. `LIMIT 1`, bo wiele różnych `date`
    (dni lookup) może mapować na tę samą `effective_date` (weekend/święto
    zwija się do tej samej ostatniej publikacji) — to ten sam numer tabeli
    za każdym razem, biorący dowolny wystarczy."""
    row = conn.execute(
        "SELECT table_no FROM nbp_rates WHERE effective_date = ? "
        "AND table_no IS NOT NULL LIMIT 1", (effective_date,)).fetchone()
    return row["table_no"] if row else None


def table_urls(table_no: str, effective_date: str) -> dict:
    """Linki do tabeli NBP dla śladu obliczeń (krok 16): `nbp` — czytelna
    strona archiwum dla człowieka (NIE dało się zweryfikować z tego
    środowiska, strona zwracała ~1KB — prawdopodobnie render JS/blokada
    bota, więc traktować jako "najlepszy wysiłek"), `api` — surowy JSON,
    zweryfikowany empirycznie 2026-07-29: zwraca dokładnie ten kurs/numer
    tabeli, zawsze pod tym adresem, niezależnie od JS."""
    slug = table_no.lower().replace("/", "-")
    return {
        "nbp": f"https://nbp.pl/archiwum-kursow/tabela-nr-{slug}-z-dnia-{effective_date}/",
        "api": f"{_BASE}/{effective_date}/?format=json",
    }


def backfill_table_numbers(conn: sqlite3.Connection) -> int:
    """Uzupełnia `table_no` dla wierszy `nbp_rates` zapisanych przed krokiem
    16 (kolumna wtedy nie istniała). Odpytuje NBP per `effective_date`
    (endpoint jednodniowy, zawsze trafia — `effective_date` to z definicji
    dzień faktycznej publikacji) i NIGDY nie dotyka `rate`/`date` — sam kurs
    pozostaje zamrożony na zawsze, dopisujemy wyłącznie metadaną. Zwraca
    liczbę uzupełnionych `effective_date` (nie wierszy — jedno zapytanie
    może uzupełnić kilka wierszy `date` naraz, jeśli mapują na tę samą
    publikację)."""
    rows = conn.execute(
        "SELECT DISTINCT effective_date FROM nbp_rates WHERE table_no IS NULL"
    ).fetchall()
    filled = 0
    for row in rows:
        effective_date = row["effective_date"]

        def _do_request(_eff=effective_date):
            return requests.get(f"{_BASE}/{_eff}/", params={"format": "json"}, timeout=15)

        resp = ratelimit.backoff_retry(
            _do_request, provider="nbp", retryable_statuses=(429, 502, 503))
        if resp is None or resp.status_code != 200:
            continue
        rates = resp.json().get("rates", [])
        if not rates:
            continue
        table_no = rates[0].get("no")
        if table_no is None:
            continue
        conn.execute(
            "UPDATE nbp_rates SET table_no = ? WHERE effective_date = ? AND table_no IS NULL",
            (table_no, effective_date))
        filled += 1
    conn.commit()
    return filled
