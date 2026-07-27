"""Licznik wywołań i tokenów AI (tabela ai_usage) + twardy dzienny limit
(BLUEPRINT §1: kontrola kosztów). `total_tokens` z odpowiedzi API czytane
wprost, NIE sumowane z prompt+completion — zmierzone empirycznie na
freellmapi: total_tokens bywa większe (tokeny myślenia modeli reasoningowych).
"""
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


def calls_today(conn: sqlite3.Connection) -> int:
    """Suma wywołań AI dzisiaj, przez WSZYSTKICH providerów — limit jest
    globalny (ai_max_calls_per_day), nie per-provider."""
    row = conn.execute(
        "SELECT COALESCE(SUM(calls), 0) c FROM ai_usage WHERE day = ?", (_today(),)
    ).fetchone()
    return row["c"]


def tokens_today(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(total_tokens), 0) t FROM ai_usage WHERE day = ?", (_today(),)
    ).fetchone()
    return row["t"]


def allow(conn: sqlite3.Connection, max_per_day: int) -> bool:
    if max_per_day <= 0:
        return True
    return calls_today(conn) < max_per_day
