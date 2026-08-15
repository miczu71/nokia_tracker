"""Granty i transze vestingu (ESPP match / LTI RSU) — BLUEPRINT §3a, krok 13.

CRUD idempotentny po `natural_key`, ten sam wzorzec co `tax/lots.py::add_lot`. Konsument
tych zapisów (auto-tworzenie lotów `matched`/`lti` w dniu przekroczenia `vest_date`) to
scheduler kroku 14 (`tax/vesting.py`) — ten moduł tylko utrwala harmonogram w bazie.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from . import lots as taxlots


def add_grant(conn: sqlite3.Connection, program: str, grant_date: str, quantity: float,
              natural_key: str, declared_amount_eur: float | None = None,
              match_pct: float = 0.0, notes: str | None = None) -> int:
    """Wstawia grant; idempotentne po `natural_key` — istniejący klucz zwraca id
    istniejącego grantu bez wstawiania ponownie."""
    existing = conn.execute(
        "SELECT id FROM grants WHERE natural_key = ?", (natural_key,)).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO grants (program, grant_date, declared_amount_eur, quantity, "
        "match_pct, natural_key, notes) VALUES (?,?,?,?,?,?,?)",
        (program, grant_date, declared_amount_eur, quantity, match_pct, natural_key, notes))
    conn.commit()
    return cur.lastrowid


def add_vest(conn: sqlite3.Connection, grant_id: int, vest_date: str, quantity: float,
             natural_key: str, status: str = "pending",
             available_from: str | None = None) -> int:
    """Wstawia transzę vestingu; idempotentne po `natural_key`. Jeden grant (zwłaszcza LTI)
    może mieć wiele transz — każda z własnym `natural_key`.

    `available_from` (krok 21, docs/PLAN_KROK_21_portfel_calkowity.md): data realnego
    wpłynięcia akcji na konto z wyciągu Computershare — odrębna od `vest_date` (data
    NABYCIA, nie DOSTĘPNOŚCI; w praktyce ESPP ma ~4-tygodniową różnicę). `None` gdy
    nieznana (historyczne wiersze sprzed tego kroku) — patrz `backfill_available_from`."""
    existing = conn.execute(
        "SELECT id FROM vests WHERE natural_key = ?", (natural_key,)).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO vests (grant_id, vest_date, quantity, status, natural_key, "
        "available_from) VALUES (?,?,?,?,?,?)",
        (grant_id, vest_date, quantity, status, natural_key, available_from))
    conn.commit()
    return cur.lastrowid


def backfill_available_from(conn: sqlite3.Connection, vest_id: int,
                            available_from: str) -> None:
    """Uzupełnia `available_from` na transzy, która już istnieje w bazie (dodanej przed
    krokiem 21, więc kolumna jest NULL) — `add_vest` przy istniejącym `natural_key`
    zwraca wcześnie i NIC nie aktualizuje, więc bez tego wywołania kolumna zostałaby
    pusta na zawsze. Nie nadpisuje wartości już ustawionej (`WHERE available_from IS
    NULL`) — raz zapisana data z wyciągu jest ostateczna, kolejny import tego samego
    wiersza nie powinien jej ruszać."""
    conn.execute(
        "UPDATE vests SET available_from = ? WHERE id = ? AND available_from IS NULL",
        (available_from, vest_id))
    conn.commit()


def find_grant_by_natural_key(conn: sqlite3.Connection, natural_key: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM grants WHERE natural_key = ?", (natural_key,)).fetchone()
    return dict(row) if row else None


def find_vest_by_natural_key(conn: sqlite3.Connection, natural_key: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM vests WHERE natural_key = ?", (natural_key,)).fetchone()
    return dict(row) if row else None


def due_for_reminder(conn: sqlite3.Connection, vest_reminder_days: int,
                     today: str | None = None) -> list[dict]:
    """Transze 'pending' z vest_date w oknie [dziś, dziś+vest_reminder_days], którym jeszcze
    nie wysłano przypomnienia (krok 14, docs/PLAN_KROK_14_vesting_reconcile.md). Zwraca też
    participation_description (LTI) do treści powiadomienia."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")
    horizon = (datetime.strptime(today, "%Y-%m-%d")
              + timedelta(days=vest_reminder_days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT v.*, g.program, g.grant_date, g.natural_key AS grant_natural_key FROM vests v "
        "JOIN grants g ON g.id = v.grant_id "
        "WHERE v.status = 'pending' AND v.reminder_sent_at IS NULL "
        "AND v.vest_date BETWEEN ? AND ?", (today, horizon)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["participation_description"] = (
            d["grant_natural_key"].split("lti_grant:", 1)[-1]
            if d["program"] == "lti" and d["grant_natural_key"] else None)
        result.append(d)
    return result


def mark_reminder_sent(conn: sqlite3.Connection, vest_id: int) -> None:
    conn.execute(
        "UPDATE vests SET reminder_sent_at = ? WHERE id = ?",
        (datetime.now().isoformat(), vest_id))
    conn.commit()


def list_espp(conn: sqlite3.Connection, today: str | None = None) -> list[dict]:
    """ESPP: jeden grant = jedna transza (import_statement zawsze wstawia oba 1:1).

    `overdue` (krok 13.6, docs/PLAN_KROK_13_6_vesting_gap.md; przełączone na
    `available_from` w kroku 21, docs/PLAN_KROK_21_portfel_calkowity.md): sygnał czysto
    DATOWY, `status == 'pending' and COALESCE(available_from, vest_date) < today` — NIE
    zgadujemy, czy transza faktycznie zvestowała (wymagałoby kruchego dopasowania ilości
    do "Vested Matching Shares"/Withhold-to-Cover Typu A, patrz plan) — tylko uczciwie
    sygnalizujemy, że akcje POWINNY już być dostępne i warto sprawdzić wyciąg.
    `vest_date` (data NABYCIA) i `available_from` (data DOSTĘPNOŚCI) różnią się realnie
    o ~4 tygodnie dla ESPP — użycie samego `vest_date` fałszywie oznaczało transze jako
    zaległe, zanim Computershare w ogóle je zaksięgował."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT v.id AS vest_id, g.id AS grant_id, g.grant_date, g.match_pct, "
        "v.vest_date, v.available_from, v.quantity, v.status "
        "FROM grants g JOIN vests v ON v.grant_id = g.id "
        "WHERE g.program = 'espp' ORDER BY g.grant_date"
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        effective_date = d["available_from"] or d["vest_date"]
        d["overdue"] = d["status"] == "pending" and effective_date < today
        result.append(d)
    return result


_PROGRAM_TO_LOT_TYPE = {"espp": "matched", "lti": "lti"}


def reconcile_vesting(conn: sqlite3.Connection, today: str | None = None) -> int:
    """Dopasowuje loty `matched`/`lti` (utworzone z 'Vested Matching Shares'/Withhold Typ A,
    krok 13.6) do transz `vests` wciąż oznaczonych 'pending' — TYLKO gdy dopasowanie po
    (program, ilość) jest DOKŁADNE i JEDNOZNACZNE po obu stronach (dokładnie jeden nierozliczony
    lot danego typu o tej ilości I dokładnie jedna pasująca oczekująca transza). W przeciwnym
    razie transza zostaje 'pending' — uczciwie, bo nie potrafimy tego udowodnić z danych PDF
    (część historycznych dopasowań ESPP sprzed 2022 nigdy nie pojawia się w naszym harmonogramie,
    więc ich saldo nigdy się nie dopasuje - to jest OK, nie próbujemy zgadywać, patrz
    docs/PLAN_KROK_14_vesting_reconcile.md). Zwraca liczbę rozwiązanych transz."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")
    resolved = 0
    for program, lot_type in _PROGRAM_TO_LOT_TYPE.items():
        pending_vests = conn.execute(
            "SELECT v.* FROM vests v JOIN grants g ON g.id = v.grant_id "
            "WHERE g.program = ? AND v.status = 'pending' AND v.vest_date <= ?",
            (program, today)).fetchall()
        unlinked_lots = conn.execute(
            "SELECT * FROM lots WHERE lot_type = ? AND id NOT IN "
            "(SELECT lot_id FROM vests WHERE lot_id IS NOT NULL)", (lot_type,)).fetchall()
        for vest in pending_vests:
            matches = [l for l in unlinked_lots if abs(l["quantity"] - vest["quantity"]) < 1e-9]
            vest_matches = [v for v in pending_vests
                            if abs(v["quantity"] - vest["quantity"]) < 1e-9]
            if len(matches) == 1 and len(vest_matches) == 1:
                conn.execute(
                    "UPDATE vests SET status = 'vested', lot_id = ? WHERE id = ?",
                    (matches[0]["id"], vest["id"]))
                resolved += 1
    conn.commit()
    return resolved


def valuation(conn: sqlite3.Connection, current_price_eur: float | None,
             current_eurpln: float | None) -> dict[int, dict]:
    """Wartość każdej transzy vestingu (krok 16, docs/PLAN_KROK_16_transparentnosc.md),
    kluczowana `vest_id`: część OTWARTA (nadal w `lots.qty_remaining`) wyceniona po
    BIEŻĄCEJ cenie/kursie — zawsze szacunek, cena jutro będzie inna — i część
    ZREALIZOWANA (skonsumowana przez `sale_allocations`) wyceniona po CENIE I KURSIE
    NBP Z DNIA TEJ SPRZEDAŻY, czyli fakt, nie prognoza.

    Transze bez `lot_id` (jeszcze nie rozwiązane przez `reconcile_vesting` — patrz
    ten sam mechanizm wyżej) nie mają żadnego lotu do podziału na otwarte/zrealizowane,
    więc CAŁA `quantity` transzy trafia do `open_*` z `reconciled=False` — jawnie
    oznaczone jako prognoza, nie realizacja, bo nie potrafimy uczciwie stwierdzić,
    czy i ile z niej zostało już sprzedane."""
    result: dict[int, dict] = {}
    for v in conn.execute("SELECT * FROM vests").fetchall():
        if v["lot_id"] is None:
            qty = v["quantity"]
            open_eur = qty * current_price_eur if current_price_eur is not None else None
            open_pln = (open_eur * current_eurpln
                       if open_eur is not None and current_eurpln else None)
            result[v["id"]] = {
                "reconciled": False,
                "open_qty": qty,
                "open_value_eur": round(open_eur, 2) if open_eur is not None else None,
                "open_value_pln": round(open_pln, 2) if open_pln is not None else None,
                "realized": [],
                "realized_qty": 0.0,
                "realized_value_eur": 0.0,
                "realized_value_pln": 0.0,
            }
            continue

        lot = conn.execute("SELECT * FROM lots WHERE id = ?", (v["lot_id"],)).fetchone()
        if lot is None:
            continue

        open_qty = lot["qty_remaining"]
        open_eur = open_qty * current_price_eur if current_price_eur is not None else None
        open_pln = (open_eur * current_eurpln
                   if open_eur is not None and current_eurpln else None)

        alloc_rows = conn.execute(
            "SELECT sa.quantity, sa.revenue_pln, s.sale_date, s.price_eur AS sale_price_eur, "
            "s.nbp_rate, s.nbp_rate_date FROM sale_allocations sa "
            "JOIN sales s ON s.id = sa.sale_id WHERE sa.lot_id = ? ORDER BY s.sale_date",
            (lot["id"],)).fetchall()
        realized = []
        realized_qty = 0.0
        realized_eur_total = 0.0
        realized_pln_total = 0.0
        for a in alloc_rows:
            value_pln = a["revenue_pln"]
            value_eur = value_pln / a["nbp_rate"] if a["nbp_rate"] else None
            realized.append({
                "sale_date": a["sale_date"],
                "quantity": a["quantity"],
                "sale_price_eur": a["sale_price_eur"],
                "nbp_rate": a["nbp_rate"],
                "nbp_rate_date": a["nbp_rate_date"],
                "value_eur": round(value_eur, 2) if value_eur is not None else None,
                "value_pln": round(value_pln, 2),
            })
            realized_qty += a["quantity"]
            realized_eur_total += value_eur or 0.0
            realized_pln_total += value_pln

        result[v["id"]] = {
            "reconciled": True,
            "open_qty": open_qty,
            "open_value_eur": round(open_eur, 2) if open_eur is not None else None,
            "open_value_pln": round(open_pln, 2) if open_pln is not None else None,
            "realized": realized,
            "realized_qty": round(realized_qty, 4),
            "realized_value_eur": round(realized_eur_total, 2),
            "realized_value_pln": round(realized_pln_total, 2),
        }
    return result


def list_lti_grouped(conn: sqlite3.Connection, today: str | None = None) -> list[dict]:
    """LTI: jeden grant (jedna `participation_description`) + zagnieżdżone transze.
    `total_quantity` sumuje transze, bo `grants.quantity` jest celowo NULL dla LTI —
    pojedynczy wiersz RS AWARD nie zna sumy całego grantu (patrz importers/computershare_pdf.py).

    `overdue` per transza — patrz `list_espp`."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")
    grants = conn.execute(
        "SELECT * FROM grants WHERE program = 'lti' ORDER BY grant_date"
    ).fetchall()
    result = []
    for g in grants:
        vests = [dict(v) for v in conn.execute(
            "SELECT * FROM vests WHERE grant_id = ? ORDER BY vest_date", (g["id"],)).fetchall()]
        for v in vests:
            effective_date = v["available_from"] or v["vest_date"]
            v["overdue"] = v["status"] == "pending" and effective_date < today
        description = (g["natural_key"].split("lti_grant:", 1)[-1]
                        if g["natural_key"] else None)
        result.append({
            **dict(g),
            "participation_description": description,
            "vests": vests,
            "total_quantity": sum(v["quantity"] for v in vests),
        })
    return result


def _value(qty: float, price_eur: float | None, eurpln_rate: float | None
          ) -> tuple[float | None, float | None]:
    eur = qty * price_eur if price_eur is not None else None
    pln = eur * eurpln_rate if eur is not None and eurpln_rate else None
    return eur, pln


def _effective_date(row: dict) -> str:
    """COALESCE(available_from, vest_date) w Pythonie — jedno miejsce, którego używają
    `restricted_own_lots` i `vesting_timeline` (krok 26, docs/PLAN_KROK_26_doradca.md).
    `unvested_summary`/`list_espp`/`list_lti_grouped` celowo NIE zrefaktoryzowane na to
    wywołanie — te trzy mają produkcyjnych konsumentów (encje MQTT, pulpit, kubełki
    portfela), a zero zysku nie jest warte ryzyka zmiany zachowania przy okazji."""
    return row["available_from"] or row["vest_date"]


def restricted_own_lots(conn: sqlite3.Connection, today: str | None = None) -> list[dict]:
    """Krok 26 (docs/PLAN_KROK_26_doradca.md): surowe fakty per ograniczony lot `own` —
    ile dopasowania ESPP wisi na TYM KONKRETNYM locie, zero wyceny (wycenę i przepadek
    liczy `advisor.py`). Reguła wykrycia ograniczenia jest identyczna jak w
    `restricted_own_summary` przed tym krokiem (JAKAKOLWIEK transza `pending`, także LTI,
    z `grant_date` == `lots.acquired_date`) — zawężenie do samego ESPP zmieniłoby
    `restricted_qty` widoczne na pulpicie.

    `matched_qty` (dopasowanie ESPP przypisane TEMU lotowi) jest inną rzeczą niż samo
    wykrycie: liczy się WYŁĄCZNIE z transz `pending` programu `espp` — lot ograniczony
    wyłącznie grantem LTI ma `matched_qty == 0.0` (sprzedaż akcji własnych nie unieważnia
    transzy RSU). Gdy dwa loty `own` dzielą jedną `grant_date` (jedyne powiązanie to data,
    `lots.grant_id` jest martwe — nigdy niezapisywane), dopasowanie jest dzielone PRO RATA
    po ORYGINALNEJ ilości (`lots.quantity`, mianownik obejmuje też loty sprzedane do zera —
    stąd `SELECT ... FROM lots`, nie `open_lots`), żeby nie podwoić przepadku."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    pending = conn.execute(
        "SELECT v.id AS vest_id, v.quantity, v.available_from, v.vest_date, "
        "g.grant_date, g.program FROM vests v JOIN grants g ON g.id = v.grant_id "
        "WHERE v.status = 'pending'").fetchall()

    detect_by_date: dict[str, list[str]] = {}
    matched_by_date: dict[str, dict] = {}
    for p in pending:
        effective_date = _effective_date(p)
        detect_by_date.setdefault(p["grant_date"], []).append(effective_date)
        if p["program"] == "espp":
            bucket = matched_by_date.setdefault(p["grant_date"], {"qty": 0.0, "vest_ids": []})
            bucket["qty"] += p["quantity"]
            bucket["vest_ids"].append(p["vest_id"])

    result: list[dict] = []
    for lot in taxlots.open_lots(conn, as_of=today):
        if lot["lot_type"] != "own":
            continue
        date = lot["acquired_date"]
        matches = detect_by_date.get(date)
        if not matches:
            continue

        matched_bucket = matched_by_date.get(date)
        if matched_bucket:
            siblings = conn.execute(
                "SELECT quantity FROM lots WHERE lot_type = 'own' AND acquired_date = ?",
                (date,)).fetchall()
            denom = sum(s["quantity"] for s in siblings)
            matched_qty = (matched_bucket["qty"] * lot["quantity"] / denom) if denom else 0.0
            pending_vest_ids = matched_bucket["vest_ids"]
        else:
            matched_qty = 0.0
            pending_vest_ids = []

        original_quantity = lot["quantity"]
        match_rate = matched_qty / original_quantity if original_quantity else 0.0

        result.append({
            "lot_id": lot["id"],
            "acquired_date": date,
            "original_quantity": original_quantity,
            "qty_remaining": lot["qty_remaining"],
            "matched_qty": matched_qty,
            "match_rate": match_rate,
            "free_until": max(matches),
            "pending_vest_ids": pending_vest_ids,
        })
    return result


def vesting_timeline(conn: sqlite3.Connection, price_eur: float | None = None,
                     eurpln_rate: float | None = None, today: str | None = None) -> dict:
    """Krok 26 (docs/PLAN_KROK_26_doradca.md): oś czasu transz `pending` — funkcja
    SIOSTRZANA do `unvested_summary`, nie jej rozszerzenie (tamta ma trzech produkcyjnych
    konsumentów, zmiana jej kontraktu jest ryzykiem bez zysku).

    `tranches` posortowane po `(effective_date, vest_id)` — kolejność monotoniczna, żeby
    kropki na osi czasu nie lądowały losowo. `offset_pct` (0..100, pozycja na szynie)
    liczony w Pythonie po CAŁEJ liście (także `overdue`) — szablon dostaje gotową liczbę,
    a filtrowanie zaległych z wizualnej szyny (pokazywane osobno jako pasek ostrzegawczy)
    to decyzja widoku, nie tej funkcji. Jedna transza -> `offset_pct = 50.0`.

    `buckets` (`this_quarter`/`this_year` KUMULATYWNIE/`next_year`/`later`) liczone
    WYŁĄCZNIE z transz nie-zaległych — `overdue` jest z definicji niepewne (ta sama zasada
    co `unvested_summary`, krok 21) i nigdy nie wchodzi do sum po cichu, tylko do własnego
    klucza `overdue`. Wyceny `None` (nie `0.0`) gdy brakuje ceny/kursu — milczymy uczciwie,
    zero nie znaczy "bez wartości", znaczy "nieznane"."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")
    has_price = price_eur is not None
    has_pln = has_price and eurpln_rate is not None

    rows = conn.execute(
        "SELECT v.id AS vest_id, v.grant_id, v.vest_date, v.available_from, v.quantity, "
        "g.program, g.grant_date FROM vests v JOIN grants g ON g.id = v.grant_id "
        "WHERE v.status = 'pending'").fetchall()

    tranches: list[dict] = []
    for r in rows:
        d = dict(r)
        effective_date = _effective_date(d)
        overdue = effective_date < today
        value_eur, value_pln = _value(d["quantity"], price_eur, eurpln_rate)
        days_until = None
        if not overdue:
            days_until = (datetime.strptime(effective_date, "%Y-%m-%d")
                         - datetime.strptime(today, "%Y-%m-%d")).days
        year = int(effective_date[:4])
        quarter = (int(effective_date[5:7]) - 1) // 3 + 1
        tranches.append({
            "vest_id": d["vest_id"], "grant_id": d["grant_id"], "program": d["program"],
            "grant_date": d["grant_date"], "vest_date": d["vest_date"],
            "available_from": d["available_from"], "effective_date": effective_date,
            "quantity": d["quantity"], "value_eur": value_eur, "value_pln": value_pln,
            "overdue": overdue, "days_until": days_until,
            "quarter": f"{year}-Q{quarter}", "year": str(year),
        })
    tranches.sort(key=lambda t: (t["effective_date"], t["vest_id"]))

    if tranches:
        first_d = datetime.strptime(tranches[0]["effective_date"], "%Y-%m-%d")
        last_d = datetime.strptime(tranches[-1]["effective_date"], "%Y-%m-%d")
        span_days = (last_d - first_d).days
        for t in tranches:
            if span_days == 0:
                t["offset_pct"] = 50.0
            else:
                d2 = datetime.strptime(t["effective_date"], "%Y-%m-%d")
                t["offset_pct"] = (d2 - first_d).days / span_days * 100

    def _empty_bucket() -> dict:
        return {"qty": 0.0, "value_eur": (0.0 if has_price else None),
                "value_pln": (0.0 if has_pln else None)}

    def _add(bucket: dict, qty: float, value_eur: float | None, value_pln: float | None) -> None:
        bucket["qty"] += qty
        if has_price:
            bucket["value_eur"] += value_eur or 0.0
        if has_pln:
            bucket["value_pln"] += value_pln or 0.0

    today_year = int(today[:4])
    today_quarter = (int(today[5:7]) - 1) // 3 + 1

    this_quarter = _empty_bucket()
    this_year = _empty_bucket()
    next_year = _empty_bucket()
    later = _empty_bucket()
    overdue_bucket = _empty_bucket()
    overdue_count = 0

    for t in tranches:
        if t["overdue"]:
            _add(overdue_bucket, t["quantity"], t["value_eur"], t["value_pln"])
            overdue_count += 1
            continue
        y = int(t["year"])
        q = int(t["quarter"].split("Q")[1])
        if y == today_year:
            _add(this_year, t["quantity"], t["value_eur"], t["value_pln"])
            if q == today_quarter:
                _add(this_quarter, t["quantity"], t["value_eur"], t["value_pln"])
        elif y == today_year + 1:
            _add(next_year, t["quantity"], t["value_eur"], t["value_pln"])
        else:
            _add(later, t["quantity"], t["value_eur"], t["value_pln"])

    overdue_bucket["count"] = overdue_count

    return {
        "tranches": tranches,
        "buckets": {"this_quarter": this_quarter, "this_year": this_year,
                    "next_year": next_year, "later": later},
        "overdue": overdue_bucket,
        "span": {"first": tranches[0]["effective_date"] if tranches else None,
                 "last": tranches[-1]["effective_date"] if tranches else None},
    }


def unvested_summary(conn: sqlite3.Connection, price_eur: float | None = None,
                     eurpln_rate: float | None = None, today: str | None = None) -> dict:
    """Krok 21 (docs/PLAN_KROK_21_portfel_calkowity.md): jedno źródło prawdy dla
    „ile jest jeszcze zablokowane" — zastępuje wcześniejszą, samodzielną implementację
    w `sensors.py::grants_values` (patrz tam delegacja). Dzieli WSZYSTKIE transze
    `pending` na `upcoming` (data dostępności w przyszłości — normalne, czekamy) i
    `overdue` (data dostępności minęła, a transza wciąż `pending` — sygnał do
    sprawdzenia wyciągu, patrz `list_espp`/`list_lti_grouped`). Rozstrzyga po
    `COALESCE(available_from, vest_date)`, tak samo jak `overdue` tam.

    Tylko `upcoming` wchodzi do „Razem" na pulpicie — `overdue` jest z definicji
    niepewne (mogło już wpłynąć na konto pod inną transzą, patrz audyt ducha 24,42
    w planie), więc pokazywane osobno jako ostrzeżenie, nigdy zsumowane po cichu.

    Wyceny `None` gdy brak ceny/kursu — nie zgadujemy, milczymy uczciwie (ten sam
    wzorzec co `sensors.whatif_values`)."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT * FROM vests WHERE status = 'pending'").fetchall()

    pending_qty = 0.0
    upcoming_qty = 0.0
    overdue_qty = 0.0
    overdue_items: list[dict] = []
    upcoming_dated: list[tuple[str, float]] = []

    for r in rows:
        d = dict(r)
        effective_date = d["available_from"] or d["vest_date"]
        pending_qty += d["quantity"]
        if effective_date < today:
            overdue_qty += d["quantity"]
            overdue_items.append(d)
        else:
            upcoming_qty += d["quantity"]
            upcoming_dated.append((effective_date, d["quantity"]))

    next_vest_date = None
    next_vest_qty = None
    if upcoming_dated:
        upcoming_dated.sort(key=lambda x: x[0])
        next_vest_date, next_vest_qty = upcoming_dated[0]

    upcoming_value_eur, upcoming_value_pln = _value(upcoming_qty, price_eur, eurpln_rate)
    overdue_value_eur, overdue_value_pln = _value(overdue_qty, price_eur, eurpln_rate)

    return {
        "pending_qty": pending_qty,
        "upcoming_qty": upcoming_qty,
        "upcoming_value_eur": upcoming_value_eur,
        "upcoming_value_pln": upcoming_value_pln,
        "overdue_qty": overdue_qty,
        "overdue_value_eur": overdue_value_eur,
        "overdue_value_pln": overdue_value_pln,
        "next_vest_date": next_vest_date,
        "next_vest_qty": next_vest_qty,
        "overdue_items": overdue_items,
    }


def restricted_own_summary(conn: sqlite3.Connection, price_eur: float | None = None,
                           eurpln_rate: float | None = None,
                           today: str | None = None) -> dict:
    """Krok 21 (docs/PLAN_KROK_21_portfel_calkowity.md): ile z JUŻ POSIADANYCH akcji
    `own` jest objęte ograniczeniem zbycia (sprzedaż przed uwolnieniem odpowiadającego
    dopasowania ESPP = utrata dopasowania 50%).

    Reguła WYPROWADZONA z danych, nie sparsowana z sekcji „Available with restrictions"
    wyciągu (patrz uzasadnienie w planie — wyprowadzenie samo się aktualizuje, gdy
    dopasowanie zvestuje, a zdjęcie stanu z wyciągu zestarzeje się w tydzień): lot
    `own` jest ograniczony dokładnie wtedy, gdy istnieje transza `pending`, której
    grant (`grants.grant_date` = Allocation Date) ma DOKŁADNIE tę samą datę co
    `lots.acquired_date` (Trade Date — w realnych danych te dwie daty są identyczne
    dla wszystkich sześciu par zakup/dopasowanie). Lot bez pasującej transzy `pending`
    jest wolny — bezpieczny kierunek błędu, nie zawyżamy ograniczeń.

    Loty innych typów (`matched`/`lti`/`dividend_drip`) nigdy nie są ograniczone —
    ograniczenie w wyciągu dotyczy wyłącznie świeżo kupionych akcji własnych czekających
    na własne dopasowanie, nie już nabytych/podarowanych akcji.

    Krok 26 (docs/PLAN_KROK_26_doradca.md): przepisane na delegację do
    `restricted_own_lots()`, żeby reguła wykrycia ograniczenia żyła w jednym miejscu —
    ta funkcja tylko agreguje. Wyjście bit-w-bit identyczne z wersją przed refaktorem
    (patrz `test_restricted_own_summary_still_matches_after_delegation`)."""
    lots = restricted_own_lots(conn, today=today)

    restricted_qty = sum(item["qty_remaining"] for item in lots)
    free_dates = [item["free_until"] for item in lots]
    items = [{
        "acquired_date": item["acquired_date"],
        "quantity": item["qty_remaining"],
        "free_until": item["free_until"],
    } for item in lots]

    value_eur, value_pln = _value(restricted_qty, price_eur, eurpln_rate)

    return {
        "restricted_qty": restricted_qty,
        "restricted_value_eur": value_eur,
        "restricted_value_pln": value_pln,
        "free_until": max(free_dates) if free_dates else None,
        "items": items,
    }
