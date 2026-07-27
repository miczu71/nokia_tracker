"""Prosty portfel 0.1.0: stan posiadania (ilość + średni koszt) z ustawień,
P&L w EUR i PLN. Loty/FIFO dochodzą w 0.2.0 — funkcja tu zostaje wąska
i czysta, żeby dało się ją łatwo zastąpić/rozszerzyć bez zmiany wywołujących
(BLUEPRINT §3).
"""
from __future__ import annotations


def position_values(position_qty: float, avg_cost_eur: float, price_eur: float | None,
                    eurpln_rate: float | None, dividends_net_total_eur: float = 0.0) -> dict:
    """total_return_pct = (niezrealizowany P&L + suma netto dywidend) / koszt
    bazowy — świadome uproszczenie 0.1.0, bez efektu walutowego (konto w
    EUR, PLN wyłącznie prezentacyjnie po kursie bieżącym, patrz BLUEPRINT §2)."""
    cost_basis_eur = position_qty * avg_cost_eur
    market_value_eur = position_qty * price_eur if price_eur is not None else None
    unrealized_pnl_eur = (
        market_value_eur - cost_basis_eur if market_value_eur is not None else None)
    unrealized_pnl_pct = (
        unrealized_pnl_eur / cost_basis_eur * 100
        if unrealized_pnl_eur is not None and cost_basis_eur else None)

    total_return_pct = None
    if cost_basis_eur and unrealized_pnl_eur is not None:
        total_return_pct = (unrealized_pnl_eur + dividends_net_total_eur) / cost_basis_eur * 100

    market_value_pln = (
        market_value_eur * eurpln_rate
        if market_value_eur is not None and eurpln_rate else None)
    cost_basis_pln = cost_basis_eur * eurpln_rate if eurpln_rate else None
    unrealized_pnl_pln = (
        unrealized_pnl_eur * eurpln_rate
        if unrealized_pnl_eur is not None and eurpln_rate else None)

    return {
        "position_qty": position_qty,
        "avg_cost_eur": avg_cost_eur,
        "cost_basis_eur": cost_basis_eur,
        "market_value_eur": market_value_eur,
        "unrealized_pnl_eur": unrealized_pnl_eur,
        "unrealized_pnl_pct": unrealized_pnl_pct,
        "total_return_pct": total_return_pct,
        "market_value_pln": market_value_pln,
        "cost_basis_pln": cost_basis_pln,
        "unrealized_pnl_pln": unrealized_pnl_pln,
    }
