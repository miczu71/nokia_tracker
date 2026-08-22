"""Dane dla / — Stan konta (krok E5, docs/ROADMAP_V3.md). Odpowiedź na
"jaki jest mój stan": akcje (pozycja + kubełki wolne/z ograniczeniem/
zablokowane), gotówka i podatek (`cash.ledger`), najbliższe zdarzenia
(`account_events.upcoming_events`), "dziś warto wiedzieć". Wydzielone z
dawnego `views/dashboard.py` — wywołania portfelowe identyczne i w tej samej
kolejności co przed E5, zero zmiany liczb. Jedyne NOWE wywołania to
kompozycja już istniejących funkcji: `cash.ledger()` (E4) i
`dividend_outlook.calendar()` (już karmi /dywidendy) — zero nowej matematyki
finansowej w tym module."""
from __future__ import annotations

from datetime import datetime

from .. import account_events as account_eventsm
from .. import advisor as advisorm
from .. import cash as cashm
from .. import dashboard_insights
from .. import dividend_outlook
from .. import portfolio as portfoliom
from .. import sensors
from ..tax import grants as grantsm
from ..tax import losses as taxlosses
from ..tax import policy as taxpolicy


def account_view(conn, cfg: dict, ids: dict, year: int) -> dict:
    # Identyczne wywołania co dawny `views/dashboard.py::dashboard_view` (nie
    # `market_context.latest_price_and_rate` — TA para pochodzi z
    # `market_values`/`benchmark_values`, dowolnej granularności, nie tylko
    # dziennej; podmiana zmieniłaby liczby wobec 0.20.0).
    price_values = sensors.market_values(conn, ids["primary"])
    price_eur = price_values.get("price_eur")
    eurpln_rate = sensors.benchmark_values(
        conn, ids["primary"], ids["ericsson"], ids["omxh25"], ids["eurpln"],
        ids["adr"], ids["eurusd"]).get("eurpln_rate")

    cost_basis_eur = cfg["position_qty"] * cfg["avg_cost_eur"]
    dividends = sensors.dividends_values(conn, cfg, cost_basis_eur)
    position = portfoliom.position_values_auto(
        conn, cfg, price_eur, eurpln_rate,
        dividends_net_total_eur=dividends["dividends_net_eur"])

    # Krok 21 (docs/PLAN_KROK_21_portfel_calkowity.md): zablokowane (nienabyte
    # dopasowania ESPP/transze LTI) i posiadane-ale-ograniczone (świeże zakupy
    # własne czekające na własne dopasowanie).
    unvested = grantsm.unvested_summary(conn, price_eur, eurpln_rate)
    restricted = grantsm.restricted_own_summary(conn, price_eur, eurpln_rate)
    # Krok 23 (docs/PLAN_KROK_23_portfel_kafelki.md): position/restricted/unvested
    # złożone w trzy kubełki (wolne/z ograniczeniem/zablokowane) + sumę.
    buckets = portfoliom.dashboard_buckets(position, restricted, unvested)
    # Krok 26 (docs/PLAN_KROK_26_doradca.md): kwota przepadującego dopasowania
    # ESPP.
    forfeit = advisorm.forfeit_summary(conn, price_eur, eurpln_rate)

    # Krok E4: księga gotówki — wpływy ze sprzedaży, dywidendy (bezgotówkowe,
    # DRIP), podatek należny vs zapłacony, saldo u brokera (ręczne, `None` gdy
    # brak odczytu — nie zero, patrz `cash.py` docstring).
    ledger = cashm.ledger(conn, cfg, year)

    # Kalendarz dywidend — już karmi /dywidendy (`dividend_outlook.py`), tu
    # tylko `next_event` do osi czasu poniżej. `years_ahead=1` wystarcza dla
    # "najbliższe zdarzenie" (nie potrzeba pełnego 3-letniego horyzontu).
    dividend_calendar = dividend_outlook.calendar(
        conn, cfg, years_ahead=1, eurpln_rate=eurpln_rate)

    current_year = datetime.now().year
    loss_available_pln = taxlosses.available_for_year(
        conn, cfg, current_year)["total_remaining_pln"]
    income_pln_this_year = taxpolicy.compute_all_policies(
        conn, cfg, current_year)[cfg["cost_basis_policy"]]["income_pln"]
    insights = dashboard_insights.today_worth_knowing(
        change_pct_day=price_values.get("change_pct_day"),
        next_vest_date=unvested.get("next_vest_date"),
        next_vest_qty=unvested.get("next_vest_qty"),
        loss_available_pln=loss_available_pln,
        income_pln_this_year=income_pln_this_year)

    events = account_eventsm.upcoming_events(
        next_vest_date=unvested.get("next_vest_date"),
        next_vest_qty=unvested.get("next_vest_qty"),
        next_dividend=dividend_calendar.get("next_event"),
        free_until=buckets["restricted"].get("free_until"),
        days_until_free=forfeit.get("days_until_free"),
        forfeit_qty=forfeit.get("forfeit_qty"),
        forfeit_value_pln=forfeit.get("forfeit_value_pln"),
        tax_deadline=ledger["tax_liability"].get("deadline"),
        tax_outstanding_pln=ledger["tax_liability"].get("outstanding_pln"))

    return {
        "position": position, "dividends": dividends, "unvested": unvested,
        "restricted": restricted, "buckets": buckets, "forfeit": forfeit,
        "eurpln_rate": eurpln_rate, "ledger": ledger, "events": events,
        "insights": insights,
    }
