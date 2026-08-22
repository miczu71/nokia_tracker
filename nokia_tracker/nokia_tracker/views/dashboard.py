"""Dane dla pulpitu (/) — pozycja, dywidendy, kurs, alerty, „dziś warto
wiedzieć". Wyodrębnione z `web.py::dashboard` (E3 — docs/ROADMAP_V3.md), zero
zmiany liczb: identyczne wywołania, identyczna kolejność. `chart_ranges`/
`default_chart_range`/`chart_api_url` zostają w trasie — `url_for()` jest
zakazane w tej warstwie."""
from __future__ import annotations

from datetime import datetime

from .. import advisor as advisorm
from .. import dashboard_insights
from .. import portfolio as portfoliom
from .. import quotes
from .. import sensors
from .. import settings as settingsm
from ..tax import grants as grantsm
from ..tax import losses as taxlosses
from ..tax import policy as taxpolicy


def dashboard_view(conn, ids: dict) -> dict:
    values = sensors.market_values(conn, ids["primary"])
    values.update(sensors.benchmark_values(
        conn, ids["primary"], ids["ericsson"], ids["omxh25"], ids["eurpln"],
        ids["adr"], ids["eurusd"]))
    values.update(sensors.ai_values(conn))
    values.update(sensors.forecast_values(conn))

    cfg = settingsm.get_settings(conn)
    cost_basis_eur = cfg["position_qty"] * cfg["avg_cost_eur"]
    dividends = sensors.dividends_values(conn, cfg, cost_basis_eur)
    position = portfoliom.position_values_auto(
        conn, cfg, values.get("price_eur"), values.get("eurpln_rate"),
        dividends_net_total_eur=dividends["dividends_net_eur"])

    # Krok 21 (docs/PLAN_KROK_21_portfel_calkowity.md): zablokowane (nienabyte
    # dopasowania ESPP/transze LTI) i posiadane-ale-ograniczone (świeże zakupy
    # własne czekające na własne dopasowanie) — dotąd niewidoczne na pulpicie,
    # który pokazywał tylko "position" (same uwolnione akcje).
    unvested = grantsm.unvested_summary(
        conn, values.get("price_eur"), values.get("eurpln_rate"))
    restricted = grantsm.restricted_own_summary(
        conn, values.get("price_eur"), values.get("eurpln_rate"))
    # Krok 23 (docs/PLAN_KROK_23_portfel_kafelki.md): position/restricted/unvested
    # złożone w trzy kubełki (wolne/z ograniczeniem/zablokowane) + sumę — zastępuje
    # ręczną arytmetykę total_qty/total_value_* dawniej inline tutaj.
    buckets = portfoliom.dashboard_buckets(position, restricted, unvested)
    # Krok 26 (docs/PLAN_KROK_26_doradca.md): kwota przepadującego dopasowania
    # ESPP dopisana do istniejącego zdania ostrzegawczego (dawniej bez kwoty).
    forfeit = advisorm.forfeit_summary(
        conn, values.get("price_eur"), values.get("eurpln_rate"))

    # Krok 18: metadane kursu EUR/PLN (skąd, kiedy) dla linii rozgraniczającej
    # kurs bieżący (Yahoo/ECB, prezentacyjny) od kursu NBP zamrożonego na
    # zdarzenie (podatkowy, patrz /dywidendy i /pit38). Sama wartość kursu
    # to już `values["eurpln_rate"]` (sensors.py::benchmark_values).
    eurpln_row = quotes.latest_quote(conn, ids["eurpln"], granularity="daily")
    fx_info = {
        "rate": eurpln_row["close"] if eurpln_row else None,
        "ts": eurpln_row["ts"] if eurpln_row else None,
        "source": eurpln_row["source"] if eurpln_row else None,
    }

    recent_alerts = conn.execute(
        "SELECT * FROM alerts_log ORDER BY fired_at DESC LIMIT 5").fetchall()

    # Krok 28.5 (docs/PLAN_KROK_28_ux_mobile.md §5): "Dziś warto wiedzieć" —
    # reużywa dane już policzone powyżej (unvested) + dwa lekkie odczyty
    # (dostępna strata, dochód tego roku wg aktywnej polityki), zero nowej
    # matematyki podatkowej — te same funkcje co /pit38 i /pit38/kreator.
    current_year = datetime.now().year
    loss_available_pln = taxlosses.available_for_year(
        conn, cfg, current_year)["total_remaining_pln"]
    income_pln_this_year = taxpolicy.compute_all_policies(
        conn, cfg, current_year)[cfg["cost_basis_policy"]]["income_pln"]
    insights = dashboard_insights.today_worth_knowing(
        change_pct_day=values.get("change_pct_day"),
        next_vest_date=unvested.get("next_vest_date"),
        next_vest_qty=unvested.get("next_vest_qty"),
        loss_available_pln=loss_available_pln,
        income_pln_this_year=income_pln_this_year)

    return {
        "values": values, "position": position, "dividends": dividends,
        "fx_info": fx_info, "unvested": unvested, "restricted": restricted,
        "buckets": buckets, "forfeit": forfeit,
        "alerts": [dict(r) for r in recent_alerts], "insights": insights,
    }
