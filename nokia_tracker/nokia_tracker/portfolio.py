"""Prosty portfel 0.1.0: stan posiadania (ilość + średni koszt) z ustawień,
P&L w EUR i PLN. Loty/FIFO dochodzą w 0.2.0 — funkcja tu zostaje wąska
i czysta, żeby dało się ją łatwo zastąpić/rozszerzyć bez zmiany wywołujących
(BLUEPRINT §3).
"""
from __future__ import annotations

import sqlite3

from .tax import lots as taxlots
from .tax import policy as taxpolicy


def lots_based_position_values(conn: sqlite3.Connection, cfg: dict, price_eur: float | None,
                               eurpln_rate: float | None,
                               dividends_net_total_eur: float = 0.0) -> dict:
    """Portfel liczony z realnych lotów (krok 12/13), zamiast ręcznie wpisywanej
    `position_qty`/`avg_cost_eur` z ustawień (krok 9) — domyka lukę znalezioną po pierwszym
    realnym imporcie PDF (docs/PLAN_KROK_13_5_gaps.md).

    `position_qty` = suma `qty_remaining` PO WSZYSTKICH typach lotu (`own`/`matched`/`lti`/
    `dividend_drip`) — to fizyczna liczba posiadanych akcji, niezależna od polityki
    podatkowej. Koszt bazowy liczy się WYŁĄCZNIE z lotów należących do aktywnej polityki
    (`cfg['cost_basis_policy']`, ten sam mechanizm co `sensors.py::lots_values()`) — przy
    domyślnym `own_only` loty `matched`/`lti`/`dividend_drip` wnoszą pełną wartość rynkową,
    ale zero kosztu. To ŚWIADOMY efekt (dostałeś je za darmo lub prawie za darmo, opodatkowanie
    odroczone do zbycia), nie błąd — niezrealizowany zysk wygląda wyżej niż z samych zakupów,
    dokładnie zgodnie z tabelą „Trzy polityki kosztu" na `/lots`.

    Reużywa `position_values()` bez zmian: `avg_cost_eur` przekazane tu to
    `koszt_uznany / position_qty` (nie `/ policy_qty`) — dzielenie przez CAŁKOWITĄ ilość, żeby
    `position_qty * avg_cost_eur` w `position_values()` dało dokładnie `koszt_uznany`, a nie go
    zawyżyło o darmowe akcje."""
    open_rows = taxlots.open_lots(conn)
    position_qty = sum(r["qty_remaining"] for r in open_rows)

    active_policy = cfg.get("cost_basis_policy", "own_only")
    allowed = taxpolicy.POLICIES.get(active_policy, {"own"})
    recognized_cost_eur = sum(
        r["price_eur"] * r["qty_remaining"] for r in open_rows if r["lot_type"] in allowed)

    avg_cost_eur = recognized_cost_eur / position_qty if position_qty else 0.0
    return position_values(position_qty, avg_cost_eur, price_eur, eurpln_rate,
                           dividends_net_total_eur)


def position_values_auto(conn: sqlite3.Connection, cfg: dict, price_eur: float | None,
                         eurpln_rate: float | None,
                         dividends_net_total_eur: float = 0.0) -> dict:
    """Dispatcher: loty istnieją -> `lots_based_position_values()`; brak lotów (użytkownik,
    który jeszcze nic nie zaimportował/nie dodał ręcznie) -> stary, ręczny
    `position_values(cfg['position_qty'], cfg['avg_cost_eur'], ...)` z kroku 9, żeby nie
    stracić możliwości orientacyjnego wpisania stanu posiadania przed pierwszym importem."""
    if taxlots.open_lots(conn):
        return lots_based_position_values(conn, cfg, price_eur, eurpln_rate,
                                          dividends_net_total_eur)
    return position_values(cfg["position_qty"], cfg["avg_cost_eur"], price_eur, eurpln_rate,
                           dividends_net_total_eur)


def position_values(position_qty: float, avg_cost_eur: float, price_eur: float | None,
                    eurpln_rate: float | None, dividends_net_total_eur: float = 0.0) -> dict:
    """total_return_pct = (niezrealizowany P&L + suma netto dywidend) / koszt
    bazowy — świadome uproszczenie 0.1.0, bez efektu walutowego (konto w
    EUR, PLN wyłącznie prezentacyjnie po kursie bieżącym, patrz BLUEPRINT §2)."""
    cost_basis_eur = position_qty * avg_cost_eur
    market_value_eur = position_qty * price_eur if price_eur is not None else None
    unrealized_pnl_eur = (
        market_value_eur - cost_basis_eur if market_value_eur is not None else None)
    unrealized_pnl_pct = (
        unrealized_pnl_eur / cost_basis_eur * 100
        if unrealized_pnl_eur is not None and cost_basis_eur else None)

    total_return_pct = None
    if cost_basis_eur and unrealized_pnl_eur is not None:
        total_return_pct = (unrealized_pnl_eur + dividends_net_total_eur) / cost_basis_eur * 100

    market_value_pln = (
        market_value_eur * eurpln_rate
        if market_value_eur is not None and eurpln_rate else None)
    cost_basis_pln = cost_basis_eur * eurpln_rate if eurpln_rate else None
    unrealized_pnl_pln = (
        unrealized_pnl_eur * eurpln_rate
        if unrealized_pnl_eur is not None and eurpln_rate else None)

    return {
        "position_qty": position_qty,
        "avg_cost_eur": avg_cost_eur,
        "cost_basis_eur": cost_basis_eur,
        "market_value_eur": market_value_eur,
        "unrealized_pnl_eur": unrealized_pnl_eur,
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "total_return_pct": total_return_pct,
        "market_value_pln": market_value_pln,
        "cost_basis_pln": cost_basis_pln,
        "unrealized_pnl_pln": unrealized_pnl_pln,
    }


def _sub(a: float | None, b: float | None) -> float | None:
    return a - b if a is not None and b is not None else None


def _add(a: float | None, b: float | None) -> float | None:
    return a + b if a is not None and b is not None else None


def dashboard_buckets(position: dict, restricted: dict, unvested: dict) -> dict:
    """Krok 23 (docs/PLAN_KROK_23_portfel_kafelki.md): składa `position_values()` (lub
    `lots_based_position_values()`), `tax/grants.py::restricted_own_summary()` i
    `unvested_summary()` — WSZYSTKIE trzy już liczone w `web.py::dashboard` — w trzy
    kubełki karty „Portfel" (wolne / z ograniczeniem / zablokowane) + sumę. Czysta
    funkcja na już policzonych słownikach, zero zapytań do DB, zero nowej logiki
    podatkowej/rynkowej.

    `free` = `position` minus `restricted` (ta sama ilość akcji `own`, ten sam kurs —
    odejmowanie jest spójne, patrz `restricted_own_summary()`). `locked` = wyłącznie
    `unvested.upcoming_*` — `overdue` świadomie zostaje poza kubełkami (jak w kroku 21,
    pokazywane osobno jako ostrzeżenie, bo status jest niepewny). `total` zastępuje
    ręczne składanie, które wcześniej siedziało w `web.py::dashboard`."""
    free = {
        "qty": position["position_qty"] - restricted["restricted_qty"],
        "value_eur": _sub(position["market_value_eur"], restricted["restricted_value_eur"]),
        "value_pln": _sub(position["market_value_pln"], restricted["restricted_value_pln"]),
    }
    restricted_bucket = {
        "qty": restricted["restricted_qty"],
        "value_eur": restricted["restricted_value_eur"],
        "value_pln": restricted["restricted_value_pln"],
        "free_until": restricted["free_until"],
    }
    locked = {
        "qty": unvested["upcoming_qty"],
        "value_eur": unvested["upcoming_value_eur"],
        "value_pln": unvested["upcoming_value_pln"],
        "next_date": unvested["next_vest_date"],
        "next_qty": unvested["next_vest_qty"],
    }
    total = {
        "qty": position["position_qty"] + unvested["upcoming_qty"],
        "value_eur": _add(position["market_value_eur"], unvested["upcoming_value_eur"]),
        "value_pln": _add(position["market_value_pln"], unvested["upcoming_value_pln"]),
    }
    return {"free": free, "restricted": restricted_bucket, "locked": locked, "total": total}
