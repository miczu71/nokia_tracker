"""Dane dla /rynek — kurs, wykres, sentyment/briefing, rekomendacja AI,
prognozy, ostatnie alerty. Wydzielone z dawnego `views/dashboard.py` (E5 —
docs/ROADMAP_V3.md): część portfelowa/gotówkowa/podatkowa przeniosła się do
`views/account.py`, ta funkcja zostaje z czysto rynkową połową, zero zmiany
liczb wobec 0.20.0 dla wartości, które tu zostają. `chart_ranges`/
`default_chart_range`/`chart_api_url` zostają w trasie — `url_for()` jest
zakazane w tej warstwie."""
from __future__ import annotations

from .. import quotes
from .. import sensors


def market_view(conn, ids: dict) -> dict:
    values = sensors.market_values(conn, ids["primary"])
    values.update(sensors.benchmark_values(
        conn, ids["primary"], ids["ericsson"], ids["omxh25"], ids["eurpln"],
        ids["adr"], ids["eurusd"]))
    values.update(sensors.ai_values(conn))
    values.update(sensors.forecast_values(conn))

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

    return {
        "values": values, "fx_info": fx_info,
        "alerts": [dict(r) for r in recent_alerts],
    }
