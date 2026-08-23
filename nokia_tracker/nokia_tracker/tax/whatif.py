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

import math

from ..providers import fx_nbp
from . import lots as taxlots
from . import losses as taxlosses
from . import policy as taxpolicy
from . import trace as taxtrace


class TargetUnreachableError(Exception):
    """`solve_for_net()`: cel niedostępny dostępnymi lotami, albo bisekcja nie
    zbiegła w limicie iteracji — nigdy nie zwracamy przybliżenia po cichu
    (kryterium twarde E6, docs/ROADMAP_V3.md)."""


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


def _annual_tax(base_income_pln: float, sale_income_pln: float,
                loss_available_pln: float, tax_rate: float) -> dict:
    """Rdzeń „dochód roku + dochód scenariusza − strata z lat ubiegłych" (E6 krok 1,
    docs/PLAN_E6_wyplata.md). CZYSTA — wydzielona z `advisor.py::optimize_sale_timing`
    i `exit_plan`, które liczyły dokładnie tę samą arytmetykę w dwóch osobnych kopiach.
    `solve_for_net()` (E6 krok 3) woła ten rdzeń dziesiątki razy w bisekcji, więc musi
    być odseparowany od zapytań do bazy (patrz `annual_tax_breakdown` niżej)."""
    combined_income_pln = base_income_pln + sale_income_pln
    tax_without_loss_pln = round(max(0.0, combined_income_pln) * tax_rate, 2)

    usable_loss_pln = min(loss_available_pln, max(0.0, combined_income_pln))
    income_after_loss_pln = max(0.0, combined_income_pln - usable_loss_pln)
    tax_with_max_loss_pln = round(income_after_loss_pln * tax_rate, 2)

    return {
        "combined_income_pln": round(combined_income_pln, 2),
        "tax_without_loss_pln": tax_without_loss_pln,
        "usable_loss_pln": round(usable_loss_pln, 2),
        "income_after_loss_pln": round(income_after_loss_pln, 2),
        "tax_with_max_loss_pln": tax_with_max_loss_pln,
    }


def annual_tax_breakdown(conn: sqlite3.Connection, cfg: dict, year: int,
                         sale_income_pln: float, policy: str | None = None) -> dict:
    """Wersja `_annual_tax` z bazą: dobiera `base_income_pln`
    (`tax/policy.py::compute_all_policies`) i `loss_available_pln`
    (`tax/losses.py::available_for_year`) dla `year`/`policy`, tak samo jak dziś robią
    to inline `advisor.py::optimize_sale_timing` i `exit_plan` — jedno miejsce zamiast
    dwóch identycznych par wywołań."""
    if policy is None:
        policy = cfg.get("cost_basis_policy", "own_only")
    tax_rate = cfg.get("pl_capital_gains_tax_pct", 19.0) / 100

    base_income_pln = taxpolicy.compute_all_policies(conn, cfg, year=year)[policy]["income_pln"]
    loss_avail = taxlosses.available_for_year(conn, cfg, year, policy=policy)

    return _annual_tax(base_income_pln, sale_income_pln, loss_avail["total_remaining_pln"], tax_rate)


def annual_net_for_quantity(conn: sqlite3.Connection, cfg: dict, quantity: float,
                            price_eur: float, fee_eur: float = 0.0,
                            sale_date: str | None = None) -> dict:
    """„Na rękę" dla ZNANEJ ilości akcji, modelem ROCZNYM (E6 krok 3,
    docs/PLAN_E6_wyplata.md) — nie tax_pln pojedynczej sprzedaży z
    `simulate_sale()["policies"]` (ten liczy podatek w izolacji, bez dochodu roku ani
    straty z lat ubiegłych). Woła `simulate_sale()` RAZ — daje ślad FIFO/NBP
    (`lots_consumed_detailed`, hak pod E8) — a `income_pln` aktywnej polityki z jej
    wyniku karmi `annual_tax_breakdown()`. Kierunek B kalkulatora wypłaty i finalny
    krok `solve_for_net()` (kierunek A) kończą w TEJ SAMEJ funkcji — zero drugiej
    kopii tej matematyki."""
    sale = simulate_sale(conn, cfg, quantity, price_eur, fee_eur, sale_date=sale_date)
    year = int(sale["sale_date"][:4])
    sale_income_pln = sale["policies"][sale["active_policy"]]["income_pln"]
    annual = annual_tax_breakdown(conn, cfg, year, sale_income_pln, policy=sale["active_policy"])
    net_pln = round(sale["revenue_pln"] - annual["tax_with_max_loss_pln"], 2)
    return {**sale, "annual": annual, "net_pln": net_pln}


def solve_for_net(conn: sqlite3.Connection, cfg: dict, target_net_pln: float,
                  price_eur: float, *, fee_pct: float = 0.0, sale_date: str | None = None,
                  tol_pln: float = 0.01, max_iter: int = 64) -> dict:
    """„Ile akcji sprzedać, żeby na rękę wyszło `target_net_pln`" (E6 krok 3,
    kierunek A) — JEDYNA nowa matematyka w całej roadmapie v3. Bisekcja po ilości nad
    istniejącym `tax/lots.py::_plan_fifo`: `net(q) = revenue(q) − tax(income(q))` jest
    ściśle rosnąca w `q` (przychód i koszt oba rosną liniowo z ilością, podatek —
    proporcjonalnie do dochodu po odjęciu ewentualnej straty), więc IVT gwarantuje
    dokładnie jedno rozwiązanie w `[0, total_available]`.

    Wydajność: `base_income_pln`/`loss_available_pln`/kurs NBP/`open_lots` pobrane
    RAZ przed pętlą (niezależne od `q`) — bisekcja woła wyłącznie czystą `_plan_fifo`
    i czysty rdzeń `_annual_tax`, zero zapytań do bazy per iteracja.

    Nigdy nie przybliża po cichu: cel powyżej maksimum osiągalnego ze wszystkich
    otwartych lotów, albo brak zbieżności w `max_iter`, podnosi
    `TargetUnreachableError` zamiast zwrócić najbliższy wynik."""
    if price_eur <= 0:
        raise ValueError("Cena musi być dodatnia")
    if target_net_pln <= 0:
        raise ValueError("Kwota docelowa musi być dodatnia")
    if sale_date is None:
        sale_date = datetime.now().strftime("%Y-%m-%d")

    open_rows = taxlots.open_lots(conn, as_of=sale_date)
    total_available = sum(lot["qty_remaining"] for lot in open_rows)
    if total_available <= taxlots._EPS:
        raise taxlots.InsufficientLotsError("Brak otwartych lotów do sprzedania")

    rate = fx_nbp.rate_for_event(conn, sale_date)
    if rate is None:
        raise taxlots.CostBasisMissingError(
            f"Brak kursu NBP dla dnia {sale_date} (spróbuj ponownie później)")
    nbp_rate, _nbp_rate_date = rate

    active_policy = cfg.get("cost_basis_policy", "own_only")
    allowed_types = taxpolicy.POLICIES[active_policy]
    tax_rate = cfg.get("pl_capital_gains_tax_pct", 19.0) / 100
    year = int(sale_date[:4])

    base_income_pln = taxpolicy.compute_all_policies(
        conn, cfg, year=year)[active_policy]["income_pln"]
    loss_available_pln = taxlosses.available_for_year(
        conn, cfg, year, policy=active_policy)["total_remaining_pln"]

    def _net_for(qty: float) -> float:
        if qty <= taxlots._EPS:
            return 0.0
        fee_eur = fee_pct / 100 * qty * price_eur
        plan = taxlots._plan_fifo(open_rows, qty, price_eur, fee_eur, nbp_rate)
        revenue_pln = sum(a["revenue_pln"] for a in plan)
        cost_pln = sum(a["cost_pln"] for a in plan if a["lot_type"] in allowed_types)
        annual = _annual_tax(
            base_income_pln, revenue_pln - cost_pln, loss_available_pln, tax_rate)
        return revenue_pln - annual["tax_with_max_loss_pln"]

    net_at_max = _net_for(total_available)
    if target_net_pln - net_at_max > tol_pln:
        raise TargetUnreachableError(
            f"Cel {target_net_pln:.2f} zł nieosiągalny — maksymalne netto ze "
            f"wszystkich dostępnych akcji ({total_available:.4f} szt.) to "
            f"{net_at_max:.2f} zł")

    lo, hi = 0.0, total_available
    quantity = hi
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        net_mid = _net_for(mid)
        quantity = mid
        if abs(net_mid - target_net_pln) <= tol_pln:
            break
        if net_mid < target_net_pln:
            lo = mid
        else:
            hi = mid
    else:
        raise TargetUnreachableError(
            f"Bisekcja nie zbiegła w {max_iter} iteracjach dla celu "
            f"{target_net_pln:.2f} zł")

    fee_eur = fee_pct / 100 * quantity * price_eur
    detail = annual_net_for_quantity(conn, cfg, quantity, price_eur, fee_eur, sale_date=sale_date)

    whole_shares = None
    whole_qty = math.ceil(quantity)
    if whole_qty > 0 and whole_qty - total_available <= taxlots._EPS:
        whole_fee_eur = fee_pct / 100 * whole_qty * price_eur
        whole_shares = annual_net_for_quantity(
            conn, cfg, whole_qty, price_eur, whole_fee_eur, sale_date=sale_date)

    return {"target_net_pln": target_net_pln, "whole_shares": whole_shares, **detail}
