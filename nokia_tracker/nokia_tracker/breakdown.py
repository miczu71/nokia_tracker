"""Ślad „skąd ta liczba" (E8, docs/PLAN_E8_slad.md) — model odczytu nad
`portfolio.py`/`cash.py`/`advisor.py`/`tax/grants.py`/`tax/pit38.py`/
`tax/whatif.py`, zero zapisu, zero nowej matematyki finansowej.

**Świadomie NIE jest to rozszerzenie `tax/trace.py`** (choć roadmapa mówiła
"uogólnienie") — `tax/` to beton (docs/ROADMAP_V3.md:54), a rozbicia tutaj
komponują sześć modułów naraz, czyli leżą NAD silnikami, nie w środku
silnika podatkowego. `tax/trace.py::fx_derivation` jest stąd konsumowane
bez zmian (noga NBP każdego śladu do lotu/sprzedaży).

Kryterium twarde (ROADMAP_V3.md, E8): suma składników w rozwinięciu ==
wyświetlana kwota, co do grosza. Domykane tutaj w kodzie, nie tylko w
teście — `close_sum`/`close_formula` rzucają `BreakdownNotClosedError`
zamiast po cichu pokazać rozjazd. Widok łapie ten wyjątek per kwota i
degraduje się do renderu bez śladu (patrz `views/account.py`,
`views/withdrawal.py`) — rozjazd ląduje jako finding w `integrity.py`,
nigdy jako gołe 500 na stronie, która jest celem całej roadmapy v3."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from . import cash as cashm
from .tax import grants as grantsm
from .tax import lots as taxlots
from .tax import losses as taxlosses
from .tax import policy as taxpolicy
from .tax import trace as taxtrace
from .tax.dividends import compute_dividend_tax

_TOLERANCE = 0.01
_LOT_TYPE_PL = {"own": "własne", "matched": "podarowane", "lti": "LTI",
                "dividend_drip": "dywidenda"}


class BreakdownNotClosedError(Exception):
    """Rozjazd między wyświetlaną kwotą a sumą/formułą składników większy niż
    1 grosz. Nigdy nie łapane wewnątrz `breakdown.py` — widok łapie to per
    kwota i pomija JEDEN ślad, reszta strony renderuje się normalnie."""

    def __init__(self, key: str, shown: float, recomputed: float):
        self.key = key
        self.shown = shown
        self.recomputed = recomputed
        super().__init__(
            f"{key}: wyświetlane {shown} != przeliczone {recomputed} "
            f"(różnica {round(shown - recomputed, 4)})")


@dataclass(frozen=True)
class Source:
    """Skąd pochodzi liczba w bazie. `kind`: 'lot' | 'sale' | 'vest' |
    'dividend' | 'manual' | 'pdf_import' | 'holdings_snapshot' | 'statement'
    | 'nbp' | 'engine'. `ref`: natural_key / numer tabeli NBP / nazwa pliku —
    tożsamość źródła, nie link (breakdown.py nie zna Flaska/`url_for`)."""
    kind: str
    label: str
    ref: str | None = None


@dataclass(frozen=True)
class Component:
    """Jeden składnik rozbicia. `value=None` = wiersz informacyjny (np. nota
    o kursie) — nie wchodzi do sumy w `close_sum`. `unit=None` dziedziczy
    jednostkę z `Breakdown.unit` — ustaw jawnie, gdy składnik jest w innej
    jednostce niż wynik (np. „ilość" w szt. wewnątrz rozbicia kwoty w EUR)."""
    label: str
    value: float | None
    detail: str | None = None
    sources: tuple[Source, ...] = ()
    unit: str | None = None


@dataclass(frozen=True)
class Breakdown:
    key: str
    label: str
    amount: float
    unit: str
    formula: str
    components: tuple[Component, ...]
    shown: float
    recomputed: float
    note: str | None = None


def _finalize(key: str, label: str, shown: float, unit: str, formula: str,
             components: tuple[Component, ...], recomputed: float,
             note: str | None) -> Breakdown:
    shown_r = round(shown, 2)
    recomputed_r = round(recomputed, 2)
    diff = round(shown_r - recomputed_r, 2)
    if abs(diff) > _TOLERANCE:
        raise BreakdownNotClosedError(key, shown_r, recomputed_r)
    if diff != 0:
        components = components + (
            Component(f"zaokrąglenie ({diff:+.2f})", diff),)
        recomputed_r = shown_r
    return Breakdown(key=key, label=label, amount=shown_r, unit=unit, formula=formula,
                     components=components, shown=shown_r, recomputed=recomputed_r,
                     note=note)


def close_sum(key: str, label: str, shown: float, unit: str, formula: str,
             components: tuple[Component, ...], note: str | None = None) -> Breakdown:
    """Składniki się SUMUJĄ do wyświetlanej kwoty (np. koszt bazowy = Σ per lot).
    Reszta ≤ 1 gr dostaje jawny składnik „zaokrąglenie"; więcej = wyjątek."""
    recomputed = sum(c.value for c in components if c.value is not None)
    return _finalize(key, label, shown, unit, formula, components, recomputed, note)


def close_formula(key: str, label: str, shown: float, unit: str, formula: str,
                  components: tuple[Component, ...], recomputed: float,
                  note: str | None = None) -> Breakdown:
    """Kwota jest WYNIKIEM FORMUŁY nad składnikami (podatek = podstawa × stawka,
    zwrot = P&L / koszt), nie ich sumą. `recomputed` liczone przez wołającego
    z TYCH SAMYCH składników, które pokazuje — realny test krzyżowy z liczbą
    silnika, nie ozdobnik."""
    return _finalize(key, label, shown, unit, formula, components, recomputed, note)


@dataclass
class BreakdownCtx:
    """Budowany RAZ na żądanie (`build_ctx`), żeby strona z wieloma śladami
    (Stan konta: 11) nie robiła N+1 zapytań. `lots_by_id`: jeden `SELECT *
    FROM lots`. `statement_index`: `{natural_key: {filename, as_of_date,
    period_start, period_end}}` zbudowany raz ze WSZYSTKICH
    `statement_snapshots` — na dzisiejszej produkcji pusty (importy sprzed
    0.23.0, ROADMAP_V3.md:394-401), wypełni się przy re-imporcie."""
    lots_by_id: dict[int, dict]
    statement_index: dict[str, dict]
    _fx_cache: dict = field(default_factory=dict)


def build_ctx(conn: sqlite3.Connection) -> BreakdownCtx:
    lots_by_id = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM lots").fetchall()}

    statement_index: dict[str, dict] = {}
    snap_rows = conn.execute(
        "SELECT ss.snapshot_json, ss.as_of_date, ss.period_start, ss.period_end, "
        "i.filename FROM statement_snapshots ss JOIN imports i ON i.id = ss.import_id "
        "ORDER BY ss.as_of_date ASC").fetchall()
    for row in snap_rows:
        snapshot = json.loads(row["snapshot_json"])
        meta = {"filename": row["filename"], "as_of_date": row["as_of_date"],
                "period_start": row["period_start"], "period_end": row["period_end"]}
        for bucket in ("pending_tranches", "dividends", "purchases"):
            for item in snapshot.get(bucket, []):
                nk = item.get("natural_key")
                if nk:
                    statement_index[nk] = meta

    return BreakdownCtx(lots_by_id=lots_by_id, statement_index=statement_index)


def provenance(ctx: BreakdownCtx, row: dict) -> tuple[Source, ...]:
    """Noga „skąd to jest w bazie" dla dowolnego wiersza `lots`/`vests`/
    `dividends` (minimum kluczy `source`/`natural_key`). `source`/
    `natural_key` pokazywane ZAWSZE — działa na dzisiejszej produkcji, gdzie
    `statement_index` jest pusty. Plik wyciągu doklejany TYLKO przy
    dopasowaniu w `statement_index` — jawne „plik nieznany" zamiast pustego
    śladu, gdy dopasowania nie ma (decyzja zatwierdzona w planie E8)."""
    source = row.get("source") or "manual"
    natural_key = row.get("natural_key")

    if source == "manual":
        return (Source("manual", "Wpisane ręcznie"),)

    label = ("Import z wyciągu PDF" if source == "pdf_import"
             else "Snapshot stanu posiadania (import historyczny)")
    sources = [Source(source, label, ref=natural_key)]

    if natural_key and natural_key in ctx.statement_index:
        meta = ctx.statement_index[natural_key]
        sources.append(Source(
            "statement",
            f"Plik: {meta['filename']} (okres {meta['period_start']} – {meta['period_end']})",
            ref=meta["as_of_date"]))
    else:
        sources.append(Source(
            "statement",
            "Plik wyciągu nieznany (import sprzed 0.23.0 albo natural_key spoza "
            "zapisanych snapshotów)"))
    return tuple(sources)


def lot_fx(ctx: BreakdownCtx, conn: sqlite3.Connection, lot: dict) -> dict:
    """Wyprowadzenie kursu NBP nabycia dla lotu, cache'owane per `lot_id` —
    ten sam wzorzec co `tax/trace.py::enrich_allocations` (`lot_fx_cache`)."""
    lot_id = lot["id"]
    if lot_id not in ctx._fx_cache:
        ctx._fx_cache[lot_id] = taxtrace.fx_derivation(
            conn, lot.get("acquired_date"), lot.get("nbp_rate"),
            lot.get("nbp_rate_date"), "nabycie")
    return ctx._fx_cache[lot_id]


def _lot_label(ctx: BreakdownCtx, lot_id: int, acquired_date: str | None = None) -> str:
    lot = ctx.lots_by_id.get(lot_id, {})
    kind = _LOT_TYPE_PL.get(lot.get("lot_type"), lot.get("lot_type", "?"))
    date = acquired_date or lot.get("acquired_date", "?")
    return f"lot #{lot_id} · {kind} · {date}"


# ============================================================
# /wyplata — kalkulator wypłaty (9 śladów)
# ============================================================
# Budowniczy dostają wyłącznie liczby, które `views/withdrawal.py` już
# policzył (`result`, `engine` z `tax/whatif.py`, `forfeit` z
# `advisor.py::forfeit_for_quantity`) — zero nowej matematyki finansowej,
# jedynym wyjątkiem jest algebra przeliczeniowa w `close_formula`
# (przelicza ze składników, które i tak pokazuje).


def _wyplata_quantity(result: dict, target_net_pln: float | None) -> Breakdown:
    shown = result["quantity"]
    if result["direction"] == "quantity":
        components = (Component("Wpisana ilość akcji", shown),)
        return close_formula(
            "wyplata.quantity", "Ilość akcji", shown, "szt.",
            "wartość wpisana przez użytkownika", components, recomputed=shown)

    components = (
        Component("cel — kwota netto", target_net_pln, unit="zł"),
        Component("wynik bisekcji — ilość akcji", shown),
    )
    return close_formula(
        "wyplata.quantity", "Ilość akcji", shown, "szt.",
        "bisekcja: najmniejsza ilość akcji, dla której „na rękę” ≥ celu (±0,01 zł)",
        components, recomputed=shown,
        note="Wynik iteracyjnego rozwiązania równania (nie sumy) — zweryfikowany "
             "krzyżowo przez ślad „Na rękę” poniżej: podstawienie tej ilości z "
             "powrotem przez ten sam silnik musi dać kwotę bliską celowi.")


def _wyplata_gross(result: dict) -> Breakdown:
    components = (
        Component("ilość akcji", result["quantity"], unit="szt."),
        Component("cena", result["price_eur"], unit="EUR/akcję"),
    )
    recomputed = round(result["quantity"] * result["price_eur"], 2)
    return close_formula(
        "wyplata.gross_eur", "Brutto", result["gross_eur"], "EUR",
        "ilość × cena", components, recomputed=recomputed)


def _wyplata_revenue(ctx: BreakdownCtx, result: dict) -> Breakdown:
    detail = result["lots_consumed_detailed"]
    components = tuple(
        Component(_lot_label(ctx, a["lot_id"], a["acquired_date"]), a["revenue_pln"],
                  detail=f"{a['qty_taken']:.4f} szt. sprzedanych z tego lotu",
                  sources=provenance(ctx, ctx.lots_by_id.get(a["lot_id"], {})))
        for a in detail["allocations"])
    return close_sum(
        "wyplata.revenue_pln", "Przychód (PLN)", result["revenue_pln"], "zł",
        "Σ przychód PLN per lot (alokacja FIFO)", components,
        note=f"Kurs sprzedaży: {detail['sale_fx']['explanation_pl']}")


def _wyplata_cost_fifo(ctx: BreakdownCtx, result: dict, engine: dict) -> Breakdown:
    active_policy = engine["active_policy"]
    detail = result["lots_consumed_detailed"]
    relevant = [a for a in detail["allocations"] if active_policy in a["counted_in"]]
    components = tuple(
        Component(_lot_label(ctx, a["lot_id"], a["acquired_date"]), a["cost_pln"],
                  detail=f"{a['qty_taken']:.4f} szt. × {a['lot_price_eur']} EUR/akcję",
                  sources=provenance(ctx, ctx.lots_by_id.get(a["lot_id"], {})))
        for a in relevant)
    policy_pl = taxpolicy.LEGAL_BASIS_PL.get(active_policy, active_policy)
    return close_sum(
        "wyplata.cost_fifo", "Koszt FIFO", result["cost_fifo_pln"], "zł",
        "Σ koszt PLN per lot uznany w aktywnej polityce kosztu", components,
        note=f"Polityka: {active_policy} — {policy_pl}")


def _wyplata_income(result: dict, engine: dict) -> Breakdown:
    active_policy = engine["active_policy"]
    sale_income_pln = engine["policies"][active_policy]["income_pln"]
    base_income_pln = round(result["income_pln"] - sale_income_pln, 2)
    components = (
        Component("dochód już zrealizowany w tym roku (przed tą sprzedażą)", base_income_pln),
        Component("dochód z tej sprzedaży", sale_income_pln),
    )
    return close_sum(
        "wyplata.income", "Dochód roku", result["income_pln"], "zł",
        "dochód zrealizowany wcześniej + dochód z tej sprzedaży", components)


def _wyplata_usable_loss(conn: sqlite3.Connection, cfg: dict, result: dict,
                         engine: dict) -> Breakdown:
    year = int(result["sale_date"][:4])
    loss_info = taxlosses.available_for_year(conn, cfg, year, policy=engine["active_policy"])
    combined_income_pln = result["income_pln"]
    components = (
        Component("dostępna strata z lat ubiegłych (max 5 lat wstecz)",
                  loss_info["total_remaining_pln"]),
        Component("dochód roku (ten + poprzednie sprzedaże)", combined_income_pln),
    )
    recomputed = round(
        min(loss_info["total_remaining_pln"], max(0.0, combined_income_pln)), 2)
    return close_formula(
        "wyplata.usable_loss", "Wykorzystana strata", result["usable_loss_pln"], "zł",
        "min(dostępna strata, max(0, dochód roku))", components, recomputed=recomputed)


def _wyplata_tax(cfg: dict, result: dict, engine: dict) -> Breakdown:
    income_after_loss_pln = engine["annual"]["income_after_loss_pln"]
    tax_rate_pct = cfg.get("pl_capital_gains_tax_pct", 19.0)
    components = (
        Component("dochód po odliczeniu wykorzystanej straty", income_after_loss_pln),
        Component(f"stawka podatku ({tax_rate_pct:g}%)", None),
    )
    recomputed = round(income_after_loss_pln * tax_rate_pct / 100, 2)
    return close_formula(
        "wyplata.tax", "Podatek", result["tax_pln"], "zł",
        "dochód po stracie × stawka podatku", components, recomputed=recomputed)


def _wyplata_net(result: dict) -> Breakdown:
    components = (
        Component("przychód", result["revenue_pln"]),
        Component("podatek", -result["tax_pln"]),
    )
    return close_sum(
        "wyplata.net", "Na rękę", result["net_pln"], "zł",
        "przychód − podatek", components)


def _wyplata_forfeit(ctx: BreakdownCtx, result: dict, forfeit: dict,
                     eurpln_rate: float | None) -> Breakdown | None:
    touched = forfeit.get("lots_touched") or []
    if not touched or not result.get("forfeit_value_pln"):
        return None
    price_eur = result["price_eur"]
    components = tuple(
        Component(_lot_label(ctx, t["lot_id"]),
                  t["forfeit_qty"] * price_eur * eurpln_rate if eurpln_rate else None,
                  detail=f"{t['forfeit_qty']:.4f} szt. utraconego dopasowania "
                         f"({t['match_rate']:.0%} z {t['taken_qty']:.4f} sprzedanych z tego lotu)",
                  sources=provenance(ctx, ctx.lots_by_id.get(t["lot_id"], {})))
        for t in touched)
    return close_sum(
        "wyplata.forfeit", "Przepadek dopasowania ESPP", result["forfeit_value_pln"], "zł",
        "Σ (utracone szt. × cena × kurs) per dotknięty lot", components)


def withdrawal_traces(conn: sqlite3.Connection, ctx: BreakdownCtx, cfg: dict,
                      result: dict, engine: dict, forfeit: dict,
                      *, target_net_pln: float | None = None) -> dict[str, Breakdown]:
    """Wszystkie ślady dla `/wyplata`. Rozjazd na pojedynczej kwocie NIE wywala
    strony — ta jedna kwota renderuje się bez `<details>` (degradacja opisana
    w module docstring). Inputy zależą od formularza użytkownika (cena, data),
    więc — inaczej niż `account_traces` — awarie nie są tu zbierane dla
    `integrity.py`: nie ma ustalonego zestawu „dzisiejszych" wejść do
    powtórzenia poza żądaniem."""
    builders: dict[str, object] = {
        "wyplata.quantity": lambda: _wyplata_quantity(result, target_net_pln),
        "wyplata.gross_eur": lambda: _wyplata_gross(result),
        "wyplata.revenue_pln": lambda: _wyplata_revenue(ctx, result),
        "wyplata.cost_fifo": lambda: _wyplata_cost_fifo(ctx, result, engine),
        "wyplata.income": lambda: _wyplata_income(result, engine),
        "wyplata.usable_loss": lambda: _wyplata_usable_loss(conn, cfg, result, engine),
        "wyplata.tax": lambda: _wyplata_tax(cfg, result, engine),
        "wyplata.net": lambda: _wyplata_net(result),
        "wyplata.forfeit": lambda: _wyplata_forfeit(
            ctx, result, forfeit, engine.get("nbp_rate")),
    }
    traces: dict[str, Breakdown] = {}
    for key, build in builders.items():
        try:
            trace = build()
        except BreakdownNotClosedError:
            continue
        if trace is not None:
            traces[key] = trace
    return traces


# ============================================================
# / — Stan konta (11 śladów)
# ============================================================


def _portfel_total(position: dict, unvested: dict, buckets: dict) -> Breakdown | None:
    shown = buckets["total"]["value_pln"]
    if shown is None:
        return None
    components = (
        Component("wartość pozycji (posiadane akcje)", position["market_value_pln"]),
        Component("wartość nienabytych transz", unvested["upcoming_value_pln"]),
    )
    return close_sum(
        "portfel.total", "Wartość całkowita", shown, "zł",
        "wartość pozycji + wartość nienabytych transz", components)


def _portfel_free(position: dict, restricted: dict, buckets: dict) -> Breakdown | None:
    shown = buckets["free"]["value_pln"]
    if shown is None:
        return None
    components = (
        Component("wartość pozycji", position["market_value_pln"]),
        Component("minus: z ograniczeniem", -(restricted["restricted_value_pln"] or 0.0)),
    )
    return close_sum(
        "portfel.free", "Wolne", shown, "zł",
        "wartość pozycji − wartość z ograniczeniem", components)


def _portfel_restricted(conn: sqlite3.Connection, ctx: BreakdownCtx, restricted: dict,
                        price_eur: float | None, eurpln_rate: float | None
                        ) -> Breakdown | None:
    shown = restricted["restricted_value_pln"]
    if shown is None:
        return None
    items = grantsm.restricted_own_lots(conn)
    if not items:
        return None
    components = tuple(
        Component(_lot_label(ctx, it["lot_id"], it["acquired_date"]),
                  it["qty_remaining"] * price_eur * eurpln_rate,
                  detail=f"{it['qty_remaining']:.4f} szt., wolne od {it['free_until']}",
                  sources=provenance(ctx, ctx.lots_by_id.get(it["lot_id"], {})))
        for it in items)
    return close_sum(
        "portfel.restricted", "Z ograniczeniem", shown, "zł",
        "Σ (pozostała ilość × cena × kurs) per ograniczony lot", components)


def _portfel_locked(conn: sqlite3.Connection, buckets: dict,
                    price_eur: float | None, eurpln_rate: float | None
                    ) -> Breakdown | None:
    shown = buckets["locked"]["value_pln"]
    if shown is None:
        return None
    tranches = [t for t in grantsm.vesting_timeline(conn, price_eur, eurpln_rate)["tranches"]
               if not t["overdue"]]
    if not tranches:
        return None
    components = tuple(
        Component(f"transza {t['program']} · dostępność {t['effective_date']}",
                  t["value_pln"], detail=f"{t['quantity']:.4f} szt.")
        for t in tranches)
    return close_sum(
        "portfel.locked", "Zablokowane", shown, "zł",
        "Σ wartość nienabytych transz (nie licząc zaległych)", components)


def _portfel_cost_basis(conn: sqlite3.Connection, ctx: BreakdownCtx, cfg: dict,
                        position: dict, eurpln_rate: float | None) -> Breakdown | None:
    shown = position["cost_basis_pln"]
    if shown is None or eurpln_rate is None:
        return None
    open_rows = taxlots.open_lots(conn)
    if not open_rows:
        return None
    allowed = taxpolicy.POLICIES.get(cfg.get("cost_basis_policy", "own_only"), {"own"})
    relevant = [r for r in open_rows if r["lot_type"] in allowed]
    components = tuple(
        Component(_lot_label(ctx, r["id"], r["acquired_date"]),
                  r["price_eur"] * r["qty_remaining"] * eurpln_rate,
                  detail=f"{r['qty_remaining']:.4f} szt. × {r['price_eur']} EUR/akcję "
                         f"× kurs bieżący {eurpln_rate:.4f}",
                  sources=provenance(ctx, dict(r)))
        for r in relevant)
    return close_sum(
        "portfel.cost_basis", "Koszt bazowy", shown, "zł",
        "Σ (ilość × cena × kurs bieżący) per lot uznany w aktywnej polityce kosztu",
        components,
        note="Kurs bieżący (prezentacyjny), nie zamrożony kurs NBP nabycia — patrz "
             "PIT-38 dla wartości rozliczeniowej.")


def _portfel_unrealized(position: dict) -> Breakdown | None:
    shown = position["unrealized_pnl_pln"]
    if shown is None:
        return None
    components = (
        Component("wartość rynkowa", position["market_value_pln"]),
        Component("minus: koszt bazowy", -position["cost_basis_pln"]),
    )
    return close_sum(
        "portfel.unrealized", "Niezrealizowany P&L", shown, "zł",
        "wartość rynkowa − koszt bazowy", components)


def _portfel_total_return(position: dict, dividends: dict) -> Breakdown | None:
    shown = position["total_return_pct"]
    cost_basis_eur = position["cost_basis_eur"]
    unrealized_pnl_eur = position["unrealized_pnl_eur"]
    if shown is None or not cost_basis_eur or unrealized_pnl_eur is None:
        return None
    dividends_net_eur = dividends["dividends_net_eur"]
    components = (
        Component("niezrealizowany P&L", unrealized_pnl_eur, unit="EUR"),
        Component("dywidendy netto, całościowo", dividends_net_eur, unit="EUR"),
        Component("koszt bazowy", cost_basis_eur, unit="EUR"),
    )
    recomputed = round((unrealized_pnl_eur + dividends_net_eur) / cost_basis_eur * 100, 2)
    return close_formula(
        "portfel.total_return", "Całkowity zwrot", shown, "%",
        "(niezrealizowany P&L + dywidendy netto) / koszt bazowy", components,
        recomputed=recomputed)


def _portfel_dividends_net(conn: sqlite3.Connection, ctx: BreakdownCtx, cfg: dict,
                           dividends: dict, eurpln_rate: float | None) -> Breakdown | None:
    if eurpln_rate is None:
        return None
    shown = round(dividends["dividends_net_eur"] * eurpln_rate, 2)
    rows = conn.execute(
        "SELECT pay_date, gross_eur, withholding_pct, natural_key FROM dividends "
        "ORDER BY pay_date").fetchall()
    if not rows:
        return None
    components = []
    for r in rows:
        withholding_pct = r["withholding_pct"]
        if withholding_pct is None:
            withholding_pct = cfg["finnish_withholding_pct"]
        # Ta sama funkcja i te same argumenty co `sensors.py::dividends_values` —
        # gwarancja, że `shown` (dividends_net_eur × kurs) i suma tych
        # składników liczą DOKŁADNIE tę samą rzecz.
        net_eur = compute_dividend_tax(
            r["gross_eur"], withholding_pct, cfg["treaty_withholding_pct"],
            cfg["pl_capital_gains_tax_pct"])["net_received_eur"]
        # `dividends.natural_key` jest generowany ZAWSZE (`add_dividend`, także dla
        # wpisu ręcznego przez formularz web) — w odróżnieniu od `lots.source`, nie
        # da się stąd wprost odróżnić PDF od ręcznego wpisu, więc etykieta jest
        # neutralna; dopasowanie do `statement_index` (ten sam format klucza co
        # `statement_snapshot()`) doklejane, gdy jest.
        nk = r["natural_key"]
        div_sources = [Source("dividend", "Zarejestrowana dywidenda", ref=nk)]
        if nk and nk in ctx.statement_index:
            meta = ctx.statement_index[nk]
            div_sources.append(Source(
                "statement",
                f"Plik: {meta['filename']} (okres {meta['period_start']} – {meta['period_end']})",
                ref=meta["as_of_date"]))
        components.append(Component(
            f"dywidenda {r['pay_date']}", net_eur * eurpln_rate,
            detail=f"{r['gross_eur']:.2f} EUR brutto, {withholding_pct:g}% u źródła",
            sources=tuple(div_sources)))
    return close_sum(
        "portfel.dividends_net", "Dywidendy netto", shown, "zł",
        "Σ netto per dywidenda (wszystkie lata) × kurs bieżący EUR/PLN",
        tuple(components),
        note="Kurs bieżący, nie zamrożony kurs NBP D-1 z rozliczenia podatkowego "
             "(patrz PIT-38 sekcja G dla wartości rozliczeniowej).")


def _cash_broker_balance(ledger: dict) -> Breakdown | None:
    bb = ledger["broker_balance"]
    if bb is None:
        return None
    components = (
        Component(f"odczyt z {bb['as_of_date']}", bb["amount"],
                  detail=f"źródło: {bb['source']}"),
    )
    note = f"Wiek odczytu: {bb['age_days']} dni"
    if bb["is_stale"]:
        note += " — NIEAKTUALNE (starsze niż 30 dni)"
    return close_formula(
        "cash.broker_balance", "Saldo u brokera", bb["amount"], bb["currency"],
        "ostatni ręczny odczyt (historia append-only, nie z wyciągu — Computershare "
        "nie raportuje salda gotówkowego)", components, recomputed=bb["amount"], note=note)


def _cash_sale_proceeds(conn: sqlite3.Connection, year: int, ledger: dict
                        ) -> Breakdown | None:
    rows = conn.execute(
        "SELECT id, sale_date, revenue_pln, nbp_rate, nbp_rate_date FROM sales "
        "WHERE strftime('%Y', sale_date) = ?", (str(year),)).fetchall()
    if not rows:
        return None
    shown = ledger["sale_proceeds"]["by_year"].get(str(year), {}).get("pln", 0.0)
    components = tuple(
        Component(f"sprzedaż #{r['id']} z {r['sale_date']}", r["revenue_pln"],
                  detail=taxtrace.fx_derivation(
                      conn, r["sale_date"], r["nbp_rate"], r["nbp_rate_date"],
                      "sprzedaż")["explanation_pl"])
        for r in rows)
    return close_sum(
        "cash.sale_proceeds", f"Wpływy ze sprzedaży w {year}", shown, "PLN",
        "Σ revenue_pln (po opłacie, kurs NBP D-1) per sprzedaż", components)


def _cash_tax_outstanding(conn: sqlite3.Connection, year: int, ledger: dict
                          ) -> Breakdown | None:
    due_pln = ledger["tax_liability"]["due_pln"]
    payments = cashm.tax_payments_for_year(conn, year)
    components = (Component("podatek należny za rok (raport PIT-38)", due_pln),) + tuple(
        Component(f"wpłacone {p['paid_date']}", -p["amount_pln"], detail=p.get("notes"))
        for p in payments)
    return close_sum(
        "cash.tax_outstanding", "Podatek do zapłaty",
        ledger["tax_liability"]["outstanding_pln"], "PLN",
        "podatek należny − Σ wpłat", components)


def account_traces(conn: sqlite3.Connection, ctx: BreakdownCtx, cfg: dict, year: int,
                   *, price_eur: float | None, eurpln_rate: float | None,
                   position: dict, dividends: dict, buckets: dict, restricted: dict,
                   unvested: dict, ledger: dict
                   ) -> tuple[dict[str, Breakdown], list[BreakdownNotClosedError]]:
    """Wszystkie ślady dla `/` (Stan konta). Inputy — poza `conn`/`ctx`/rok —
    to liczby, które `views/account.py::account_view` i tak już policzył.
    Awarie domykania są ZBIERANE (drugi element krotki), nie tylko
    połykane — `integrity.py` powtarza to samo wywołanie na dzisiejszych
    danych i zgłasza rozjazd jako finding na karcie „Spójność danych"."""
    builders: dict[str, object] = {
        "portfel.total": lambda: _portfel_total(position, unvested, buckets),
        "portfel.free": lambda: _portfel_free(position, restricted, buckets),
        "portfel.restricted": lambda: _portfel_restricted(
            conn, ctx, restricted, price_eur, eurpln_rate),
        "portfel.locked": lambda: _portfel_locked(conn, buckets, price_eur, eurpln_rate),
        "portfel.cost_basis": lambda: _portfel_cost_basis(conn, ctx, cfg, position, eurpln_rate),
        "portfel.unrealized": lambda: _portfel_unrealized(position),
        "portfel.total_return": lambda: _portfel_total_return(position, dividends),
        "portfel.dividends_net": lambda: _portfel_dividends_net(
            conn, ctx, cfg, dividends, eurpln_rate),
        "cash.broker_balance": lambda: _cash_broker_balance(ledger),
        "cash.sale_proceeds": lambda: _cash_sale_proceeds(conn, year, ledger),
        "cash.tax_outstanding": lambda: _cash_tax_outstanding(conn, year, ledger),
    }
    traces: dict[str, Breakdown] = {}
    failures: list[BreakdownNotClosedError] = []
    for key, build in builders.items():
        try:
            trace = build()
        except BreakdownNotClosedError as e:
            failures.append(e)
            continue
        if trace is not None:
            traces[key] = trace
    return traces, failures
