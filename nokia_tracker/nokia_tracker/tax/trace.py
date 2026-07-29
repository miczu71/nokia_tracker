"""Pełne rozbicie alokacji FIFO — jedno źródło formatowania dla symulacji
(„co jeśli sprzedam teraz"), zrealizowanych sprzedaży (`/sales`) i eksportów
(BLUEPRINT §3a, krok 16, docs/PLAN_KROK_16_transparentnosc.md).

Cel: każda kwota PLN w PIT-38 ma dać się rozłożyć aż do numeru tabeli NBP.
Ten moduł NIGDY nie odpytuje NBP przez sieć — czyta wyłącznie już zamrożone
kolumny (`nbp_rate`/`nbp_rate_date` na `lots`/`sales`) i lokalną tabelę
`nbp_rates` (dla numeru tabeli, `fx_nbp.table_no_for_effective_date`).
Renderowanie strony ma być tanie i deterministyczne — jeśli kursu brakuje
(NBP było niedostępne przy zapisie), pokazujemy to wprost, nie dociągamy go
w locie.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from ..providers import fx_nbp
from . import policy as taxpolicy

_WEEKDAY_PL = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]


def _weekday_pl(d: date) -> str:
    return _WEEKDAY_PL[d.weekday()]


def fx_derivation(conn: sqlite3.Connection, event_date: str, nbp_rate: float | None,
                  effective_date: str | None, kind: str) -> dict:
    """Wyprowadzenie kursu NBP do wyświetlenia: `event_date` (zdarzenie) → D-1
    (dzień roboczy poprzedzający, art. 11a) → `effective_date` (dzień
    FAKTYCZNEJ publikacji — może być wcześniejszy niż D-1, gdy D-1 wypadł w
    weekend/święto) → numer tabeli → linki → gotowe zdanie po polsku.

    `nbp_rate`/`effective_date` przyjmowane jako argumenty (już zamrożone w
    `lots`/`sales`/`dividends`), NIE przeliczane na nowo przez
    `fx_nbp.rate_for_event` — to gwarantuje, że wyświetlone wyprowadzenie
    opisuje DOKŁADNIE ten kurs, który został użyty do policzenia kwoty PLN,
    nawet gdyby aktualny stan `nbp_rates` się zmienił.

    `kind`: opis zdarzenia po polsku do zdania, np. 'nabycie', 'sprzedaż',
    'dywidenda'."""
    try:
        event = date.fromisoformat(event_date)
    except (TypeError, ValueError):
        # Zdarzenie z niepoprawną/zaślepioną datą (np. testy mockujące
        # fx_nbp na sztywno) — nie wywalamy się, po prostu nie da się
        # wyliczyć D-1/dni tygodnia.
        return {
            "event_date": event_date, "event_weekday": None,
            "d_minus_1": None, "d_minus_1_weekday": None,
            "effective_date": effective_date, "effective_weekday": None,
            "rate": nbp_rate, "table_no": None, "urls": None,
            "explanation_pl": f"{kind}: kurs {nbp_rate} ({effective_date})"
                              if nbp_rate is not None else None,
        }
    d_minus_1 = event - timedelta(days=1)
    result: dict = {
        "event_date": event_date,
        "event_weekday": _weekday_pl(event),
        "d_minus_1": d_minus_1.isoformat(),
        "d_minus_1_weekday": _weekday_pl(d_minus_1),
        "effective_date": effective_date,
        "effective_weekday": None,
        "rate": nbp_rate,
        "table_no": None,
        "urls": None,
        "explanation_pl": None,
    }
    if nbp_rate is None or effective_date is None:
        result["explanation_pl"] = (
            f"Kurs NBP dla zdarzenia „{kind}” ({event_date}) nie jest jeszcze "
            f"zamrożony — uruchom backfill lub spróbuj ponownie później.")
        return result

    try:
        effective = date.fromisoformat(effective_date)
    except (TypeError, ValueError):
        effective = None
    if effective is not None:
        result["effective_weekday"] = _weekday_pl(effective)

    table_no = fx_nbp.table_no_for_effective_date(conn, effective_date)
    result["table_no"] = table_no
    if table_no:
        result["urls"] = fx_nbp.table_urls(table_no, effective_date)

    rate_str = f"{nbp_rate:.4f}".replace(".", ",")
    table_part = f", tabela {table_no}" if table_no else ""
    if effective is not None and d_minus_1 == effective:
        result["explanation_pl"] = (
            f"{kind}: {event_date} ({_weekday_pl(event)}) → dzień roboczy poprzedzający "
            f"(art. 11a): {d_minus_1.isoformat()} ({_weekday_pl(d_minus_1)}){table_part} "
            f"→ kurs {rate_str}")
    else:
        effective_weekday_part = (
            f" ({_weekday_pl(effective)})" if effective is not None else "")
        result["explanation_pl"] = (
            f"{kind}: {event_date} ({_weekday_pl(event)}) → dzień roboczy poprzedzający: "
            f"{d_minus_1.isoformat()} ({_weekday_pl(d_minus_1)}, brak publikacji) → ostatnia "
            f"opublikowana tabela: {effective_date}{effective_weekday_part}{table_part} "
            f"→ kurs {rate_str}")
    return result


def enrich_allocations(conn: sqlite3.Connection, allocations: list[dict], sale_ctx: dict,
                       cfg: dict) -> dict:
    """Dokłada do każdej alokacji FIFO (z `tax/lots.py::_plan_fifo` — symulacja —
    albo wprost z `sale_allocations` — sprzedaż zrealizowana) komplet szczegółów:
    dane lotu, wyprowadzenie obu kursów NBP (nabycie i sprzedaż), kwoty w EUR
    (pochodne zamrożonego PLN, więc zawsze spójne), i które polityki kosztu
    uznają dany lot.

    `allocations`: lista z minimum `lot_id`, `quantity` (ile z tego lotu wzięto),
    `cost_pln`, `revenue_pln` — dokładnie to, co zwraca `_plan_fifo()` i co leży
    w `sale_allocations`. Dane lotu (typ, data, cena, kurs) są DOCIĄGANE tutaj
    z tabeli `lots` po `lot_id`, żeby wywołujący nie musiał ich sam kompletować.

    `sale_ctx`: `sale_date`, `price_eur`, `fee_eur`, `quantity` (CAŁKOWITA ilość
    sprzedaży — do podziału prowizji proporcjonalnie, ta sama formuła co
    `_plan_fifo`), `nbp_rate`, `nbp_rate_date` (zamrożone na sprzedaży).

    Zwraca `allocations` (szczegóły per lot), `sale_fx` (wyprowadzenie kursu
    sprzedaży - wspólne dla wszystkich alokacji tej sprzedaży), sumy
    `revenue_eur/pln`, `policies` (koszt/dochód/podatek per polityka, tak jak
    `tax/policy.py::compute_all_policies`, ale liczone TYLKO z przekazanych
    alokacji — pozwala to samo API użyć dla jednej sprzedaży albo dla symulacji),
    `net_pln`/`net_eur` („ile finalnie dostaję" wg aktywnej polityki — `net_eur`
    jest PREZENTACYJNE: podatek płaci się w PLN, przeliczenie na EUR służy tylko
    porównaniu z wpływem na rachunek maklerski)."""
    sale_quantity = sale_ctx.get("quantity") or sum(a["quantity"] for a in allocations)
    price_eur = sale_ctx["price_eur"]
    fee_eur = sale_ctx.get("fee_eur", 0.0)
    sale_nbp_rate = sale_ctx.get("nbp_rate")

    sale_fx = fx_derivation(
        conn, sale_ctx["sale_date"], sale_nbp_rate, sale_ctx.get("nbp_rate_date"), "sprzedaż")

    lot_fx_cache: dict[int, dict] = {}
    detailed: list[dict] = []
    for alloc in allocations:
        lot_row = conn.execute(
            "SELECT * FROM lots WHERE id = ?", (alloc["lot_id"],)).fetchone()
        lot = dict(lot_row) if lot_row else {}

        qty_taken = alloc["quantity"]
        cost_pln = alloc["cost_pln"]
        revenue_pln = alloc["revenue_pln"]

        lot_nbp_rate = lot.get("nbp_rate")
        cost_eur = cost_pln / lot_nbp_rate if lot_nbp_rate else None

        fee_share_eur = fee_eur * (qty_taken / sale_quantity) if sale_quantity else 0.0
        revenue_eur = qty_taken * price_eur - fee_share_eur

        lot_id = alloc["lot_id"]
        if lot_id not in lot_fx_cache:
            lot_fx_cache[lot_id] = fx_derivation(
                conn, lot.get("acquired_date"), lot_nbp_rate,
                lot.get("nbp_rate_date"), "nabycie")
        lot_fx = lot_fx_cache[lot_id]

        income_pln = revenue_pln - cost_pln
        counted_in = [name for name, allowed in taxpolicy.POLICIES.items()
                      if lot.get("lot_type") in allowed]

        detailed.append({
            "lot_id": lot_id,
            "lot_type": lot.get("lot_type"),
            "acquired_date": lot.get("acquired_date"),
            "lot_quantity": lot.get("quantity"),
            "lot_price_eur": lot.get("price_eur"),
            "lot_fee_eur": lot.get("fee_eur"),
            "qty_taken": qty_taken,
            "cost_eur": round(cost_eur, 2) if cost_eur is not None else None,
            "cost_pln": round(cost_pln, 2),
            "lot_fx": lot_fx,
            "sale_price_eur": price_eur,
            "fee_share_eur": round(fee_share_eur, 2),
            "revenue_eur": round(revenue_eur, 2),
            "revenue_pln": round(revenue_pln, 2),
            "income_pln": round(income_pln, 2),
            "counted_in": counted_in,
        })

    revenue_eur_total = sum(d["revenue_eur"] for d in detailed)
    revenue_pln_total = sum(d["revenue_pln"] for d in detailed)
    tax_rate = cfg.get("pl_capital_gains_tax_pct", 19.0) / 100

    policies_out: dict[str, dict] = {}
    for name, allowed in taxpolicy.POLICIES.items():
        cost_pln = sum(d["cost_pln"] for d in detailed if name in d["counted_in"])
        cost_eur = sum((d["cost_eur"] or 0.0) for d in detailed if name in d["counted_in"])
        income_pln = revenue_pln_total - cost_pln
        tax_pln = round(max(0.0, income_pln * tax_rate), 2)
        policies_out[name] = {
            "cost_eur": round(cost_eur, 2),
            "cost_pln": round(cost_pln, 2),
            "income_pln": round(income_pln, 2),
            "tax_pln": tax_pln,
            "legal_basis_pl": taxpolicy.LEGAL_BASIS_PL[name],
        }

    active_policy = cfg.get("cost_basis_policy", "own_only")
    active_tax_pln = policies_out[active_policy]["tax_pln"]
    for data in policies_out.values():
        data["delta_vs_active_pln"] = round(data["tax_pln"] - active_tax_pln, 2)

    net_pln = round(revenue_pln_total - active_tax_pln, 2)
    net_eur = (round(revenue_eur_total - active_tax_pln / sale_nbp_rate, 2)
              if sale_nbp_rate else None)

    return {
        "allocations": detailed,
        "sale_fx": sale_fx,
        "revenue_eur": round(revenue_eur_total, 2),
        "revenue_pln": round(revenue_pln_total, 2),
        "policies": policies_out,
        "active_policy": active_policy,
        "net_pln": net_pln,
        "net_eur": net_eur,
    }
