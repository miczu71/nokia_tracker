"""Prosty kalkulator podatku od dywidend 0.1.0 — u źródła w Finlandii,
zaliczenie w Polsce ograniczone do stawki traktatowej, kwota do odzyskania
z fińskiego Vero (BLUEPRINT §2, sekcja "Dywidendy i podatki").

Świadome uproszczenie: liczy na kursie bieżącym (eurpln_rate prezentacyjny),
NIE na zamrożonym kursie NBP D-1 wymaganym przez art. 11a ustawy o PIT dla
faktycznego rozliczenia — to dochodzi w kroku 11 (0.2.0) razem z resztą
silnika podatkowego opartego na lotach. Wartości tutaj są orientacyjne/
edukacyjne, nie do bezpośredniego wpisania do PIT-38 — patrz DISCLAIMER
w ai/prompts.py, ten sam duch dotyczy tej funkcji.

Zweryfikowane względem przykładu z BLUEPRINT: 100 EUR brutto, 35% u źródła
-> 65 EUR netto, zaliczenie 15 EUR, Belka 19 EUR -> 4 EUR dopłaty w PL,
20 EUR do odzyskania z Vero.
"""
from __future__ import annotations


def compute_dividend_tax(gross_eur: float, withholding_pct: float,
                         treaty_withholding_pct: float,
                         pl_capital_gains_tax_pct: float) -> dict:
    withholding_paid_eur = gross_eur * withholding_pct / 100
    net_received_eur = gross_eur - withholding_paid_eur

    treaty_cap_eur = gross_eur * treaty_withholding_pct / 100
    credit_eur = min(withholding_paid_eur, treaty_cap_eur)
    belka_eur = gross_eur * pl_capital_gains_tax_pct / 100
    pl_tax_due_eur = max(0.0, belka_eur - credit_eur)
    reclaimable_from_finland_eur = max(0.0, withholding_paid_eur - treaty_cap_eur)

    return {
        "withholding_paid_eur": round(withholding_paid_eur, 2),
        "net_received_eur": round(net_received_eur, 2),
        "pl_tax_due_eur": round(pl_tax_due_eur, 2),
        "reclaimable_from_finland_eur": round(reclaimable_from_finland_eur, 2),
    }
