"""Roczny raport PIT-38 ze śladem obliczeń (BLUEPRINT §3a, krok 15).

Jedna funkcja wejściowa, `annual_report()`, spina moduły zbudowane w
krokach 11-15 bez duplikowania ich logiki:
- poz. C (kapitały): `tax/policy.py::compute_all_policies()` — trzy polityki
  kosztu obok siebie, ta sama funkcja co na stronie `/lots`.
- sekcja G (dywidendy): `tax/dividends.py::compute_dividend_tax_pln()` — u
  źródła / zaliczenie traktatowe / Belka / dopłata w PL, na kursie NBP
  zamrożonym na Record Date, zsumowane per rok.
- PIT/ZG: pochodna sekcji G (Finlandia jako jedyny kraj źródła).
- ślad per lot: `sale_allocations` JOIN `sales`/`lots` — pokazuje DOKŁADNIE,
  który lot ile kosztował i po jakim kursie, żeby dało się zweryfikować
  liczbę, a nie przyjąć ją na wiarę (klauzula z BLUEPRINT §3a).

Rok filtruje sprzedaże po `sales.sale_date` i dywidendy po `dividends.pay_date`
(w tym schemacie `pay_date` to w istocie Record Date, patrz `add_dividend`) —
NIE po dacie nabycia lotu, bo lot mógł być kupiony w innym roku niż sprzedany.
"""
from __future__ import annotations

import sqlite3

from . import dividends as taxdiv
from . import policy as taxpolicy

_EMPTY_SECTION_G = {
    "dividend_count": 0,
    "gross_pln": 0.0,
    "withholding_paid_pln": 0.0,
    "credit_pln": 0.0,
    "belka_pln": 0.0,
    "pl_tax_due_pln": 0.0,
    "reclaimable_from_finland_pln": 0.0,
}


def _section_g(conn: sqlite3.Connection, cfg: dict, year: int) -> dict:
    rows = conn.execute(
        "SELECT * FROM dividends WHERE strftime('%Y', pay_date) = ? ORDER BY pay_date",
        (str(year),)).fetchall()
    if not rows:
        return dict(_EMPTY_SECTION_G)

    result = dict(_EMPTY_SECTION_G)
    result["dividend_count"] = len(rows)
    for row in rows:
        result["gross_pln"] += row["gross_pln"] or 0.0
        t = taxdiv.compute_dividend_tax_pln(row, cfg)
        if t["pl_tax_due_pln"] is None:
            continue
        result["withholding_paid_pln"] += t["withholding_paid_pln"]
        result["credit_pln"] += t["credit_pln"]
        result["belka_pln"] += t["belka_pln"]
        result["pl_tax_due_pln"] += t["pl_tax_due_pln"]
        result["reclaimable_from_finland_pln"] += t["reclaimable_from_finland_pln"]

    for key in result:
        if key != "dividend_count":
            result[key] = round(result[key], 2)
    return result


def _sale_trace(conn: sqlite3.Connection, year: int) -> list[dict]:
    rows = conn.execute(
        "SELECT sa.id AS alloc_id, sa.quantity, sa.cost_pln, sa.revenue_pln, "
        "s.sale_date, s.nbp_rate AS sale_nbp_rate, s.nbp_rate_date AS sale_nbp_rate_date, "
        "l.id AS lot_id, l.acquired_date, l.lot_type, "
        "l.nbp_rate AS lot_nbp_rate, l.nbp_rate_date AS lot_nbp_rate_date "
        "FROM sale_allocations sa "
        "JOIN sales s ON s.id = sa.sale_id "
        "JOIN lots l ON l.id = sa.lot_id "
        "WHERE strftime('%Y', s.sale_date) = ? "
        "ORDER BY s.sale_date, sa.id",
        (str(year),)).fetchall()
    return [dict(r) for r in rows]


def annual_report(conn: sqlite3.Connection, cfg: dict, year: int) -> dict:
    """Zwraca pełny raport PIT-38 za `year`: `policies` (poz. C, trzy
    polityki kosztu naraz), `section_g` (dywidendy w PLN), `pit_zg`
    (dochód zagraniczny z Finlandii), `sale_trace` (ślad obliczeń per
    alokacja sprzedaży-lotu). Pusty rok zwraca wyzerowane sekcje, nie
    wyjątek — użytkownik może otworzyć raport za rok, w którym jeszcze
    nic się nie wydarzyło."""
    policies = taxpolicy.compute_all_policies(conn, cfg, year=year)
    section_g = _section_g(conn, cfg, year)

    return {
        "year": year,
        "policies": policies,
        "section_g": section_g,
        "pit_zg": {
            "country": "Finlandia",
            "foreign_income_pln": section_g["gross_pln"],
            "foreign_tax_paid_pln": section_g["withholding_paid_pln"],
        },
        "sale_trace": _sale_trace(conn, year),
    }
