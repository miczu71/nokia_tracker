""""Co jeśli sprzedam teraz" (BLUEPRINT §3a, krok 15) — symuluje sprzedaż
BEZ zapisu do bazy, żeby spiąć silnik podatkowy z rekomendacją AI: „sprzedaj"
znaczy co innego, gdy wiadomo, że fiskus weźmie konkretną kwotę.

Używa `tax/lots.py::_plan_fifo()` — tej samej czystej funkcji, którą
`record_sale()` woła przed zapisem — więc symulacja nie kłamie: identyczne
dane wejściowe (te same otwarte loty, ta sama ilość/cena) dają identyczną
alokację co realna, zapisana sprzedaż. Różnica jest tylko jedna: `conn`
tutaj służy wyłącznie do ODCZYTU (`open_lots`, kurs NBP) — żadnego
`INSERT`/`UPDATE`/`commit`.

Kurs NBP: D-1 od dnia symulacji (domyślnie dzisiaj), zamrożony tylko na
czas tego wywołania — nigdy nie trafia do tabeli `nbp_rates` jako
przypisany do lotu/sprzedaży."""
from __future__ import annotations

import sqlite3
from datetime import datetime

from ..providers import fx_nbp
from . import lots as taxlots
from . import policy as taxpolicy
from . import trace as taxtrace


def _apply_policies(plan: list[dict], revenue_pln: float, cfg: dict
                    ) -> tuple[dict[str, dict], str]:
    """Krok 26 (docs/PLAN_KROK_26_doradca.md): pętla trzech polityk kosztu wydzielona
    z `simulate_sale` bez zmiany zachowania, żeby `advisor.espp_plan()` mogła wołać
    DOKŁADNIE tę samą matematykę na syntetycznej alokacji FIFO (akcje planera, które
    jeszcze nie istnieją jako loty) — bez tego dwa miejsca liczyłyby podatek dwiema
    kopiami tej samej pętli, gwarancja rozjazdu prędzej czy później."""
    tax_rate = cfg.get("pl_capital_gains_tax_pct", 19.0) / 100
    policies: dict[str, dict] = {}
    for name, allowed_types in taxpolicy.POLICIES.items():
        cost_pln = sum(a["cost_pln"] for a in plan if a["lot_type"] in allowed_types)
        income_pln = revenue_pln - cost_pln
        tax_pln = round(max(0.0, income_pln * tax_rate), 2)
        policies[name] = {
            "cost_pln": round(cost_pln, 2),
            "income_pln": round(income_pln, 2),
            "tax_pln": tax_pln,
            "legal_basis_pl": taxpolicy.LEGAL_BASIS_PL[name],
        }

    active_policy = cfg.get("cost_basis_policy", "own_only")
    active_tax_pln = policies[active_policy]["tax_pln"]
    for data in policies.values():
        data["delta_vs_active_pln"] = round(data["tax_pln"] - active_tax_pln, 2)

    return policies, active_policy


def simulate_sale(conn: sqlite3.Connection, cfg: dict, quantity: float,
                  price_eur: float, fee_eur: float = 0.0,
                  sale_date: str | None = None) -> dict:
    """Symuluje sprzedaż `quantity` akcji po `price_eur` na dzień `sale_date`
    (domyślnie dzisiaj). Podnosi `InsufficientLotsError`/`CostBasisMissingError`
    tak samo jak `record_sale()` — sama symulacja też musi się nie udać
    uczciwie, gdy pokrycia brakuje, zamiast pokazać zmyślony wynik.

    Zwraca `revenue_pln`, `lots_consumed` (ślad FIFO — które loty, ile,
    jakim kosztem), `policies` (trzy polityki kosztu naraz, jak na `/lots`),
    `net_proceeds_pln` (przychód minus podatek wg AKTYWNEJ polityki z
    `cfg['cost_basis_policy']`)."""
    if sale_date is None:
        sale_date = datetime.now().strftime("%Y-%m-%d")

    open_rows = taxlots.open_lots(conn, as_of=sale_date)
    total_available = sum(lot["qty_remaining"] for lot in open_rows)
    if quantity - total_available > taxlots._EPS:
        raise taxlots.InsufficientLotsError(
            f"Brak pokrycia: chcesz sprzedać {quantity}, dostępne {total_available}")

    rate = fx_nbp.rate_for_event(conn, sale_date)
    if rate is None:
        raise taxlots.CostBasisMissingError(
            f"Brak kursu NBP dla dnia {sale_date} (spróbuj ponownie później)")
    nbp_rate, nbp_rate_date = rate

    plan = taxlots._plan_fifo(open_rows, quantity, price_eur, fee_eur, nbp_rate)
    revenue_pln = sum(alloc["revenue_pln"] for alloc in plan)

    policies, active_policy = _apply_policies(plan, revenue_pln, cfg)

    # Krok 16: rozbicie do numeru tabeli NBP — patrz tax/trace.py. Osobny klucz
    # `lots_consumed_detailed` obok `lots_consumed`, żeby nie psuć istniejących
    # konsumentów starego, płaskiego kształtu (testy krok 15).
    detailed = taxtrace.enrich_allocations(
        conn, plan,
        {"sale_date": sale_date, "price_eur": price_eur, "fee_eur": fee_eur,
         "quantity": quantity, "nbp_rate": nbp_rate, "nbp_rate_date": nbp_rate_date},
        cfg)

    return {
        "sale_date": sale_date,
        "nbp_rate": nbp_rate,
        "nbp_rate_date": nbp_rate_date,
        "quantity": quantity,
        "revenue_pln": round(revenue_pln, 2),
        "lots_consumed": plan,
        "lots_consumed_detailed": detailed,
        "policies": policies,
        "active_policy": active_policy,
        "net_proceeds_pln": round(revenue_pln - policies[active_policy]["tax_pln"], 2),
    }
