"""Księga gotówki (krok E4, docs/ROADMAP_V3.md) — model odczytu nad
istniejącymi tabelami (`sales`, `dividends`, PIT-38) plus dwa nowe źródła
wpisywane ręcznie (`tax_payments`, `broker_cash`).

**Twarda zasada modułu: gotówka != przychód podatkowy.** Trzy konkretne
rozjazdy, każdy z osobnym testem w `tests/test_cash.py`:

1. **Opłaty i kurs** — `sale_proceeds()` czyta `sales.revenue_pln` (już
   pomniejszone o `fee_eur`, po kursie NBP D-1 zamrożonym w `record_sale`),
   NIE przelicza od nowa `quantity * price_eur`. Ten wzór gubi
   `proceeds_eur` — realny override „Sale Proceeds" z Withhold-to-Cover
   Typ B (patrz `tax/lots.py::record_sale` docstring), gdzie cena w PDF
   bywa zaokrąglona do 2 miejsc i przy dużych ilościach akcji daje
   kilkuzłotowy błąd (znalezione na realnych danych, krok 19).
2. **Deklaracja vs wpływ** — `sales.reported_revenue_pln` (v4) nadpisuje
   PIT-38, gdy deklaracja już złożona z inną liczbą niż silnik FIFO
   wylicza. To poprawka DEKLARACJI, nie tego, co faktycznie wpłynęło na
   konto — `sale_proceeds()` go celowo ignoruje.
3. **Moment (DRIP)** — dywidenda reinwestowana (`reinvested_lot_id` nie
   NULL) jest przychodem podatkowym (trafia do PIT-38 sekcja G), ale
   ZEROWYM przepływem gotówki — reinwestuje się natychmiast w nowy lot,
   nigdy nie ląduje na koncie jako gotówka. Sprawdzone na produkcji
   2026-08-22: wszystkie 20 dywidend w bazie mają `reinvested_lot_id`
   wypełniony. `dividend_flow()` liczy takie wiersze do
   `cash_contribution_eur == 0.0`, ale nadal raportuje `net_received_eur`
   (przychód podatkowy) osobno — nic nie znika, tylko nie wchodzi do salda.

**Saldo u brokera jest wyłącznie ręczne.** Sprawdzone na realnym wyciągu
(`/config/nokia_import/Plan holdings statement 13219230 2026-08-19 14_00_16.pdf`):
sekcja „Assets by type" ma tylko `Shares`/`Restricted stock units`, zero
wystąpień `Cash`/`Balance`/`Currency` w całym dokumencie. `broker_cash` jest
więc szeregiem ODCZYTÓW (append-only, `UNIQUE(as_of_date, currency)` jako
UPSERT), nie jedną nadpisywaną wartością — inaczej nie dałoby się pokazać,
kiedy saldo przestało być aktualne. `broker_balance()` zwraca `None`, gdy
brak jakiegokolwiek odczytu — to „brak danych", NIE zero (E7 rozróżni to od
realnej niezgodności z wyciągiem).
"""
from __future__ import annotations

import sqlite3
from datetime import date

from .tax import dividends as taxdiv
from .tax import pit38 as taxpit38

_BROKER_BALANCE_STALE_DAYS = 30


def sale_proceeds(conn: sqlite3.Connection, year: int | None = None) -> dict:
    """Wpływy ze sprzedaży, per rok i narastająco. EUR odtworzone z
    `revenue_pln / nbp_rate` — patrz zasada 1 w docstringu modułu."""
    query = "SELECT sale_date, revenue_pln, nbp_rate FROM sales"
    params: tuple = ()
    if year is not None:
        query += " WHERE strftime('%Y', sale_date) = ?"
        params = (str(year),)
    rows = conn.execute(query, params).fetchall()

    by_year: dict[str, dict] = {}
    total_pln = 0.0
    total_eur = 0.0
    for r in rows:
        y = r["sale_date"][:4]
        pln = r["revenue_pln"] or 0.0
        eur = (pln / r["nbp_rate"]) if r["nbp_rate"] else 0.0
        bucket = by_year.setdefault(y, {"pln": 0.0, "eur": 0.0})
        bucket["pln"] += pln
        bucket["eur"] += eur
        total_pln += pln
        total_eur += eur

    return {
        "total_pln": round(total_pln, 2),
        "total_eur": round(total_eur, 2),
        "by_year": {y: {"pln": round(v["pln"], 2), "eur": round(v["eur"], 2)}
                    for y, v in sorted(by_year.items())},
    }


def dividend_flow(conn: sqlite3.Connection, cfg: dict, year: int | None = None) -> dict:
    """Dywidendy przez `tax/dividends.py::payouts()` (grupowane po WYPŁACIE,
    nie po wierszu — lekcja 0.17.2/0.17.3, patrz `payouts()` docstring).
    Bezgotówkowe (DRIP) rozróżnione od realnie odebranej gotówki per wypłata:
    grupa jest DRIP-owa (`cash_contribution_eur += 0`), gdy KAŻDY realny
    wiersz w grupie ma `reinvested_lot_id`; w przeciwnym razie liczy się jako
    gotówka (ostrożniejsze założenie — nie ukrywa wpływu, którego pochodzenie
    jest niejasne)."""
    payout_rows = taxdiv.payouts(conn, year=year)

    net_received_eur = 0.0
    cash_contribution_eur = 0.0
    reinvested_shares = 0.0
    drip_payout_count = 0

    raw_query = "SELECT * FROM dividends"
    raw_params: tuple = ()
    if year is not None:
        raw_query += " WHERE strftime('%Y', pay_date) = ?"
        raw_params = (str(year),)
    raw_rows = {r["id"]: r for r in conn.execute(raw_query, raw_params).fetchall()}

    # taxdiv.payouts() nie zwraca net_received_eur bezpośrednio (tylko
    # gross_eur/withholding_pct agregowane) — liczymy netto z surowych
    # wierszy `dividends`, ale grupujemy "czy to DRIP" po tych samych `ids`
    # co payouts(), żeby definicja wypłaty zostawała jedna (payouts()).
    for payout in payout_rows:
        if not payout["is_real"]:
            continue
        group_rows = [raw_rows[i] for i in payout["ids"] if i in raw_rows]
        # "realny" = dokładnie kryterium payouts() (real_row_count): notes
        # puste I quantity > 0 — nie tylko is_estimated(), inaczej grupa
        # mogłaby tu wyjść "realna" mimo że payouts() już ją odrzuciło.
        real_rows = [
            r for r in group_rows
            if not taxdiv.is_estimated(r) and (r["quantity"] or 0) > 0]
        if not real_rows:
            continue
        group_net = sum(r["net_received_eur"] or 0.0 for r in real_rows)
        net_received_eur += group_net
        is_drip = all(r["reinvested_lot_id"] is not None for r in real_rows)
        if is_drip:
            drip_payout_count += 1
            for r in real_rows:
                if r["reinvested_lot_id"]:
                    lot = conn.execute(
                        "SELECT quantity FROM lots WHERE id = ?",
                        (r["reinvested_lot_id"],)).fetchone()
                    if lot:
                        reinvested_shares += lot["quantity"]
        else:
            cash_contribution_eur += group_net

    return {
        "payout_count": len(payout_rows),
        "drip_payout_count": drip_payout_count,
        "net_received_eur": round(net_received_eur, 4),
        "cash_contribution_eur": round(cash_contribution_eur, 4),
        "reinvested_shares": round(reinvested_shares, 5),
    }


def tax_liability(conn: sqlite3.Connection, cfg: dict, year: int) -> dict:
    """Saldo zobowiązania PIT-38: `total_due_pln` z
    `tax/pit38.py::annual_report` (zero nowej matematyki) minus suma
    `tax_payments` faktycznie zapłaconych za `year`. Termin 30.04 roku
    następnego (ustawowy termin złożenia PIT-38 i zapłaty)."""
    report = taxpit38.annual_report(conn, cfg, year)
    due_pln = report["total_due_pln"]

    paid_pln = conn.execute(
        "SELECT COALESCE(SUM(amount_pln), 0) FROM tax_payments WHERE tax_year = ?",
        (year,)).fetchone()[0]

    deadline = f"{year + 1}-04-30"
    days_to_deadline = (date.fromisoformat(deadline) - date.today()).days

    return {
        "year": year,
        "due_pln": due_pln,
        "paid_pln": round(paid_pln, 2),
        "outstanding_pln": round(due_pln - paid_pln, 2),
        "deadline": deadline,
        "days_to_deadline": days_to_deadline,
    }


def add_tax_payment(conn: sqlite3.Connection, tax_year: int, paid_date: str,
                     amount_pln: float, notes: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO tax_payments (tax_year, paid_date, amount_pln, notes) "
        "VALUES (?,?,?,?)",
        (tax_year, paid_date, amount_pln, notes))
    conn.commit()
    return cur.lastrowid


def delete_tax_payment(conn: sqlite3.Connection, payment_id: int) -> bool:
    exists = conn.execute(
        "SELECT 1 FROM tax_payments WHERE id = ?", (payment_id,)).fetchone()
    if not exists:
        return False
    conn.execute("DELETE FROM tax_payments WHERE id = ?", (payment_id,))
    conn.commit()
    return True


def tax_payments_for_year(conn: sqlite3.Connection, year: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM tax_payments WHERE tax_year = ? ORDER BY paid_date DESC",
        (year,)).fetchall()
    return [dict(r) for r in rows]


def broker_balance(conn: sqlite3.Connection, today: str | None = None) -> dict | None:
    """Ostatni odczyt salda u brokera (po `as_of_date`), z wiekiem w dniach.
    `None` gdy nigdy nic nie wpisano — „brak danych", nie zero (E7 rozróżni
    to od realnej niezgodności z wyciągiem)."""
    row = conn.execute(
        "SELECT * FROM broker_cash ORDER BY as_of_date DESC, id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    today = today or date.today().isoformat()
    age_days = (date.fromisoformat(today) - date.fromisoformat(row["as_of_date"])).days
    return {
        "as_of_date": row["as_of_date"],
        "amount": row["amount"],
        "currency": row["currency"],
        "source": row["source"],
        "notes": row["notes"],
        "age_days": age_days,
        "is_stale": age_days > _BROKER_BALANCE_STALE_DAYS,
    }


def broker_history(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM broker_cash ORDER BY as_of_date DESC, id DESC LIMIT ?",
        (limit,)).fetchall()
    return [dict(r) for r in rows]


def record_broker_balance(conn: sqlite3.Connection, as_of_date: str, amount: float,
                           currency: str = "EUR", notes: str | None = None) -> int:
    """UPSERT po `(as_of_date, currency)` — poprawka odczytu z tego samego
    dnia nadpisuje, nie duplikuje (wzorzec z `dividend_schedule`, v10)."""
    cur = conn.execute(
        "INSERT INTO broker_cash (as_of_date, amount, currency, source, notes) "
        "VALUES (?,?,?,'manual',?) "
        "ON CONFLICT(as_of_date, currency) DO UPDATE SET "
        "amount = excluded.amount, notes = excluded.notes",
        (as_of_date, amount, currency, notes))
    conn.commit()
    return cur.lastrowid


def ledger(conn: sqlite3.Connection, cfg: dict, year: int) -> dict:
    """Spina wszystko powyższe w jeden słownik — konsument: `views/cash.py`
    (E4) i `views/account.py` — „Stan konta" (E5)."""
    return {
        "sale_proceeds": sale_proceeds(conn, year=year),
        "dividend_flow": dividend_flow(conn, cfg, year=year),
        "tax_liability": tax_liability(conn, cfg, year),
        "broker_balance": broker_balance(conn),
    }
