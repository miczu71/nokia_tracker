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

from .tax import trace as taxtrace

_TOLERANCE = 0.01


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
    o kursie) — nie wchodzi do sumy w `close_sum`."""
    label: str
    value: float | None
    detail: str | None = None
    sources: tuple[Source, ...] = ()


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
