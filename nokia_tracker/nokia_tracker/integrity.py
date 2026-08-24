"""Niezmienniki spójności danych (krok E2, docs/ROADMAP_V3.md).

Audyt E1 (2026-08-22) uruchomił ten sam zestaw zapytań ręcznie na eksporcie
produkcyjnym — 9 z 10 czyste, jedno realne znalezisko (`_stale_pending_vest`,
transza vestingu przeterminowana o 386 dni bez lotu). `check_all()` to jedno
źródło prawdy zamiast osobnego skryptu audytowego: wołane z nocnego joba
(main.py) i z karty „Spójność danych" na /dane.

Świadomie READ-ONLY — nic tu nie naprawia, tylko zgłasza. Naprawa (E2) to
osobny, jawny krok per finding, z eksportem ZIP przed."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import reconcile as reconcilem
from . import settings as settingsm
from .tax import pit38 as taxpit38
from .views.account import account_view
from .views.market_context import instrument_ids as _instrument_ids

_QTY_EPSILON = 0.001
_MONEY_EPSILON_PLN = 0.02  # grosz + margines na zaokrąglenia pośrednie
_STALE_PENDING_VEST_DAYS = 60


@dataclass
class Finding:
    check: str
    severity: str  # "error" | "warning"
    message: str
    count: int
    details: list[dict] = field(default_factory=list)


def _qty_remaining_mismatch(conn: sqlite3.Connection) -> Finding | None:
    rows = conn.execute(
        "SELECT l.id AS lot_id, l.quantity, l.qty_remaining, "
        "COALESCE((SELECT SUM(sa.quantity) FROM sale_allocations sa "
        "WHERE sa.lot_id = l.id), 0) AS allocated FROM lots l"
    ).fetchall()
    bad = [
        {"lot_id": r["lot_id"], "quantity": r["quantity"],
         "qty_remaining": r["qty_remaining"], "allocated": r["allocated"]}
        for r in rows
        if r["qty_remaining"] is None
        or abs(r["qty_remaining"] - (r["quantity"] - r["allocated"])) > _QTY_EPSILON
    ]
    if not bad:
        return None
    return Finding(
        "qty_remaining_mismatch", "error",
        "qty_remaining lotu nie zgadza się z quantity minus suma alokacji sprzedaży",
        len(bad), bad)


def _sale_allocation_sum_mismatch(conn: sqlite3.Connection) -> Finding | None:
    rows = conn.execute(
        "SELECT s.id AS sale_id, s.quantity AS sale_qty, "
        "COALESCE((SELECT SUM(sa.quantity) FROM sale_allocations sa "
        "WHERE sa.sale_id = s.id), 0) AS alloc_sum FROM sales s"
    ).fetchall()
    bad = [
        {"sale_id": r["sale_id"], "sale_qty": r["sale_qty"], "alloc_sum": r["alloc_sum"]}
        for r in rows if abs(r["sale_qty"] - r["alloc_sum"]) > _QTY_EPSILON
    ]
    if not bad:
        return None
    return Finding(
        "sale_allocation_sum_mismatch", "error",
        "Suma alokacji sprzedaży nie zgadza się z ilością sprzedaną",
        len(bad), bad)


def _vest_referential_integrity(conn: sqlite3.Connection) -> list[Finding]:
    findings = []
    dangling_lot = conn.execute(
        "SELECT id AS vest_id, lot_id FROM vests "
        "WHERE lot_id IS NOT NULL AND lot_id NOT IN (SELECT id FROM lots)"
    ).fetchall()
    if dangling_lot:
        findings.append(Finding(
            "vest_dangling_lot_id", "error",
            "vests.lot_id wskazuje na nieistniejący lot",
            len(dangling_lot), [dict(r) for r in dangling_lot]))

    dangling_grant = conn.execute(
        "SELECT id AS vest_id, grant_id FROM vests "
        "WHERE grant_id NOT IN (SELECT id FROM grants)"
    ).fetchall()
    if dangling_grant:
        findings.append(Finding(
            "vest_dangling_grant_id", "error",
            "vests.grant_id wskazuje na nieistniejący grant",
            len(dangling_grant), [dict(r) for r in dangling_grant]))
    return findings


def _vested_without_lot(conn: sqlite3.Connection) -> Finding | None:
    """Krok E9 (docs/PLAN_E9_transza_w_puli.md): `pooled_lot_id` jest RÓWNIE
    dobrym dowodem uwolnienia jak `lot_id` — transza wydana w ramach zbiorczego
    lotu Withhold-to-Cover (Computershare łączy w jeden wiersz kilka transz
    odblokowanych tego samego dnia) nigdy nie dostanie WŁASNEGO lotu, patrz
    `data_fixes.py::link_pooled_espp_match_2025_08`. Luka jest realna tylko gdy
    OBA są NULL."""
    rows = conn.execute(
        "SELECT id AS vest_id, grant_id, vest_date, quantity FROM vests "
        "WHERE status = 'vested' AND lot_id IS NULL AND pooled_lot_id IS NULL"
    ).fetchall()
    if not rows:
        return None
    return Finding(
        "vested_without_lot", "error",
        "Transza oznaczona jako 'vested', ale bez lotu własnego ani zbiorczego",
        len(rows), [dict(r) for r in rows])


def _orphaned_sale_allocations(conn: sqlite3.Connection) -> Finding | None:
    rows = conn.execute(
        "SELECT sa.id AS allocation_id, sa.sale_id, sa.lot_id FROM sale_allocations sa "
        "WHERE sa.sale_id NOT IN (SELECT id FROM sales)"
    ).fetchall()
    if not rows:
        return None
    return Finding(
        "orphaned_sale_allocation", "error",
        "sale_allocations bez odpowiadającej sprzedaży",
        len(rows), [dict(r) for r in rows])


def _unresolved_import_conflicts(conn: sqlite3.Connection) -> Finding | None:
    rows = conn.execute(
        "SELECT id AS conflict_id, import_id, entity_type, natural_key FROM import_conflicts "
        "WHERE resolved = 0"
    ).fetchall()
    if not rows:
        return None
    return Finding(
        "unresolved_import_conflict", "warning",
        "Nierozstrzygnięty konflikt importu",
        len(rows), [dict(r) for r in rows])


def _dividend_arithmetic_mismatch(conn: sqlite3.Connection) -> Finding | None:
    rows = conn.execute(
        "SELECT id AS dividend_id, pay_date, gross_eur, withholding_paid_eur, "
        "net_received_eur FROM dividends"
    ).fetchall()
    bad = []
    for r in rows:
        if r["withholding_paid_eur"] is None or r["net_received_eur"] is None:
            bad.append(dict(r))
            continue
        lhs = r["gross_eur"] - r["withholding_paid_eur"]
        if abs(lhs - r["net_received_eur"]) > _MONEY_EPSILON_PLN:
            bad.append(dict(r))
    if not bad:
        return None
    return Finding(
        "dividend_arithmetic_mismatch", "error",
        "gross_eur - withholding_paid_eur != net_received_eur",
        len(bad), bad)


def _missing_or_future_nbp_rate(conn: sqlite3.Connection) -> Finding | None:
    checks = [
        ("lots", "acquired_date"),
        ("sales", "sale_date"),
        ("dividends", "pay_date"),
    ]
    bad: list[dict] = []
    for table, date_col in checks:
        rows = conn.execute(
            f"SELECT id, {date_col} AS event_date, nbp_rate, nbp_rate_date FROM {table} "
            f"WHERE nbp_rate IS NULL OR nbp_rate_date IS NULL OR nbp_rate_date > {date_col}"
        ).fetchall()
        bad.extend({"table": table, **dict(r)} for r in rows)
    if not bad:
        return None
    return Finding(
        "missing_or_future_nbp_rate", "error",
        "Brak kursu NBP albo data kursu późniejsza niż data zdarzenia",
        len(bad), bad)


def _tax_loss_limits(conn: sqlite3.Connection, today: str) -> list[Finding]:
    findings = []

    exceeded = conn.execute(
        "SELECT tlc.id AS loss_id, tlc.loss_pln, "
        "COALESCE(SUM(tld.amount_pln), 0) AS deducted "
        "FROM tax_loss_carryforward tlc "
        "LEFT JOIN tax_loss_deductions tld ON tld.loss_id = tlc.id "
        "GROUP BY tlc.id HAVING deducted > tlc.loss_pln + ?", (_MONEY_EPSILON_PLN,)
    ).fetchall()
    if exceeded:
        findings.append(Finding(
            "tax_loss_deductions_exceed_loss", "error",
            "Suma odliczeń straty przekracza kwotę straty z lat ubiegłych",
            len(exceeded), [dict(r) for r in exceeded]))

    current_year = int(today[:4])
    expired = conn.execute(
        "SELECT tlc.id AS loss_id, tlc.origin_year, tlc.loss_pln, "
        "COALESCE(SUM(tld.amount_pln), 0) AS deducted "
        "FROM tax_loss_carryforward tlc "
        "LEFT JOIN tax_loss_deductions tld ON tld.loss_id = tlc.id "
        "WHERE tlc.origin_year <= ? - 5 "
        "GROUP BY tlc.id HAVING deducted < tlc.loss_pln - ?",
        (current_year, _MONEY_EPSILON_PLN)
    ).fetchall()
    if expired:
        findings.append(Finding(
            "tax_loss_expired_unclaimed", "warning",
            "Strata starsza niż 5 lat, nie w pełni odliczona — okres na odliczenie minął",
            len(expired), [dict(r) for r in expired]))
    return findings


def _stale_pending_vest(
    conn: sqlite3.Connection, today: str, grace_days: int = _STALE_PENDING_VEST_DAYS
) -> Finding | None:
    """Znalezisko z audytu E1: `reconcile_vesting()` (tax/grants.py) celowo NIE
    zgaduje, czy przeterminowana transza faktycznie zvestowała — wymagałoby
    kruchego dopasowania ilości. Ten niezmiennik tylko ZGŁASZA przeterminowanie,
    nie próbuje go rozwiązać; próg liczony od `available_from` gdy jest znane
    (dla ESPP różni się od `vest_date` o ~4 tygodnie), inaczej od `vest_date`."""
    today_d = date.fromisoformat(today)
    rows = conn.execute(
        "SELECT id AS vest_id, grant_id, vest_date, available_from, quantity FROM vests "
        "WHERE status = 'pending'"
    ).fetchall()
    bad = []
    for r in rows:
        effective = r["available_from"] or r["vest_date"]
        days_overdue = (today_d - date.fromisoformat(effective)).days
        if days_overdue > grace_days:
            bad.append({
                "vest_id": r["vest_id"], "grant_id": r["grant_id"],
                "vest_date": r["vest_date"], "available_from": r["available_from"],
                "quantity": r["quantity"], "days_overdue": days_overdue,
            })
    if not bad:
        return None
    return Finding(
        "stale_pending_vest", "error",
        f"Transza 'pending' przeterminowana o ponad {grace_days} dni bez lotu — "
        "prawdopodobnie brakujący import",
        len(bad), bad)


def _tax_payments_exceed_due(conn: sqlite3.Connection, cfg: dict) -> Finding | None:
    """Krok E4 (0.20.0): `tax_payments` (wpisywane ręcznie, `cash.py`) sumowane
    per rok istotnie WIĘKSZE niż `total_due_pln` z `tax/pit38.py::annual_report`
    tego roku — prawdopodobna literówka w kwocie albo realna nadpłata do
    odzyskania. Sprawdza WYŁĄCZNIE lata, w których jest choć jedna wpłata —
    zero wpłat nigdy nie jest błędem."""
    years = [r["tax_year"] for r in conn.execute(
        "SELECT DISTINCT tax_year FROM tax_payments").fetchall()]
    bad = []
    for year in years:
        due_pln = taxpit38.annual_report(conn, cfg, year)["total_due_pln"]
        paid_pln = conn.execute(
            "SELECT COALESCE(SUM(amount_pln), 0) FROM tax_payments WHERE tax_year = ?",
            (year,)).fetchone()[0]
        if paid_pln > due_pln + _MONEY_EPSILON_PLN:
            bad.append({
                "year": year, "due_pln": round(due_pln, 2),
                "paid_pln": round(paid_pln, 2),
                "overpaid_pln": round(paid_pln - due_pln, 2)})
    if not bad:
        return None
    return Finding(
        "tax_payments_exceed_due", "warning",
        "Suma wpłat podatku za rok przekracza wyliczone total_due_pln — literówka "
        "w kwocie albo realna nadpłata do odzyskania",
        len(bad), bad)


def _allocation_predates_its_lot(conn: sqlite3.Connection) -> Finding | None:
    """Krok E7 (docs/PLAN_E7_uzgodnienie.md): alokacja FIFO przypięta do lotu nabytego
    PO dacie sprzedaży, którą konsumuje. Filtr `acquired_date <= as_of` w
    `tax/lots.py::open_lots` wymusza ten niezmiennik od kroku 19 — dane zapisane PRZED
    tą naprawą mogą go łamać (docstring `open_lots`, realny przypadek ze sprzedaży
    2025-10-27). Naruszenie blokuje rekonstrukcję stanu na dzień D w `reconcile.py`
    (`shares_as_of` zwraca wtedy `None` — `no_data`, nie błędna liczba)."""
    rows = conn.execute(
        "SELECT sa.id AS allocation_id, sa.sale_id AS sale_id, sa.lot_id AS lot_id, "
        "l.acquired_date AS lot_acquired_date, s.sale_date AS sale_date "
        "FROM sale_allocations sa "
        "JOIN sales s ON s.id = sa.sale_id "
        "JOIN lots l ON l.id = sa.lot_id "
        "WHERE l.acquired_date > s.sale_date"
    ).fetchall()
    if not rows:
        return None
    return Finding(
        "allocation_predates_its_lot", "error",
        "Alokacja sprzedaży przypięta do lotu nabytego PO dacie tej sprzedaży — "
        "dane sprzed naprawy z kroku 19, blokuje uzgodnienie z wyciągiem",
        len(rows), [dict(r) for r in rows])


def _statement_mismatch(conn: sqlite3.Connection) -> list[Finding]:
    """Krok E7: jeden `Finding` na pozycję uzgodnienia z wyciągiem o statusie
    `mismatch` (`reconcile.reconcile()` na najnowszym `statement_snapshots`).
    `warning`, nie `error` — rozjazd bywa uzasadniony (np. niepotwierdzona sprzedaż
    Withhold-to-Cover) i nie jest sam w sobie korupcją danych, ta sama waga co
    `unresolved_import_conflict`. Brak zapisanego snapshotu ⇒ ZERO findingów, nie
    finding „brak danych" — ten sam wzorzec co `_tax_payments_exceed_due` („zero
    wpłat nigdy nie jest błędem")."""
    snapshot = reconcilem.latest_snapshot(conn)
    if snapshot is None:
        return []
    findings = []
    for p in reconcilem.reconcile(conn, snapshot):
        if p.status != "mismatch":
            continue
        findings.append(Finding(
            f"statement_mismatch:{p.key}", "warning",
            f"Uzgodnienie z wyciągiem ({snapshot.get('as_of_date')}): pozycja "
            f"'{p.label}' się nie zgadza",
            1, [{"key": p.key, "label": p.label, "statement": p.statement,
                 "database": p.database, "diff": p.diff, "tolerance": p.tolerance}]))
    return findings


def _breakdown_not_closed(conn: sqlite3.Connection, cfg: dict, today: str) -> list[Finding]:
    """Krok E8 (docs/PLAN_E8_slad.md): powtarza DOKŁADNIE to samo wywołanie, którego
    używa `/` (`views/account.py::account_view`), i zgłasza każdy ślad, który nie
    domknął się do grosza (`breakdown.BreakdownNotClosedError`, zebrane tam jako
    `trace_failures` zamiast po cichu połknięte). `warning`, nie `error` — strona
    już się z tym poprawnie degraduje (kwota renderuje się bez `<details>`), to
    znalezisko jest sygnałem do zbadania, nie dowodem korupcji danych.

    Świadomie WYŁĄCZNIE `/` — ślady `/wyplata` zależą od wejścia z formularza
    (cena, data, ilość), więc nie ma ustalonego „dzisiejszego" zestawu do
    powtórzenia poza samym żądaniem (patrz `breakdown.withdrawal_traces`
    docstring)."""
    year = cfg.get("tax_year") or int(today[:4])
    ids = _instrument_ids(conn)
    view = account_view(conn, cfg, ids, year)
    findings = []
    for failure in view.get("trace_failures", []):
        findings.append(Finding(
            f"breakdown_not_closed:{failure.key}", "warning",
            f"Ślad „skąd ta liczba” dla '{failure.key}' się nie domyka — wyświetlane "
            f"{failure.shown} != przeliczone {failure.recomputed}",
            1, [{"key": failure.key, "shown": failure.shown,
                 "recomputed": failure.recomputed}]))
    return findings


def check_all(
    conn: sqlite3.Connection, today: str | None = None, cfg: dict | None = None
) -> list[Finding]:
    today = today or date.today().isoformat()
    cfg = cfg if cfg is not None else settingsm.get_settings(conn)
    findings: list[Finding] = []
    for check in (
        _qty_remaining_mismatch,
        _sale_allocation_sum_mismatch,
        _vested_without_lot,
        _orphaned_sale_allocations,
        _unresolved_import_conflicts,
        _dividend_arithmetic_mismatch,
        _missing_or_future_nbp_rate,
        _allocation_predates_its_lot,
    ):
        f = check(conn)
        if f:
            findings.append(f)
    findings.extend(_vest_referential_integrity(conn))
    findings.extend(_tax_loss_limits(conn, today))
    stale = _stale_pending_vest(conn, today)
    if stale:
        findings.append(stale)
    tax_payments_finding = _tax_payments_exceed_due(conn, cfg)
    if tax_payments_finding:
        findings.append(tax_payments_finding)
    findings.extend(_statement_mismatch(conn))
    findings.extend(_breakdown_not_closed(conn, cfg, today))
    return findings


def should_notify(finding: Finding) -> bool:
    """Krok E9 (docs/PLAN_E9_transza_w_puli.md): tylko `error` uzasadnia codzienny
    push (`main.py::integrity_check_job`) — `statement_mismatch`,
    `unresolved_import_conflict`, `breakdown_not_closed` i `tax_payments_exceed_due`
    to stany, które bywają POPRAWNE i TRWAŁE (np. znany, udokumentowany rozjazd
    z tolerancji liczonej z danych, E7). Codzienny push za coś, czego nie da się
    „naprawić", uczy ignorować powiadomienia — waga `warning` zostaje widoczna
    na karcie „Spójność danych" (`/dane`) i w `alerts_log`, bez pushu na telefon."""
    return finding.severity == "error"
