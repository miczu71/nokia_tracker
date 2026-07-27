"""Kalendarz sesji Nasdaq Helsinki — do oszczędzania quoty API (BLUEPRINT §1):
poll co poll_interval_minutes tylko w sesji, poza sesją rzadziej, w
weekend wcale.

Uwaga na zakres: to sprawdza dzień tygodnia + godziny (10:00-18:30
Europe/Helsinki), NIE fińskie święta giełdowe (Wielki Piątek, Wniebowstąpienie,
Wigilia Świętojańska itp. — ruchome daty, wymagają rocznej aktualizacji).
Efekt pominięcia świąt: jeden dodatkowy, niepotrzebny poll tego dnia — nie
błąd danych (Yahoo i tak zwróci ostatni dostępny kurs), więc świadomie
uproszczone dla 0.1.0.
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

HELSINKI_TZ = ZoneInfo("Europe/Helsinki")
_SESSION_OPEN = time(10, 0)
_SESSION_CLOSE = time(18, 30)


def is_session_open(now: datetime | None = None) -> bool:
    """True w dni robocze 10:00-18:30 czasu Helsinek."""
    if now is None:
        now = datetime.now(HELSINKI_TZ)
    local = now.astimezone(HELSINKI_TZ)
    if local.weekday() >= 5:  # sobota=5, niedziela=6
        return False
    return _SESSION_OPEN <= local.time() < _SESSION_CLOSE
