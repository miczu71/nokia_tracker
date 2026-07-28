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

import sqlite3

from ..providers import fx_nbp
from . import lots as taxlots


def add_dividend(conn: sqlite3.Connection, record_date: str, purchase_date: str,
                 entitled_quantity: float, gross_eur: float, taxes_eur: float,
                 fees_eur: float, reinvested_eur: float, purchase_price_eur: float,
                 purchased_shares: float, natural_key: str | None = None) -> int:
    """Zapisuje dywidendę (rejestr, krok 13) + tworzy JEDNOCZEŚNIE lot `dividend_drip`
    (DRIP nie ma odroczonego vestingu jak ESPP match/LTI — reinwestycja wykonuje się
    natychmiast, więc lot powstaje od razu, nie przez scheduler kroku 14).

    `withholding_pct` liczone z REALNYCH `taxes_eur/gross_eur` per wiersz (dokładniejsze
    niż stała z ustawień — potwierdzone na 5 niezależnych dywidendach w zakresie
    34,9-35,0%, patrz BLUEPRINT §3a). Kurs NBP zamrożony na Record Date (dzień uzyskania
    przychodu wg art. 11a), NIE na Purchase Date (dzień reinwestycji).

    `pl_tax_due_pln` (zaliczenie stawki traktatowej + Belka) celowo zostaje `NULL` —
    wymaga ustawień treaty/Belka z configu, to zakres orkiestracji kroku 14
    (`tax/dividends.py`: u źródła/zaliczenie/odzysk z Vero), nie samego zapisu do rejestru.
    """
    if natural_key is None:
        natural_key = f"dividend:{record_date}:{purchase_date}:{entitled_quantity}"
    existing = conn.execute(
        "SELECT id FROM dividends WHERE natural_key = ?", (natural_key,)).fetchone()
    if existing:
        return existing["id"]

    withholding_pct = (taxes_eur / gross_eur * 100) if gross_eur else None
    withholding_paid_eur = taxes_eur
    net_received_eur = gross_eur - taxes_eur

    rate = fx_nbp.rate_for_event(conn, record_date)
    nbp_rate, nbp_rate_date = rate if rate else (None, None)
    gross_pln = gross_eur * nbp_rate if nbp_rate is not None else None

    cur = conn.execute(
        "INSERT INTO dividends (pay_date, quantity, gross_eur, withholding_pct, "
        "withholding_paid_eur, net_received_eur, nbp_rate, nbp_rate_date, gross_pln, "
        "natural_key) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (record_date, entitled_quantity, gross_eur, withholding_pct, withholding_paid_eur,
         net_received_eur, nbp_rate, nbp_rate_date, gross_pln, natural_key))
    dividend_id = cur.lastrowid
    conn.commit()

    drip_natural_key = f"drip:{record_date}:{purchase_date}:{entitled_quantity}"
    lot_id = taxlots.add_lot(
        conn, purchase_date, "dividend_drip", purchased_shares, purchase_price_eur,
        natural_key=drip_natural_key)
    conn.execute(
        "UPDATE dividends SET reinvested_lot_id = ? WHERE id = ?", (lot_id, dividend_id))
    conn.commit()
    return dividend_id


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
