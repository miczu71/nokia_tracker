"""Dane dla /sales — rejestr zrealizowanych sprzedaży z pełnym rozbiciem FIFO
per numer tabeli NBP (krok 16), tym samym `_alloc_detail.html`/`tax/trace.py`
co karta „co jeśli sprzedam teraz" na /pit38. Wyodrębnione z
`web.py::sales_get` (E3 — docs/ROADMAP_V3.md), zero zmiany liczb."""
from __future__ import annotations

from ..tax import trace as taxtrace


def sales_view(conn, cfg: dict, year: int | None) -> dict:
    query = "SELECT * FROM sales"
    params: tuple = ()
    if year:
        query += " WHERE strftime('%Y', sale_date) = ?"
        params = (str(year),)
    query += " ORDER BY sale_date DESC, id DESC"
    sale_rows = conn.execute(query, params).fetchall()

    sales = []
    for s in sale_rows:
        allocations = [dict(r) for r in conn.execute(
            "SELECT lot_id, quantity, cost_pln, revenue_pln FROM sale_allocations "
            "WHERE sale_id = ? ORDER BY lot_id", (s["id"],)).fetchall()]
        sale_ctx = {
            "sale_date": s["sale_date"], "price_eur": s["price_eur"],
            "fee_eur": s["fee_eur"], "quantity": s["quantity"],
            "nbp_rate": s["nbp_rate"], "nbp_rate_date": s["nbp_rate_date"],
        }
        reported = None
        if s["reported_revenue_pln"] is not None or s["reported_cost_pln"] is not None:
            reported = {"reported_revenue_pln": s["reported_revenue_pln"],
                        "reported_cost_pln": s["reported_cost_pln"]}
        detail = taxtrace.enrich_allocations(conn, allocations, sale_ctx, cfg, reported)
        sales.append({"sale": dict(s), "detail": detail})

    # Krok 17: pasek KPI nad rejestrem — suma wg AKTYWNEJ polityki kosztu,
    # ta sama, którą pokazuje szczegół każdej sprzedaży.
    active_policy = cfg.get("cost_basis_policy", "own_only")
    totals = {
        "count": len(sales),
        "revenue_pln": round(sum(i["detail"]["revenue_pln"] for i in sales), 2),
        "cost_pln": round(sum(
            i["detail"]["policies"][active_policy]["cost_pln"] for i in sales), 2),
        "income_pln": round(sum(
            i["detail"]["policies"][active_policy]["income_pln"] for i in sales), 2),
        "tax_pln": round(sum(
            i["detail"]["policies"][active_policy]["tax_pln"] for i in sales), 2),
        "net_pln": round(sum(i["detail"]["net_pln"] for i in sales), 2),
    }
    return {"sales": sales, "totals": totals}
