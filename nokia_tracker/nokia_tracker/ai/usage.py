"""Licznik wywołań i tokenów AI (tabela ai_usage) + dzienny limit
(BLUEPRINT §1: kontrola kosztów). `total_tokens` z odpowiedzi API czytane
wprost, NIE sumowane z prompt+completion — zmierzone empirycznie na
freellmapi: total_tokens bywa większe (tokeny myślenia modeli reasoningowych).

Krok 29: limit sprawdzany PER PROVIDER, nie globalnie — provider.py::analyze()
naprawia realny błąd, w którym wyczerpanie wspólnej puli przez jedno (płatne)
ogniwo blokowało też darmowy 'local'. `provider=None` zachowuje stare,
zsumowane-po-wszystkich zachowanie tam, gdzie to wciąż właściwe (sensor
ai_calls_today na pulpicie ma pokazywać sumę, nie jedno ogniwo)."""
from __future__ import annotations

import sqlite3
from datetime import date


def _today() -> str:
    return date.today().isoformat()


def record_call(conn: sqlite3.Connection, provider: str, model: str, task: str,
                total_tokens: int = 0) -> None:
    conn.execute(
        "INSERT INTO ai_usage (provider, model, task, day, calls, total_tokens) "
        "VALUES (?, ?, ?, ?, 1, ?) "
        "ON CONFLICT(provider, model, task, day) DO UPDATE SET "
        "calls = calls + 1, total_tokens = total_tokens + excluded.total_tokens",
        (provider, model, task, _today(), total_tokens))
    conn.commit()


def calls_today(conn: sqlite3.Connection, provider: str | None = None) -> int:
    if provider is None:
        row = conn.execute(
            "SELECT COALESCE(SUM(calls), 0) c FROM ai_usage WHERE day = ?",
            (_today(),)).fetchone()
    else:
        row = conn.execute(
            "SELECT COALESCE(SUM(calls), 0) c FROM ai_usage WHERE day = ? AND provider = ?",
            (_today(), provider)).fetchone()
    return row["c"]


def tokens_today(conn: sqlite3.Connection, provider: str | None = None) -> int:
    if provider is None:
        row = conn.execute(
            "SELECT COALESCE(SUM(total_tokens), 0) t FROM ai_usage WHERE day = ?",
            (_today(),)).fetchone()
    else:
        row = conn.execute(
            "SELECT COALESCE(SUM(total_tokens), 0) t FROM ai_usage WHERE day = ? AND provider = ?",
            (_today(), provider)).fetchone()
    return row["t"]


def allow(conn: sqlite3.Connection, provider: str, max_per_day: int) -> bool:
    """False, gdy DZIENNY LIMIT TEGO OGNIWA już wyczerpany (max_per_day<=0 =
    bez limitu). Odpowiednik ratelimit.allow(), ale na tabeli ai_usage
    (wywołania AI), nie api_usage (zewnętrzne API danych rynkowych)."""
    if max_per_day <= 0:
        return True
    return calls_today(conn, provider) < max_per_day
