"""Silniki scenariuszy dla /plan i ich bliźniacy JSON /api/preview/espp,
/api/preview/sale-timing, /api/preview/exit-plan (E3 §3b — docs/ROADMAP_V3.md).

Dzielone jest WYŁĄCZNIE wywołanie silnika (`advisorm.*`) + jego obsługa
błędu — parsowanie query stringów i walidacja (`quantity > 0`, komunikaty
"Niepoprawna liczba." vs generyczny `str(ValueError)`) ZOSTAJĄ w trasach,
bo się różnią między HTML a JSON i zszycie ich zmieniłoby zachowanie:

- `espp_scenario`: obie trasy łapią dziś TYLKO `ValueError` wokół wywołania
  silnika — bezpieczne do zszycia.
- `timing_scenario`: obie trasy łapią `(taxlots.InsufficientLotsError,
  taxlots.CostBasisMissingError)` wokół wywołania silnika (E6 krok 6,
  docs/PLAN_E6_wyplata.md — spłata długu z E3 §3b: `forfeit_for_quantity`
  wołane wewnątrz `optimize_sale_timing` może rzucić niezależnie od tego, czy
  `simulate_sale` się powiodło).
- `exit_scenario`: obie trasy łapią dziś `(ValueError,
  taxlots.InsufficientLotsError)` wokół TEGO SAMEGO wywołania — bezpieczne
  do zszycia."""
from __future__ import annotations

from .. import advisor as advisorm
from ..tax import lots as taxlots


def espp_scenario(cfg: dict, eurpln_rate: float | None, monthly_eur: float,
                  months: int, price_eur: float) -> tuple[dict | None, str | None]:
    try:
        result = advisorm.espp_plan(
            monthly_eur, months, price_eur, eurpln_rate=eurpln_rate,
            match_pct=cfg["espp_match_pct"], cost_basis_policy=cfg["cost_basis_policy"],
            tax_pct=cfg["pl_capital_gains_tax_pct"])
        return result, None
    except ValueError as e:
        return None, str(e)


def timing_scenario(conn, cfg: dict, eurpln_rate: float | None,
                    quantity: float, price_eur: float) -> tuple[dict | None, str | None]:
    try:
        result = advisorm.optimize_sale_timing(
            conn, cfg, quantity, price_eur, eurpln_rate=eurpln_rate)
        return result, None
    except (taxlots.InsufficientLotsError, taxlots.CostBasisMissingError) as e:
        return None, str(e)


def exit_scenario(conn, cfg: dict, eurpln_rate: float | None, price_eur: float | None,
                  shares_per_period: float, frequency: str,
                  num_periods: int) -> tuple[dict | None, str | None]:
    try:
        result = advisorm.exit_plan(
            conn, cfg, shares_per_period, frequency, num_periods,
            price_eur or 0.0, eurpln_rate=eurpln_rate)
        return result, None
    except (ValueError, taxlots.InsufficientLotsError) as e:
        return None, str(e)
