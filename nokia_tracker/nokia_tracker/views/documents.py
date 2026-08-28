"""Kompozycja danych dla dokumentów dowodowych — HTML/PDF do pobrania: pojedyncza
zrealizowana sprzedaż, symulacja, roczne dossier PIT-38 (E10, docs/PLAN_E10_dokumenty.md).

Zero nowej matematyki finansowej — czyste złożenie tego, co już liczą
`views/sales.py`, `views/withdrawal.py`, `tax/pit38.py`, `views/pit38.py`,
`tax/policy.py`. Ten moduł tylko SKŁADA `dict` gotowy do wyrenderowania przez
`exports/documents.py::render_html` — nie zna Jinja ani Flaska."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
from datetime import datetime
from urllib.parse import urlencode

from .. import __version__
from .. import breakdown as bd
from .. import db as dbm
from ..tax import dividends as taxdiv
from ..tax import pit38 as taxpit38
from ..tax import policy as taxpolicy
from .pit38 import waterfall
from .sales import sale_detail, sales_view
from .withdrawal import withdrawal_view

# Etap 5 (docs/PLAN_E10_dokumenty.md): powyżej tylu alokacji sprzedaży-lotu w
# jednym roku dossier NIE renderuje pełnego śladu FIFO per sprzedaż w
# załączniku — tylko tabelę zbiorczą (już zawsze widoczną) + jawną notkę.
# Pełny ślad per lot dla KAŻDEJ sprzedaży zostaje dostępny osobno pod
# `/sales/<id>/dokument.{html,pdf}` — strażnik ogranicza rozmiar JEDNEGO
# dokumentu, nie dostępność danych. 200 to konserwatywny próg: dzisiejsza
# produkcja ma dziesiątki alokacji na cały rok, nie setki — do zweryfikowania
# na realnym roku przy pierwszym dossier, które go faktycznie dotknie.
_DOSSIER_TRACE_ALLOCATION_LIMIT = 200


def _json_default(obj):
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    raise TypeError(f"Nie da się zserializować {type(obj)!r} do sumy kontrolnej dokumentu")


def _payload_digest(payload: dict) -> str:
    """Suma kontrolna DANYCH dokumentu (bez `meta`, bo `meta` niesie znacznik
    czasu generowania i samą sumę — hashowanie ich razem dałoby inny skrót
    za każdym razem, nawet dla identycznych liczb). 16 znaków hex (64 bity) —
    odtwarzalność, nie kryptograficzny dowód: każdy z aplikacją może
    przeliczyć ten sam skrót z tych samych danych, to NIE jest podpis."""
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, default=_json_default)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _trace_failures_out(failures: list) -> list[dict]:
    return [{"key": f.key, "shown": f.shown, "recomputed": f.recomputed} for f in failures]


def _meta(cfg: dict, *, kind: str, title: str, scope: str) -> dict:
    active_policy = cfg.get("cost_basis_policy", "own_only")
    return {
        "kind": kind,
        "title": title,
        "scope": scope,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "app_version": __version__,
        "schema_version": dbm.SCHEMA_VERSION,
        "active_policy": active_policy,
        "active_policy_legal_basis": taxpolicy.LEGAL_BASIS_PL.get(active_policy, ""),
        "tax_rate_pct": cfg.get("pl_capital_gains_tax_pct", 19.0),
    }


def _finalize(meta: dict, payload: dict) -> dict:
    meta["data_hash"] = _payload_digest(payload)
    return {"meta": meta, **payload}


def sale_document(conn: sqlite3.Connection, cfg: dict, sale_id: int) -> dict | None:
    """Dokument jednej zrealizowanej sprzedaży: `sale`, `detail`
    (`tax/trace.py::enrich_allocations`), `traces`/`trace_failures`
    (`breakdown.sale_traces`) — dokładnie to, co dziś pokazuje rejestr
    `/sales` dla jednej pozycji, plus prowenniencja per lot. `None`, gdy
    sprzedaż o tym id nie istnieje (usunięta/nigdy nie istniała)."""
    row = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
    if row is None:
        return None
    ctx = bd.build_ctx(conn)
    item = sale_detail(conn, cfg, row, ctx=ctx)
    meta = _meta(
        cfg, kind="sale", title=f"Zrealizowana sprzedaż #{sale_id}",
        scope=f"sprzedaż #{sale_id} z {row['sale_date']}")
    payload = {
        "sale": item["sale"],
        "detail": item["detail"],
        "traces": item["traces"],
        "trace_failures": _trace_failures_out(item["trace_failures"]),
    }
    return _finalize(meta, payload)


def _replay_query(params: dict) -> str:
    """Query string, jako LITERALNY TEKST (nie link — `url_for` jest
    bezużyteczny poza sesją ingressu i zabroniony przez sam Environment
    dokumentów), który odtwarza ten wynik pod `/wyplata` — jedyny nośnik
    odtwarzalności dla bezstanowej symulacji. Stan lotów w bazie może się do
    tego czasu zmienić (patrz zastrzeżenie w `doc_simulation.html`), więc
    „odtworzy" znaczy „poda te same wejścia", nie „da ten sam wynik"."""
    fields = {
        "direction": params["direction"],
        "wyplata_price": params["price_eur"],
        "wyplata_fee_pct": params["fee_pct"],
        "wyplata_date": params["sale_date"],
    }
    if params["direction"] == "target":
        fields["wyplata_target"] = params["target_net_pln"]
    else:
        fields["wyplata_qty"] = params["quantity"]
    return "?" + urlencode(fields)


def simulation_document(conn: sqlite3.Connection, cfg: dict, params: dict
                        ) -> tuple[dict | None, str | None]:
    """Dokument symulacji sprzedaży. `params` — już sparsowane wejścia
    identyczne z `/wyplata` (`web/routes_plan.py::_wyplata_params`):
    `direction`, `price_eur`, `fee_pct`, `sale_date`, i `target_net_pln`
    XOR `quantity` zależnie od kierunku. Symulacja jest bezstanowa — dokument
    zapisuje `params` dosłownie, żeby dało się odtworzyć wynik pod tym samym
    adresem `/wyplata` później (stan lotów w bazie może się do tego czasu
    zmienić — patrz zastrzeżenie w `doc_simulation.html`).

    Zwraca `(None, komunikat_błędu)` gdy `withdrawal_view` nie policzy wyniku
    (np. za mało lotów, cel nieosiągalny) — ten sam kontrakt co
    `withdrawal_view` sam w sobie."""
    result, error = withdrawal_view(
        conn, cfg, params["direction"], params["price_eur"], params["fee_pct"],
        params["sale_date"], target_net_pln=params.get("target_net_pln"),
        quantity=params.get("quantity"))
    if error:
        return None, error

    meta = _meta(
        cfg, kind="simulation", title="Symulacja sprzedaży",
        scope=f"symulacja na {params['sale_date']}")
    payload = {"params": params, "result": result, "replay_query": _replay_query(params)}
    return _finalize(meta, payload), None


def year_dossier(conn: sqlite3.Connection, cfg: dict, year: int) -> dict:
    """Pełne dossier PIT-38 za `year`: raport roczny (`tax/pit38.py::
    annual_report` — poz. C w trzech politykach, Sekcja G, PIT/ZG, strata z
    lat ubiegłych), waterfall Poz. C, wyjaśnienie nadpisań zgłoszonej
    wartości, i załącznik — rejestr sprzedaży ze śladem per lot
    (`views/sales.py::sales_view(..., with_traces=True)`). Pusty rok zwraca
    wyzerowane sekcje, nie wyjątek (ten sam kontrakt co `annual_report`)."""
    taxdiv.backfill_pl_tax_due(conn, cfg)
    report = taxpit38.annual_report(conn, cfg, year)
    wf = waterfall(report, cfg)
    override_summary = taxpolicy.reported_override_summary(conn, cfg, year=year)
    sales = sales_view(conn, cfg, year, with_traces=True)

    allocation_count = sum(len(item["detail"]["allocations"]) for item in sales["sales"])
    include_sale_traces = allocation_count <= _DOSSIER_TRACE_ALLOCATION_LIMIT

    meta = _meta(
        cfg, kind="dossier", title=f"Dokumentacja PIT-38 — {year}",
        scope=f"rok podatkowy {year}")
    payload = {
        "year": year,
        "report": report,
        "waterfall": wf,
        "override_summary": override_summary,
        "sales": [
            {"sale": item["sale"], "detail": item["detail"],
             "traces": item["traces"] if include_sale_traces else {},
             "trace_failures": (
                 _trace_failures_out(item["trace_failures"]) if include_sale_traces else [])}
            for item in sales["sales"]
        ],
        "sales_totals": sales["totals"],
        "include_sale_traces": include_sale_traces,
        "allocation_count": allocation_count,
    }
    return _finalize(meta, payload)
