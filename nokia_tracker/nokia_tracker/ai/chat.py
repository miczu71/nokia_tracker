"""Rdzeń asystenta czatu (krok 29): rozpoznanie intencji (AI #1) ->
istniejący silnik Pythona (HANDLERS) -> narracja PL (AI #2, opcjonalna).

`ai/provider.py::analyze()` obsługuje wyłącznie ustrukturyzowany JSON wg
schematu — nie ma pętli tool-calling i nie dorabiamy jej. Stąd trójstopniowa
architektura zamiast jednego wywołania z narzędziami: AI rozpoznaje CO
użytkownik chce wiedzieć, Python to LICZY (zero nowej matematyki — każdy
handler woła istniejący, już przetestowany silnik z tax/*.py, advisor.py,
sensors.py), a druga AI tylko UBIERA policzone liczby w naturalny język
polski. Liczby renderuje Jinja z `lines`, nie tekst modelu — halucynacja
kwoty jest strukturalnie niemożliwa.

Kontrakt wyniku handlera jest CELOWO tym samym kształtem co /api/preview/*
w web.py (`{"ok", "title", "lines": [{"label","value","unit","emphasis"}],
"detail_url", "error"}`), więc `NT.initFormPreview()` w static/app.js
renderuje odpowiedź czatu bez nowego kodu JS.

HANDLERS są WYŁĄCZNIE do odczytu — żaden nie zapisuje do bazy (jedyny
zapis w tym module jest w `_log()`, do `chat_log`, poza HANDLERS). Patrz
test_ai_chat.py::test_all_handlers_are_read_only."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Callable

from .. import advisor as advisorm
from .. import db as dbm
from .. import fx, portfolio as portfoliom, quotes, sensors
from ..tax import grants as grantsm
from ..tax import losses as taxlosses
from ..tax import lots as taxlots
from ..tax import pit38 as taxpit38
from ..tax import policy as taxpolicy
from ..tax import whatif as taxwhatif
from . import prompts, provider
from .errors import AIProviderError

logger = logging.getLogger(__name__)

# Duplikat _PRIMARY_SYMBOL z web.py/main.py — te dwa moduły już duplikują tę
# samą stałą między sobą (main.py backfill vs web.py routy), więc trzecia
# kopia tutaj jest zgodna z istniejącym (niedoskonałym, ale ustalonym)
# wzorcem, nie nowym antywzorcem. Importowanie `_ids()` z web.py stworzyłoby
# cykl (web.py importuje ten moduł dla trasy /asystent w kolejnym commicie).
_PRIMARY_SYMBOL = "NOKIA.HE"
_OMXH25_SYMBOL = "^OMXH25"

_MAX_TOKENS = 1500
_HISTORY_KEEP = 200


# --- kontrakt wyniku handlera ---

def _line(label: str, value, unit: str = "", emphasis: bool = False) -> dict:
    """`value=None` -> '—' zamiast surowego None docierającego do JS/Jinja
    (ten sam None-guard co `pf_money()`/`stat()` gdzie indziej w aplikacji —
    krok 23 nauczył, że jednostka obok None-a jest tym samym błędem co sam None)."""
    if value is None:
        return {"label": label, "value": "—", "unit": "", "emphasis": emphasis}
    return {"label": label, "value": value, "unit": unit, "emphasis": emphasis}


def _ok(title: str, lines: list[dict], detail_url: str | None) -> dict:
    return {"ok": True, "title": title, "lines": lines, "detail_url": detail_url, "error": None}


def _fail(title: str, error: str, detail_url: str | None = None) -> dict:
    return {"ok": False, "title": title, "lines": [], "detail_url": detail_url, "error": error}


def _try_engine(fn: Callable, *args, **kwargs):
    """Woła silnik podatkowy, łapie te same dwa wyjątki co `/api/preview/*`
    (`InsufficientLotsError`/`CostBasisMissingError`) — uczciwa porażka
    zamiast zmyślonej liczby. Zwraca (wynik, None) albo (None, komunikat)."""
    try:
        return fn(*args, **kwargs), None
    except (taxlots.InsufficientLotsError, taxlots.CostBasisMissingError) as exc:
        return None, str(exc)


def _positive_qty(params: dict) -> float | None:
    q = params.get("quantity")
    if q is None:
        return None
    try:
        q = float(q)
    except (TypeError, ValueError):
        return None
    return q if q > taxlots._EPS else None


def _resolve_year(params: dict, cfg: dict, today: str) -> int:
    y = params.get("year")
    if y:
        try:
            return int(y)
        except (TypeError, ValueError):
            pass
    tax_year = cfg.get("tax_year") or 0
    return int(tax_year) if tax_year else int(today[:4])


def _market(conn: sqlite3.Connection) -> tuple[float | None, float | None]:
    """(price_eur, eurpln_rate) na dziś — ten sam wzorzec co
    web.py::dashboard/plan_get (`_ids()` + `quotes.latest_quote`),
    zduplikowany celowo (patrz nagłówek modułu)."""
    primary_id = quotes.ensure_instrument(conn, _PRIMARY_SYMBOL, "Nokia Oyj", "EUR", "primary")
    eurpln_id = quotes.ensure_instrument(conn, fx.EURPLN_SYMBOL, "EUR/PLN", "PLN", "fx")
    price_row = quotes.latest_quote(conn, primary_id, granularity="daily")
    eurpln_row = quotes.latest_quote(conn, eurpln_id, granularity="daily")
    return (
        price_row["close"] if price_row else None,
        eurpln_row["close"] if eurpln_row else None,
    )


# --- handlery: 11 intencji z roadmapy/rozszerzeń, każda deleguje do istniejącego silnika ---

def _h_podatek_ze_sprzedazy(conn, cfg, params, ctx):
    title = "Podatek ze sprzedaży"
    quantity = _positive_qty(params)
    if quantity is None:
        return _fail(title, "Podaj dodatnią liczbę akcji do sprzedania.")
    price_eur = params.get("price_eur") or ctx["price_eur"]
    if not price_eur:
        return _fail(title, "Brak aktualnej ceny akcji — spróbuj ponownie później.")
    result, err = _try_engine(taxwhatif.simulate_sale, conn, cfg, quantity, price_eur,
                              sale_date=ctx["today"])
    if err:
        return _fail(title, err, "/lots")
    pol = result["policies"][result["active_policy"]]
    lines = [
        _line("Przychód", result["revenue_pln"], "PLN"),
        _line("Dochód", pol["income_pln"], "PLN"),
        _line("Podatek", pol["tax_pln"], "PLN"),
        _line("Na rękę", result["net_proceeds_pln"], "PLN", emphasis=True),
    ]
    return _ok(title, lines, "/lots")


def _h_ile_moge_sprzedac(conn, cfg, params, ctx):
    title = "Ile mogę sprzedać"
    open_rows = taxlots.open_lots(conn)
    total_qty = sum(r["qty_remaining"] for r in open_rows)
    restricted = grantsm.restricted_own_summary(
        conn, ctx["price_eur"], ctx["eurpln_rate"], ctx["today"])
    free_qty = total_qty - restricted["restricted_qty"]
    lines = [
        _line("Wszystkie posiadane", round(total_qty, 4), "szt."),
        _line("Objęte ograniczeniem zbycia", round(restricted["restricted_qty"], 4), "szt."),
        _line("Wolne bez utraty dopasowania", round(free_qty, 4), "szt.", emphasis=True),
    ]
    return _ok(title, lines, "/plan")


def _h_kiedy_vesting(conn, cfg, params, ctx):
    title = "Harmonogram vestingu"
    timeline = grantsm.vesting_timeline(conn, ctx["price_eur"], ctx["eurpln_rate"], ctx["today"])
    if not timeline["tranches"]:
        return _ok(title, [_line("Oczekujące transze", 0, "szt.")], "/plan")
    next_t = timeline["tranches"][0]
    lines = [
        _line("Najbliższa transza", round(next_t["quantity"], 4), "szt.", emphasis=True),
        _line("Data dostępności", next_t["effective_date"]),
        _line("W tym kwartale", round(timeline["buckets"]["this_quarter"]["qty"], 4), "szt."),
    ]
    if timeline["overdue"]["count"]:
        lines.append(_line("Zaległe (do sprawdzenia)", round(timeline["overdue"]["qty"], 4), "szt."))
    return _ok(title, lines, "/plan")


def _h_ile_zarobilem(conn, cfg, params, ctx):
    title = "Ile zarobiłem"
    pos = portfoliom.position_values_auto(conn, cfg, ctx["price_eur"], ctx["eurpln_rate"])
    year = _resolve_year(params, cfg, ctx["today"])
    active_policy = cfg.get("cost_basis_policy", "own_only")
    realized = taxpolicy.compute_all_policies(conn, cfg, year=year)[active_policy]
    lines = [
        _line("Wartość rynkowa", pos["market_value_pln"], "PLN"),
        _line("Niezrealizowany zysk/strata", pos["unrealized_pnl_pln"], "PLN", emphasis=True),
        _line("Niezrealizowany zysk/strata", pos["unrealized_pnl_pct"], "%"),
        _line(f"Zrealizowany dochód {year}", realized["income_pln"], "PLN"),
    ]
    return _ok(title, lines, "/wyniki")


def _h_dywidendy_w_roku(conn, cfg, params, ctx):
    year = _resolve_year(params, cfg, ctx["today"])
    title = f"Dywidendy {year}"
    section_g = taxpit38.annual_report(conn, cfg, year)["section_g"]
    lines = [
        _line("Liczba wypłat", section_g["dividend_count"], "szt."),
        _line("Brutto", section_g["gross_pln"], "PLN"),
        _line("Podatek u źródła (Finlandia)", section_g["withholding_paid_pln"], "PLN"),
        _line("Dopłata w Polsce", section_g["pl_tax_due_pln"], "PLN", emphasis=True),
    ]
    return _ok(title, lines, f"/dividends?year={year}")


def _h_koszt_sprzedazy_teraz(conn, cfg, params, ctx):
    title = "Koszt sprzedaży teraz"
    quantity = _positive_qty(params)
    if quantity is None:
        summary = advisorm.forfeit_summary(conn, ctx["price_eur"], ctx["eurpln_rate"], ctx["today"])
        lines = [
            _line("Ograniczone akcje", round(summary["restricted_qty"], 4), "szt."),
            _line("Przepadek przy sprzedaży wszystkich", round(summary["forfeit_qty"], 4), "szt."),
            _line("Wartość przepadku", summary["forfeit_value_pln"], "PLN", emphasis=True),
        ]
        return _ok(title, lines, "/plan")
    result, err = _try_engine(advisorm.forfeit_for_quantity, conn, quantity,
                              ctx["price_eur"], ctx["eurpln_rate"], ctx["today"])
    if err:
        return _fail(title, err, "/plan")
    lines = [
        _line("Sprzedawana ilość", result["sell_qty"], "szt."),
        _line("Przepadek dopasowania", round(result["forfeit_qty"], 4), "szt."),
        _line("Wartość przepadku", result["forfeit_value_pln"], "PLN", emphasis=True),
    ]
    return _ok(title, lines, "/plan")


def _h_porownanie_z_benchmarkiem(conn, cfg, params, ctx):
    title = "Porównanie z benchmarkiem"
    omxh25_id = quotes.ensure_instrument(conn, _OMXH25_SYMBOL, "OMX Helsinki 25", "EUR", "benchmark")
    result = sensors.results_values(conn, ctx["price_eur"], ctx["eurpln_rate"], omxh25_id)
    lines = [
        _line("Twój zwrot (XIRR)", result["xirr_own_pct"], "%"),
        _line("Zwrot ważony czasem (TWR)", result["twr_pct"], "%"),
        _line("Efekt walutowy", result["fx_effect_pln"], "PLN"),
        _line("Ile miałbyś, trzymając OMXH25",
             result["benchmark_omxh25_counterfactual_pln"], "PLN", emphasis=True),
    ]
    return _ok(title, lines, "/wyniki")


def _h_pit_za_rok(conn, cfg, params, ctx):
    year = _resolve_year(params, cfg, ctx["today"])
    title = f"PIT-38 za {year}"
    report = taxpit38.annual_report(conn, cfg, year)
    active_policy = cfg.get("cost_basis_policy", "own_only")
    pol = report["policies"][active_policy]
    lc = report["loss_carryforward"]
    lines = [
        _line("Dochód (poz. C)", pol["income_pln"], "PLN"),
        _line("Podatek przed odliczeniem strat", lc["tax_before_loss_pln"], "PLN"),
        _line("Odliczona strata", lc["total_used_this_year_pln"], "PLN"),
        _line("Do zapłaty razem", report["total_due_pln"], "PLN", emphasis=True),
    ]
    return _ok(title, lines, f"/pit38?year={year}")


def _h_straty_z_lat_ubieglych(conn, cfg, params, ctx):
    year = _resolve_year(params, cfg, ctx["today"])
    title = f"Straty z lat ubiegłych ({year})"
    info = taxlosses.available_for_year(conn, cfg, year)
    lines = [
        _line("Dostępna strata do odliczenia", info["total_remaining_pln"], "PLN", emphasis=True),
        _line("Już odliczona w tym roku", info["total_used_this_year_pln"], "PLN"),
        _line("Liczba źródłowych lat straty", len(info["items"]), "szt."),
    ]
    return _ok(title, lines, f"/pit38/kreator?year={year}")


def _h_koncentracja_majatku(conn, cfg, params, ctx):
    title = "Koncentracja majątku"
    overview = advisorm.overview(conn, cfg, ctx["price_eur"], ctx["eurpln_rate"], ctx["today"])
    conc = overview["concentration"]
    if not conc["configured"]:
        return _fail(
            title,
            "Uzupełnij pole „Reszta majątku poza akcjami pracodawcy” w Ustawieniach, "
            "żeby policzyć koncentrację.", "/settings")
    lines = [
        _line("Wartość akcji pracodawcy", conc["employer_value_pln"], "PLN"),
        _line("Udział w majątku", conc["pct"], "%", emphasis=True),
        _line("Próg ostrzeżenia", conc["threshold_pct"], "%"),
    ]
    return _ok(title, lines, "/plan")


def _h_kiedy_sprzedac(conn, cfg, params, ctx):
    title = "Kiedy sprzedać"
    quantity = _positive_qty(params)
    if quantity is None:
        return _fail(title, "Podaj dodatnią liczbę akcji.")
    price_eur = params.get("price_eur") or ctx["price_eur"]
    if not price_eur:
        return _fail(title, "Brak aktualnej ceny akcji — spróbuj ponownie później.")
    result = advisorm.optimize_sale_timing(
        conn, cfg, quantity, price_eur, ctx["eurpln_rate"], ctx["today"])
    if result["today"] is None or result["jan2_next_year"] is None:
        return _fail(title, "Brak pokrycia lotami dla porównania dwóch dat.", "/plan")
    lines = [
        _line("Podatek, gdybyś sprzedał dziś",
             result["today"]["tax_with_max_loss_pln"], "PLN"),
        _line("Podatek 2 stycznia przyszłego roku",
             result["jan2_next_year"]["tax_with_max_loss_pln"], "PLN"),
        _line("Różnica netto (podatek + przepadek)",
             result["delta_total_pln"], "PLN", emphasis=True),
    ]
    return _ok(title, lines, "/plan")


_INNE_MESSAGE = (
    "Nie rozpoznałem pytania jako jednego z tematów, które umiem policzyć — podatek "
    "ze sprzedaży, ile mogę sprzedać, vesting, ile zarobiłem, dywidendy, PIT-38, straty "
    "z lat ubiegłych, koncentracja majątku, porównanie z benchmarkiem, moment sprzedaży. "
    "Spróbuj przeformułować pytanie albo sprawdź stronę w nawigacji."
)


def _h_inne(conn, cfg, params, ctx):
    return _ok(_INNE_MESSAGE, [], None)


HANDLERS: dict[str, Callable] = {
    "podatek_ze_sprzedazy": _h_podatek_ze_sprzedazy,
    "ile_moge_sprzedac": _h_ile_moge_sprzedac,
    "kiedy_vesting": _h_kiedy_vesting,
    "ile_zarobilem": _h_ile_zarobilem,
    "dywidendy_w_roku": _h_dywidendy_w_roku,
    "koszt_sprzedazy_teraz": _h_koszt_sprzedazy_teraz,
    "porownanie_z_benchmarkiem": _h_porownanie_z_benchmarkiem,
    "pit_za_rok": _h_pit_za_rok,
    "straty_z_lat_ubieglych": _h_straty_z_lat_ubieglych,
    "koncentracja_majatku": _h_koncentracja_majatku,
    "kiedy_sprzedac": _h_kiedy_sprzedac,
}


# --- orkiestracja: AI #1 -> handler -> AI #2 opcjonalna -> log ---

def recognize_intent(conn: sqlite3.Connection, cfg: dict, question: str, ctx: dict
                     ) -> tuple[str, dict]:
    prompt = prompts.chat_intent_prompt(question, {
        "today": ctx["today"], "years_with_data": ctx["years_with_data"],
        "cost_basis_policy": cfg.get("cost_basis_policy", "own_only"),
    })
    try:
        parsed = provider.analyze(conn, cfg, "chat_intent", prompt,
                                  prompts.CHAT_INTENT_SCHEMA, _MAX_TOKENS)
    except AIProviderError as exc:
        logger.info("chat: rozpoznanie intencji nieudane (%s), degraduję do 'inne'", exc)
        return "inne", {}
    intent = parsed.get("intent")
    if intent not in HANDLERS:
        intent = "inne"
    raw_params = parsed.get("params") or {}
    params = {k: v for k, v in raw_params.items() if v is not None}
    return intent, params


def narrate(conn: sqlite3.Connection, cfg: dict, question: str, result: dict) -> str | None:
    if not result["lines"]:
        return None
    prompt = prompts.chat_narration_prompt(question, result["title"], result["lines"])
    try:
        parsed = provider.analyze(conn, cfg, "chat_narration", prompt,
                                  prompts.CHAT_NARRATION_SCHEMA, _MAX_TOKENS)
    except AIProviderError as exc:
        logger.info("chat: narracja nieudana (%s), pokazuję zdanie deterministyczne", exc)
        return None
    return parsed.get("answer_pl")


def fallback_sentence(result: dict) -> str:
    if not result["ok"]:
        return result.get("error") or "Nie udało się policzyć odpowiedzi."
    if not result["lines"]:
        return result["title"]
    parts = [f"{l['label']}: {l['value']}{(' ' + l['unit']) if l.get('unit') else ''}"
            for l in result["lines"]]
    return result["title"] + " — " + ", ".join(parts) + "."


def _log(conn: sqlite3.Connection, question: str, intent: str, params: dict,
         result: dict, answer_pl: str) -> None:
    with dbm.WRITE_LOCK:
        conn.execute(
            "INSERT INTO chat_log (question, intent, params_json, result_json, answer_pl, "
            "provider, ok, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (question, intent, json.dumps(params), json.dumps(result), answer_pl,
             provider.active_provider(), 1 if result["ok"] else 0, result.get("error")))
        conn.execute(
            "DELETE FROM chat_log WHERE id NOT IN "
            "(SELECT id FROM chat_log ORDER BY id DESC LIMIT ?)", (_HISTORY_KEEP,))
        conn.commit()


def ask(conn: sqlite3.Connection, cfg: dict, question: str) -> dict:
    """Punkt wejścia dla web.py (/asystent). `cfg`: settings.get_settings(conn)
    + klucze API z ENV (web.py::_ai_keys)."""
    question = (question or "").strip()
    if not question:
        return {"ok": False, "intent": None, "params": {}, "title": "Puste pytanie",
                "lines": [], "detail_url": None,
                "error": "Napisz pytanie, np. „ile zapłacę podatku sprzedając 200 akcji?”.",
                "answer_pl": "Napisz pytanie, np. „ile zapłacę podatku sprzedając 200 akcji?”."}

    today = datetime.now().strftime("%Y-%m-%d")
    price_eur, eurpln_rate = _market(conn)
    ctx = {
        "today": today,
        "years_with_data": taxpit38.years_with_data(conn),
        "price_eur": price_eur,
        "eurpln_rate": eurpln_rate,
    }

    intent, params = recognize_intent(conn, cfg, question, ctx)
    handler = HANDLERS.get(intent, _h_inne)
    result = handler(conn, cfg, params, ctx)

    answer_pl = None
    if result["ok"] and cfg.get("ai_chat_narration_enabled", 1):
        answer_pl = narrate(conn, cfg, question, result)
    if answer_pl is None:
        answer_pl = fallback_sentence(result)

    _log(conn, question, intent, params, result, answer_pl)

    return {
        "ok": result["ok"], "intent": intent, "params": params,
        "title": result["title"], "lines": result["lines"],
        "detail_url": result.get("detail_url"), "error": result.get("error"),
        "answer_pl": answer_pl,
    }


def history(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT id, created_at, question, intent, answer_pl, ok, error "
        "FROM chat_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
