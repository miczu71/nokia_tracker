"""Dane dla /dividends — jedno źródło prawdy z `taxdiv.add_dividend()` (krok
16/18/30). Wyodrębnione z `web.py::dividends_get` (E3 — docs/ROADMAP_V3.md),
zero zmiany liczb.

Zapisy (`backfill_missing_dividend_rates`, `reconcile_schedule` pod
`dbm.WRITE_LOCK`) ZOSTAJĄ w trasie, wołane PRZED tą funkcją, w tej samej
względnej kolejności co w oryginale (backfill przed odczytem `dividends`,
reconcile przed odczytem `dividend_schedule`) — `reconcile_schedule` czyta i
zapisuje wyłącznie `dividend_schedule`, nigdy `dividends`, więc przesunięcie
go przed `items`/`totals` (dawniej po nich) nie zmienia żadnej z tych
liczb — zweryfikowane w `dividend_outlook.py::reconcile_schedule`."""
from __future__ import annotations

from .. import portfolio as portfoliom
from .. import dividend_outlook as outlookm
from ..tax import dividends as taxdiv


def dividends_view(conn, cfg: dict, years_ahead: int, eurpln_rate: float | None) -> dict:
    rows = conn.execute("SELECT * FROM dividends ORDER BY pay_date DESC").fetchall()
    items = []
    for r in rows:
        t = taxdiv.compute_dividend_tax_pln(r, cfg)
        d = dict(r)
        d.update(t)
        if d.get("reinvested_lot_id"):
            lot = conn.execute(
                "SELECT acquired_date, quantity, price_eur FROM lots WHERE id = ?",
                (d["reinvested_lot_id"],)).fetchone()
            d["reinvested_lot"] = dict(lot) if lot else None
        items.append(d)

    gross_pln = sum(i["gross_pln"] or 0 for i in items)
    withholding_paid_pln = sum(i.get("withholding_paid_pln") or 0 for i in items)

    # Krok 18: kafelki EUR pod PLN — NIE jest to osobna konwersja walutowa (a
    # więc nie wraca "dwóch matematyk"): `compute_dividend_tax` liczy tymi samymi
    # % (u źródła z wiersza / traktat / Belka z `cfg`) co `compute_dividend_tax_pln`
    # powyżej, po prostu bez mnożenia przez zamrożony kurs NBP — EUR to naturalna
    # waluta wypłaty, PLN to waluta rozliczenia z fiskusem. Jedno źródło procentów.
    eur_totals = [
        taxdiv.compute_dividend_tax(
            i["gross_eur"],
            i["withholding_pct"] if i["withholding_pct"] is not None
            else cfg["finnish_withholding_pct"],
            cfg["treaty_withholding_pct"], cfg["pl_capital_gains_tax_pct"])
        for i in items
    ]
    gross_eur = sum(i["gross_eur"] for i in items)

    # Krok 18: dawniej `cost_basis_eur = cfg["position_qty"] * cfg["avg_cost_eur"]` —
    # pola ręczne z ustawień, które po pierwszym imporcie PDF są zerami (stan
    # posiadania żyje w lotach). Kafelek „Yield on cost" pokazywał wtedy `—` na
    # stałe u każdego użytkownika po imporcie. `position_values_auto` przełącza
    # się na loty automatycznie, tak jak już robi to pulpit.
    position = portfoliom.position_values_auto(conn, cfg, None, None)
    cost_basis_eur = position["cost_basis_eur"]
    totals = {
        "dividends_gross_pln": gross_pln,
        "dividends_gross_eur": gross_eur,
        "withholding_paid_pln": withholding_paid_pln,
        "withholding_paid_eur": sum(t["withholding_paid_eur"] for t in eur_totals),
        "dividends_net_pln": gross_pln - withholding_paid_pln,
        "dividends_net_eur": sum(t["net_received_eur"] for t in eur_totals),
        "pl_tax_due_pln": sum(i.get("pl_tax_due_pln") or 0 for i in items),
        "pl_tax_due_eur": sum(t["pl_tax_due_eur"] for t in eur_totals),
        "reclaimable_from_finland_pln": sum(
            i.get("reclaimable_from_finland_pln") or 0 for i in items),
        "reclaimable_from_finland_eur": sum(
            t["reclaimable_from_finland_eur"] for t in eur_totals),
        "dividend_yield_on_cost_pct": (
            gross_eur / cost_basis_eur * 100 if cost_basis_eur else None),
    }

    # Krok 28.4 (docs/PLAN_KROK_28_ux_mobile.md §4): słupki dywidend rok po
    # roku — agregacja `gross_pln`/`net_pln` już policzonych powyżej per
    # wiersz, zero nowej logiki podatkowej.
    yearly: dict[str, dict] = {}
    for i in items:
        y = i["pay_date"][:4]
        bucket = yearly.setdefault(y, {"gross_pln": 0.0, "net_pln": 0.0})
        bucket["gross_pln"] += i["gross_pln"] or 0
        bucket["net_pln"] += (i["gross_pln"] or 0) - (i.get("withholding_paid_pln") or 0)
    yearly_dividends = [
        {"year": y, "gross_pln": round(v["gross_pln"], 2), "net_pln": round(v["net_pln"], 2)}
        for y, v in sorted(yearly.items())]

    schedule = outlookm.list_schedule(conn)
    outlook = outlookm.calendar(conn, cfg, years_ahead=years_ahead, eurpln_rate=eurpln_rate)

    return {
        "items": items, "totals": totals, "yearly_dividends": yearly_dividends,
        "schedule": schedule, "outlook": outlook,
    }
