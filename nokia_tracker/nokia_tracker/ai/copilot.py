"""Asystent proaktywny / co-pilot (krok 33, docs/PLAN_KROK_33_copilot.md).

Jeden dzienny scheduler job (`main.py::copilot_job`) spina trzy już policzone
warunki (zbliżający się vesting, niewykorzystana strata z lat ubiegłych + zysk
w bieżącym roku, zbliżająca się dywidenda) w JEDNĄ złączoną wiadomość push,
narrowaną przez AI #2 — ten sam kontrakt co `ai/chat.py`: liczby liczy silnik,
AI tylko ubiera je w język. W odróżnieniu od czatu, gdzie `answer_pl` siedzi
obok renderowanej przez Jinja tabelki `lines` (halucynacja liczby jest
widocznie sprzeczna z tabelką), push to sam tekst — więc wiadomość ZAWSZE
zawiera deterministyczne zdania silnika, niezależnie czy narracja AI się
powiodła (`build_message`).

Anti-spam per warunek przez `alerts_log` — `alerts.allow_fire`/`log_fired`
(publiczne od tego kroku właśnie dla tego reużycia, ten sam mechanizm co
`alert_min_interval_minutes` z kroku 8).

Vesting NIE używa `cfg["vest_reminder_days"]` (to już konsumuje
`main.py::check_vest_reminders` o 06:30 — reużycie tego samego progu dałoby
DWA powiadomienia o tej samej transzy tego samego ranka) ani
`due_for_reminder()`/`mark_reminder_sent()` (też już konsumowane przez ten
sam job — drugie wywołanie zakłóciłoby jego kolejkę). Zamiast tego:
`unvested_summary()` (read-only) + własny `_LOOKAHEAD_DAYS`.

WAŻNE: ten moduł NIE bierze `dbm.WRITE_LOCK` — `WRITE_LOCK` to zwykły
`threading.Lock()` (nie reentrant), a `main.py::copilot_job` już go trzyma
tak jak każdy inny job schedulera. Wzięcie go tutaj drugi raz byłoby
gwarantowanym deadlockiem w produkcji."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from .. import dashboard_insights, dividend_outlook as outlookm, format as fmt, ha_client
from ..alerts import allow_fire, last_fired_at, log_fired
from ..tax import grants as grantsm
from ..tax import losses as taxlosses
from ..tax import policy as taxpolicy
from . import chat, prompts, provider
from .errors import AIProviderError

logger = logging.getLogger(__name__)

# Wspólny próg "zbliża się" dla obu warunków datowych (vesting/dywidenda) —
# przy 30-dniowym cooldownie (copilot_min_interval_days) daje dokładnie
# jeden nudge na zdarzenie, patrz docstring modułu i PLAN_KROK_33 §0.3/§0.4.
_LOOKAHEAD_DAYS = 30
_MAX_TOKENS = 1500

KIND_VESTING = "copilot_vesting"
KIND_TAX_LOSS = "copilot_tax_loss"
KIND_DIVIDEND = "copilot_dividend"

_CERTAINTY_LABELS = {
    "confirmed": "potwierdzona", "announced": "zapowiedziana", "estimated": "szacowana",
}


def _context(conn: sqlite3.Connection, today: str | None = None) -> dict:
    today = today or datetime.now().strftime("%Y-%m-%d")
    price_eur, eurpln_rate = chat._market(conn)
    return {"today": today, "year": int(today[:4]),
            "price_eur": price_eur, "eurpln_rate": eurpln_rate}


def _days_until(date_str: str, today: str) -> int:
    return (datetime.strptime(date_str, "%Y-%m-%d")
            - datetime.strptime(today, "%Y-%m-%d")).days


# --- trzy warunki: każdy None (cisza) albo {"kind","title","sentence","lines"} ---

def check_vesting(conn: sqlite3.Connection, cfg: dict, ctx: dict) -> dict | None:
    summary = grantsm.unvested_summary(conn, ctx["price_eur"], ctx["eurpln_rate"], ctx["today"])
    d, q = summary.get("next_vest_date"), summary.get("next_vest_qty")
    if d is None or q is None:
        return None
    days = _days_until(d, ctx["today"])
    if days > _LOOKAHEAD_DAYS:
        return None
    # Reuse KODU (nie kopiuj-wklej) zdania z dashboard_insights.py, z resztą
    # sygnałów wyzerowaną — treść dashboardu i push nigdy się nie rozjadą.
    sentence = dashboard_insights.today_worth_knowing(
        change_pct_day=None, next_vest_date=d, next_vest_qty=q,
        loss_available_pln=0.0, income_pln_this_year=0.0, today=ctx["today"])[0]
    lines = [
        chat._line("Data dostępności", d),
        chat._line("Ilość", round(q, 4), "szt.", emphasis=True),
        chat._line("Za ile dni", days, "dni"),
    ]
    return {"kind": KIND_VESTING, "title": "Zbliżający się vesting",
            "sentence": sentence, "lines": lines}


def check_tax_loss(conn: sqlite3.Connection, cfg: dict, ctx: dict) -> dict | None:
    loss = taxlosses.available_for_year(conn, cfg, ctx["year"])["total_remaining_pln"]
    policy_name = cfg.get("cost_basis_policy", "own_only")
    income = taxpolicy.compute_all_policies(conn, cfg, ctx["year"])[policy_name]["income_pln"]
    if not (loss > 0 and income > 0):
        return None
    sentence = dashboard_insights.today_worth_knowing(
        change_pct_day=None, next_vest_date=None, next_vest_qty=None,
        loss_available_pln=loss, income_pln_this_year=income, today=ctx["today"])[0]
    lines = [
        chat._line("Zysk w tym roku", round(income, 2), "PLN"),
        chat._line("Dostępna strata", round(loss, 2), "PLN", emphasis=True),
        chat._line("Rok", ctx["year"]),
    ]
    return {"kind": KIND_TAX_LOSS, "title": "Niewykorzystana strata z lat ubiegłych",
            "sentence": sentence, "lines": lines}


def check_dividend(conn: sqlite3.Connection, cfg: dict, ctx: dict) -> dict | None:
    # years_ahead=1 wystarcza (patrzymy tylko _LOOKAHEAD_DAYS w przód) i
    # połowicznie odciąża per_share_history()/entitled_base() w calendar().
    calendar = outlookm.calendar(conn, cfg, years_ahead=1,
                                 eurpln_rate=ctx["eurpln_rate"], today=ctx["today"])
    ev = calendar.get("next_event")
    if ev is None:
        return None
    days = _days_until(ev["record_date"], ctx["today"])
    if days > _LOOKAHEAD_DAYS:
        return None
    label = _CERTAINTY_LABELS.get(ev["certainty"], ev["certainty"])
    if ev.get("net_in_hand_pln") is not None:
        amount = round(ev["net_in_hand_pln"], 2)
        sentence = (
            f"Dywidenda ({label}): dzień ustalenia praw {ev['record_date']} "
            f"({days} dni), ok. {fmt.money(amount, decimals=2)} zł na rękę.")
        amount_line = chat._line("Na rękę", amount, "PLN", emphasis=True)
    else:
        amount = round(ev["net_in_hand_eur"], 2)
        sentence = (
            f"Dywidenda ({label}): dzień ustalenia praw {ev['record_date']} "
            f"({days} dni), ok. {fmt.money(amount, decimals=2)} EUR na rękę "
            "(brak kursu EUR/PLN).")
        amount_line = chat._line("Na rękę", amount, "EUR", emphasis=True)
    lines = [
        chat._line("Dzień ustalenia praw", ev["record_date"]),
        chat._line("Za ile dni", days, "dni"),
        amount_line,
        chat._line("Pewność", label),
    ]
    return {"kind": KIND_DIVIDEND, "title": "Zbliżająca się dywidenda",
            "sentence": sentence, "lines": lines}


CHECKS = (check_vesting, check_tax_loss, check_dividend)


def detect(conn: sqlite3.Connection, cfg: dict, today: str | None = None) -> list[dict]:
    """Stała kolejność (vesting, strata, dywidenda) — deterministyczne
    wiadomości/testy. Brak gatingu anti-spamu, brak zapisu — czysty odczyt."""
    ctx = _context(conn, today)
    return [r for r in (check(conn, cfg, ctx) for check in CHECKS) if r is not None]


def narrate(conn: sqlite3.Connection, cfg: dict, conditions: list[dict]) -> str | None:
    prompt = prompts.copilot_narration_prompt(conditions)
    try:
        parsed = provider.analyze(conn, cfg, "copilot_narration", prompt,
                                  prompts.CHAT_NARRATION_SCHEMA, _MAX_TOKENS)
    except AIProviderError as exc:
        logger.info("copilot: narracja nieudana (%s), wysyłam zdania deterministyczne", exc)
        return None
    return parsed.get("answer_pl")


def build_message(conditions: list[dict], narration: str | None) -> str:
    """Narracja (jeśli jest) + ZAWSZE deterministyczne zdania silnika —
    inaczej niż w czacie, push to sam tekst, zdanie AI nie ma obok siebie
    renderowanej tabelki, więc nie może być jedyną treścią wiadomości."""
    sentences = [c["sentence"] for c in conditions]
    if len(sentences) > 1:
        body = "\n".join(f"• {s}" for s in sentences)
    else:
        body = sentences[0] if sentences else ""
    if narration:
        return f"{narration}\n\n{body}"
    return body


def _title(conditions: list[dict]) -> str:
    if len(conditions) == 1:
        return f"Nokia — {conditions[0]['title']}"
    return f"Nokia — co-pilot ({len(conditions)} sprawy)"


def run(conn: sqlite3.Connection, cfg: dict, today: str | None = None) -> dict:
    """Orkiestracja: detekcja -> gate anty-spamu per warunek -> narracja
    opcjonalna -> JEDEN złączony `ha_client.notify` -> znacznik `fired_at` per
    uwzględniony warunek (tylko po udanej wysyłce — HA offline kosztuje dzień
    opóźnienia, nie utratę nudge'a, wzorem `notifier.notify_new_news`)."""
    base = {"sent": False, "detected": [], "kinds": [], "title": None,
            "message": None, "narrated": False}

    if not cfg.get("copilot_enabled", 0):
        return {**base, "reason": "disabled"}
    notify_service = cfg.get("notify_service", "")
    if not notify_service:
        return {**base, "reason": "no_notify_service"}

    conditions = detect(conn, cfg, today)
    base["detected"] = [c["kind"] for c in conditions]
    if not conditions:
        return {**base, "reason": "no_conditions"}

    min_interval_minutes = int(cfg.get("copilot_min_interval_days", 30)) * 1440
    allowed = [c for c in conditions if allow_fire(conn, c["kind"], min_interval_minutes)]
    if not allowed:
        return {**base, "reason": "cooldown"}

    narration = None
    if cfg.get("ai_chat_narration_enabled", 1):
        narration = narrate(conn, cfg, allowed)

    title = _title(allowed)
    message = build_message(allowed, narration)
    kinds = [c["kind"] for c in allowed]

    # Bez url/clickAction — ścieżka ingressu jest znana dopiero per-request
    # w web.py, tutaj (job schedulera) nie ma jej skąd wziąć.
    ok = ha_client.notify(
        notify_service.replace(".", "/", 1), title, message,
        {"tag": "nokia-copilot", "group": "nokia-copilot", "channel": "Nokia"})
    if not ok:
        logger.warning("copilot: wysyłka nieudana, nic nie oznaczam jako wysłane")
        return {**base, "kinds": kinds, "title": title, "message": message,
                "reason": "notify_failed"}

    for c in allowed:
        log_fired(conn, c["kind"], "info", c["title"], c["sentence"],
                  {"lines": c["lines"], "bundled_with": [k for k in kinds if k != c["kind"]]})

    return {"sent": True, "reason": "sent", "detected": base["detected"], "kinds": kinds,
            "title": title, "message": message, "narrated": narration is not None}


def preview(conn: sqlite3.Connection, cfg: dict, today: str | None = None) -> dict:
    """Podgląd BEZ skutków ubocznych: bez AI, bez `ha_client.notify`, bez
    zapisu do `alerts_log` (cooldown nietknięty) — bezpieczny do wołania na
    produkcji do weryfikacji (web.py::preview_copilot)."""
    today_resolved = today or datetime.now().strftime("%Y-%m-%d")
    datetime.strptime(today_resolved, "%Y-%m-%d")  # walidacja formatu; ValueError -> caller

    conditions = detect(conn, cfg, today_resolved)
    min_interval_minutes = int(cfg.get("copilot_min_interval_days", 30)) * 1440

    enriched = []
    allowed = []
    for c in conditions:
        is_allowed = allow_fire(conn, c["kind"], min_interval_minutes)
        if is_allowed:
            allowed.append(c)
        enriched.append({
            "kind": c["kind"], "title": c["title"], "sentence": c["sentence"],
            "allowed": is_allowed, "last_fired_at": last_fired_at(conn, c["kind"]),
            "lines": c["lines"],
        })

    would_send = bool(cfg.get("copilot_enabled", 0)) and bool(cfg.get("notify_service", "")) \
        and bool(allowed)

    return {
        "ok": True,
        "enabled": bool(cfg.get("copilot_enabled", 0)),
        "notify_service": cfg.get("notify_service", ""),
        "min_interval_days": int(cfg.get("copilot_min_interval_days", 30)),
        "today": today_resolved,
        "would_send": would_send,
        "conditions": enriched,
        "message": build_message(allowed, None) if allowed else None,
        "title": _title(allowed) if allowed else None,
        "lines": [{"label": c["title"], "value": c["sentence"]} for c in conditions],
    }
