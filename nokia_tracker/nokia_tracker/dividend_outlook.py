"""Kalendarz i prognoza dywidend (krok 30, docs/PLAN_KROK_30_dywidendy.md).

Nowy moduł, nie rozszerzenie `tax/dividends.py`: ten drugi jest księgą + łańcuchem
podatkowym, zamraża kursy NBP na Record Date (art. 11a) — projekcja czytająca
`vests`/`lots`/kurs BIEŻĄCY tam nie należy (ta sama argumentacja co `advisor.py`, który
z tego samego powodu nie jest częścią `tax/`). Nazwa świadomie unika `forecast_*` —
ten prefiks jest zajęty przez prognozy CENOWE (tabela `forecasts`, `forecasts.py`,
`sensors.forecast_values`, encje `forecast_1w_eur`) i kolizja nazwy pomyliłaby dwie
zupełnie różne rzeczy.

Fakt zweryfikowany na realnych danych produkcyjnych przy planowaniu: dywidenda należy
się WYŁĄCZNIE od akcji faktycznie posiadanych (wolne + z ograniczeniem zbycia), NIE od
zablokowanych transz ESPP/LTI — dowód: wypłata 2026-01-30 miała `entitled_quantity`
dokładnie równe stanowi posiadania z końca poprzedniego roku. `entitled_base()` liczy
to wprost: `held_qty` z `tax/lots.py::open_lots` (loty z ograniczeniem SĄ tam — to
`vests`, nie `lots`, reprezentują to, co jeszcze nie jest posiadane), przyszłe
przyrosty z `tax/grants.py::vesting_timeline` (transze `pending`, nie-zaległe).

Drugi fakt zweryfikowany na realnych danych: `dividends.quantity`/`gross_eur` mają DWIE
semantyki zależnie od pochodzenia wiersza. Wiersze transakcyjne (sekcja "Dividend
(Reinvested)" wyciągu) mają bazę uprawnioną w `quantity` i realną stawkę w
`gross_eur/quantity`. Wiersze odtworzone z "Vested Dividend Shares" (lata bez sekcji
transakcyjnej — patrz `importers/computershare_pdf.py:589-625`) mają w `quantity`
liczbę akcji KUPIONYCH z reinwestycji (~0,19), a `gross_eur` odtworzone jako
`quantity * cena_akcji / (1 - stawka_u_zrodla)` — tam `gross_eur/quantity` to CENA
AKCJI (~kilka EUR), nie stawka dywidendy. Naiwne liczenie dałoby stawkę ~150x za
wysoką. Odróżnia je `taxdiv.is_estimated()` (niepuste `notes`) — `per_share_history()`
te wiersze bezwzględnie wyklucza.

Trzeci fakt (krok 0.17.2, po naprawie regexu Entitled Quantity w 0.17.1): Computershare
drukuje w sekcji "Dividend (Reinvested)" OSOBNY wiersz na każdy koszyk planu (ESPP, LTI)
uprawniony do tej samej wypłaty — bez żadnego identyfikatora planu w kolumnach (sprawdzone
na realnym wyciągu 2026-08-19: wypłata 2026-07-24 ma dwa wiersze, 2734 akcji LTI i
154.663115 akcji ESPP, nierozróżnialne poza ilością). `pay_date` w `dividends` więc NIE
jest unikalny. `per_share_history()` grupuje realne wiersze po `pay_date` PRZED liczeniem
mediany/kadencji/okna lookback — bez tego zdublowana data wstrzykuje odstęp 0 dni do
mediany kadencji i wypycha starszą wypłatę z okna, zawyżając prognozę (~14% na danych
produkcyjnych, patrz CHANGELOG 0.17.2)."""
from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime, timedelta

from .tax import dividends as taxdiv
from .tax import grants as grantsm
from .tax import lots as taxlots

# Poniżej tylu realnych (nie-szacunkowych) wypłat kalendarz nie zgaduje daty/kwoty
# przyszłej wypłaty — pokazuje tylko to, co jest jawnie ogłoszone w
# `dividend_schedule`. Cztery = jeden pełny rok przy kwartalnym rytmie, więc jedna
# nietypowa wypłata (np. specjalna dywidenda) nie zdominuje jeszcze medianę.
_MIN_REAL_PAYMENTS = 4

# Kadencja wyprowadzona z mediany odstępów między realnymi record date, przyciągana
# do najbliższej z tych trzech wartości (dni) — realne dane Nokii dają ~90.
_CADENCE_DAYS = {4: 91, 2: 182, 1: 365}


def _snap_cadence(median_gap_days: float) -> int:
    return min(_CADENCE_DAYS, key=lambda n: abs(_CADENCE_DAYS[n] - median_gap_days))


def per_share_history(conn: sqlite3.Connection, lookback: int = 4) -> dict:
    """Stawka na akcję i kadencja wyprowadzone WYŁĄCZNIE z realnych wypłat
    (`taxdiv.is_estimated() is False`) — patrz docstring modułu dla uzasadnienia,
    dlaczego wiersze szacunkowe muszą być wykluczone, nie tylko ważone niżej.

    Punkt centralny to MEDIANA z ostatnich `lookback` realnych wypłat (odporna na
    pojedynczy nietypowy kwartał), `per_share_low_eur`/`_high_eur` to min/max tego
    samego okna — pokazywane jako pasmo, nie fałszywy przedział ufności. Kadencja
    liczona z WSZYSTKICH realnych wypłat (więcej danych = stabilniejsza), nie tylko
    okna `lookback`.

    `sufficient=False` (poniżej `_MIN_REAL_PAYMENTS`) oznacza: silnik NIE zgaduje.
    `per_share_eur`/`payments_per_year` zostają `None`, `reason` tłumaczy dlaczego —
    honesty contract, nigdy zmyślonej stawki z 1-2 punktów danych."""
    rows = conn.execute(
        "SELECT pay_date, gross_eur, quantity, withholding_pct, notes FROM dividends "
        "ORDER BY pay_date ASC").fetchall()

    excluded = 0
    real_rows = []
    for r in rows:
        if taxdiv.is_estimated(r):
            excluded += 1
            continue
        if r["quantity"] and r["quantity"] > 0:
            real_rows.append(r)

    # Grupowanie po pay_date PRZED liczeniem czegokolwiek — patrz trzeci fakt w docstringu
    # modułu. Jedna wypłata = jeden punkt danych w medianie/kadencji/oknie, niezależnie od
    # tego, ile wierszy-koszyków planu Computershare dla niej wydrukował.
    payments: dict[str, dict] = {}
    for r in real_rows:
        p = payments.setdefault(r["pay_date"], {
            "pay_date": r["pay_date"], "gross_eur": 0.0, "quantity": 0.0,
            "withholding_sum": 0.0, "withholding_weight": 0.0})
        p["gross_eur"] += r["gross_eur"]
        p["quantity"] += r["quantity"]
        if r["withholding_pct"] is not None:
            p["withholding_sum"] += r["withholding_pct"] * r["gross_eur"]
            p["withholding_weight"] += r["gross_eur"]
    payment_list = list(payments.values())

    if len(payment_list) < _MIN_REAL_PAYMENTS:
        return {
            "items": [], "excluded_estimated_count": excluded,
            "per_share_eur": None, "per_share_low_eur": None,
            "per_share_high_eur": None, "last_per_share_eur": None,
            "withholding_pct": None, "median_gap_days": None,
            "payments_per_year": None, "sufficient": False,
            "reason": (
                f"Za mało realnych wypłat ({len(payment_list)} z {_MIN_REAL_PAYMENTS} "
                "wymaganych) — kalendarz pokazuje tylko potwierdzone/zapowiedziane "
                "raty z ogłoszonego harmonogramu."),
        }

    window = payment_list[-lookback:]
    rates = [p["gross_eur"] / p["quantity"] for p in window]
    withholdings = [p["withholding_sum"] / p["withholding_weight"]
                    for p in window if p["withholding_weight"]]

    gaps = []
    for prev, cur in zip(payment_list, payment_list[1:]):
        d1 = datetime.strptime(prev["pay_date"], "%Y-%m-%d")
        d2 = datetime.strptime(cur["pay_date"], "%Y-%m-%d")
        gaps.append((d2 - d1).days)
    median_gap = statistics.median(gaps) if gaps else None

    return {
        "items": [{"pay_date": p["pay_date"], "per_share_eur": p["gross_eur"] / p["quantity"]}
                  for p in window],
        "excluded_estimated_count": excluded,
        "per_share_eur": statistics.median(rates),
        "per_share_low_eur": min(rates),
        "per_share_high_eur": max(rates),
        "last_per_share_eur": rates[-1],
        "withholding_pct": statistics.median(withholdings) if withholdings else None,
        "median_gap_days": median_gap,
        "payments_per_year": _snap_cadence(median_gap) if median_gap is not None else None,
        "sufficient": True,
        "reason": None,
    }


def entitled_base(conn: sqlite3.Connection, today: str | None = None) -> dict:
    """Baza uprawniona DZIŚ (`held_qty`) + oś przyszłych przyrostów (`tranches`).

    `held_qty` = suma `qty_remaining` po WSZYSTKICH lotach (`tax/lots.py::open_lots`)
    — loty `own` z ograniczeniem zbycia SĄ tu wliczone (są posiadane, ograniczenie
    dotyczy tylko prawa do SPRZEDAŻY, nie prawa do dywidendy). Zablokowane transze
    ESPP/LTI to `vests`, nie `lots` — nie są posiadane, więc nie wchodzą do `held_qty`.

    `tranches` to transze `pending` NIE-zaległe z `tax/grants.py::vesting_timeline`,
    po `effective_date` (`COALESCE(available_from, vest_date)` — data faktycznego
    wpłynięcia akcji, nie samego nabycia; konserwatywne, nigdy nie zawyża). Transze
    zaległe są WYKLUCZONE i zsumowane osobno w `overdue_excluded_qty` — ta sama zasada
    co `unvested_summary()`/`dashboard_buckets()`: zaległa transza być może już została
    wciągnięta do `lots` przez `reconcile_vesting()`, więc liczenie jej tu drugi raz
    podwoiłoby bazę uprawnioną."""
    lots = taxlots.open_lots(conn)
    held_qty = sum(l["qty_remaining"] for l in lots)

    timeline = grantsm.vesting_timeline(conn, today=today)
    tranches = [
        {"effective_date": t["effective_date"], "quantity": t["quantity"],
         "program": t["program"]}
        for t in timeline["tranches"] if not t["overdue"]
    ]
    overdue_excluded_qty = sum(
        t["quantity"] for t in timeline["tranches"] if t["overdue"])

    return {
        "held_qty": held_qty,
        "tranches": tranches,
        "overdue_excluded_qty": overdue_excluded_qty,
    }


def qty_on(base: dict, record_date: str) -> float:
    """CZYSTA: `held_qty` + suma transz z `effective_date <= record_date`. Osobna od
    `entitled_base()` żeby ta droższa (czyta `lots`+`vests`) liczyła się RAZ na
    wywołanie `calendar()`, a nie raz na każde z ~20 przyszłych zdarzeń."""
    return base["held_qty"] + sum(
        t["quantity"] for t in base["tranches"] if t["effective_date"] <= record_date)


def _quarter_slot(date_str: str) -> tuple[int, int]:
    year = int(date_str[:4])
    month = int(date_str[5:7])
    return year, (month - 1) // 3 + 1


def _snap_off_weekend(dt: datetime) -> datetime:
    """Sobota/niedziela -> najbliższy wcześniejszy piątek. Tylko dla dat SZACOWANYCH
    (ogłoszony harmonogram wpisuje realne daty, nie ma czego prostować)."""
    if dt.weekday() == 5:
        return dt - timedelta(days=1)
    if dt.weekday() == 6:
        return dt - timedelta(days=2)
    return dt


def calendar(conn: sqlite3.Connection, cfg: dict, years_ahead: int = 3,
            eurpln_rate: float | None = None, today: str | None = None) -> dict:
    """Kalendarz dywidend: raty z `dividend_schedule` (niedopasowane, w horyzoncie) +
    zdarzenia szacowane z historii — z deduplikacją po slocie (rok, kwartał), rata
    ogłoszona zawsze wypiera zdarzenie szacowane w tym samym kwartale.

    Podatek liczony PRZEZ `taxdiv.compute_dividend_tax`/`compute_dividend_tax_pln` —
    ten sam łańcuch co sekcja G PIT-38, zero nowej matematyki. `compute_dividend_tax_pln`
    zwraca tylko 2 klucze (`pl_tax_due_pln`/`reclaimable_from_finland_pln` = None) gdy
    `gross_pln is None` (brak `eurpln_rate`) — stąd wszędzie `.get()`, nigdy indeksowania.

    `reclaimable_from_finland_*` jest osobną linią na każdym zdarzeniu, NIGDY wliczaną
    do `net_in_hand_*` — odzyskanie nadpłaty wymaga złożenia wniosku do fińskiego Vero,
    nie jest gotówką "na rękę" automatycznie.

    PLN liczone po kursie BIEŻĄCYM (`eurpln_rate`), nie zamrożonym NBP D-1 (nie ma
    zamrożonego kursu dla daty w przyszłości) — `assumptions["fx_basis"] = "current"`
    jawnie to znaczy. Ta funkcja NIGDY nie wchodzi do `totals` na `/dividends` (te
    liczą się z zaksięgowanej historii, na zamrożonych kursach) - krok 18 naprawiał
    dokładnie taką pomyłkę w innym miejscu tej strony."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")
    today_dt = datetime.strptime(today, "%Y-%m-%d")
    horizon_dt = today_dt + timedelta(days=365 * years_ahead)
    horizon = horizon_dt.strftime("%Y-%m-%d")

    per_share = per_share_history(conn)
    base = entitled_base(conn, today=today)

    schedule_rows = conn.execute(
        "SELECT * FROM dividend_schedule WHERE status = 'announced' "
        "AND matched_dividend_id IS NULL AND record_date >= ? AND record_date <= ? "
        "ORDER BY record_date ASC", (today, horizon)).fetchall()

    schedule_slots = set()
    raw_events = []
    for r in schedule_rows:
        schedule_slots.add(_quarter_slot(r["record_date"]))
        raw_events.append({
            "record_date": r["record_date"],
            "fiscal_year": r["fiscal_year"], "instalment": r["instalment"],
            "gross_per_share_eur": r["gross_per_share_eur"],
            "certainty": "confirmed" if r["dates_confirmed"] else "announced",
            "date_precision": "day",
        })

    if per_share["sufficient"]:
        last_real = per_share["items"][-1]["pay_date"]
        last_schedule = schedule_rows[-1]["record_date"] if schedule_rows else None
        seed = max(last_real, last_schedule) if last_schedule else last_real
        gap_days = int(round(per_share["median_gap_days"]))
        if gap_days <= 0:
            # Zabezpieczenie: per_share_history() grupuje po pay_date właśnie po to, żeby
            # mediana nigdy nie spadła do 0 (patrz trzeci fakt w docstringu modułu), ale to
            # jedyna bariera między błędem danych i pętlą `while True` poniżej, która bez
            # dodatniego gap_days nigdy się nie kończy — twardy fallback na kadencję.
            gap_days = _CADENCE_DAYS[per_share["payments_per_year"]]

        cur_dt = datetime.strptime(seed, "%Y-%m-%d")
        while True:
            cur_dt = cur_dt + timedelta(days=gap_days)
            if cur_dt > horizon_dt:
                break
            snapped = _snap_off_weekend(cur_dt)
            if snapped <= today_dt:
                continue
            slot = _quarter_slot(snapped.strftime("%Y-%m-%d"))
            if slot in schedule_slots:
                continue  # rata ogłoszona już pokrywa ten kwartał - bez dublowania
            precision = "day" if (snapped - today_dt).days <= 365 else "quarter"
            raw_events.append({
                "record_date": snapped.strftime("%Y-%m-%d"),
                "fiscal_year": None, "instalment": None,
                "gross_per_share_eur": per_share["per_share_eur"],
                "certainty": "estimated",
                "date_precision": precision,
                "quarter_label": f"{slot[0]}-Q{slot[1]}",
            })

    raw_events.sort(key=lambda e: e["record_date"])

    withholding_pct = per_share["withholding_pct"] or cfg.get("finnish_withholding_pct", 35.0)

    events = []
    for ev in raw_events:
        entitled_qty = qty_on(base, ev["record_date"])
        gross_eur = entitled_qty * ev["gross_per_share_eur"]
        eur = _tax_eur(gross_eur, withholding_pct, cfg)
        gross_pln = gross_eur * eurpln_rate if eurpln_rate is not None else None
        pln = _tax_pln(gross_pln, withholding_pct, cfg)
        net_in_hand_pln = (
            gross_pln - pln["withholding_paid_pln"] - pln["pl_tax_due_pln"]
            if gross_pln is not None else None)

        events.append({
            **ev,
            "entitled_qty": entitled_qty,
            "gross_eur": gross_eur,
            "withholding_paid_eur": eur["withholding_paid_eur"],
            "pl_tax_due_eur": eur["pl_tax_due_eur"],
            "net_received_eur": eur["net_received_eur"],
            "reclaimable_from_finland_eur": eur["reclaimable_from_finland_eur"],
            "net_in_hand_eur": gross_eur - eur["withholding_paid_eur"] - eur["pl_tax_due_eur"],
            "gross_pln": gross_pln,
            "withholding_paid_pln": pln.get("withholding_paid_pln"),
            "pl_tax_due_pln": pln.get("pl_tax_due_pln"),
            "reclaimable_from_finland_pln": pln.get("reclaimable_from_finland_pln"),
            "net_in_hand_pln": net_in_hand_pln,
        })

    by_year: dict[str, dict] = {}
    for e in events:
        y = e["record_date"][:4]
        bucket = by_year.setdefault(y, {"year": y, "gross_eur": 0.0, "net_in_hand_eur": 0.0})
        bucket["gross_eur"] += e["gross_eur"]
        bucket["net_in_hand_eur"] += e["net_in_hand_eur"]

    ntm_cutoff = (today_dt + timedelta(days=365)).strftime("%Y-%m-%d")
    ntm_events = [e for e in events if e["record_date"] <= ntm_cutoff]
    ttm_cutoff = (today_dt - timedelta(days=365)).strftime("%Y-%m-%d")
    ttm_rows = conn.execute(
        "SELECT gross_eur FROM dividends WHERE pay_date > ? AND pay_date <= ?",
        (ttm_cutoff, today)).fetchall()

    assumptions = {k: v for k, v in per_share.items() if k != "items"}
    assumptions["real_payments_used"] = len(per_share["items"])
    assumptions["sufficient_history"] = assumptions.pop("sufficient")
    assumptions["overdue_excluded_qty"] = base["overdue_excluded_qty"]
    assumptions["eurpln_rate"] = eurpln_rate
    assumptions["fx_basis"] = "current"
    assumptions["years_ahead"] = years_ahead

    return {
        "events": events,
        "by_year": sorted(by_year.values(), key=lambda b: b["year"]),
        "next_event": events[0] if events else None,
        "ntm_gross_eur": sum(e["gross_eur"] for e in ntm_events),
        "ntm_net_in_hand_eur": sum(e["net_in_hand_eur"] for e in ntm_events),
        "ntm_net_in_hand_pln": (
            sum(e["net_in_hand_pln"] for e in ntm_events)
            if ntm_events and all(e["net_in_hand_pln"] is not None for e in ntm_events)
            else None),
        "ttm_gross_eur": sum(r["gross_eur"] for r in ttm_rows),
        "assumptions": assumptions,
    }


def _tax_eur(gross_eur: float, withholding_pct: float, cfg: dict) -> dict:
    return taxdiv.compute_dividend_tax(
        gross_eur, withholding_pct, cfg["treaty_withholding_pct"],
        cfg["pl_capital_gains_tax_pct"])


def _tax_pln(gross_pln: float | None, withholding_pct: float, cfg: dict) -> dict:
    return taxdiv.compute_dividend_tax_pln(
        {"gross_pln": gross_pln, "withholding_pct": withholding_pct}, cfg)


def add_instalment(conn: sqlite3.Connection, fiscal_year: int, instalment: int,
                   record_date: str, gross_per_share_eur: float,
                   payment_date: str | None = None, ex_date: str | None = None,
                   currency: str = "EUR", dates_confirmed: bool = False,
                   announced_on: str | None = None, notes: str | None = None) -> int:
    """UPSERT po `(fiscal_year, instalment)` — WZA uchwala kwotę roczną w N ratach,
    ponowne wysłanie formularza z tą samą (rok, rata) AKTUALIZUJE wiersz (data
    orientacyjna staje się potwierdzoną), nie duplikuje. `matched_dividend_id`/
    `status` celowo nie są parametrami — pierwsze ustawia wyłącznie
    `reconcile_schedule()`, drugie zmienia się tylko przez osobną akcję (anulowanie),
    nie przez zwykły zapis formularza."""
    existing = conn.execute(
        "SELECT id FROM dividend_schedule WHERE fiscal_year = ? AND instalment = ?",
        (fiscal_year, instalment)).fetchone()
    if existing:
        conn.execute(
            "UPDATE dividend_schedule SET record_date = ?, payment_date = ?, "
            "ex_date = ?, gross_per_share_eur = ?, currency = ?, dates_confirmed = ?, "
            "announced_on = ?, notes = ? WHERE id = ?",
            (record_date, payment_date, ex_date, gross_per_share_eur, currency,
             1 if dates_confirmed else 0, announced_on, notes, existing["id"]))
        conn.commit()
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO dividend_schedule (fiscal_year, instalment, record_date, "
        "payment_date, ex_date, gross_per_share_eur, currency, dates_confirmed, "
        "announced_on, notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (fiscal_year, instalment, record_date, payment_date, ex_date,
         gross_per_share_eur, currency, 1 if dates_confirmed else 0, announced_on, notes))
    conn.commit()
    return cur.lastrowid


def list_schedule(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM dividend_schedule ORDER BY fiscal_year DESC, instalment ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def delete_instalment(conn: sqlite3.Connection, schedule_id: int) -> bool:
    cur = conn.execute("DELETE FROM dividend_schedule WHERE id = ?", (schedule_id,))
    conn.commit()
    return cur.rowcount > 0


def _shift_date(date_str: str, days: int) -> str:
    return (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


def reconcile_schedule(conn: sqlite3.Connection, today: str | None = None) -> int:
    """Dopasowuje niedopasowane raty `dividend_schedule` do realnych wypłat w
    `dividends` — wzorem `tax/grants.py::reconcile_vesting`, TEN SAM kontrakt: dopasuj
    tylko gdy jednoznaczne, w przeciwnym razie zostaw i bądź szczery. Reguła: (1)
    dokładne `pay_date == record_date` przy dokładnie jednej takiej, jeszcze
    niedopasowanej DACIE; (2) inaczej dokładnie jedna kandydująca data w oknie ±5 dni;
    (3) inaczej `NULL`, nigdy zgadywania.

    Jednoznaczność liczona jest na poziomie DATY, nie pojedynczego wiersza `dividends` —
    Computershare drukuje osobny wiersz na każdy koszyk planu tej samej wypłaty (patrz
    trzeci fakt w docstringu modułu), więc jedna data może mieć >1 wiersz i to wciąż jest
    jednoznaczne dopasowanie. `matched_dividend_id` (kolumna FK na jeden wiersz) wskazuje
    wtedy na wiersz o najniższym `id` w grupie jako deterministycznego reprezentanta całej
    daty. Raz dopasowana data jest CAŁA wyłączona z dalszej puli kandydatów — inaczej druga
    rata mogłaby złapać drugi wiersz tej samej wypłaty. Zwraca liczbę nowo rozwiązanych rat."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    matched_ids = {
        r["matched_dividend_id"] for r in conn.execute(
            "SELECT matched_dividend_id FROM dividend_schedule "
            "WHERE matched_dividend_id IS NOT NULL").fetchall()
    }
    already_matched_dates = set()
    if matched_ids:
        placeholders = ",".join("?" * len(matched_ids))
        already_matched_dates = {
            r["pay_date"] for r in conn.execute(
                f"SELECT pay_date FROM dividends WHERE id IN ({placeholders})",
                tuple(matched_ids)).fetchall()
        }

    pending = conn.execute(
        "SELECT * FROM dividend_schedule WHERE status = 'announced' "
        "AND matched_dividend_id IS NULL AND record_date <= ? "
        "ORDER BY record_date ASC", (today,)).fetchall()

    resolved = 0
    for row in pending:
        record_date = row["record_date"]
        if record_date in already_matched_dates:
            continue

        exact_ids = [d["id"] for d in conn.execute(
            "SELECT id FROM dividends WHERE pay_date = ?", (record_date,)).fetchall()]

        if exact_ids:
            candidate_ids = exact_ids
            match_date = record_date
        else:
            window_rows = conn.execute(
                "SELECT id, pay_date FROM dividends WHERE pay_date BETWEEN ? AND ?",
                (_shift_date(record_date, -5), _shift_date(record_date, 5))
            ).fetchall()
            window_dates = {d["pay_date"] for d in window_rows
                             if d["pay_date"] not in already_matched_dates}
            if len(window_dates) != 1:
                continue
            (match_date,) = window_dates
            candidate_ids = [d["id"] for d in window_rows if d["pay_date"] == match_date]

        matched_id = min(candidate_ids)
        conn.execute(
            "UPDATE dividend_schedule SET matched_dividend_id = ? WHERE id = ?",
            (matched_id, row["id"]))
        already_matched_dates.add(match_date)
        resolved += 1

    conn.commit()
    return resolved
