"""Jednorazowe, idempotentne naprawy danych produkcyjnych — odkryte przez
audyt E1 / `integrity.py` (docs/ROADMAP_V3.md). Każda naprawa sprawdza swój
efekt (istnienie natural_key) zanim coś zrobi — bezpieczne do wołania przy
KAŻDYM starcie add-onu, nie tylko raz; kolejne naprawy dopisywać tu, nie
usuwać stare (ślad tego, co kiedykolwiek naprawiono)."""
from __future__ import annotations

import logging
import sqlite3

from .tax import lots as taxlots

logger = logging.getLogger(__name__)

_MISSING_MATCH_LOT_2025_08_KEY = "vested_matching:2025-08-28:3.71:24.42"
_MISSING_MATCH_GRANT_KEY = "espp_grant:2024-10-21:24.42"


def fix_missing_espp_match_lot_2025_08(conn: sqlite3.Connection) -> None:
    """Audyt E1 (2026-08-22): dopasowanie ESPP z grantu 2024-10-21 (24.42 szt.,
    vest_date=2025-08-01) nigdy nie dostało lotu — jedyny brak w całej
    historii grantów (`reconcile_vesting()` w tax/grants.py celowo nie zgaduje
    dopasowania bez dokładnego trafienia ilością, więc nie mogła go sama
    naprawić). Rekonstrukcja z sąsiednich lotów tej samej paczki vestingu
    (2025-08-28, 3.71 EUR, NBP 4.2639/2025-08-27 — ta sama cena dla całej
    paczki, wzorzec potwierdzony na WSZYSTKICH innych paczkach w bazie).

    Te akcje uczestniczyły w jedynej zarejestrowanej sprzedaży (sale_id=1,
    2025-10-27, 784 szt.): bez tego lotu FIFO sięgnęło 8.48 szt. za dużo do
    droższego lotu z dnia sprzedaży (5.41 EUR) zamiast do tańszej partii z
    2025-08-28 (3.71 EUR). PIT-38 tej sprzedaży ma nadpisanie
    `reported_cost_pln`/`reported_revenue_pln` (krok 20) — ta naprawa NIE
    zmienia niczego zadeklarowanego, tylko przywraca poprawny ślad audytowy
    w `lots`/`sale_allocations`, na którym opierają się przyszłe analizy
    (koncentracja, plan sprzedaży, stan konta)."""
    existing = conn.execute(
        "SELECT id FROM lots WHERE natural_key = ?",
        (_MISSING_MATCH_LOT_2025_08_KEY,)).fetchone()
    if existing:
        return

    lot_id = taxlots.add_lot(
        conn, "2025-08-28", "matched", 24.42, 3.71, source="manual_reconciliation",
        natural_key=_MISSING_MATCH_LOT_2025_08_KEY,
        notes="Rekonstrukcja E2 (docs/ROADMAP_V3.md) — brakujący lot dopasowania "
              "ESPP z grantu 2024-10-21, znaleziony przez audyt E1 2026-08-22 jako "
              "vest przeterminowany o 386 dni. Cena/kurs z sąsiednich lotów tej "
              "samej paczki (2025-08-28, natural_key vested_matching/vested_release "
              "...:3.71:*).")

    vest = conn.execute(
        "SELECT v.id FROM vests v JOIN grants g ON g.id = v.grant_id "
        "WHERE g.natural_key = ? AND v.vest_date = '2025-08-01' AND v.status = 'pending'",
        (_MISSING_MATCH_GRANT_KEY,)).fetchone()
    if vest:
        conn.execute(
            "UPDATE vests SET status = 'vested', lot_id = ? WHERE id = ?",
            (lot_id, vest["id"]))

    _reallocate_sale(conn, sale_id=1)
    conn.commit()
    logger.warning(
        "Naprawa danych (audyt E1): dodano brakujący lot matched (id=%d, 24.42 szt., "
        "2025-08-28) i przeliczono alokację sprzedaży #1", lot_id)


def _reallocate_sale(conn: sqlite3.Connection, sale_id: int) -> None:
    """Przywraca `qty_remaining` lotów tej sprzedaży do stanu SPRZED niej,
    usuwa stare alokacje i przelicza od zera przez `_plan_fifo` — dokładnie tę
    samą funkcję i matematykę, której użyłaby realna sprzedaż, więc wynik jest
    identyczny z tym, co silnik zrobiłby, gdyby brakujący lot istniał od
    początku."""
    sale = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
    old_allocs = conn.execute(
        "SELECT * FROM sale_allocations WHERE sale_id = ?", (sale_id,)).fetchall()

    for a in old_allocs:
        conn.execute(
            "UPDATE lots SET qty_remaining = qty_remaining + ? WHERE id = ?",
            (a["quantity"], a["lot_id"]))
    conn.execute("DELETE FROM sale_allocations WHERE sale_id = ?", (sale_id,))

    candidates = taxlots.open_lots(conn, as_of=sale["sale_date"])
    allocations = taxlots._plan_fifo(
        candidates, sale["quantity"], sale["price_eur"], sale["fee_eur"], sale["nbp_rate"])

    for alloc in allocations:
        conn.execute(
            "INSERT INTO sale_allocations (sale_id, lot_id, quantity, cost_pln, revenue_pln) "
            "VALUES (?, ?, ?, ?, ?)",
            (sale_id, alloc["lot_id"], alloc["quantity"], alloc["cost_pln"],
             alloc["revenue_pln"]))
        conn.execute(
            "UPDATE lots SET qty_remaining = qty_remaining - ? WHERE id = ?",
            (alloc["quantity"], alloc["lot_id"]))


def apply_all(conn: sqlite3.Connection) -> None:
    """Wołane raz przy starcie (main.py) — bezpieczne przy każdym restarcie,
    każda naprawa jest idempotentna."""
    fix_missing_espp_match_lot_2025_08(conn)
