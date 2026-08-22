"""Dane dla /gotowka (krok E4 — docs/ROADMAP_V3.md). Składanie, zero zapisu —
kontrakt `views/__init__.py`. Cała matematyka gotówka/podatek/dywidendy żyje w
`cash.py`; ta funkcja tylko spina jego wynik z listą wpłat podatku (dla
tabeli na stronie) i historią odczytów salda (dla wykresu/tabeli)."""
from __future__ import annotations

from .. import cash as cashm


def cash_view(conn, cfg: dict, year: int) -> dict:
    ledger = cashm.ledger(conn, cfg, year)
    return {
        **ledger,
        "tax_payments": cashm.tax_payments_for_year(conn, year),
        "broker_history": cashm.broker_history(conn),
    }
