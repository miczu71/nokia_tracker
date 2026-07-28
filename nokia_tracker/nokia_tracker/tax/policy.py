"""Trzy polityki kosztu liczone równolegle na tym samym zbiorze alokacji
(BLUEPRINT §3a). Żadna z nich nie jest "poprawniejsza" niż inne — różnią
się tym, które loty (poza `own`) uznają za mające koszt nabycia, więc
dają trzy różne, prawnie uzasadnione kwoty podatku. Decyzję, którą
zastosować w PIT-38, użytkownik podejmuje świadomie, widząc wszystkie
trzy obok siebie z podstawą prawną — nie przez domyślne ustawienie
`cost_basis_policy`, które tylko wskazuje, która jest "aktywna" do
porównania (`delta_vs_active_pln`).

`sale_allocations.cost_pln` to zawsze surowy koszt nabycia zapisany przez
`tax/lots.py` — filtr polityki nakłada się dopiero tutaj, przy odczycie.
"""
from __future__ import annotations

import sqlite3

POLICIES: dict[str, set[str]] = {
    "own_only": {"own"},
    "own_plus_drip": {"own", "dividend_drip"},
    "all_at_acquisition": {"own", "matched", "lti", "dividend_drip"},
}

LEGAL_BASIS_PL: dict[str, str] = {
    "own_only": (
        "Za pozostałe akcje nic nie zapłaciłeś, a ich opodatkowanie odroczono "
        "do zbycia (art. 24 ust. 11-12a ustawy o PIT) — nie ma czego odliczyć. "
        "Najwyższy podatek, najmniejsze ryzyko sporu."
    ),
    "own_plus_drip": (
        "DRIP kupujesz za pieniądze już opodatkowane jako dywidenda — "
        "koszt realnie poniesiony."
    ),
    "all_at_acquisition": (
        "Dopuszczalne TYLKO jeśli wartość dokładki i LTI była wykazana jako "
        "przychód ze stosunku pracy (PIT-11) — w przeciwnym razie to podwójne "
        "odliczenie."
    ),
}


def compute_all_policies(conn: sqlite3.Connection, cfg: dict, year: int | None = None) -> dict:
    """Zwraca `{polityka: {revenue_pln, cost_pln, income_pln, tax_pln,
    legal_basis_pl, delta_vs_active_pln}}` dla wszystkich trzech polityk
    naraz, policzone z tych samych zapisanych alokacji sprzedaży.

    `year`: rok podatkowy (kalendarzowy, wg `sales.sale_date`). `None`
    (domyślnie, gdy też `cfg['tax_year']` nie jest ustawione) = wszystkie
    lata łącznie."""
    if year is None:
        year = cfg.get("tax_year") or None

    query = (
        "SELECT sa.cost_pln, sa.revenue_pln, l.lot_type "
        "FROM sale_allocations sa "
        "JOIN lots l ON l.id = sa.lot_id "
        "JOIN sales s ON s.id = sa.sale_id"
    )
    params: tuple = ()
    if year:
        query += " WHERE strftime('%Y', s.sale_date) = ?"
        params = (str(year),)
    rows = conn.execute(query, params).fetchall()

    # Przychód nie zależy od polityki kosztu — to ta sama sprzedaż,
    # różni się tylko to, ile kosztu wolno od niej odjąć.
    total_revenue_pln = sum(r["revenue_pln"] for r in rows)
    tax_rate = cfg.get("pl_capital_gains_tax_pct", 19.0) / 100

    result: dict[str, dict] = {}
    for name, allowed_types in POLICIES.items():
        cost_pln = sum(r["cost_pln"] for r in rows if r["lot_type"] in allowed_types)
        income_pln = total_revenue_pln - cost_pln
        # Strata z akcji nie obniża podatku (nie miesza się ze strumieniem
        # dywidendowym - BLUEPRINT §3a, PIT-38 sekcja G) - podatek 0, nigdy ujemny.
        tax_pln = round(max(0.0, income_pln * tax_rate), 2)
        result[name] = {
            "revenue_pln": round(total_revenue_pln, 2),
            "cost_pln": round(cost_pln, 2),
            "income_pln": round(income_pln, 2),
            "tax_pln": tax_pln,
            "legal_basis_pl": LEGAL_BASIS_PL[name],
        }

    active_policy = cfg.get("cost_basis_policy", "own_only")
    active_tax_pln = result[active_policy]["tax_pln"]
    for data in result.values():
        data["delta_vs_active_pln"] = round(data["tax_pln"] - active_tax_pln, 2)

    return result
