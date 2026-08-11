"""Parser wyciągów Computershare „Plan Holdings Statement" (krok 13, BLUEPRINT §3a).

Strategia: dopasowanie PO KSZTAŁCIE wiersza (regex na liczbę dat/wartości), nie po
rekonstrukcji nagłówka. Zweryfikowane empirycznie na 5 realnych plikach użytkownika
(pomiar pozycji znaków, nie zgadywanie z kolejności czytania — patrz docs/PLAN_KROK_13.md):
nagłówki w `extraction_mode="layout"` bywają zawinięte na 3-4 linie w kolejności, która
w tekście wypada PO wierszach danych (nie przed), a tytuły sekcji ("Purchases",
"Withhold-to-Cover") potrafią wystąpić w dowolnym miejscu względem swoich wierszy. Każdy
typ wiersza ma za to unikalny, stały kształt — rozpoznanie nie zależy od kontekstu wcale.

Kolumny „Purchases" (potwierdzone pomiarem pozycji, nie samą kolejnością w strumieniu PDF —
tryb `default` pypdf jest w tym mylący): Allocation Date, Contribution Date, Trade Date,
Settlement Date, Contribution Amount (EUR), Residual Amount Previous (EUR), Fees (EUR),
Fair Market Value (EUR), Purchase Price (EUR), Purchased Shares (ilość), Residual Amount
finalne (EUR). `lots.acquired_date` = Trade Date (dzień faktycznego wykonania zakupu).

Withhold-to-Cover ma DWA różne kształty w realnych danych:
- Typ A (etykieta instrumentu "Nokia Share", `Net Units == Quantity`) — zero podatku
  potrąconego TERAZ (art. 24 ust. 11 ustawy o PIT, opodatkowanie odroczone do zbycia), ale
  akcje realnie stają się własnością — TWORZY lot (krok 13.6, docs/PLAN_KROK_13_6_vesting_gap.md).
- Typ B (bez etykiety instrumentu, kolumny Sale Proceeds/Net proceeds w EUR) — prawdziwa,
  gotówkowa sprzedaż. Nigdy nie księgowana automatycznie — zawsze do ręcznego potwierdzenia.

Tabele „Matching Shares"/„RS AWARD" pokazują WYŁĄCZNIE transze wciąż oczekujące na dzień
wygenerowania wyciągu — raz zvestowana transza znika z nich na zawsze i przenosi się do
tabeli „Vested Matching Shares" (powtarzający się snapshot aktualnie posiadanego salda, nie
harmonogram) oraz/lub do Withhold-to-Cover Typu A (patrz `parse_vested_matching_shares`,
krok 13.6). Bez czytania obu tych źródeł zvestowane akcje nigdy nie stają się `lots` —
potwierdzone empirycznie na realnych danych użytkownika jako przyczyna `InsufficientLotsError`
przy księgowaniu sprzedaży, która historycznie się powiodła.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime
from io import BytesIO

from pypdf import PdfReader

from ..tax import dividends as taxdiv
from ..tax import grants as grantsm
from ..tax import lots as taxlots

logger = logging.getLogger(__name__)

_EPS = 1e-9

# --- prymitywy liczbowe/datowe, wspólne dla wszystkich kształtów wierszy ---
_DATE = r"\d{1,2}\s*[A-Za-z]{3}\s*\d{4}"
# Separator tysięcy bywa spacją zwykłą ALBO cienką spacją Unicode U+2009 (zaobserwowane
# empirycznie w realnych plikach, np. "1 036.84") — \s łapie obie.
_NUM = r"-?\d+(?:\s\d{3})*\.\d+"              # kwota/ilość z kropką dziesiętną
_INT = r"-?\d+(?:\s\d{3})*(?:\.\d+)?"         # jak wyżej, ale kropka opcjonalna (Withhold Typ A)
_SEP = r"[ \t]{2,}"                           # separator kolumn: 2+ zwykłe spacje/taby (odróżnia
                                               # od pojedynczej spacji separatora tysięcy w liczbie)


def _date_iso(raw: str) -> str:
    """'27 Oct 2025' / '4Feb  2026' -> '2025-10-27'. Toleruje brak spacji dzień/miesiąc."""
    compact = re.sub(r"\s+", " ", raw.strip())
    m = re.match(r"(\d{1,2})\s*([A-Za-z]{3})\s*(\d{4})", compact)
    day, mon, year = m.group(1), m.group(2), m.group(3)
    dt = datetime.strptime(f"{day} {mon} {year}", "%d %b %Y")
    return dt.date().isoformat()


def _num(raw: str) -> float:
    return float(re.sub(r"\s", "", raw))


def extract_layout_text(pdf_bytes: bytes) -> str:
    """Tekst wszystkich stron w trybie layout (zachowuje pozycję kolumn), sklejony."""
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text(extraction_mode="layout") for page in reader.pages)


_PERIOD_RE = re.compile(rf"({_DATE})\s*-\s*({_DATE})")
_AS_OF_RE = re.compile(rf"as of\s*({_DATE})", re.IGNORECASE)


def parse_document_meta(text: str) -> dict:
    """period_start/period_end (zakres wyciągu) + as_of_date (dzień wygenerowania)."""
    period = _PERIOD_RE.search(text)
    as_of = _AS_OF_RE.search(text)
    return {
        "period_start": _date_iso(period.group(1)) if period else None,
        "period_end": _date_iso(period.group(2)) if period else None,
        "as_of_date": _date_iso(as_of.group(1)) if as_of else None,
    }


# --- Purchases: 4 daty + 7 wartości (Contribution Amount, Residual Previous, Fees,
# Fair Market Value, Purchase Price, Purchased Shares, Residual Amount finalne) ---
_PURCHASE_RE = re.compile(
    rf"^({_DATE}){_SEP}({_DATE}){_SEP}({_DATE}){_SEP}({_DATE}){_SEP}"
    rf"({_NUM})\s*EUR{_SEP}({_NUM})\s*EUR{_SEP}({_NUM})\s*EUR{_SEP}"
    rf"({_NUM})\s*EUR{_SEP}({_NUM})\s*EUR{_SEP}({_NUM}){_SEP}({_NUM})\s*EUR\s*$"
)


def parse_purchases(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        m = _PURCHASE_RE.match(line.strip())
        if not m:
            continue
        g = m.groups()
        rows.append({
            "allocation_date": _date_iso(g[0]),
            "contribution_date": _date_iso(g[1]),
            "trade_date": _date_iso(g[2]),
            "settlement_date": _date_iso(g[3]),
            "contribution_amount_eur": _num(g[4]),
            "residual_amount_previous_eur": _num(g[5]),
            "fees_eur": _num(g[6]),
            "fair_market_value_eur": _num(g[7]),
            "purchase_price_eur": _num(g[8]),
            "quantity": _num(g[9]),
            "residual_amount_eur": _num(g[10]),
        })
    return rows


# --- Matching Shares (ESPP): label + 3 daty (Allocation/Vesting/Available from) + qty + PLN ---
_MATCHING_RE = re.compile(
    rf"^Matching\s+Shares{_SEP}({_DATE}){_SEP}({_DATE}){_SEP}({_DATE}){_SEP}"
    rf"({_NUM}){_SEP}({_NUM})\s*PLN\s*$"
)


def parse_matching_shares(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        m = _MATCHING_RE.match(line.strip())
        if not m:
            continue
        g = m.groups()
        rows.append({
            "allocation_date": _date_iso(g[0]),
            "vesting_date": _date_iso(g[1]),
            "available_from": _date_iso(g[2]),
            "quantity": _num(g[3]),
            "estimated_value_pln": _num(g[4]),
        })
    return rows


# --- Vested Matching Shares: powtarzający się snapshot aktualnie posiadanego,
# już-zvestowanego salda dopasowań ESPP (NIE harmonogram - to już się wydarzyło).
# "Vested Dividend Shares" w tej samej tabeli świadomie pomijamy (patrz docstring
# import_statement) - już poprawnie pokryte przez parse_dividends/dividend_drip.
_VESTED_MATCHING_RE = re.compile(
    rf"^Vested\s+Matching\s+Shares{_SEP}({_DATE}){_SEP}"
    rf"({_NUM})\s*EUR{_SEP}({_NUM})\s*EUR{_SEP}({_NUM}){_SEP}({_NUM})\s*PLN\s*$"
)


def parse_vested_matching_shares(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        m = _VESTED_MATCHING_RE.match(line.strip())
        if not m:
            continue
        g = m.groups()
        rows.append({
            "vested_date": _date_iso(g[0]),
            "cost_basis_eur": _num(g[1]),
            "gain_per_share_eur": _num(g[2]),
            "quantity": _num(g[3]),
            "estimated_value_pln": _num(g[4]),
        })
    return rows


# --- Vested Dividend Shares: powtarzający się snapshot już-zvestowanego salda akcji z
# reinwestowanej dywidendy (krok 19). Źródło ZAPASOWE — wyciągi 2022-2024 nie mają sekcji
# "Dividend (Reinvested)" transakcyjnej (ta pojawia się dopiero od wyciągu 2025), więc
# import_statement() używa tego parsera TYLKO gdy parse_dividends() nie znalazł żadnego
# wiersza w całym dokumencie (patrz tam). Dane są uboższe niż transakcyjne (brak Gross/
# Taxes/Fees, tylko cena nabycia i ilość) — wystarczają na lot `dividend_drip` do FIFO/
# kosztu, ale NIE na sekcję G PIT-38 (podatek u źródła) tych lat — to ograniczenie
# samego źródła (Computershare), nie błąd parsera.
_VESTED_DIVIDEND_RE = re.compile(
    rf"^Vested\s+Dividend\s+Shares{_SEP}({_DATE}){_SEP}"
    rf"({_NUM})\s*EUR{_SEP}({_NUM})\s*EUR{_SEP}({_NUM}){_SEP}({_NUM})\s*PLN\s*$"
)


def parse_vested_dividend_shares(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        m = _VESTED_DIVIDEND_RE.match(line.strip())
        if not m:
            continue
        g = m.groups()
        rows.append({
            "vested_date": _date_iso(g[0]),
            "cost_basis_eur": _num(g[1]),
            "gain_per_share_eur": _num(g[2]),
            "quantity": _num(g[3]),
            "estimated_value_pln": _num(g[4]),
        })
    return rows


# --- RS AWARD (LTI): "<rok> RS AWARD <DD-MON(TH)-RRRR>" + 3 daty + qty + PLN ---
_RS_AWARD_RE = re.compile(
    rf"^(\d{{4}}\s+RS\s+AWARD\s+[\d\-A-Za-z]+){_SEP}({_DATE}){_SEP}({_DATE}){_SEP}({_DATE}){_SEP}"
    rf"({_NUM}){_SEP}({_NUM})\s*PLN\s*$"
)


def parse_rs_award(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        m = _RS_AWARD_RE.match(line.strip())
        if not m:
            continue
        g = m.groups()
        rows.append({
            "participation_description": re.sub(r"\s+", " ", g[0]).strip(),
            "allocation_date": _date_iso(g[1]),
            "vesting_date": _date_iso(g[2]),
            "available_from": _date_iso(g[3]),
            "quantity": _num(g[4]),
            "estimated_value_pln": _num(g[5]),
        })
    return rows


# --- Dividend (Reinvested): 2 daty + entitled qty + 7 wartości (Gross Dividend Payment,
# Taxes, Fees, Dividend Reinvested, Purchase Price, Purchased shares, Residual Amount) ---
_DIVIDEND_RE = re.compile(
    rf"^({_DATE}){_SEP}({_DATE}){_SEP}({_NUM}){_SEP}"
    rf"({_NUM})\s*EUR{_SEP}({_NUM})\s*EUR{_SEP}({_NUM})\s*EUR{_SEP}"
    rf"({_NUM})\s*EUR{_SEP}({_NUM})\s*EUR{_SEP}({_NUM}){_SEP}({_NUM})\s*EUR\s*$"
)


def parse_dividends(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        m = _DIVIDEND_RE.match(line.strip())
        if not m:
            continue
        g = m.groups()
        rows.append({
            "record_date": _date_iso(g[0]),
            "purchase_date": _date_iso(g[1]),
            "entitled_quantity": _num(g[2]),
            "gross_dividend_payment_eur": _num(g[3]),
            "taxes_eur": _num(g[4]),
            "fees_eur": _num(g[5]),
            "dividend_reinvested_eur": _num(g[6]),
            "purchase_price_eur": _num(g[7]),
            "purchased_shares": _num(g[8]),
            "residual_amount_eur": _num(g[9]),
        })
    return rows


# --- Withhold-to-Cover Typ A: data + "Nokia Share" + qty + Sale Price + Taxes + Fees + Net Units
# (zero-efektowe potwierdzenie, Net Units == Quantity) ---
_WITHHOLD_A_RE = re.compile(
    rf"^({_DATE}){_SEP}Nokia\s+Share{_SEP}({_INT}){_SEP}"
    rf"({_NUM})\s*EUR{_SEP}({_NUM})\s*EUR{_SEP}({_NUM})\s*EUR{_SEP}({_INT})\s*$"
)

# --- Withhold-to-Cover Typ B: data + qty + Sale Price + Sale Proceeds + Taxes + Fees +
# Net proceeds (prawdziwa sprzedaż gotówkowa — NIGDY nie księgowana automatycznie) ---
_WITHHOLD_B_RE = re.compile(
    rf"^({_DATE}){_SEP}({_INT}){_SEP}"
    rf"({_NUM})\s*EUR{_SEP}({_NUM})\s*EUR{_SEP}({_NUM})\s*EUR{_SEP}({_NUM})\s*EUR{_SEP}"
    rf"({_NUM})\s*EUR\s*$"
)


def parse_withhold_to_cover(text: str) -> tuple[list[dict], list[dict]]:
    """Zwraca (type_a_confirmations, type_b_real_sales). Typ B nigdy nie jest księgowany
    automatycznie — wywołujący (import_statement) ma go zawsze kierować do kolejki
    ręcznego potwierdzenia (import_conflicts)."""
    type_a: list[dict] = []
    type_b: list[dict] = []
    for line in text.splitlines():
        stripped = line.strip()
        m = _WITHHOLD_A_RE.match(stripped)
        if m:
            g = m.groups()
            type_a.append({
                "execution_date": _date_iso(g[0]),
                "quantity": _num(g[1]),
                "sale_price_eur": _num(g[2]),
                "taxes_eur": _num(g[3]),
                "fees_eur": _num(g[4]),
                "net_units": _num(g[5]),
            })
            continue
        m = _WITHHOLD_B_RE.match(stripped)
        if m:
            g = m.groups()
            type_b.append({
                "execution_date": _date_iso(g[0]),
                "quantity": _num(g[1]),
                "sale_price_eur": _num(g[2]),
                "sale_proceeds_eur": _num(g[3]),
                "taxes_eur": _num(g[4]),
                "fees_eur": _num(g[5]),
                "net_proceeds_eur": _num(g[6]),
            })
    return type_a, type_b


# --- krok 19: kontrola krzyżowa salda (BLUEPRINT §3a) — strona 1 wyciągu, sekcja
# "Assets by type", etykieta "Shares". Liczba stoi na linii BEZPOŚREDNIO PRZED linią
# zaczynającą się od "Shares" (małe wcięcie - odróżnia od nagłówka kolumny "Shares /
# Total" w tabelach "Available for trading" dalej w dokumencie, który ma duże wcięcie),
# w tej samej pozycji kolumnowej (layout mode zachowuje wyrównanie). Ograniczone do
# "Shares" (widok wg TYPU) celowo — "Assets by plan" pokazuje te same akcje jeszcze raz
# z innej strony (per plan), sumowanie obu widoków podwoiłoby liczbę.
_SHARES_LABEL_RE = re.compile(r"^\s{0,3}Shares\b")
_LEADING_NUM_RE = re.compile(rf"^\s*({_NUM})")


def parse_shares_total(text: str) -> float | None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if i > 0 and _SHARES_LABEL_RE.match(line):
            m = _LEADING_NUM_RE.match(lines[i - 1])
            if m:
                return _num(m.group(1))
    return None


def reconcile_holdings(conn: sqlite3.Connection, text: str, as_of_date: str | None,
                        import_id: int) -> bool:
    """Kontrola krzyżowa BLUEPRINT §3a: SUM(qty_remaining) WSZYSTKICH lotów vs "Shares"
    ze strony 1 TEGO wyciągu. Zweryfikowane na realnych danych (krok 19): loty 'lti'
    (RS Award) SĄ wliczane — raz zvestowane (Withhold-to-Cover Typ A) przechodzą z
    bucketu "Restricted Shares" do zwykłego "Shares", tak samo jak dopasowania ESPP;
    wcześniejsze założenie, że zostają osobno, było błędne i dawało fałszywe alarmy.

    Odejmuje ilości z NIEROZSTRZYGNIĘTYCH konfliktów Withhold-to-Cover Typu B
    (`entity_type='withhold_to_cover_sale'`) — Computershare pokazuje je jako już
    sprzedane w swoim saldzie, a nasza baza świadomie NIE księguje ich automatycznie
    dopóki użytkownik ręcznie nie potwierdzi (patrz `parse_withhold_to_cover`) — bez tego
    każda nierozstrzygnięta prawdziwa sprzedaż wyglądałaby jak rozjazd danych.

    Uruchamia się tylko, gdy `as_of_date` tego importu jest NAJNOWSZY spośród
    wszystkich dotychczasowych importów (inaczej porównanie starego zdjęcia salda z
    pełną, dzisiejszą bazą byłoby mylące — analogicznie do reguły „kontrola sumy używa
    najnowszego pliku, nie ostatnio wgranego" z BLUEPRINT §3a).

    Tolerancja 2,0 akcji (nie 0,01) — `parse_vested_dividend_shares` (źródło zapasowe
    dla dywidend 2022-2024) ma udokumentowaną precyzję ~0,01/wiersz, która realnie
    kumuluje się przez lata; to diagnostyka najlepszego wysiłku, nie księgowość co do
    grosza (patrz `_check_dividend_arithmetic` dla tej samej filozofii).

    Nie blokuje importu — rozjazd trafia do `import_conflicts` (`entity_type='balance'`),
    widoczny jako pozycja w kolejce konfliktów na /imports. Zwraca True, jeśli zapisano
    ostrzeżenie."""
    expected = parse_shares_total(text)
    if expected is None or as_of_date is None:
        return False

    latest_known = conn.execute(
        "SELECT MAX(as_of_date) d FROM imports WHERE as_of_date IS NOT NULL").fetchone()["d"]
    if latest_known is not None and as_of_date < latest_known:
        return False

    actual = conn.execute(
        "SELECT COALESCE(SUM(qty_remaining), 0) t FROM lots").fetchone()["t"]
    pending_sales = conn.execute(
        "SELECT incoming_json FROM import_conflicts "
        "WHERE entity_type = 'withhold_to_cover_sale' AND resolved = 0").fetchall()
    for row in pending_sales:
        actual -= json.loads(row["incoming_json"]).get("quantity", 0.0)

    if abs(actual - expected) <= 2.0:
        return False

    nk = f"balance:{as_of_date}"
    return _record_conflict(
        conn, import_id, "balance", nk,
        {"qty_remaining_total_minus_pending_sales": round(actual, 4)},
        {"shares_total_from_pdf": expected, "as_of_date": as_of_date})


def _record_conflict(conn: sqlite3.Connection, import_id: int, entity_type: str,
                      natural_key: str, existing: dict, incoming: dict) -> bool:
    """Wstawia wiersz do import_conflicts, chyba że natural_key tego typu już tam czeka
    na rozstrzygnięcie (idempotencja konfliktu — ponowny import tego samego pliku nie
    duplikuje wpisu w kolejce). Zwraca True, jeśli faktycznie wstawiono nowy wiersz."""
    dup = conn.execute(
        "SELECT id FROM import_conflicts WHERE entity_type = ? AND natural_key = ?",
        (entity_type, natural_key)).fetchone()
    if dup:
        return False
    conn.execute(
        "INSERT INTO import_conflicts (import_id, entity_type, natural_key, existing_json, "
        "incoming_json) VALUES (?,?,?,?,?)",
        (import_id, entity_type, natural_key, json.dumps(existing), json.dumps(incoming)))
    conn.commit()
    return True


def _check_dividend_arithmetic(row: dict) -> None:
    """Sanity-check, nie twardy gate: Gross - Taxes - Fees powinno się równać Dividend
    Reinvested + Residual Amount. Łapie błędy pozycjonowania kolumn, nie blokuje importu —
    pełna kontrola krzyżowa portfela wraca w kroku 14 (patrz docs/PLAN_KROK_13.md)."""
    lhs = row["gross_dividend_payment_eur"] - row["taxes_eur"] - row["fees_eur"]
    rhs = row["dividend_reinvested_eur"] + row["residual_amount_eur"]
    if abs(lhs - rhs) > 0.02:
        logger.warning(
            "Dywidenda %s: arytmetyka się nie zgadza (gross-taxes-fees=%.2f, "
            "reinvested+residual=%.2f) — możliwy błąd parsowania kolumn",
            row["record_date"], lhs, rhs)


def import_statement(conn: sqlite3.Connection, pdf_bytes: bytes, filename: str,
                      cfg: dict | None = None) -> dict:
    """Importuje jeden wyciąg Computershare: parsuje wszystkie sekcje i UPSERT-uje do
    lots/grants/vests/dividends z detekcją konfliktu (nigdy nie nadpisuje cicho — patrz
    docs/PLAN_KROK_13.md). Zwraca {import_id, rows_inserted, rows_unchanged, rows_conflict}.

    Ten sam plik zaimportowany drugi raz: wszystkie natural_key już istnieją z identycznymi
    wartościami → same rows_unchanged, zero duplikatów."""
    espp_match_pct = (cfg or {}).get("espp_match_pct", 50.0)

    text = extract_layout_text(pdf_bytes)
    meta = parse_document_meta(text)
    file_sha256 = hashlib.sha256(pdf_bytes).hexdigest()

    cur = conn.execute(
        "INSERT INTO imports (filename, file_sha256, period_start, period_end, as_of_date) "
        "VALUES (?,?,?,?,?)",
        (filename, file_sha256, meta["period_start"], meta["period_end"], meta["as_of_date"]))
    import_id = cur.lastrowid
    conn.commit()

    rows_inserted = rows_unchanged = rows_conflict = 0

    for row in parse_purchases(text):
        nk = f"purchase:{row['contribution_date']}:{row['trade_date']}:{row['quantity']}"
        existing = conn.execute("SELECT * FROM lots WHERE natural_key = ?", (nk,)).fetchone()
        if existing is None:
            taxlots.add_lot(
                conn, row["trade_date"], "own", row["quantity"], row["purchase_price_eur"],
                fee_eur=row["fees_eur"], source="pdf_import", natural_key=nk)
            rows_inserted += 1
        elif (existing["acquired_date"] == row["trade_date"]
              and abs(existing["quantity"] - row["quantity"]) < _EPS
              and abs(existing["price_eur"] - row["purchase_price_eur"]) < _EPS):
            rows_unchanged += 1
        elif _record_conflict(conn, import_id, "lot", nk, dict(existing), row):
            rows_conflict += 1

    for row in parse_matching_shares(text):
        grant_nk = f"espp_grant:{row['allocation_date']}:{row['quantity']}"
        existing_grant = grantsm.find_grant_by_natural_key(conn, grant_nk)
        if existing_grant is None:
            grant_id = grantsm.add_grant(
                conn, "espp", row["allocation_date"], row["quantity"], grant_nk,
                match_pct=espp_match_pct)
            rows_inserted += 1
        else:
            grant_id = existing_grant["id"]
            rows_unchanged += 1

        vest_nk = f"espp_vest:{row['allocation_date']}:{row['vesting_date']}:{row['quantity']}"
        existing_vest = grantsm.find_vest_by_natural_key(conn, vest_nk)
        if existing_vest is None:
            grantsm.add_vest(conn, grant_id, row["vesting_date"], row["quantity"], vest_nk)
            rows_inserted += 1
        elif abs(existing_vest["quantity"] - row["quantity"]) < _EPS:
            rows_unchanged += 1
        elif _record_conflict(conn, import_id, "vest", vest_nk, dict(existing_vest), row):
            rows_conflict += 1

    for row in parse_vested_matching_shares(text):
        nk = f"vested_matching:{row['vested_date']}:{row['cost_basis_eur']}:{row['quantity']}"
        existing = conn.execute("SELECT * FROM lots WHERE natural_key = ?", (nk,)).fetchone()
        if existing is None:
            taxlots.add_lot(
                conn, row["vested_date"], "matched", row["quantity"], row["cost_basis_eur"],
                source="pdf_import", natural_key=nk)
            rows_inserted += 1
        elif (abs(existing["quantity"] - row["quantity"]) < _EPS
              and abs(existing["price_eur"] - row["cost_basis_eur"]) < _EPS):
            rows_unchanged += 1
        elif _record_conflict(conn, import_id, "lot", nk, dict(existing), row):
            rows_conflict += 1

    for row in parse_rs_award(text):
        # grant.quantity zostaje NULL: jeden grant LTI zbiera wiele transz (wiele wierszy
        # RS AWARD), każda z własną ilością w vests — suma per grant liczona z SUM(vests),
        # nie z tego pojedynczego wiersza (patrz docs/PLAN_KROK_13.md).
        grant_nk = f"lti_grant:{row['participation_description']}"
        existing_grant = grantsm.find_grant_by_natural_key(conn, grant_nk)
        if existing_grant is None:
            grant_id = grantsm.add_grant(
                conn, "lti", row["allocation_date"], None, grant_nk)
            rows_inserted += 1
        else:
            grant_id = existing_grant["id"]
            rows_unchanged += 1

        vest_nk = f"lti_vest:{row['participation_description']}:{row['vesting_date']}:{row['quantity']}"
        existing_vest = grantsm.find_vest_by_natural_key(conn, vest_nk)
        if existing_vest is None:
            grantsm.add_vest(conn, grant_id, row["vesting_date"], row["quantity"], vest_nk)
            rows_inserted += 1
        elif abs(existing_vest["quantity"] - row["quantity"]) < _EPS:
            rows_unchanged += 1
        elif _record_conflict(conn, import_id, "vest", vest_nk, dict(existing_vest), row):
            rows_conflict += 1

    for row in parse_dividends(text):
        _check_dividend_arithmetic(row)
        nk = f"dividend:{row['record_date']}:{row['purchase_date']}:{row['entitled_quantity']}"
        existing = conn.execute(
            "SELECT * FROM dividends WHERE natural_key = ?", (nk,)).fetchone()
        if existing is None:
            taxdiv.add_dividend(
                conn, record_date=row["record_date"],
                entitled_quantity=row["entitled_quantity"],
                gross_eur=row["gross_dividend_payment_eur"], taxes_eur=row["taxes_eur"],
                fees_eur=row["fees_eur"], reinvested_eur=row["dividend_reinvested_eur"],
                purchase_date=row["purchase_date"],
                purchase_price_eur=row["purchase_price_eur"],
                purchased_shares=row["purchased_shares"], natural_key=nk)
            rows_inserted += 1
        elif abs(existing["gross_eur"] - row["gross_dividend_payment_eur"]) < _EPS:
            rows_unchanged += 1
        elif _record_conflict(conn, import_id, "dividend", nk, dict(existing), row):
            rows_conflict += 1

    dividend_transaction_rows = parse_dividends(text)
    if not dividend_transaction_rows:
        # Wyciąg bez sekcji "Dividend (Reinvested)" transakcyjnej (2022-2024) - jedyne
        # źródło reinwestowanej dywidendy to snapshot "Vested Dividend Shares". Tworzy
        # tylko lot dividend_drip (koszt/FIFO), NIE wiersz w `dividends` (brakuje Gross/
        # Taxes/Fees do sekcji G - patrz docstring parse_vested_dividend_shares).
        for row in parse_vested_dividend_shares(text):
            nk = f"vested_dividend:{row['vested_date']}:{row['cost_basis_eur']}:{row['quantity']}"
            existing = conn.execute(
                "SELECT * FROM lots WHERE natural_key = ?", (nk,)).fetchone()
            if existing is None:
                taxlots.add_lot(
                    conn, row["vested_date"], "dividend_drip", row["quantity"],
                    row["cost_basis_eur"], source="holdings_snapshot", natural_key=nk,
                    notes="Ilość/cena z podsumowania Vested Dividend Shares (wyciąg bez "
                          "sekcji transakcyjnej Dividend (Reinvested)) - precyzja ~0,01.")
                rows_inserted += 1
            elif (abs(existing["quantity"] - row["quantity"]) < _EPS
                  and abs(existing["price_eur"] - row["cost_basis_eur"]) < _EPS):
                rows_unchanged += 1
            elif _record_conflict(conn, import_id, "lot", nk, dict(existing), row):
                rows_conflict += 1

    vested_matching_dates = {row["vested_date"] for row in parse_vested_matching_shares(text)}

    type_a, type_b = parse_withhold_to_cover(text)
    for row in type_a:
        # Realne uwolnienie akcji (RS Award/LTI lub, gdy data pokrywa się z "Vested
        # Matching Shares" z tego samego wyciągu, wspólna kohorta dopasowań ESPP) - zero
        # podatku potrąconego teraz (art. 24 ust. 11 ustawy o PIT, opodatkowanie odroczone
        # do zbycia). Rozróżnienie matched/lti nie wpływa na żadną kwotę podatkową
        # (tax/policy.py::POLICIES traktuje je identycznie w każdej z 3 polityk) - służy
        # tylko czytelności /lots i /grants (krok 13.6, docs/PLAN_KROK_13_6_vesting_gap.md).
        lot_type = "matched" if row["execution_date"] in vested_matching_dates else "lti"
        nk = f"vested_release:{row['execution_date']}:{row['sale_price_eur']}:{row['quantity']}"
        existing = conn.execute("SELECT * FROM lots WHERE natural_key = ?", (nk,)).fetchone()
        if existing is None:
            taxlots.add_lot(
                conn, row["execution_date"], lot_type, row["quantity"], row["sale_price_eur"],
                source="pdf_import", natural_key=nk)
            rows_inserted += 1
            logger.info(
                "Withhold-to-Cover Typ A: %s, %.4f akcji @ %.2f EUR zaksięgowane jako lot '%s'",
                row["execution_date"], row["quantity"], row["sale_price_eur"], lot_type)
        elif (abs(existing["quantity"] - row["quantity"]) < _EPS
              and abs(existing["price_eur"] - row["sale_price_eur"]) < _EPS):
            rows_unchanged += 1
        elif _record_conflict(conn, import_id, "lot", nk, dict(existing), row):
            rows_conflict += 1
    for row in type_b:
        # Prawdziwa sprzedaż gotówkowa — NIGDY nie księgowana automatycznie.
        nk = f"wtc:{row['execution_date']}:{row['quantity']}:{row['net_proceeds_eur']}"
        if _record_conflict(conn, import_id, "withhold_to_cover_sale", nk, {}, row):
            rows_conflict += 1
            logger.warning(
                "Withhold-to-Cover Typ B (prawdziwa sprzedaż): %s, %.4f akcji, %.2f EUR "
                "netto — wymaga ręcznego potwierdzenia przez /lots/sell",
                row["execution_date"], row["quantity"], row["net_proceeds_eur"])

    if reconcile_holdings(conn, text, meta["as_of_date"], import_id):
        rows_conflict += 1

    conn.execute(
        "UPDATE imports SET rows_inserted = ?, rows_unchanged = ?, rows_conflict = ? "
        "WHERE id = ?",
        (rows_inserted, rows_unchanged, rows_conflict, import_id))
    conn.commit()

    return {
        "import_id": import_id,
        "rows_inserted": rows_inserted,
        "rows_unchanged": rows_unchanged,
        "rows_conflict": rows_conflict,
    }
