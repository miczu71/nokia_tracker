"""Prosty kalkulator podatku od dywidend 0.1.0 — u źródła w Finlandii,
zaliczenie w Polsce ograniczone do stawki traktatowej, kwota do odzyskania
z fińskiego Vero (BLUEPRINT §2, sekcja "Dywidendy i podatki").

Świadome uproszczenie: liczy na kursie bieżącym (eurpln_rate prezentacyjny),
NIE na zamrożonym kursie NBP D-1 wymaganym przez art. 11a ustawy o PIT dla
faktycznego rozliczenia — to dochodzi w kroku 11 (0.2.0) razem z resztą
silnika podatkowego opartego na lotach. Wartości tutaj są orientacyjne/
edukacyjne, nie do bezpośredniego wpisania do PIT-38 — patrz DISCLAIMER
w ai/prompts.py, ten sam duch dotyczy tej funkcji.

Zweryfikowane względem przykładu z BLUEPRINT: 100 EUR brutto, 35% u źródła
-> 65 EUR netto, zaliczenie 15 EUR, Belka 19 EUR -> 4 EUR dopłaty w PL,
20 EUR do odzyskania z Vero.
"""
from __future__ import annotations

import sqlite3

from ..providers import fx_nbp
from . import lots as taxlots


def add_dividend(conn: sqlite3.Connection, record_date: str, purchase_date: str,
                 entitled_quantity: float, gross_eur: float, taxes_eur: float,
                 fees_eur: float, reinvested_eur: float, purchase_price_eur: float,
                 purchased_shares: float, natural_key: str | None = None) -> int:
    """Zapisuje dywidendę (rejestr, krok 13) + tworzy JEDNOCZEŚNIE lot `dividend_drip`
    (DRIP nie ma odroczonego vestingu jak ESPP match/LTI — reinwestycja wykonuje się
    natychmiast, więc lot powstaje od razu, nie przez scheduler kroku 14).

    `withholding_pct` liczone z REALNYCH `taxes_eur/gross_eur` per wiersz (dokładniejsze
    niż stała z ustawień — potwierdzone na 5 niezależnych dywidendach w zakresie
    34,9-35,0%, patrz BLUEPRINT §3a). Kurs NBP zamrożony na Record Date (dzień uzyskania
    przychodu wg art. 11a), NIE na Purchase Date (dzień reinwestycji).

    `pl_tax_due_pln` (zaliczenie stawki traktatowej + Belka) celowo zostaje `NULL` —
    wymaga ustawień treaty/Belka z configu, to zakres orkiestracji kroku 14
    (`tax/dividends.py`: u źródła/zaliczenie/odzysk z Vero), nie samego zapisu do rejestru.
    """
    if natural_key is None:
        natural_key = f"dividend:{record_date}:{purchase_date}:{entitled_quantity}"
    existing = conn.execute(
        "SELECT id FROM dividends WHERE natural_key = ?", (natural_key,)).fetchone()
    if existing:
        return existing["id"]

    withholding_pct = (taxes_eur / gross_eur * 100) if gross_eur else None
    withholding_paid_eur = taxes_eur
    net_received_eur = gross_eur - taxes_eur

    rate = fx_nbp.rate_for_event(conn, record_date)
    nbp_rate, nbp_rate_date = rate if rate else (None, None)
    gross_pln = gross_eur * nbp_rate if nbp_rate is not None else None

    cur = conn.execute(
        "INSERT INTO dividends (pay_date, quantity, gross_eur, withholding_pct, "
        "withholding_paid_eur, net_received_eur, nbp_rate, nbp_rate_date, gross_pln, "
        "natural_key) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (record_date, entitled_quantity, gross_eur, withholding_pct, withholding_paid_eur,
         net_received_eur, nbp_rate, nbp_rate_date, gross_pln, natural_key))
    dividend_id = cur.lastrowid
    conn.commit()

    drip_natural_key = f"drip:{record_date}:{purchase_date}:{entitled_quantity}"
    lot_id = taxlots.add_lot(
        conn, purchase_date, "dividend_drip", purchased_shares, purchase_price_eur,
        natural_key=drip_natural_key)
    conn.execute(
        "UPDATE dividends SET reinvested_lot_id = ? WHERE id = ?", (lot_id, dividend_id))
    conn.commit()
    return dividend_id


def compute_dividend_tax(gross_eur: float, withholding_pct: float,
                         treaty_withholding_pct: float,
                         pl_capital_gains_tax_pct: float) -> dict:
    withholding_paid_eur = gross_eur * withholding_pct / 100
    net_received_eur = gross_eur - withholding_paid_eur

    treaty_cap_eur = gross_eur * treaty_withholding_pct / 100
    credit_eur = min(withholding_paid_eur, treaty_cap_eur)
    belka_eur = gross_eur * pl_capital_gains_tax_pct / 100
    pl_tax_due_eur = max(0.0, belka_eur - credit_eur)
    reclaimable_from_finland_eur = max(0.0, withholding_paid_eur - treaty_cap_eur)

    return {
        "withholding_paid_eur": round(withholding_paid_eur, 2),
        "net_received_eur": round(net_received_eur, 2),
        "pl_tax_due_eur": round(pl_tax_due_eur, 2),
        "reclaimable_from_finland_eur": round(reclaimable_from_finland_eur, 2),
    }


def compute_dividend_tax_pln(row, cfg: dict) -> dict:
    """Krok 15 — sekcja G PIT-38: ten sam łańcuch co `compute_dividend_tax()`
    (u źródła -> zaliczenie ograniczone stawką traktatową -> Belka -> dopłata
    w PL / kwota do odzysku z Vero), ale liczony w PLN na `row['gross_pln']`,
    czyli na kursie NBP ZAMROŻONYM na Record Date (art. 11a) przez
    `add_dividend()`. `row` może być `sqlite3.Row` lub `dict` — używa tylko
    `gross_pln`/`withholding_pct`.

    Zamrożony jest kurs, NIE stawki procentowe: `treaty_withholding_pct`/
    `pl_capital_gains_tax_pct` z `cfg` stosowane są w momencie wywołania, więc
    zmiana ustawień w UI przelicza kwoty PLN na nowo (po kursach z dnia
    zdarzenia) — to celowe, nie niedopatrzenie (patrz `backfill_pl_tax_due`).

    Zwraca `None` dla obu kwot, gdy `gross_pln` jeszcze nie istnieje (kurs
    NBP nie został jeszcze zamrożony, np. NBP było niedostępne przy zapisie).
    """
    gross_pln = row["gross_pln"]
    if gross_pln is None:
        return {"pl_tax_due_pln": None, "reclaimable_from_finland_pln": None}

    withholding_pct = row["withholding_pct"]
    if withholding_pct is None:
        withholding_pct = cfg.get("finnish_withholding_pct", 35.0)

    withholding_paid_pln = gross_pln * withholding_pct / 100
    treaty_cap_pln = gross_pln * cfg["treaty_withholding_pct"] / 100
    credit_pln = min(withholding_paid_pln, treaty_cap_pln)
    belka_pln = gross_pln * cfg["pl_capital_gains_tax_pct"] / 100
    pl_tax_due_pln = max(0.0, belka_pln - credit_pln)
    reclaimable_from_finland_pln = max(0.0, withholding_paid_pln - treaty_cap_pln)

    return {
        "withholding_paid_pln": round(withholding_paid_pln, 2),
        "belka_pln": round(belka_pln, 2),
        "credit_pln": round(credit_pln, 2),
        "pl_tax_due_pln": round(pl_tax_due_pln, 2),
        "reclaimable_from_finland_pln": round(reclaimable_from_finland_pln, 2),
    }


def backfill_pl_tax_due(conn: sqlite3.Connection, cfg: dict) -> int:
    """Przelicza `pl_tax_due_pln` dla wszystkich dywidend z już zamrożonym
    kursem NBP (`gross_pln IS NOT NULL`) na podstawie AKTUALNYCH stawek
    traktat/Belka z `cfg`.

    W odróżnieniu od `tax/lots.py::backfill_missing_rates` (które nigdy nie
    nadpisuje zamrożonego kursu, bo kurs jest prawnie zamrożony na dobre),
    to przeliczenie robi się na nowo za KAŻDYM wywołaniem — to czysta
    arytmetyka na już zamrożonym `gross_pln`, zero wywołań NBP, więc zmiana
    ustawień w UI ma się odzwierciedlić natychmiast po następnym backfillu
    (job schedulera lub odczyt strony `/pit38`), a nie zostać w zawieszeniu
    do ręcznej edycji rekordu. Wiersze bez zamrożonego kursu (`gross_pln IS
    NULL`) są pomijane — nie ma z czego policzyć, `backfill_missing_rates`
    dla lotów to inny mechanizm i nie dotyczy tabeli `dividends`.

    Zwraca liczbę zaktualizowanych wierszy."""
    rows = conn.execute(
        "SELECT id, gross_pln, withholding_pct FROM dividends "
        "WHERE gross_pln IS NOT NULL").fetchall()
    updated = 0
    for row in rows:
        result = compute_dividend_tax_pln(row, cfg)
        conn.execute(
            "UPDATE dividends SET pl_tax_due_pln = ? WHERE id = ?",
            (result["pl_tax_due_pln"], row["id"]))
        updated += 1
    conn.commit()
    return updated
