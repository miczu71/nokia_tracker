"""Doradca planu pracowniczego (krok 26, docs/PLAN_KROK_26_doradca.md) — jedyna część
roadmapy, której nie da się kupić w narzędziu premium: dodatek zna już wszystkie fakty
(loty, granty, transze, polityki kosztu, kurs), tylko nie odpowiada wprost na cztery
pytania, które użytkownik realnie sobie zadaje.

Podział odpowiedzialności: `tax/grants.py` trzyma REGUŁĘ (który lot `own` jest
ograniczony i jaka transza dopasowania na nim wisi — skonsolidowana w kroku 21, nie
duplikowana tutaj), ten moduł liczy PIENIĄDZE (przepadek w EUR/PLN, planer ESPP, ryzyko
koncentracji) — nie jest to `tax/`, bo składa podatki + portfel + ustawienia razem,
a nie prowadzi księgi podatkowej."""
from __future__ import annotations

import sqlite3
from datetime import datetime

from . import portfolio as portfoliom
from .tax import grants as grantsm
from .tax import lots as taxlots
from .tax import whatif as taxwhatif


def forfeit_for_allocations(allocations: list[dict], rates_by_lot_id: dict[int, float]) -> dict:
    """CZYSTA: karmiona listą alokacji w kształcie `tax/lots.py::_plan_fifo` (klucze
    `lot_id`/`quantity`) — czyli DOSŁOWNIE `simulate_sale(...)["lots_consumed"]` — i mapą
    `{lot_id: match_rate}` z `grants.restricted_own_lots()`. Zero `conn`, zero nowej
    matematyki: to jest hak, na którym przyszły what-if na `/pit38` dostanie wiersz
    „utracone dopasowanie" bez pisania nowego silnika."""
    forfeit_qty = 0.0
    lots_touched: list[dict] = []
    for alloc in allocations:
        rate = rates_by_lot_id.get(alloc["lot_id"], 0.0)
        if not rate:
            continue
        forfeit = alloc["quantity"] * rate
        forfeit_qty += forfeit
        lots_touched.append({
            "lot_id": alloc["lot_id"],
            "taken_qty": alloc["quantity"],
            "match_rate": rate,
            "forfeit_qty": forfeit,
        })
    return {"forfeit_qty": forfeit_qty, "lots_touched": lots_touched}


def forfeit_for_quantity(conn: sqlite3.Connection, quantity: float,
                         price_eur: float | None = None, eurpln_rate: float | None = None,
                         today: str | None = None) -> dict:
    """„Co stracę, sprzedając dokładnie `quantity` akcji dziś" — iteruje po WSZYSTKICH
    otwartych lotach w kolejności FIFO (nie tylko ograniczonych): sprzedaż 15 akcji, gdy
    pierwsze 10 w FIFO to wolny lot z 2020, przepala tylko 5 z lotu ograniczonego, nie 15.
    Podnosi `InsufficientLotsError` tak samo jak `record_sale`/`simulate_sale` — ta sama
    zasada „nie zgaduj, gdy pokrycia brakuje"."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    candidates = taxlots.open_lots(conn, as_of=today)
    total_available = sum(c["qty_remaining"] for c in candidates)
    if quantity - total_available > taxlots._EPS:
        raise taxlots.InsufficientLotsError(
            f"Brak pokrycia: chcesz sprzedać {quantity}, dostępne {total_available}")

    remaining = quantity
    allocations: list[dict] = []
    acquired_by_lot: dict[int, str] = {}
    for lot in candidates:
        if remaining <= taxlots._EPS:
            break
        take = min(lot["qty_remaining"], remaining)
        allocations.append({"lot_id": lot["id"], "quantity": take})
        acquired_by_lot[lot["id"]] = lot["acquired_date"]
        remaining -= take

    rates = {item["lot_id"]: item["match_rate"]
            for item in grantsm.restricted_own_lots(conn, today=today)}
    result = forfeit_for_allocations(allocations, rates)
    for touched in result["lots_touched"]:
        touched["acquired_date"] = acquired_by_lot.get(touched["lot_id"])

    forfeit_value_eur, forfeit_value_pln = grantsm._value(
        result["forfeit_qty"], price_eur, eurpln_rate)

    return {
        "sell_qty": quantity,
        "forfeit_qty": result["forfeit_qty"],
        "forfeit_value_eur": forfeit_value_eur,
        "forfeit_value_pln": forfeit_value_pln,
        "lots_touched": result["lots_touched"],
    }


def _days_until(date_str: str | None, today: str) -> int | None:
    if date_str is None:
        return None
    delta = (datetime.strptime(date_str, "%Y-%m-%d")
            - datetime.strptime(today, "%Y-%m-%d")).days
    return max(delta, 0)


def forfeit_summary(conn: sqlite3.Connection, price_eur: float | None = None,
                    eurpln_rate: float | None = None, today: str | None = None) -> dict:
    """„Ile tracę, sprzedając DZIŚ wszystko, co jest ograniczone" — kubełkowa wersja
    `forfeit_for_quantity`. Przepadek per lot = `match_rate * qty_remaining` (reguła
    proporcjonalna do sprzedanych sztuk, mianownik = oryginalna `lots.quantity`, decyzja
    użytkownika z planu tego kroku) — `qty_remaining` spada tylko wtedy, gdy FIFO faktycznie
    dotarło do tego lotu, więc to jest prawda księgi, nie przybliżenie: wcześniejsza
    sprzedaż mogła w całości zjeść starszy, NIEOGRANICZONY lot, a wtedy ten lot wciąż ma
    pełne dopasowanie do stracenia."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    restricted = grantsm.restricted_own_lots(conn, today=today)
    restricted_qty = sum(item["qty_remaining"] for item in restricted)
    free_dates = [item["free_until"] for item in restricted]
    free_until = max(free_dates) if free_dates else None

    items: list[dict] = []
    forfeit_qty = 0.0
    for item in restricted:
        item_forfeit_qty = item["match_rate"] * item["qty_remaining"]
        forfeit_qty += item_forfeit_qty
        item_forfeit_eur, item_forfeit_pln = grantsm._value(
            item_forfeit_qty, price_eur, eurpln_rate)
        items.append({
            **item,
            "forfeit_qty": item_forfeit_qty,
            "forfeit_value_eur": item_forfeit_eur,
            "forfeit_value_pln": item_forfeit_pln,
            "days_until_free": _days_until(item["free_until"], today),
        })

    forfeit_value_eur, forfeit_value_pln = grantsm._value(forfeit_qty, price_eur, eurpln_rate)
    _, restricted_value_pln = grantsm._value(restricted_qty, price_eur, eurpln_rate)

    return {
        "forfeit_qty": forfeit_qty,
        "forfeit_value_eur": forfeit_value_eur,
        "forfeit_value_pln": forfeit_value_pln,
        "restricted_qty": restricted_qty,
        "restricted_value_pln": restricted_value_pln,
        "free_until": free_until,
        "days_until_free": _days_until(free_until, today),
        "items": items,
    }


def espp_plan(monthly_eur: float, months: int, price_eur: float,
             eurpln_rate: float | None = None, match_pct: float = 50.0,
             cost_basis_policy: str = "own_only", tax_pct: float = 19.0,
             horizon_date: str | None = None) -> dict:
    """„Ile mi da wpłacanie X EUR/mc przez N miesięcy" — CZYSTA (bez `conn`).

    `simulate_sale()` nie może tu posłużyć wprost: czyta `open_lots(conn)`, a
    hipotetyczne akcje z przyszłości nie istnieją w bazie i nie mają prawa tam trafić
    (żadnych atrap w `lots`, żeby nie zatruć FIFO/`WRITE_LOCK`). Ale silnikiem pod
    `simulate_sale` jest czysta `tax/lots.py::_plan_fifo` — karmimy ją DOKŁADNIE tym
    samym kształtem kandydatów, tylko syntetycznym (dwa loty: `own` i `matched`, oba
    datowane na `horizon_date`), więc to nie jest podrabianie danych, to dokładnie ten
    scenariusz, dla którego krok 15 wydzielił `_plan_fifo` z `_allocate_fifo`.

    Cztery uproszczenia, którymi żyje ten scenariusz (patrz też akapit `.muted` na
    `/plan`): (1) cena płaska — ta sama przy „zakupie" i „sprzedaży", stąd przy polityce
    `all_at_acquisition` podatek wychodzi dokładnie 0; (2) podatek liczony na SAMYCH
    nowych akcjach, w izolacji od realnego stosu FIFO; (3) jeden dzisiejszy kurs EUR/PLN
    dla wszystkich wpłat, nie kurs NBP D-1 zamrożony per zdarzenie; (4) dopasowanie
    zakłada dotrwanie do vestingu — sprzedaż wcześniej je kasuje (patrz `forfeit_summary`)."""
    if monthly_eur <= 0:
        raise ValueError("Wpłata miesięczna musi być dodatnia")
    if months <= 0:
        raise ValueError("Liczba miesięcy musi być dodatnia")
    if price_eur <= 0:
        raise ValueError("Cena musi być dodatnia")
    if horizon_date is None:
        horizon_date = datetime.now().strftime("%Y-%m-%d")

    contributed_eur = monthly_eur * months
    own_shares = contributed_eur / price_eur
    matched_shares = own_shares * match_pct / 100
    total_shares = own_shares + matched_shares
    end_value_eur = total_shares * price_eur

    result: dict = {
        "contributed_eur": round(contributed_eur, 2),
        "own_shares": round(own_shares, 4),
        "matched_shares": round(matched_shares, 4),
        "total_shares": round(total_shares, 4),
        "end_value_eur": round(end_value_eur, 2),
        "end_value_pln": None,
        "revenue_pln": None,
        "policies": None,
        "active_policy": cost_basis_policy,
        "tax_pln": None,
        "net_proceeds_pln": None,
    }
    if eurpln_rate is None:
        return result

    result["end_value_pln"] = round(end_value_eur * eurpln_rate, 2)

    cfg = {"cost_basis_policy": cost_basis_policy, "pl_capital_gains_tax_pct": tax_pct}
    candidates = [
        {"id": None, "lot_type": "own", "acquired_date": horizon_date,
         "quantity": own_shares, "qty_remaining": own_shares,
         "cost_pln": contributed_eur * eurpln_rate},
        {"id": None, "lot_type": "matched", "acquired_date": horizon_date,
         "quantity": matched_shares, "qty_remaining": matched_shares,
         "cost_pln": matched_shares * price_eur * eurpln_rate},
    ]
    plan = taxlots._plan_fifo(candidates, total_shares, price_eur, 0.0, eurpln_rate)
    revenue_pln = sum(a["revenue_pln"] for a in plan)
    policies, active_policy = taxwhatif._apply_policies(plan, revenue_pln, cfg)

    result["revenue_pln"] = round(revenue_pln, 2)
    result["policies"] = policies
    result["active_policy"] = active_policy
    result["tax_pln"] = policies[active_policy]["tax_pln"]
    result["net_proceeds_pln"] = round(revenue_pln - policies[active_policy]["tax_pln"], 2)
    return result


def concentration(employer_value_pln: float, other_net_worth_pln: float,
                  threshold_pct: float = 25.0) -> dict:
    """„Czy nie mam za dużo w jednym koszyku, który jest jednocześnie moim pracodawcą."
    `other_net_worth_pln == 0` -> `configured=False`, `pct=None` — inaczej wyszłoby
    matematycznie 100% i ostrzeżenie wrzeszczałoby u każdego, kto po prostu nic jeszcze
    nie wpisał w ustawieniach (decyzja użytkownika z planu tego kroku: pole liczbowe w
    ustawieniach, nie encja HA — w tym HA nie ma dziś żadnej encji z majątkiem netto)."""
    total = employer_value_pln + other_net_worth_pln
    configured = other_net_worth_pln > 0
    pct = (employer_value_pln / total * 100) if configured and total else None
    over_threshold = bool(configured and pct is not None and pct > threshold_pct)
    return {
        "employer_value_pln": employer_value_pln,
        "other_net_worth_pln": other_net_worth_pln,
        "total_net_worth_pln": total,
        "pct": round(pct, 2) if pct is not None else None,
        "threshold_pct": threshold_pct,
        "over_threshold": over_threshold,
        "configured": configured,
    }


def overview(conn: sqlite3.Connection, cfg: dict, price_eur: float | None = None,
            eurpln_rate: float | None = None, today: str | None = None) -> dict:
    """Kompozytor dla `/plan` I `sensors.advisor_values` — obie strony liczą przez TĘ
    SAMĄ funkcję, żeby strona i sensor MQTT nigdy nie pokazały dwóch różnych liczb dla
    tego samego faktu."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    forfeit = forfeit_summary(conn, price_eur, eurpln_rate, today=today)
    timeline = grantsm.vesting_timeline(conn, price_eur, eurpln_rate, today=today)

    position = portfoliom.position_values_auto(conn, cfg, price_eur, eurpln_rate)
    unvested = grantsm.unvested_summary(conn, price_eur, eurpln_rate, today=today)
    restricted = grantsm.restricted_own_summary(conn, price_eur, eurpln_rate, today=today)
    buckets = portfoliom.dashboard_buckets(position, restricted, unvested)

    employer_value_pln = buckets["total"]["value_pln"]
    if employer_value_pln is None:
        conc = {
            "employer_value_pln": None,
            "other_net_worth_pln": cfg.get("other_net_worth_pln", 0.0),
            "total_net_worth_pln": None, "pct": None,
            "threshold_pct": cfg.get("concentration_alert_pct", 25.0),
            "over_threshold": False, "configured": False,
        }
    else:
        conc = concentration(
            employer_value_pln, cfg.get("other_net_worth_pln", 0.0),
            cfg.get("concentration_alert_pct", 25.0))

    sale_today = None
    if forfeit["restricted_qty"] > 0 and price_eur is not None:
        try:
            sale_today = taxwhatif.simulate_sale(
                conn, cfg, forfeit["restricted_qty"], price_eur, sale_date=today)
        except (taxlots.InsufficientLotsError, taxlots.CostBasisMissingError):
            sale_today = None

    return {
        "forfeit": forfeit,
        "timeline": timeline,
        "concentration": conc,
        "sale_today": sale_today,
    }
