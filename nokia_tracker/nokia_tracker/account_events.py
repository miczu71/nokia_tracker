"""Krok E5 (docs/ROADMAP_V3.md) — karta "Najbliższe zdarzenia" na Stanie
konta (`/`). Cztery MOŻLIWE źródła — vesting, dywidenda, koniec restrykcji
ESPP, termin PIT-38 — złożone w jedną oś czasu, posortowaną po dacie. Funkcja
czysta (ten sam wzorzec co `dashboard_insights.py`): wołający dostarcza już
policzone wartości (`tax/grants.py::unvested_summary`,
`dividend_outlook.py::calendar()['next_event']`, `advisor.py::forfeit_summary`,
`cash.py::tax_liability`), ta funkcja liczy WYŁĄCZNIE odstęp w dniach i
formatuje — zero nowej matematyki finansowej.

Brak źródła = brak zdarzenia (nie sztuczne wypełnianie). Zdarzenie
przeterminowane (dni ujemne) NIE znika po cichu — pokazuje się z ujemnym
`days` i `severity="warning"`, tak jak `unvested_summary` traktuje `overdue`."""
from __future__ import annotations

from datetime import datetime

from . import format as fmt

_WARNING_WITHIN_DAYS = 7


def _days_until(date: str, today: str) -> int:
    return (datetime.strptime(date, "%Y-%m-%d")
            - datetime.strptime(today, "%Y-%m-%d")).days


def _severity(days: int) -> str:
    return "warning" if days <= _WARNING_WITHIN_DAYS else "info"


def upcoming_events(
    *,
    next_vest_date: str | None,
    next_vest_qty: float | None,
    next_dividend: dict | None,
    free_until: str | None,
    days_until_free: int | None,
    forfeit_qty: float | None,
    forfeit_value_pln: float | None,
    tax_deadline: str | None,
    tax_outstanding_pln: float | None,
    today: str | None = None,
) -> list[dict]:
    """Zwraca `list[dict]` posortowaną rosnąco po `date`, każdy element:
    `date`, `days` (może być ujemne), `kind`
    (`vesting`/`dividend`/`restriction_end`/`tax_deadline`), `label`
    (krótkie zdanie do UI), `detail` (drugorzędny kontekst), `severity`
    (`warning` w ciągu `_WARNING_WITHIN_DAYS` dni lub przeterminowane,
    inaczej `info` — ten sam słownik co `alerts.py`, nie nowy)."""
    as_of = today or datetime.now().strftime("%Y-%m-%d")
    events: list[dict] = []

    if next_vest_date is not None and next_vest_qty is not None:
        days = _days_until(next_vest_date, as_of)
        events.append({
            "date": next_vest_date, "days": days, "kind": "vesting",
            "label": f"Vesting: {fmt.qty(next_vest_qty)} akcji",
            "detail": None, "severity": _severity(days),
        })

    if next_dividend is not None:
        date = next_dividend["record_date"]
        days = _days_until(date, as_of)
        net_eur = next_dividend.get("net_in_hand_eur")
        label = "Dywidenda"
        if net_eur is not None:
            label += f": ~{fmt.money(net_eur, decimals=2)} EUR na rękę"
        certainty = next_dividend.get("certainty")
        detail = {
            "confirmed": "termin potwierdzony",
            "announced": "termin ogłoszony",
            "estimated": "termin szacowany z historii",
        }.get(certainty)
        events.append({
            "date": date, "days": days, "kind": "dividend",
            "label": label, "detail": detail, "severity": _severity(days),
        })

    if free_until is not None and forfeit_qty:
        days = _days_until(free_until, as_of)
        label = f"Koniec restrykcji ESPP: {fmt.qty(forfeit_qty)} akcji przestaje przepadać"
        detail = (
            f"do tej daty sprzedaż oznacza utratę {fmt.money(forfeit_value_pln, decimals=2)} zł"
            if forfeit_value_pln else None)
        events.append({
            "date": free_until, "days": days, "kind": "restriction_end",
            "label": label, "detail": detail, "severity": _severity(days),
        })

    if tax_deadline is not None and tax_outstanding_pln is not None and tax_outstanding_pln > 0:
        days = _days_until(tax_deadline, as_of)
        events.append({
            "date": tax_deadline, "days": days, "kind": "tax_deadline",
            "label": f"Termin PIT-38: {fmt.money(tax_outstanding_pln, decimals=2)} zł do zapłaty",
            "detail": None, "severity": _severity(days),
        })

    events.sort(key=lambda e: e["date"])
    return events
