# Nokia Tracker — parsowanie potwierdzonego vestingu (Vested Matching Shares + Withhold-to-Cover Typ A)

## Context

Po odtworzeniu danych z re-importu (patrz incydent redeployu — cykl uninstall/reinstall add-onu skasował
SQLite, dane odtworzone ponownym wgraniem 5 PDF-ów przez użytkownika), próba zatwierdzenia realnej
sprzedaży 784 akcji z 27.10.2025 przez nowy przycisk „Zatwierdź jako sprzedaż"
zwróciła `InsufficientLotsError: dostępne 673.534678`. Użytkownik słusznie zauważył, że ta sprzedaż wydarzyła
się w przeszłości, więc brak pokrycia dziś nie powinien mieć miejsca — a przy okazji zgłosił, że `/grants`
pokazuje daty vestingu ESPP/LTI, które dawno minęły, wciąż jako „oczekuje".

Zdiagnozowałem to empirycznie (bez zmian w kodzie), parsując surowy tekst layout wszystkich 5 realnych
plików użytkownika (`/config/akcje_temp/*.pdf`, nigdy niecommitowane — patrz `test_computershare_pdf_real_files.py`):

**Root cause:** Sekcje „Matching Shares"/„RS AWARD" w wyciągu Computershare pokazują WYŁĄCZNIE transze
wciąż oczekujące na dzień wygenerowania wyciągu — raz zvestowana transza znika z tej tabeli na zawsze.
Zvestowane akcje trafiają do dwóch zupełnie innych miejsc, których obecny parser (`computershare_pdf.py`)
w ogóle nie czyta:

1. **Tabela „Vested Matching Shares"** — powtarzający się snapshot aktualnie posiadanego, już-zvestowanego
   salda dopasowań ESPP (kolumny: data, cost basis EUR, gain/share EUR, ilość, wartość PLN). Potwierdzone
   przykłady z realnych plików: `30 Aug 2023, 3.65 EUR, {8.21, 7.20, 9.09, 7.33}`; `29 Aug 2024, 3.79 EUR,
   {33.36, 28.48, 30.62, 30.00}`; `28 Aug 2025, 3.71 EUR, 0.48`. (Wiersze „Vested Dividend Shares" w tej
   samej tabeli NIE są nową informacją — dokładnie pokrywają się ilościowo z lotami `dividend_drip`, które
   już poprawnie tworzymy z sekcji „Dividend (Reinvested)" — pomijamy je celowo, żeby nie podwoić.)
2. **Withhold-to-Cover Typ A** (`parse_withhold_to_cover`, obecnie tylko logowane, nigdy nie zapisywane) —
   już wykrywane przez istniejący regex, ale traktowane jako „zero-efektowe potwierdzenie" i odrzucane.
   Potwierdzone przykłady: `28 Aug 2025, 101.396662 @ 3.71 EUR` (ta sama data i cena co powyższy wiersz
   0.48 — silny sygnał, że to WSPÓLNA kohorta matching-shares, rozbita między „withheld" i „pozostałe");
   `9 Jul 2026, 634 @ 10.22 EUR` i `9 Jul 2026, 2100 @ 10.22 EUR` (dokładnie te same ilości co dwie transze
   LTI, które wciąż wiszą jako „oczekuje" na `/grants` — to potwierdzenie realnego uwolnienia RS Award).

Żadna z tych zvestowanych/uwolnionych partii akcji nigdy nie stała się rzeczywistym `lots` — stąd
niedopokrycie sprzedaży. `tax/lots.py::open_lots`/`_allocate_fifo` sortują globalnie po `acquired_date`
(FIFO), więc dodanie lotów datowanych na 2026 nie wpłynie retroaktywnie na sprzedaż z 2025 — najstarsze
loty i tak pójdą pierwsze, więc kolejność chronologiczna jest bezpieczna sama z siebie, bez dodatkowej
walidacji "lot musi być starszy niż sprzedaż".

**Reguła klasyfikacji typu lotu dla Typ A** (potwierdzona na danych, nie zgadywana): jeśli data
`execution_date` wiersza Typ A pokrywa się z datą jakiegoś wiersza „Vested Matching Shares" z TEGO SAMEGO
wyciągu → `lot_type='matched'` (wspólna kohorta). W przeciwnym razie → `lot_type='lti'`. Nie ma to żadnego
wpływu na żadną kwotę podatkową — `tax/policy.py::POLICIES` traktuje `matched` i `lti` identycznie w
KAŻDEJ z trzech polityk (zestawy `{"own"}` / `{"own","dividend_drip"}` / wszystkie cztery) — rozróżnienie
ma znaczenie wyłącznie dla czytelności `/lots` i `/grants`.

**Świadomie NIE robimy:** automatycznego dopasowywania nowo utworzonych lotów do konkretnego wiersza w
`vests` (np. przez zgadywanie ilości) i przestawiania `vests.status` na `'vested'` — to wymagałoby
kruchej heurystyki dopasowania ilości między tabelami, które (potwierdzone na 2023/2024 danych) nie
rozkładają się 1:1 między harmonogramem a saldem. Zamiast tego `/grants` dostaje czysto DATOWY sygnał
(„ta transza powinna już była zvestować, sprawdź wyciąg") — uczciwy, bo nie udaje pewności, której nie mamy.

## 1. Nowy parser: „Vested Matching Shares"

**Plik:** `nokia_tracker/nokia_tracker/importers/computershare_pdf.py`

Nowy regex i funkcja (obok istniejących `_MATCHING_RE`/`parse_matching_shares`):

```python
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
```

Zweryfikowane regexem na surowym tekście wszystkich 5 realnych plików (bez fałszywych trafień, bez
pominiętych wierszy) — 4+8+1+1 = 14 wierszy „Vested Matching Shares" łącznie w kolejnych statementach,
redukujących się po `natural_key` do 9 unikalnych krotek (data, cena, ilość).

## 2. `import_statement()`: nowe UPSERT-y

**Plik:** ten sam, funkcja `import_statement()`.

Po istniejącej pętli `parse_matching_shares` (linia ok. 341, przed `parse_rs_award`), dodać:

```python
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
```

Zmienić istniejącą obsługę Typ A (obecnie tylko `logger.info`, patrz linie ok. 382-388) na tworzenie lotu,
z klasyfikacją typu wg współwystępowania daty z „Vested Matching Shares" z TEGO SAMEGO tekstu:

```python
vested_matching_dates = {row["vested_date"] for row in parse_vested_matching_shares(text)}

type_a, type_b = parse_withhold_to_cover(text)
for row in type_a:
    # Realne uwolnienie akcji (RS Award/LTI lub, gdy data pokrywa się z 'Vested Matching
    # Shares' z tego samego wyciągu, wspólna kohorta dopasowań ESPP) - zero podatku
    # potrąconego teraz (art. 24 ust. 11 ustawy o PIT, opodatkowanie odroczone do zbycia).
    # Rozróżnienie matched/lti nie wpływa na żadną kwotę (tax/policy.py traktuje je
    # identycznie w każdej z 3 polityk) - służy tylko czytelności /lots i /grants.
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
    ... # bez zmian, jak dziś
```

(Usunąć starą pętlę `for row in type_a: logger.info(...)` — zastąpiona powyższą.)

Zaktualizować docstring modułu (linie 17-21) — Typ A nie jest już „zero-efektowe, brak zapisu", tylko
„zero-efektowe podatkowo, ale TWORZY lot (akcje realnie stają się własnością)".

## 3. `/grants`: sygnał „zaległe" bez zgadywania

**Plik:** `nokia_tracker/nokia_tracker/tax/grants.py`

Dodać `from datetime import datetime` i parametr `today` (testowalny, domyślnie dzisiaj) do obu funkcji:

```python
def list_espp(conn: sqlite3.Connection, today: str | None = None) -> list[dict]:
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute(...).fetchall()  # bez zmian w SQL
    result = []
    for r in rows:
        d = dict(r)
        d["overdue"] = d["status"] == "pending" and d["vest_date"] < today
        result.append(d)
    return result
```

Analogicznie w `list_lti_grouped`: dla każdego wiersza w `g["vests"]` dodać `v["overdue"] = (v["status"]
== "pending" and v["vest_date"] < today)`.

**Plik:** `nokia_tracker/nokia_tracker/web.py`, `grants_get()` — bez zmian (funkcje mają domyślny `today`).

**Plik:** `nokia_tracker/nokia_tracker/templates/grants.html` — w obu tabelach (ESPP i LTI) zmienić kolumnę
Status:

```html
{% if g.overdue %}<span class="badge">zaległe — sprawdź wyciąg</span>{% else %}{{ status_labels.get(g.status, g.status) }}{% endif %}
```

(analogicznie dla `v` w pętli transz LTI).

## 4. Testy

**`tests/test_computershare_pdf.py`** (syntetyczne fixture'y, zero PII):
- `parse_vested_matching_shares`: podstawowy wiersz; wiersz z ujemnym gain/share (`-0.60 EUR`); dwa wiersze
  tej samej daty/ceny, różnych ilości (jak realne dane) — oba muszą się sparsować osobno.
- `import_statement`: wiersz „Vested Matching Shares" tworzy lot `lot_type='matched'`,
  `source='pdf_import'`; re-import tego samego tekstu daje `rows_unchanged` (idempotencja).
- `import_statement`: wiersz Typ A, gdy jego data POKRYWA SIĘ z datą wiersza „Vested Matching Shares" w
  tym samym tekście → lot `lot_type='matched'`; gdy NIE pokrywa się → `lot_type='lti'`.

**`tests/test_tax_grants.py`**:
- `list_espp(conn, today=...)`: wiersz `vest_date` w przeszłości + `status='pending'` → `overdue=True`;
  wiersz w przyszłości → `overdue=False`; wiersz już `status='vested'` w przeszłości → `overdue=False`
  (nie chcemy fałszywie oznaczać czegoś, co już rozwiązane ręcznie).
- `list_lti_grouped(conn, today=...)`: to samo per-transza w zagnieżdżonej liście `vests`.

**`tests/test_computershare_pdf_real_files.py`** (bramkowane, real dane lokalnie):
- Nowy test: łączna liczba unikalnych wierszy „Vested Matching Shares" po wszystkich 5 plikach = 9 (po
  redukcji natural_key), suma ilości ≈ 154.77.
- Nowy test: plik o `period_end="2026-01-01"` ma dokładnie 1 wiersz Typ A (101.396662), a jego data
  pokrywa się z datą wiersza „Vested Matching Shares" w TYM SAMYM pliku → klasyfikacja `matched`.
- Nowy test: plik o `period_end="2026-07-26"` ma dokładnie 2 wiersze Typ A (634, 2100), żaden nie pokrywa
  się datą z żadnym wierszem „Vested Matching Shares" w tym pliku → klasyfikacja `lti` dla obu.
- Rozszerzyć `test_import_statement_full_pipeline_on_real_files_reimport_gives_zero_inserted` (albo dodać
  nowy test obok) o pełny import WSZYSTKICH 5 plików po kolei (najstarszy→najnowszy, jak realne użycie),
  po czym: `sum(r["qty_remaining"] for r in taxlots.open_lots(conn)) >= 784` I
  `taxlots.record_sale(conn, "2025-10-27", 784.0, 5.31, fee_eur=8.32)` nie rzuca `InsufficientLotsError`.

## Weryfikacja end-to-end

1. `pytest` — pełny zielony przebieg (`--ignore=tests/test_computershare_pdf_real_files.py` na szybko,
   potem BEZ ignore lokalnie, bo `/config/akcje_temp/` istnieje w tej instalacji).
2. Commit + push (main, jak dotychczas w tej sesji).
3. Deploy: **BEZ** cyklu uninstall/reinstall (ten cykl skasował SQLite wcześniej w tej sesji, mimo
   wcześniejszego ostrzeżenia w notatkach o tym dokładnie ryzyku) — normalny `update`/`rebuild` add-onu
   wystarczy, bo tym razem NIE MA potrzeby
   świeżego `git clone` wymuszającego kasowanie danych: **UWAGA, do ustalenia z użytkownikiem przed
   deployem, czy `rebuild` faktycznie złapie nowy kod bez bumpa wersji, czy jednak trzeba coś innego** —
   nie powtarzać destrukcyjnego cyklu bez jawnej zgody, patrz punkt 4.
4. **Przed jakimkolwiek deployem: potwierdzić z użytkownikiem sposób wgrania nowego kodu**, żeby uniknąć
   powtórki incydentu z utratą danych.
5. Po deployu: użytkownik ponownie wgrywa 5 PDF-ów przez `/imports` (idempotentne, doda tylko nowe
   `matched`/`lti` loty, których wcześniej brakowało) — potem klika „Zatwierdź jako sprzedaż" na
   konflikcie 784 akcji i tym razem powinno się powieść. Sprawdzić `/grants` — transze z datami z
   przeszłości powinny teraz pokazywać badge „zaległe — sprawdź wyciąg" zamiast cichego „oczekuje".
