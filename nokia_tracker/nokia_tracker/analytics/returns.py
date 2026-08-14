"""XIRR i TWR portfela (krok 25, docs/PLAN_KROK_25_wyniki.md).

Dwie różne definicje przepływu, bo mierzą różne rzeczy:

- **XIRR na własnych wpłatach** liczy się na GOTÓWCE realnie wydanej przez
  użytkownika (`build_xirr_cashflows`, loty `own`) — pokazuje stopę zwrotu
  Z JEGO PIENIĘDZY. Akcje `matched`/`lti`/`dividend_drip` wchodzą tylko do
  wartości końcowej jako darmowy przypływ, dlatego wynik bywa absurdalnie
  wysoki i to jest poprawne — plan pracowniczy dopłaca 50%+ i odracza
  opodatkowanie.
- **TWR** neutralizuje moment wpłat i liczy się na WARTOŚCI RYNKOWEJ dodanej/
  odjętej z portfela każdego dnia (`build_twr_cashflows`), niezależnie od
  tego, czy to był zakup za gotówkę czy darmowe akcje — `lots.price_eur` to
  fair value w dniu nabycia dla KAŻDEGO typu lotu (zweryfikowane w
  `importers/computershare_pdf.py`: kolumna źródłowa dla `matched`/`lti` to
  "cost basis", czyli wycena rynkowa w dniu vestingu, nie 0). Bez tego
  rozróżnienia każdy vesting sztucznie zawyżałby "wynik rynku" w TWR.
"""
from __future__ import annotations

import sqlite3
from datetime import date

_MAX_NEWTON_ITER = 100
_MAX_BISECT_ITER = 200
_TOL = 1e-6


def _years(d: date, t0: date) -> float:
    return (d - t0).days / 365.0


def _npv(rate: float, flows: list[tuple[date, float]], t0: date) -> float:
    return sum(amt / (1 + rate) ** _years(d, t0) for d, amt in flows)


def _dnpv(rate: float, flows: list[tuple[date, float]], t0: date) -> float:
    return sum(
        -_years(d, t0) * amt / (1 + rate) ** (_years(d, t0) + 1)
        for d, amt in flows)


def xirr(cashflows: list[tuple[str, float]]) -> float | None:
    """`cashflows`: `[(data_iso, kwota), ...]` — ujemna = wypłata z kieszeni,
    dodatnia = wpływ (wartość końcowa pozycji jako ostatni przepływ). Zwraca
    roczną stopę jako ułamek (`0.15` = 15%) albo `None`, gdy nie da się
    policzyć jednoznacznie (< 2 przepływy, same dodatnie/same ujemne)."""
    if len(cashflows) < 2:
        return None
    flows = sorted(
        ((date.fromisoformat(d), amt) for d, amt in cashflows), key=lambda x: x[0])
    if not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    t0 = flows[0][0]

    r = 0.1
    for _ in range(_MAX_NEWTON_ITER):
        f = _npv(r, flows, t0)
        if abs(f) < _TOL:
            return r
        d = _dnpv(r, flows, t0)
        if d == 0:
            break
        r = r - f / d
        if r <= -0.999999:
            r = -0.5  # Newton uciekł pod -100% zwrotu — restart w rozsądnym punkcie

    lo, hi = -0.999999, 10.0
    f_lo, f_hi = _npv(lo, flows, t0), _npv(hi, flows, t0)
    if f_lo * f_hi > 0:
        return None
    for _ in range(_MAX_BISECT_ITER):
        mid = (lo + hi) / 2
        f_mid = _npv(mid, flows, t0)
        if abs(f_mid) < _TOL:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def twr(daily_values: list[tuple[str, float]],
        cashflows: list[tuple[str, float]]) -> float | None:
    """`daily_values`: `[(data, market_value_eur), ...]` chronologicznie (z
    `portfolio_history`). `cashflows`: wartość rynkowa dodana(+)/odjęta(-)
    tego dnia — inna definicja niż `xirr()`, patrz docstring modułu. Zwraca
    łańcuchowo złożoną stopę jako ułamek, albo `None` przy < 2 punktach."""
    if len(daily_values) < 2:
        return None
    cf_by_date: dict[str, float] = {}
    for d, amt in cashflows:
        cf_by_date[d] = cf_by_date.get(d, 0.0) + amt

    factor = 1.0
    for i in range(1, len(daily_values)):
        _d_prev, v_prev = daily_values[i - 1]
        d_cur, v_cur = daily_values[i]
        if v_prev == 0:
            continue
        cf = cf_by_date.get(d_cur, 0.0)
        r_day = (v_cur - v_prev - cf) / v_prev
        factor *= (1 + r_day)
    return factor - 1


def build_xirr_cashflows(conn: sqlite3.Connection, as_of_date: str,
                         current_qty: float, current_price_eur: float
                         ) -> list[tuple[str, float]]:
    """Przepływy gotówkowe realnie zapłacone/otrzymane przez użytkownika —
    patrz tabela w docs/PLAN_KROK_25_wyniki.md. `current_qty`/`current_price_eur`
    dostarcza wywołujący (web.py/main.py), analytics nie sięga do `quotes`
    bezpośrednio, żeby zostać czystym na danych z bazy przekazanych jawnie."""
    flows: list[tuple[str, float]] = []

    own_lots = conn.execute(
        "SELECT acquired_date, quantity, price_eur, fee_eur FROM lots "
        "WHERE lot_type = 'own'").fetchall()
    for r in own_lots:
        flows.append((r["acquired_date"], -(r["quantity"] * r["price_eur"] + r["fee_eur"])))

    sales = conn.execute("SELECT sale_date, quantity, price_eur, fee_eur FROM sales").fetchall()
    for r in sales:
        flows.append((r["sale_date"], r["quantity"] * r["price_eur"] - r["fee_eur"]))

    dividends = conn.execute(
        "SELECT pay_date, net_received_eur FROM dividends "
        "WHERE reinvested_lot_id IS NULL AND net_received_eur IS NOT NULL").fetchall()
    for r in dividends:
        flows.append((r["pay_date"], r["net_received_eur"]))

    flows.append((as_of_date, current_qty * current_price_eur))
    return flows


def build_twr_cashflows(conn: sqlite3.Connection) -> list[tuple[str, float]]:
    """Wartość rynkowa dodana(+)/odjęta(-) z portfela — WSZYSTKIE typy lotu
    po fair value (`price_eur`), sprzedaże jako odjęcie realnych wpływów.
    Dywidendy gotówkowe NIE wchodzą — `portfolio_history.market_value_eur`
    to czysta wycena akcji, dywidenda nigdy jej nie dotyka; DRIP jest już
    policzony jako lot `dividend_drip` w pierwszej pętli."""
    flows: list[tuple[str, float]] = []

    lots = conn.execute(
        "SELECT acquired_date, quantity, price_eur, fee_eur FROM lots").fetchall()
    for r in lots:
        flows.append((r["acquired_date"], r["quantity"] * r["price_eur"] + r["fee_eur"]))

    sales = conn.execute("SELECT sale_date, quantity, price_eur, fee_eur FROM sales").fetchall()
    for r in sales:
        flows.append((r["sale_date"], -(r["quantity"] * r["price_eur"] - r["fee_eur"])))

    return flows
