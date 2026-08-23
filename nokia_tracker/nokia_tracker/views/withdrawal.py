"""Dane dla /wyplata — kalkulator wypłaty (E6 krok 4, docs/PLAN_E6_wyplata.md).
Jeden kształt wyniku dla obu kierunków, żeby templates/withdrawal.html miał
jeden blok renderujący zamiast dwóch:

- kierunek `target` (A): `tax/whatif.py::solve_for_net` — jedyna nowa
  matematyka całej roadmapy (bisekcja).
- kierunek `quantity` (B): `tax/whatif.py::annual_net_for_quantity` — TA SAMA
  funkcja, którą `solve_for_net` woła na końcu (jej docstring: "oba kierunki
  kończą w tej samej funkcji"), więc "na rękę" jest liczone identycznie
  niezależnie od kierunku.

Przepadek dopasowania ESPP i ryzyko koncentracji dokładane TUTAJ, nie w
`tax/whatif.py` — to portfel/ustawienia, nie księga podatkowa (ten sam podział
odpowiedzialności co `advisor.py`, patrz jego docstring)."""
from __future__ import annotations

from .. import advisor as advisorm
from .. import portfolio as portfoliom
from ..tax import grants as grantsm
from ..tax import lots as taxlots
from ..tax import whatif as taxwhatif


def withdrawal_view(conn, cfg: dict, direction: str, price_eur: float, fee_pct: float,
                    sale_date: str, *, target_net_pln: float | None = None,
                    quantity: float | None = None) -> tuple[dict | None, str | None]:
    try:
        if direction == "target":
            engine = taxwhatif.solve_for_net(
                conn, cfg, target_net_pln, price_eur, fee_pct=fee_pct, sale_date=sale_date)
        else:
            fee_eur = fee_pct / 100 * quantity * price_eur
            engine = taxwhatif.annual_net_for_quantity(
                conn, cfg, quantity, price_eur, fee_eur, sale_date=sale_date)
    except (taxlots.InsufficientLotsError, taxlots.CostBasisMissingError,
            taxwhatif.TargetUnreachableError) as e:
        return None, str(e)

    quantity_sold = engine["quantity"]
    # Kurs zamrożony przez simulate_sale() na dzień sale_date (art. 11a) — używany
    # dalej dla przepadku/koncentracji, żeby cała strona liczyła na JEDNYM kursie.
    eurpln_rate = engine["nbp_rate"]

    forfeit = advisorm.forfeit_for_quantity(
        conn, quantity_sold, price_eur, eurpln_rate, today=sale_date)

    # Wzorzec identyczny jak advisor.py::exit_plan (before/after jednej decyzji
    # sprzedaży) — koncentracja PRZED i PO tej konkretnej wypłacie.
    position = portfoliom.position_values_auto(conn, cfg, price_eur, eurpln_rate)
    unvested = grantsm.unvested_summary(conn, price_eur, eurpln_rate, today=sale_date)
    restricted = grantsm.restricted_own_summary(conn, price_eur, eurpln_rate, today=sale_date)
    buckets = portfoliom.dashboard_buckets(position, restricted, unvested)
    employer_value_pln_before = buckets["total"]["value_pln"]

    concentration_before = None
    concentration_after = None
    if employer_value_pln_before is not None:
        concentration_before = advisorm.concentration(
            employer_value_pln_before, cfg.get("other_net_worth_pln", 0.0),
            cfg.get("concentration_alert_pct", 25.0))
        sold_value_pln = quantity_sold * price_eur * eurpln_rate
        employer_value_pln_after = (
            employer_value_pln_before - sold_value_pln - (forfeit["forfeit_value_pln"] or 0.0))
        concentration_after = advisorm.concentration(
            employer_value_pln_after, cfg.get("other_net_worth_pln", 0.0),
            cfg.get("concentration_alert_pct", 25.0))

    timing = advisorm.optimize_sale_timing(
        conn, cfg, quantity_sold, price_eur, eurpln_rate, today=sale_date)

    active_policy = engine["active_policy"]
    result = {
        "direction": direction,
        "sale_date": sale_date,
        "quantity": quantity_sold,
        "whole_shares": engine.get("whole_shares"),
        "price_eur": price_eur,
        "fee_pct": fee_pct,
        "gross_eur": round(quantity_sold * price_eur, 2),
        "revenue_pln": engine["revenue_pln"],
        "cost_fifo_pln": engine["policies"][active_policy]["cost_pln"],
        "income_pln": engine["annual"]["combined_income_pln"],
        "usable_loss_pln": engine["annual"]["usable_loss_pln"],
        "tax_pln": engine["annual"]["tax_with_max_loss_pln"],
        "net_pln": engine["net_pln"],
        "forfeit_qty": forfeit["forfeit_qty"],
        "forfeit_value_pln": forfeit["forfeit_value_pln"],
        "concentration_before": concentration_before,
        "concentration_after": concentration_after,
        "lots_consumed_detailed": engine["lots_consumed_detailed"],
        "timing": timing,
    }
    return result, None
