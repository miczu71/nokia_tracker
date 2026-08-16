"""Krok 28.5 (docs/PLAN_KROK_28_ux_mobile.md §5): "Dziś warto wiedzieć" na
pulpicie. Trzy MOŻLIWE sygnały, deterministyczne (zero AI, zero LLM) — funkcja
czysta na już policzonych wartościach (ten sam wzorzec co `advisor.py`/
`sensors.py`): wołający dostarcza dane, ta funkcja tylko formatuje zdania.
Brak sygnału = brak zdania (nie sztuczne wypełnianie pustego miejsca)."""
from __future__ import annotations

from datetime import datetime

from . import format as fmt


def today_worth_knowing(
    change_pct_day: float | None,
    next_vest_date: str | None,
    next_vest_qty: float | None,
    loss_available_pln: float,
    income_pln_this_year: float,
    today: str | None = None,
) -> list[str]:
    """1) Zmiana kursu dziś (jeśli znana — `None` różni się od 0.0, milczymy
    tylko przy braku danych, nie przy realnym braku ruchu). 2) Najbliższy
    vesting (jeśli jest jakaś transza `pending`). 3) Sygnał podatkowy —
    WYŁĄCZNIE gdy jest i dostępna strata z lat ubiegłych, i dodatni dochód w
    bieżącym roku (strata bez zysku do skompensowania albo zysk bez straty do
    odliczenia nie są sygnałem, tylko szumem)."""
    lines: list[str] = []

    if change_pct_day is not None:
        direction = "wzrósł" if change_pct_day >= 0 else "spadł"
        lines.append(
            f"Dziś kurs Nokii {direction} o "
            f"{fmt.pct(abs(change_pct_day), decimals=2, signed=False)}%.")

    if next_vest_date is not None and next_vest_qty is not None:
        as_of = today or datetime.now().strftime("%Y-%m-%d")
        days_until = (datetime.strptime(next_vest_date, "%Y-%m-%d")
                     - datetime.strptime(as_of, "%Y-%m-%d")).days
        lines.append(
            f"Najbliższy vesting: {fmt.qty(next_vest_qty)} akcji {next_vest_date} "
            f"({days_until} dni).")

    if loss_available_pln > 0 and income_pln_this_year > 0:
        lines.append(
            f"Masz {fmt.money(income_pln_this_year, decimals=2)} zł zysku w tym roku i "
            f"{fmt.money(loss_available_pln, decimals=2)} zł dostępnej straty z lat "
            f"ubiegłych — rozważ jej odliczenie w kreatorze PIT-38.")

    return lines
