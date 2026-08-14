"""Atrybucja zysku (krok 25, docs/PLAN_KROK_25_wyniki.md).

Rozbija całkowity zysk OTWARTEJ pozycji (ta sama pozycja, którą
`portfolio.position_values()` pokazuje jako niezrealizowany P&L — zrealizowane
sprzedaże mają już swój obraz w `tax/pit38.py`, nie dublujemy go tutaj) w PLN
na pięć składników:

(a) `price_change_pln` — zmiana kursu akcji, licząca się dla KAŻDEGO otwartego
    lotu na jego WŁASNYM zamrożonym kursie NBP (nie dzisiejszym) — "ile bym
    zarobił na samej cenie, gdyby PLN/EUR nigdy się nie ruszył od dnia
    nabycia tego konkretnego lotu".
(b) `espp_match_pln` — wartość lotów `matched` W DNIU DOPASOWANIA (fair value
    razy zamrożony kurs NBP) — to windfall, nie coś, za co zapłacono, więc
    CAŁA ta wartość jest "zyskiem" w chwili nabycia, a dalsza zmiana ceny/FX
    tych samych akcji i tak wpada do (a)/(e) razem z resztą portfela.
(c) `lti_pln` — analogicznie dla lotów `lti`.
(d) `dividends_pln` — dywidendy gotówkowe (`net_received_eur * nbp_rate`) +
    wartość lotów `dividend_drip` w dniu reinwestycji (ten sam mechanizm
    windfallu co (b)/(c) — pieniądze z dywidendy "kupiły" darmowe akcje).
(e) `fx_effect_pln` — REZYDUUM (total − suma pozostałych czterech), *z
    definicji*, nie osobną formułą. To jedyny sposób, żeby suma zawsze
    zgadzała się co do grosza z `total_pln` — każda niezależna formuła na
    (e) wprowadzałaby błędy zaokrągleń przy sumowaniu wielu lotów. Matematycznie
    równoważne `Σ qty_i * cena_dziś * (kurs_dziś − kurs_zamrożony_i)` (patrz
    wyprowadzenie w docs/PLAN_KROK_25_wyniki.md) — sprawdzone testami.

`total_pln` = wartość rynkowa CAŁEJ otwartej pozycji dziś (PLN) minus koszt
bazowy lotów `own` (PLN, zamrożony kurs z dnia zakupu) plus dywidendy
gotówkowe. Loty `matched`/`lti`/`dividend_drip` nie mają realnego kosztu
gotówkowego (opodatkowanie odroczone do zbycia, art. 24 ust. 11-12a PIT) —
ich cała bieżąca wartość jest więc częścią zysku, rozdzieloną między (a) i
odpowiedni windfall (b)/(c)/(d).
"""
from __future__ import annotations

import sqlite3

from ..tax import lots as taxlots

_WINDFALL_COMPONENT = {"matched": "espp_match_pln", "lti": "lti_pln",
                       "dividend_drip": "dividends_pln"}


def decompose(conn: sqlite3.Connection, current_price_eur: float,
             current_eurpln_rate: float) -> dict:
    components = {
        "price_change_pln": 0.0, "espp_match_pln": 0.0, "lti_pln": 0.0,
        "dividends_pln": 0.0,
    }
    total_value_pln = 0.0
    own_cost_pln = 0.0

    for lot in taxlots.open_lots(conn):
        qty = lot["qty_remaining"]
        p0 = lot["price_eur"]
        fx0 = lot["nbp_rate"]
        if fx0 is None:
            continue  # kurs jeszcze niedomknięty (backfill_missing_rates go uzupełni)

        current_value_pln = qty * current_price_eur * current_eurpln_rate
        total_value_pln += current_value_pln

        if lot["lot_type"] == "own":
            own_cost_pln += qty * p0 * fx0
        else:
            windfall_pln = qty * p0 * fx0
            components[_WINDFALL_COMPONENT[lot["lot_type"]]] += windfall_pln

        components["price_change_pln"] += qty * (current_price_eur - p0) * fx0

    cash_dividends_pln = conn.execute(
        "SELECT COALESCE(SUM(net_received_eur * nbp_rate), 0) FROM dividends "
        "WHERE reinvested_lot_id IS NULL AND net_received_eur IS NOT NULL "
        "AND nbp_rate IS NOT NULL").fetchone()[0]
    components["dividends_pln"] += cash_dividends_pln

    total_pln = total_value_pln - own_cost_pln + cash_dividends_pln
    known_sum = sum(components.values())
    components["fx_effect_pln"] = total_pln - known_sum
    components["total_pln"] = total_pln
    return components
