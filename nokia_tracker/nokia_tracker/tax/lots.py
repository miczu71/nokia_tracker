"""Loty i alokacja FIFO (BLUEPRINT §3a, krok 12).

Zasady, którymi rządzi się ten moduł:

- **Kurs NBP zamrożony raz na zawsze.** `add_lot`/`record_sale` łapią kurs
  przez `fx_nbp.rate_for_event` (art. 11a: dzień roboczy POPRZEDZAJĄCY
  zdarzenie) w momencie zapisu. `backfill_missing_rates` uzupełnia tylko
  wiersze, gdzie kurs jest jeszcze `NULL` — nigdy nie nadpisuje już
  ustawionej wartości.
- **`sale_allocations.cost_pln` to surowy koszt nabycia**, nie koszt po
  polityce. Filtr polityki (`own_only`/`own_plus_drip`/`all_at_acquisition`)
  nakłada się dopiero przy raportowaniu (`tax/policy.py`) — inaczej nie
  dałoby się policzyć trzech polityk równolegle z tych samych zapisów.
- **Brak pokrycia = wyjątek, nie ujemne `qty_remaining`.** Sprzedaż większa
  niż suma otwartych lotów podnosi `InsufficientLotsError` i nic w bazie
  się nie zmienia.
- **NBP niedostępne przy `add_lot` nie blokuje zapisu** — lot ląduje z
  `nbp_rate = NULL`/`cost_pln = NULL`, a `backfill_missing_rates` (job
  schedulera) domyka to później. Bez tego masowy import PDF-ów (krok 13)
  odpytywałby NBP synchronicznie dla każdego wiersza w trakcie requestu.
- **Porównania ilości z epsilonem** (`_EPS`), żeby resztki po podziale
  float (akcje ułamkowe) nie zostawiały „lotów-widm" z `qty_remaining`
  rzędu 1e-16.
"""
from __future__ import annotations

import sqlite3

from ..providers import fx_nbp

_EPS = 1e-9


class InsufficientLotsError(Exception):
    """Sprzedaż przekracza sumę `qty_remaining` dostępnych lotów."""


class CostBasisMissingError(Exception):
    """Lot lub sprzedaż nie ma jeszcze zamrożonego kursu NBP — uruchom
    `backfill_missing_rates` (lub poczekaj na job schedulera) zanim ten
    lot zostanie skonsumowany przez sprzedaż."""


def add_lot(conn: sqlite3.Connection, acquired_date: str, lot_type: str,
            quantity: float, price_eur: float, fee_eur: float = 0.0,
            source: str = "manual", natural_key: str | None = None,
            grant_id: int | None = None, notes: str | None = None) -> int:
    """Wstawia lot; idempotentne po `natural_key` (istniejący klucz zwraca
    id istniejącego lotu, nic nie wstawia ponownie — ten sam błąd zjadł
    depozyt w pv_roi 0.30.x, tu zapobiegamy mu od początku)."""
    if natural_key is not None:
        existing = conn.execute(
            "SELECT id FROM lots WHERE natural_key = ?", (natural_key,)).fetchone()
        if existing:
            return existing["id"]

    rate = fx_nbp.rate_for_event(conn, acquired_date)
    nbp_rate, nbp_rate_date = rate if rate else (None, None)
    cost_pln = (quantity * price_eur + fee_eur) * nbp_rate if nbp_rate is not None else None

    cur = conn.execute(
        "INSERT INTO lots (acquired_date, lot_type, quantity, price_eur, fee_eur, "
        "nbp_rate, nbp_rate_date, cost_pln, grant_id, source, natural_key, "
        "qty_remaining, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (acquired_date, lot_type, quantity, price_eur, fee_eur, nbp_rate,
         nbp_rate_date, cost_pln, grant_id, source, natural_key, quantity, notes))
    conn.commit()
    return cur.lastrowid


def backfill_missing_rates(conn: sqlite3.Connection) -> int:
    """Uzupełnia `nbp_rate`/`nbp_rate_date`/`cost_pln` tam, gdzie kurs jest
    jeszcze `NULL`. Zwraca liczbę uzupełnionych lotów. Nigdy nie dotyka
    lotu, który już ma zamrożony kurs (filtr `nbp_rate IS NULL` też w
    samym UPDATE, na wypadek równoległego zapisu)."""
    rows = conn.execute(
        "SELECT id, acquired_date, quantity, price_eur, fee_eur FROM lots "
        "WHERE nbp_rate IS NULL").fetchall()
    filled = 0
    for row in rows:
        rate = fx_nbp.rate_for_event(conn, row["acquired_date"])
        if rate is None:
            continue
        nbp_rate, nbp_rate_date = rate
        cost_pln = (row["quantity"] * row["price_eur"] + row["fee_eur"]) * nbp_rate
        conn.execute(
            "UPDATE lots SET nbp_rate = ?, nbp_rate_date = ?, cost_pln = ? "
            "WHERE id = ? AND nbp_rate IS NULL",
            (nbp_rate, nbp_rate_date, cost_pln, row["id"]))
        filled += 1
    conn.commit()
    return filled


def open_lots(conn: sqlite3.Connection, as_of: str | None = None) -> list[dict]:
    """Loty z `qty_remaining > 0` (epsilon), w kolejności FIFO
    (`acquired_date`, potem `id` jako tie-break — NIE kolejność wstawienia).

    `as_of`: gdy podane, wyklucza loty nabyte PO tej dacie (`acquired_date > as_of`).
    Krok 19: bez tego filtra sprzedaż mogła przypadkowo skonsumować lot nabyty
    tego samego dnia co sprzedaż lub później — znalezione na realnych danych
    (sprzedaż z 2025-10-27 zjadła część zakupu z tego samego dnia zamiast zgłosić
    brak pokrycia). Data nabycia == `as_of` jest dozwolona (dzień nabycia = dzień
    zbycia bywa prawnie dopuszczalny), tylko przyszłość względem `as_of` nie."""
    if as_of is None:
        rows = conn.execute(
            "SELECT * FROM lots WHERE qty_remaining > ? ORDER BY acquired_date ASC, id ASC",
            (_EPS,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM lots WHERE qty_remaining > ? AND acquired_date <= ? "
            "ORDER BY acquired_date ASC, id ASC",
            (_EPS, as_of)).fetchall()
    return [dict(r) for r in rows]


def lots_summary(conn: sqlite3.Connection) -> dict:
    """Sumy per `lot_type` do UI/sensorów. Koszt uznany WEDŁUG POLITYKI
    liczy `tax/policy.py` — ta funkcja daje tylko surowe ilości."""
    rows = conn.execute(
        "SELECT lot_type, COUNT(*) AS lot_count, SUM(quantity) AS qty_total, "
        "SUM(qty_remaining) AS qty_remaining FROM lots GROUP BY lot_type").fetchall()
    return {
        r["lot_type"]: {
            "lot_count": r["lot_count"],
            "qty_total": r["qty_total"] or 0.0,
            "qty_remaining": r["qty_remaining"] or 0.0,
        }
        for r in rows
    }


def record_sale(conn: sqlite3.Connection, sale_date: str, quantity: float,
                price_eur: float, fee_eur: float = 0.0,
                proceeds_eur: float | None = None) -> int:
    """Zapisuje sprzedaż z własnym zamrożonym kursem D-1 i konsumuje loty
    FIFO. Podnosi `InsufficientLotsError` (brak pokrycia, sprawdzone PRZED
    jakimkolwiek zapisem) lub `CostBasisMissingError` (lot/sprzedaż bez
    zamrożonego kursu NBP mimo backfillu) — w obu przypadkach baza
    zostaje nietknięta.

    `proceeds_eur`: opcjonalne REALNE wpływy brutto ze sprzedaży (np. "Sale Proceeds"
    z Withhold-to-Cover Typu B w wyciągu Computershare — patrz
    `importers/computershare_pdf.py::parse_withhold_to_cover`), gdy różnią się od
    `quantity * price_eur` (cena w wyciągu bywa zaokrąglona do 2 miejsc, co przy dużych
    ilościach akcji daje kilkuzłotowy błąd — znalezione na realnych danych, krok 19).
    Gdy podane, ZASTĘPUJE `quantity * price_eur` w liczeniu przychodu; `fee_eur` jest
    nadal odejmowane osobno (`proceeds_eur` to wartość BRUTTO, jak "Sale Proceeds", nie
    "Net proceeds" — nie przekazywać już-pomniejszonej o prowizję kwoty, bo `fee_eur`
    zostałoby odjęte podwójnie)."""
    backfill_missing_rates(conn)

    available = open_lots(conn, as_of=sale_date)
    total_available = sum(l["qty_remaining"] for l in available)
    if quantity - total_available > _EPS:
        raise InsufficientLotsError(
            f"Brak pokrycia: chcesz sprzedać {quantity}, dostępne {total_available}")

    rate = fx_nbp.rate_for_event(conn, sale_date)
    if rate is None:
        raise CostBasisMissingError(
            f"Brak kursu NBP dla dnia sprzedaży {sale_date} (spróbuj ponownie później)")
    nbp_rate, nbp_rate_date = rate
    gross_eur = proceeds_eur if proceeds_eur is not None else quantity * price_eur
    revenue_pln = (gross_eur - fee_eur) * nbp_rate
    # Alokacja per-lot (sale_allocations, widoczna w rozwiniętym śladzie FIFO na /sales)
    # liczy przychód jako take*effective_price_eur — gdy proceeds_eur nadpisuje przychód,
    # ten "efektywny" udział na akcję musi wynikać z gross_eur, nie z nominalnego
    # price_eur, inaczej SUM(sale_allocations.revenue_pln) rozjedzie się z revenue_pln
    # zapisanym w sales (znalezione na realnych danych, krok 19).
    effective_price_eur = gross_eur / quantity if quantity else price_eur

    try:
        cur = conn.execute(
            "INSERT INTO sales (sale_date, quantity, price_eur, fee_eur, nbp_rate, "
            "nbp_rate_date, revenue_pln) VALUES (?,?,?,?,?,?,?)",
            (sale_date, quantity, price_eur, fee_eur, nbp_rate, nbp_rate_date, revenue_pln))
        sale_id = cur.lastrowid
        _allocate_fifo(
            conn, sale_id, quantity, effective_price_eur, fee_eur, nbp_rate,
            as_of=sale_date)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return sale_id


def reverse_sale(conn: sqlite3.Connection, sale_id: int) -> bool:
    """Cofa sprzedaż (krok 16, docs/PLAN_KROK_16_transparentnosc.md): przywraca
    `qty_remaining` lotom skonsumowanym przez tę sprzedaż i usuwa `sales` (kaskada
    `ON DELETE CASCADE` sama sprząta `sale_allocations` — patrz db.py). Bez tego
    literówka w formularzu sprzedaży trwale konsumuje loty i jedynym ratunkiem
    byłaby ręczna edycja SQLite. Zwraca `False` (bez żadnego zapisu), gdy
    `sale_id` nie istnieje — idempotentne wywołanie z nieistniejącym id nie jest
    błędem, tylko no-opem."""
    allocations = conn.execute(
        "SELECT lot_id, quantity FROM sale_allocations WHERE sale_id = ?",
        (sale_id,)).fetchall()
    exists = conn.execute("SELECT 1 FROM sales WHERE id = ?", (sale_id,)).fetchone()
    if not exists:
        return False
    for alloc in allocations:
        conn.execute(
            "UPDATE lots SET qty_remaining = qty_remaining + ? WHERE id = ?",
            (alloc["quantity"], alloc["lot_id"]))
    conn.execute("DELETE FROM sales WHERE id = ?", (sale_id,))
    conn.commit()
    return True


def _plan_fifo(candidates: list[dict], sale_quantity: float, price_eur: float,
               fee_eur: float, nbp_rate: float) -> list[dict]:
    """Czysta funkcja: planuje alokację FIFO bez `conn`/`INSERT` — krok 15
    wydzielił ją z `_allocate_fifo`, żeby `tax/whatif.py::simulate_sale()`
    mogła użyć DOKŁADNIE tej samej kolejności i matematyki kosztu na akcję
    co realna zapisana sprzedaż, bez pisania do bazy. `candidates` musi być
    już posortowane FIFO (`acquired_date ASC, id ASC`) z `qty_remaining > 0`
    — dokładnie to, co zwraca `open_lots()`.

    Zwraca listę alokacji (`lot_id`, `lot_type`, `acquired_date`, `quantity`,
    `cost_pln`, `revenue_pln`). Podnosi te same wyjątki co dotychczasowy
    `_allocate_fifo`: `CostBasisMissingError` (lot bez zamrożonego kursu) i
    `InsufficientLotsError` (pokrycie zmieniło się w trakcie planowania)."""
    remaining_to_allocate = sale_quantity
    allocations: list[dict] = []

    for lot in candidates:
        if remaining_to_allocate <= _EPS:
            break
        take = min(lot["qty_remaining"], remaining_to_allocate)
        if lot["cost_pln"] is None:
            raise CostBasisMissingError(
                f"Lot {lot['id']} nie ma zamrożonego kursu NBP mimo backfillu")

        per_share_cost_pln = lot["cost_pln"] / lot["quantity"]
        alloc_cost_pln = per_share_cost_pln * take
        fee_share_eur = fee_eur * (take / sale_quantity) if sale_quantity else 0.0
        alloc_revenue_pln = (take * price_eur - fee_share_eur) * nbp_rate

        allocations.append({
            "lot_id": lot["id"],
            "lot_type": lot["lot_type"],
            "acquired_date": lot["acquired_date"],
            "quantity": take,
            "cost_pln": alloc_cost_pln,
            "revenue_pln": alloc_revenue_pln,
        })
        remaining_to_allocate -= take

    if remaining_to_allocate > _EPS:
        # Nie powinno się zdarzyć — sprawdzone w record_sale przed startem
        # transakcji — ale gdyby coś zmieniło loty między sprawdzeniem
        # a alokacją (np. przyszły import współbieżny), lepiej wywalić
        # wyjątek niż po cichu zaksięgować niepełną sprzedaż.
        raise InsufficientLotsError(
            "Pokrycie zmieniło się w trakcie alokacji — sprzedaż wycofana")

    return allocations


def _allocate_fifo(conn: sqlite3.Connection, sale_id: int, sale_quantity: float,
                    price_eur: float, fee_eur: float, nbp_rate: float,
                    as_of: str | None = None) -> None:
    candidates = open_lots(conn, as_of=as_of)
    plan = _plan_fifo(candidates, sale_quantity, price_eur, fee_eur, nbp_rate)

    lots_by_id = {lot["id"]: lot for lot in candidates}
    for alloc in plan:
        conn.execute(
            "INSERT INTO sale_allocations (sale_id, lot_id, quantity, cost_pln, revenue_pln) "
            "VALUES (?,?,?,?,?)",
            (sale_id, alloc["lot_id"], alloc["quantity"], alloc["cost_pln"],
             alloc["revenue_pln"]))
        new_remaining = lots_by_id[alloc["lot_id"]]["qty_remaining"] - alloc["quantity"]
        conn.execute(
            "UPDATE lots SET qty_remaining = ? WHERE id = ?",
            (new_remaining, alloc["lot_id"]))
