"""Dane pomocnicze dla /pit38. Wyodrębnione z `web.py::pit38_get`
(E3 — docs/ROADMAP_V3.md), zero zmiany liczb — czysty reshaping już
policzonych pól `report`, zero `conn`."""
from __future__ import annotations


def waterfall(report: dict, cfg: dict) -> dict:
    """Waterfall PIT-38 (krok 28.4, docs/PLAN_KROK_28_ux_mobile.md §4) —
    WYŁĄCZNIE Poz. C (przychód ze sprzedaży), zero nowej matematyki, tylko
    reshaping już policzonych pól `report`. "Strata odliczona" to segment
    INFORMACYJNY (pokazuje wielkość tarczy), nie wchodzi do łańcucha
    przychód-koszt-podatek-na rękę — strata obniża PODATEK, nie jest realnym
    wypływem gotówki, więc doliczanie jej do łańcucha dawałoby "na rękę"
    niezgodne z prawdziwą kwotą (dochód - podatek)."""
    active_data = report["policies"][cfg["cost_basis_policy"]]
    loss_cf = report.get("loss_carryforward") or {}
    has_loss = bool(loss_cf.get("items"))
    tax_after_loss = loss_cf["tax_after_loss_pln"] if has_loss else active_data["tax_pln"]
    loss_used = loss_cf["total_used_this_year_pln"] if has_loss else 0.0
    net_in_hand = active_data["income_pln"] - tax_after_loss
    return {
        "revenue": active_data["revenue_pln"], "cost": active_data["cost_pln"],
        "income": active_data["income_pln"], "loss_used": loss_used,
        "tax": tax_after_loss, "net": net_in_hand,
    }
