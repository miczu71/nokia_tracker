"""Granty i transze vestingu (ESPP match / LTI RSU) — BLUEPRINT §3a, krok 13.

CRUD idempotentny po `natural_key`, ten sam wzorzec co `tax/lots.py::add_lot`. Konsument
tych zapisów (auto-tworzenie lotów `matched`/`lti` w dniu przekroczenia `vest_date`) to
scheduler kroku 14 (`tax/vesting.py`) — ten moduł tylko utrwala harmonogram w bazie.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta


def add_grant(conn: sqlite3.Connection, program: str, grant_date: str, quantity: float,
              natural_key: str, declared_amount_eur: float | None = None,
              match_pct: float = 0.0, notes: str | None = None) -> int:
    """Wstawia grant; idempotentne po `natural_key` — istniejący klucz zwraca id
    istniejącego grantu bez wstawiania ponownie."""
    existing = conn.execute(
        "SELECT id FROM grants WHERE natural_key = ?", (natural_key,)).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO grants (program, grant_date, declared_amount_eur, quantity, "
        "match_pct, natural_key, notes) VALUES (?,?,?,?,?,?,?)",
        (program, grant_date, declared_amount_eur, quantity, match_pct, natural_key, notes))
    conn.commit()
    return cur.lastrowid


def add_vest(conn: sqlite3.Connection, grant_id: int, vest_date: str, quantity: float,
             natural_key: str, status: str = "pending") -> int:
    """Wstawia transzę vestingu; idempotentne po `natural_key`. Jeden grant (zwłaszcza LTI)
    może mieć wiele transz — każda z własnym `natural_key`."""
    existing = conn.execute(
        "SELECT id FROM vests WHERE natural_key = ?", (natural_key,)).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO vests (grant_id, vest_date, quantity, status, natural_key) "
        "VALUES (?,?,?,?,?)",
        (grant_id, vest_date, quantity, status, natural_key))
    conn.commit()
    return cur.lastrowid


def find_grant_by_natural_key(conn: sqlite3.Connection, natural_key: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM grants WHERE natural_key = ?", (natural_key,)).fetchone()
    return dict(row) if row else None


def find_vest_by_natural_key(conn: sqlite3.Connection, natural_key: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM vests WHERE natural_key = ?", (natural_key,)).fetchone()
    return dict(row) if row else None


def due_for_reminder(conn: sqlite3.Connection, vest_reminder_days: int,
                     today: str | None = None) -> list[dict]:
    """Transze 'pending' z vest_date w oknie [dziś, dziś+vest_reminder_days], którym jeszcze
    nie wysłano przypomnienia (krok 14, docs/PLAN_KROK_14_vesting_reconcile.md). Zwraca też
    participation_description (LTI) do treści powiadomienia."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")
    horizon = (datetime.strptime(today, "%Y-%m-%d")
              + timedelta(days=vest_reminder_days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT v.*, g.program, g.grant_date, g.natural_key AS grant_natural_key FROM vests v "
        "JOIN grants g ON g.id = v.grant_id "
        "WHERE v.status = 'pending' AND v.reminder_sent_at IS NULL "
        "AND v.vest_date BETWEEN ? AND ?", (today, horizon)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["participation_description"] = (
            d["grant_natural_key"].split("lti_grant:", 1)[-1]
            if d["program"] == "lti" and d["grant_natural_key"] else None)
        result.append(d)
    return result


def mark_reminder_sent(conn: sqlite3.Connection, vest_id: int) -> None:
    conn.execute(
        "UPDATE vests SET reminder_sent_at = ? WHERE id = ?",
        (datetime.now().isoformat(), vest_id))
    conn.commit()


def list_espp(conn: sqlite3.Connection, today: str | None = None) -> list[dict]:
    """ESPP: jeden grant = jedna transza (import_statement zawsze wstawia oba 1:1).

    `overdue` (krok 13.6, docs/PLAN_KROK_13_6_vesting_gap.md): sygnał czysto DATOWY,
    `status == 'pending' and vest_date < today` — NIE zgadujemy, czy transza faktycznie
    zvestowała (wymagałoby kruchego dopasowania ilości do "Vested Matching Shares"/
    Withhold-to-Cover Typu A, patrz plan) — tylko uczciwie sygnalizujemy, że harmonogram
    już minął i warto sprawdzić wyciąg."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT g.id AS grant_id, g.grant_date, g.match_pct, "
        "v.vest_date, v.quantity, v.status "
        "FROM grants g JOIN vests v ON v.grant_id = g.id "
        "WHERE g.program = 'espp' ORDER BY g.grant_date"
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["overdue"] = d["status"] == "pending" and d["vest_date"] < today
        result.append(d)
    return result


_PROGRAM_TO_LOT_TYPE = {"espp": "matched", "lti": "lti"}


def reconcile_vesting(conn: sqlite3.Connection, today: str | None = None) -> int:
    """Dopasowuje loty `matched`/`lti` (utworzone z 'Vested Matching Shares'/Withhold Typ A,
    krok 13.6) do transz `vests` wciąż oznaczonych 'pending' — TYLKO gdy dopasowanie po
    (program, ilość) jest DOKŁADNE i JEDNOZNACZNE po obu stronach (dokładnie jeden nierozliczony
    lot danego typu o tej ilości I dokładnie jedna pasująca oczekująca transza). W przeciwnym
    razie transza zostaje 'pending' — uczciwie, bo nie potrafimy tego udowodnić z danych PDF
    (część historycznych dopasowań ESPP sprzed 2022 nigdy nie pojawia się w naszym harmonogramie,
    więc ich saldo nigdy się nie dopasuje - to jest OK, nie próbujemy zgadywać, patrz
    docs/PLAN_KROK_14_vesting_reconcile.md). Zwraca liczbę rozwiązanych transz."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")
    resolved = 0
    for program, lot_type in _PROGRAM_TO_LOT_TYPE.items():
        pending_vests = conn.execute(
            "SELECT v.* FROM vests v JOIN grants g ON g.id = v.grant_id "
            "WHERE g.program = ? AND v.status = 'pending' AND v.vest_date <= ?",
            (program, today)).fetchall()
        unlinked_lots = conn.execute(
            "SELECT * FROM lots WHERE lot_type = ? AND id NOT IN "
            "(SELECT lot_id FROM vests WHERE lot_id IS NOT NULL)", (lot_type,)).fetchall()
        for vest in pending_vests:
            matches = [l for l in unlinked_lots if abs(l["quantity"] - vest["quantity"]) < 1e-9]
            vest_matches = [v for v in pending_vests
                            if abs(v["quantity"] - vest["quantity"]) < 1e-9]
            if len(matches) == 1 and len(vest_matches) == 1:
                conn.execute(
                    "UPDATE vests SET status = 'vested', lot_id = ? WHERE id = ?",
                    (matches[0]["id"], vest["id"]))
                resolved += 1
    conn.commit()
    return resolved


def list_lti_grouped(conn: sqlite3.Connection, today: str | None = None) -> list[dict]:
    """LTI: jeden grant (jedna `participation_description`) + zagnieżdżone transze.
    `total_quantity` sumuje transze, bo `grants.quantity` jest celowo NULL dla LTI —
    pojedynczy wiersz RS AWARD nie zna sumy całego grantu (patrz importers/computershare_pdf.py).

    `overdue` per transza — patrz `list_espp`."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")
    grants = conn.execute(
        "SELECT * FROM grants WHERE program = 'lti' ORDER BY grant_date"
    ).fetchall()
    result = []
    for g in grants:
        vests = [dict(v) for v in conn.execute(
            "SELECT * FROM vests WHERE grant_id = ? ORDER BY vest_date", (g["id"],)).fetchall()]
        for v in vests:
            v["overdue"] = v["status"] == "pending" and v["vest_date"] < today
        description = (g["natural_key"].split("lti_grant:", 1)[-1]
                        if g["natural_key"] else None)
        result.append({
            **dict(g),
            "participation_description": description,
            "vests": vests,
            "total_quantity": sum(v["quantity"] for v in vests),
        })
    return result
