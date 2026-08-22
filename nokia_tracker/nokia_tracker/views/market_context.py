"""Wspólny kontekst rynkowy: get-or-create instrumentów + najświeższa cena/kurs.

Dawne `web._ids` (web.py:72-86) plus nowa preambuła kursu — powielona (w trzech
różnych wariantach, nie jednym) w ośmiu miejscach `web.py`: `portfolio_get`
(273-279) i `grants_get` (852-856) trzymały wiersze jako lokalne `price`/
`eurpln`; `wyniki_get` (878-883), `plan_get` (1000-1005) i `preview_exit_plan`
(1151-1156) rozpakowywały od razu do `price_eur`/`eurpln_rate`; `dividends_get`
(397-399), `preview_espp` (1079-1081) i `preview_sale_timing` (1117-1119)
potrzebowały tylko kursu EUR/PLN. `latest_price_and_rate`/`latest_eurpln_rate`
zszywają to w jedno miejsce (E3 §3a — docs/ROADMAP_V3.md), bez zmiany żadnej
z tych wartości: te same zapytania, ten sam fallback `["close"] if row else
None`."""
from __future__ import annotations

from .. import fx, quotes

PRIMARY_SYMBOL = "NOKIA.HE"
ERICSSON_SYMBOL = "ERIC-B.ST"
OMXH25_SYMBOL = "^OMXH25"
EURUSD_SYMBOL = "EURUSD=X"
ADR_SYMBOL = "NOK"


def instrument_ids(conn) -> dict:
    """Get-or-create instrumentów — niezależne od main.py (web może obsłużyć
    request zanim scheduler w main.py przejdzie backfill)."""
    return {
        "primary": quotes.ensure_instrument(conn, PRIMARY_SYMBOL, "Nokia Oyj", "EUR", "primary"),
        "ericsson": quotes.ensure_instrument(conn, ERICSSON_SYMBOL, "Ericsson", "SEK", "benchmark"),
        "omxh25": quotes.ensure_instrument(
            conn, OMXH25_SYMBOL, "OMX Helsinki 25", "EUR", "benchmark"),
        "eurpln": quotes.ensure_instrument(conn, fx.EURPLN_SYMBOL, "EUR/PLN", "PLN", "fx"),
        "eurusd": quotes.ensure_instrument(conn, EURUSD_SYMBOL, "EUR/USD", "USD", "fx"),
        "adr": quotes.ensure_instrument(conn, ADR_SYMBOL, "Nokia ADR (NYSE)", "USD", "adr"),
    }


def latest_price_and_rate(conn, ids: dict | None = None) -> tuple[float | None, float | None]:
    """(price_eur, eurpln_rate) z ostatnich świec dziennych."""
    ids = ids if ids is not None else instrument_ids(conn)
    price_row = quotes.latest_quote(conn, ids["primary"], granularity="daily")
    eurpln_row = quotes.latest_quote(conn, ids["eurpln"], granularity="daily")
    price_eur = price_row["close"] if price_row else None
    eurpln_rate = eurpln_row["close"] if eurpln_row else None
    return price_eur, eurpln_rate


def latest_eurpln_rate(conn, ids: dict | None = None) -> float | None:
    """Sam kurs EUR/PLN, dla wywołań którym cena akcji jest niepotrzebna."""
    ids = ids if ids is not None else instrument_ids(conn)
    eurpln_row = quotes.latest_quote(conn, ids["eurpln"], granularity="daily")
    return eurpln_row["close"] if eurpln_row else None
