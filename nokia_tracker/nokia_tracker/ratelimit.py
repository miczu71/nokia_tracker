"""Token bucket per provider (stan w tabeli api_usage) + backoff na 429/502
+ prosty circuit breaker w pamięci procesu (patrz BLUEPRINT §1)."""
from __future__ import annotations

import logging
import random
import sqlite3
import time
from datetime import date

logger = logging.getLogger(__name__)

# Circuit breaker per provider — świadomie w pamięci procesu, nie w bazie:
# restart add-onu ma dać providerowi świeżą szansę zamiast dziedziczyć
# stan "down" sprzed restartu.
_FAILURE_THRESHOLD = 3
_CIRCUIT_COOLDOWN_SECONDS = 1800
_consecutive_failures: dict[str, int] = {}
# Znacznik czasu (time.monotonic) momentu, w którym obwód przeszedł na
# "down" — bez tego, raz otwarty obwód nigdy by się sam nie zamknął
# (record_success wymaga UDANEGO wywołania, a przy is_circuit_open()
# pomijamy providera właśnie po to, żeby go nie wywoływać).
_opened_at: dict[str, float] = {}


def _today() -> str:
    return date.today().isoformat()


def calls_today(conn: sqlite3.Connection, provider: str) -> int:
    row = conn.execute(
        "SELECT calls FROM api_usage WHERE provider = ? AND day = ?",
        (provider, _today()),
    ).fetchone()
    return row["calls"] if row else 0


def record_call(conn: sqlite3.Connection, provider: str) -> None:
    conn.execute(
        "INSERT INTO api_usage (provider, day, calls) VALUES (?, ?, 1) "
        "ON CONFLICT(provider, day) DO UPDATE SET calls = calls + 1",
        (provider, _today()),
    )
    conn.commit()


def allow(conn: sqlite3.Connection, provider: str, max_per_day: int) -> bool:
    """False, gdy dzienny limit providera już wyczerpany (max_per_day<=0 = bez limitu)."""
    if max_per_day <= 0:
        return True
    return calls_today(conn, provider) < max_per_day


def record_success(provider: str) -> None:
    _consecutive_failures[provider] = 0
    _opened_at.pop(provider, None)


def record_failure(provider: str) -> None:
    n = _consecutive_failures.get(provider, 0) + 1
    _consecutive_failures[provider] = n
    if n >= _FAILURE_THRESHOLD and provider not in _opened_at:
        _opened_at[provider] = time.monotonic()


def provider_status(provider: str) -> str:
    """'ok' | 'degraded' (1-2 porażki z rzędu) | 'down' (>=3, w cooldownie)."""
    n = _consecutive_failures.get(provider, 0)
    if n == 0:
        return "ok"
    if n < _FAILURE_THRESHOLD:
        return "degraded"
    return "down"


def is_circuit_open(provider: str) -> bool:
    """True, gdy obwód jest 'down' i cooldown jeszcze trwa. Po
    _CIRCUIT_COOLDOWN_SECONDS resetuje licznik porażek — providerowi
    znów wolno spróbować (bez tego 'down' trwałoby do restartu procesu)."""
    if provider_status(provider) != "down":
        return False
    opened = _opened_at.get(provider)
    if opened is not None and time.monotonic() - opened >= _CIRCUIT_COOLDOWN_SECONDS:
        _consecutive_failures[provider] = 0
        _opened_at.pop(provider, None)
        return False
    return True


def backoff_retry(fn, provider: str, max_attempts: int = 3, base_delay: float = 1.0,
                  retryable_statuses: tuple[int, ...] = (429, 502)):
    """Woła fn() -> requests.Response, ponawia na retryable_statuses z
    exponential backoff + jitter. Aktualizuje circuit breaker providera.

    Zwraca ostatnią odpowiedź (może mieć status błędu, jeśli wszystkie
    próby zawiodły) — wywołujący sam decyduje co zrobić z nieudanym HTTP.
    """
    resp = None
    for attempt in range(max_attempts):
        resp = fn()
        if resp.status_code not in retryable_statuses:
            if resp.status_code < 400:
                record_success(provider)
            else:
                record_failure(provider)
            return resp
        if attempt < max_attempts - 1:
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            logger.warning("%s: HTTP %d, ponawiam za %.1fs (próba %d/%d)",
                          provider, resp.status_code, delay, attempt + 1, max_attempts)
            time.sleep(delay)
    record_failure(provider)
    return resp
