"""Straty z lat ubiegłych (art. 9 ust. 3-3a ustawy o PIT, krok 27) i
zamknięcie roku podatkowego.

PODSTAWĘ PRAWNĄ POTWIERDZIĆ PRZY IMPLEMENTACJI na aktualnym tekście ustawy
(BLUEPRINT §3a: "potwierdzać podstawę prawną, nie zakładać jej"). Opis
poniżej to stan wiedzy z planowania (docs/PLAN_KROK_27_straty_kreator.md),
nie cytat z Dziennika Ustaw:
- ust. 3: odliczenie w ciągu kolejnych 5 lat podatkowych po roku straty,
  w żadnym z nich nie więcej niż 50% kwoty tej straty.
- ust. 3a (od 2019): ALBO jednorazowo do 5 000 000 zł w jednym z tych lat,
  reszta (gdyby strata przekraczała 5 mln) w pozostałych latach okresu przy
  tym samym limicie 50%.

Dla strat rzędu tysięcy/dziesiątek tysięcy złotych (skala tego portfela)
próg 5 mln nigdy nie jest wiążący, więc ust. 3a w praktyce znosi limit
50%/rok — całą stratę wolno odliczyć jednym ruchem w pierwszym dochodowym
roku okresu. max_deduction_pln() poniżej implementuje WYŁĄCZNIE ten
uproszczony, bezpieczny dla realnej skali przypadek (pełna kwota przy
pierwszym użyciu straty, 50%/rok gdy odliczenie już się zaczęło rozkładać
na raty) — pełna gałąź "duża strata rozłożona na raty z jednorazowym
zastrzykiem w dowolnym roku" jest świadomie POZA krokiem, bo przy tej
skali portfela jest martwym kodem.

Uwaga o importach (uniknięcie cyklu): `tax/pit38.py` (krok 27, zadanie 2)
importuje ten moduł, żeby dołożyć krok "straty" do rocznego raportu. Gdyby
ten moduł z kolei importował `tax/pit38.py` (żeby samemu policzyć
`annual_report()` wewnątrz `close_year()`/`wizard_steps()`), powstałby
cykl `losses -> pit38 -> losses`. Rozwiązanie: `close_year()` przyjmuje
`total_due_pln` jako gotowy parametr, a `wizard_steps()` przyjmuje gotowy
`report` (wynik `pit38.annual_report()`) — wywołujący (web.py) i tak już
ma te wartości policzone z innego powodu (widok roku), więc nic nie liczy
się tu podwójnie."""
from __future__ import annotations

import sqlite3

from . import policy as taxpolicy

LOSS_YEARS_WINDOW = 5
LUMP_SUM_CAP_PLN = 5_000_000.0

# Epsilon dla porównań kwot w PLN (zaokrąglenia float) — analogiczny do
# `tax/lots.py::_EPS`, ale w innej skali (złotówki, nie sztuki akcji).
_EPS_PLN = 0.005


def rebuild(conn: sqlite3.Connection, cfg: dict) -> dict:
    """Przelicza `tax_loss_carryforward` od zera z `tax/policy.py::compute_all_policies()`
    dla każdego roku z jakąkolwiek sprzedażą i dla wszystkich trzech polityk kosztu
    naraz. NIGDY nie nadpisuje/kasuje bezwarunkowo wiersza, który ma już
    zarejestrowane odliczenia (`tax_loss_deductions`) — to najważniejsza reguła
    tego modułu, patrz komentarze niżej przy `conflicts`.

    Zwraca `{"upserted": [(origin_year, cost_basis_policy), ...],
    "conflicts": [{"origin_year", "cost_basis_policy", "old_loss_pln",
    "computed_loss_pln", "used_pln"}, ...]}`. `conflicts` sygnalizuje sytuacje,
    które wymagają ręcznej decyzji użytkownika (korekta danych zmieniła
    wynik podatkowy roku w sposób niekompatybilny z już wykorzystanymi
    odliczeniami)."""
    year_rows = conn.execute(
        "SELECT DISTINCT strftime('%Y', sale_date) AS y FROM sales").fetchall()
    years = sorted(int(r["y"]) for r in year_rows if r["y"] is not None)

    upserted: list[tuple[int, str]] = []
    conflicts: list[dict] = []

    for year in years:
        policies_data = taxpolicy.compute_all_policies(conn, cfg, year=year)
        for policy_name in taxpolicy.POLICIES:
            income_pln = policies_data[policy_name]["income_pln"]
            existing = conn.execute(
                "SELECT id, loss_pln FROM tax_loss_carryforward "
                "WHERE origin_year=? AND cost_basis_policy=?",
                (year, policy_name)).fetchone()

            if income_pln < 0:
                computed_loss_pln = round(abs(income_pln), 2)
                if existing is None:
                    conn.execute(
                        "INSERT INTO tax_loss_carryforward "
                        "(origin_year, cost_basis_policy, loss_pln) VALUES (?,?,?)",
                        (year, policy_name, computed_loss_pln))
                    upserted.append((year, policy_name))
                    continue

                used_pln = conn.execute(
                    "SELECT COALESCE(SUM(amount_pln), 0) FROM tax_loss_deductions "
                    "WHERE loss_id=?", (existing["id"],)).fetchone()[0]
                if computed_loss_pln < used_pln - _EPS_PLN:
                    # Korekta danych zmniejszyła stratę PONIŻEJ już wykorzystanej
                    # kwoty odliczeń — literalnie nie da się cofnąć czasu i
                    # "odliczyć mniej" w latach, które user już rozliczył.
                    # NIE nadpisuj loss_pln, zostaw decyzję człowiekowi.
                    conflicts.append({
                        "origin_year": year,
                        "cost_basis_policy": policy_name,
                        "old_loss_pln": existing["loss_pln"],
                        "computed_loss_pln": computed_loss_pln,
                        "used_pln": round(used_pln, 2),
                    })
                    continue

                conn.execute(
                    "UPDATE tax_loss_carryforward SET loss_pln=? WHERE id=?",
                    (computed_loss_pln, existing["id"]))
                upserted.append((year, policy_name))
            else:
                # Rok przestał być stratny (income_pln >= 0) pod tą polityką.
                if existing is None:
                    continue
                used_pln = conn.execute(
                    "SELECT COALESCE(SUM(amount_pln), 0) FROM tax_loss_deductions "
                    "WHERE loss_id=?", (existing["id"],)).fetchone()[0]
                if used_pln <= _EPS_PLN:
                    conn.execute(
                        "DELETE FROM tax_loss_carryforward WHERE id=?", (existing["id"],))
                else:
                    conflicts.append({
                        "origin_year": year,
                        "cost_basis_policy": policy_name,
                        "old_loss_pln": existing["loss_pln"],
                        "computed_loss_pln": 0.0,
                        "used_pln": round(used_pln, 2),
                    })

    conn.commit()
    return {"upserted": upserted, "conflicts": conflicts}


def max_deduction_pln(loss_pln: float, used_before: float, remaining: float) -> float:
    """Czysta funkcja (bez `conn`) — patrz uzasadnienie uproszczenia w
    docstringu modułu. `used_before <= 0` (jeszcze nic nie odliczono z tej
    straty) i `remaining` mieści się w limicie jednorazowym (5 mln zł) —
    wolno odliczyć całość naraz (ust. 3a). W przeciwnym razie (odliczenie
    już się zaczęło rozkładać na raty, albo strata przekracza limit
    jednorazowy) obowiązuje 50% pierwotnej kwoty straty rocznie (ust. 3)."""
    if used_before <= _EPS_PLN and remaining <= LUMP_SUM_CAP_PLN:
        return remaining
    return min(remaining, round(loss_pln * 0.5, 2))


def available_for_year(conn: sqlite3.Connection, cfg: dict, year: int,
                        policy: str | None = None) -> dict:
    """Straty dostępne do odliczenia w `year`: `origin_year` w oknie
    `[year-5, year-1]` (5 lat podatkowych PO roku straty, art. 9 ust. 3).
    `policy` domyślnie `cfg["cost_basis_policy"]` — straty innych polityk
    nie są widoczne (patrz `tests/test_tax_losses.py` — izolacja per
    polityka, dokładnie jak w `tax/policy.py`)."""
    if policy is None:
        policy = cfg.get("cost_basis_policy", "own_only")

    rows = conn.execute(
        "SELECT id, origin_year, loss_pln FROM tax_loss_carryforward "
        "WHERE cost_basis_policy=? AND origin_year BETWEEN ? AND ? "
        "ORDER BY origin_year ASC, id ASC",
        (policy, year - LOSS_YEARS_WINDOW, year - 1)).fetchall()

    items: list[dict] = []
    total_remaining = 0.0
    total_used_this_year = 0.0

    for row in rows:
        used_before = conn.execute(
            "SELECT COALESCE(SUM(amount_pln), 0) FROM tax_loss_deductions "
            "WHERE loss_id=? AND used_in_year<?", (row["id"], year)).fetchone()[0]
        remaining = round(row["loss_pln"] - used_before, 2)
        if remaining <= _EPS_PLN:
            continue  # strata już w pełni wykorzystana

        used_this_year = conn.execute(
            "SELECT COALESCE(SUM(amount_pln), 0) FROM tax_loss_deductions "
            "WHERE loss_id=? AND used_in_year=?", (row["id"], year)).fetchone()[0]

        items.append({
            "loss_id": row["id"],
            "origin_year": row["origin_year"],
            "loss_pln": row["loss_pln"],
            "used_before_pln": round(used_before, 2),
            "remaining_pln": remaining,
            "max_deduction_pln": max_deduction_pln(row["loss_pln"], used_before, remaining),
            "used_this_year_pln": round(used_this_year, 2),
            "expires_after_year": row["origin_year"] + LOSS_YEARS_WINDOW,
        })
        total_remaining += remaining
        total_used_this_year += used_this_year

    return {
        "year": year,
        "policy": policy,
        "items": items,
        "total_remaining_pln": round(total_remaining, 2),
        "total_used_this_year_pln": round(total_used_this_year, 2),
    }


def record_deduction(conn: sqlite3.Connection, cfg: dict, loss_id: int,
                      used_in_year: int, amount_pln: float) -> None:
    """Zapisuje (lub cofa, gdy `amount_pln == 0.0`) decyzję o odliczeniu
    straty `loss_id` w roku `used_in_year`. Waliduje: okno 5 lat, limit
    `max_deduction_pln()` (z doliczeniem tego, co ta SAMA decyzja już
    zajmowała w tym roku — inaczej user nie mógłby zmniejszyć/utrzymać
    swojej wcześniejszej kwoty), i że nie schodzi poniżej zera dochodu
    roku docelowego pod polityką kosztu tej konkretnej straty."""
    row = conn.execute(
        "SELECT * FROM tax_loss_carryforward WHERE id=?", (loss_id,)).fetchone()
    if row is None:
        raise ValueError(f"Nie znaleziono straty o id={loss_id}")

    if amount_pln == 0.0:
        # Cofnięcie decyzji — usuń wpis, żadnej dalszej walidacji (zero zawsze
        # dozwolone, to zwykłe "nie odliczam nic w tym roku").
        conn.execute(
            "DELETE FROM tax_loss_deductions WHERE loss_id=? AND used_in_year=?",
            (loss_id, used_in_year))
        conn.commit()
        return

    if not (row["origin_year"] < used_in_year <= row["origin_year"] + LOSS_YEARS_WINDOW):
        raise ValueError(
            f"Rok {used_in_year} jest poza 5-letnim oknem odliczeń dla straty "
            f"z {row['origin_year']} (dostępne lata: "
            f"{row['origin_year'] + 1}-{row['origin_year'] + LOSS_YEARS_WINDOW})")

    avail = available_for_year(conn, cfg, used_in_year, policy=row["cost_basis_policy"])
    item = next((i for i in avail["items"] if i["loss_id"] == loss_id), None)
    if item is None:
        raise ValueError(
            f"Strata id={loss_id} nie jest dostępna do odliczenia w roku "
            f"{used_in_year} (wykorzystana w całości lub poza oknem)")

    allowed_max = item["max_deduction_pln"] + item["used_this_year_pln"]
    if amount_pln > allowed_max + _EPS_PLN:
        raise ValueError(
            f"Kwota {amount_pln} PLN przekracza dopuszczalne odliczenie "
            f"{allowed_max} PLN dla tej straty w roku {used_in_year}")

    income_pln = taxpolicy.compute_all_policies(
        conn, cfg, year=used_in_year)[row["cost_basis_policy"]]["income_pln"]
    if income_pln <= 0:
        raise ValueError(
            f"Rok {used_in_year} nie ma dochodu do pomniejszenia stratą "
            f"(dochód pod polityką {row['cost_basis_policy']} = {income_pln} PLN)")
    if amount_pln > income_pln + _EPS_PLN:
        raise ValueError(
            f"Kwota {amount_pln} PLN przekracza dochód roku {used_in_year} "
            f"({income_pln} PLN) — odliczenie nie może zejść poniżej zera dochodu")

    conn.execute(
        "INSERT INTO tax_loss_deductions (loss_id, used_in_year, amount_pln) "
        "VALUES (?,?,?) ON CONFLICT(loss_id, used_in_year) DO UPDATE "
        "SET amount_pln=excluded.amount_pln",
        (loss_id, used_in_year, amount_pln))
    conn.commit()


def is_year_closed(conn: sqlite3.Connection, year: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM tax_year_closed WHERE year=?", (year,)).fetchone() is not None


def close_year(conn: sqlite3.Connection, cfg: dict, year: int, total_due_pln: float) -> dict:
    """Zamyka rok podatkowy, zapisując migawkę `total_due_pln` (policzoną
    PRZEZ WYWOŁUJĄCEGO z `pit38.annual_report(conn, cfg, year)["total_due_pln"]`
    — `tax/losses.py` sam nie importuje `tax/pit38.py`, patrz uwaga o
    cyklu importów w docstringu modułu). Idempotentne: `INSERT OR REPLACE`
    — ponowne zamknięcie po zmianie danych po prostu nadpisuje migawkę i
    `closed_at`."""
    conn.execute(
        "INSERT OR REPLACE INTO tax_year_closed (year, total_due_pln_snapshot) "
        "VALUES (?, ?)", (year, total_due_pln))
    conn.commit()
    return {"year": year, "total_due_pln_snapshot": total_due_pln}


def reopen_year(conn: sqlite3.Connection, year: int) -> None:
    """Usuwa wpis zamknięcia. Celowo NIE dotyka `lots`/`sales`/
    `tax_loss_deductions` — ponowne otwarcie roku to tylko zdjęcie blokady
    edycji, nie cofnięcie żadnych zapisanych decyzji podatkowych."""
    conn.execute("DELETE FROM tax_year_closed WHERE year=?", (year,))
    conn.commit()


def wizard_steps(conn: sqlite3.Connection, cfg: dict, year: int, report: dict) -> list[dict]:
    """Kroki kreatora zamknięcia roku podatkowego. `report` to gotowy wynik
    `pit38.annual_report(conn, cfg, year)` dostarczony przez wywołującego
    (web.py) — patrz uwaga o cyklu importów w docstringu modułu, ten moduł
    sam nie importuje `tax/pit38.py`.

    Krok `losses` jest informacyjny, nie blokujący: `done=True` gdy nie ma
    żadnej dostępnej straty do odliczenia w tym roku, ALBO gdy user podjął
    JAKĄKOLWIEK jawną decyzję o odliczeniu (choćby częściową, niekoniecznie
    maksymalną — `total_used_this_year_pln > 0`). Intencja: upewnić się, że
    user zobaczył temat i świadomie zdecydował, nie że wycisnął maksimum."""
    steps: list[dict] = []

    has_import = conn.execute(
        "SELECT 1 FROM imports WHERE as_of_date >= ? LIMIT 1",
        (f"{year}-01-01",)).fetchone() is not None
    steps.append({
        "key": "import",
        "label": "Wgraj wyciąg za ten rok",
        "done": has_import,
        "detail_pl": (
            "Brak zaimportowanego wyciągu z datą 'as of' w tym roku."
            if not has_import else "Wyciąg za ten rok zaimportowany."),
    })

    unresolved_conflicts = conn.execute(
        "SELECT COUNT(*) FROM import_conflicts WHERE resolved=0").fetchone()[0]
    steps.append({
        "key": "conflicts",
        "label": "Rozstrzygnij konflikty importu",
        "done": unresolved_conflicts == 0,
        "detail_pl": (
            f"{unresolved_conflicts} nierozstrzygniętych konfliktów importu."
            if unresolved_conflicts else "Brak nierozstrzygniętych konfliktów importu."),
    })

    unresolved_balance = conn.execute(
        "SELECT COUNT(*) FROM import_conflicts WHERE resolved=0 AND entity_type='balance'"
    ).fetchone()[0]
    steps.append({
        "key": "balance",
        "label": "Sprawdź saldo vs wyciąg",
        "done": unresolved_balance == 0,
        "detail_pl": (
            f"{unresolved_balance} nierozstrzygniętych konfliktów salda."
            if unresolved_balance else "Saldo zgodne z wyciągiem."),
    })

    has_estimated = report["section_g"]["has_estimated"]
    steps.append({
        "key": "dividends",
        "label": "Zweryfikuj sekcję G (dywidendy)",
        "done": not has_estimated,
        "detail_pl": (
            "Sekcja G zawiera dywidendy oznaczone jako szacunek — zweryfikuj ręcznie."
            if has_estimated else "Sekcja G bez szacunków, wszystkie dywidendy zmierzone."),
    })

    avail = available_for_year(conn, cfg, year, policy=cfg.get("cost_basis_policy", "own_only"))
    losses_done = avail["total_remaining_pln"] <= _EPS_PLN or avail["total_used_this_year_pln"] > 0
    steps.append({
        "key": "losses",
        "label": "Sprawdź straty z lat ubiegłych",
        "done": losses_done,
        "detail_pl": (
            "Brak dostępnych strat z lat ubiegłych do odliczenia."
            if avail["total_remaining_pln"] <= _EPS_PLN else
            (f"Dostępne {avail['total_remaining_pln']} PLN straty — podjęto decyzję o odliczeniu."
             if losses_done else
             f"Dostępne {avail['total_remaining_pln']} PLN straty do odliczenia, jeszcze nie zdecydowano.")),
    })

    return steps
