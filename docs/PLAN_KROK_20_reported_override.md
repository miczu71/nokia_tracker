# nokia_tracker — krok 20: zgłoszona wartość sprzedaży, sekcja G 2022-2024, fałszywy alarm salda

## Context

Po wydaniu 0.5.1 (krok 19) i ponownym wgraniu 5 PDF-ów przez użytkownika:
dywidendy 2022-2024 poprawnie stały się lotami `dividend_drip`, sprzedaż z 2025-10-27
pozostała nietknięta (1711,41 zł — silnik, nie arkusz). Ale trzy rzeczy zostały otwarte:

1. **Sprzedaż nadal pokazuje wynik silnika (1711,41 zł), nie wartość z arkusza
   użytkownika.** Użytkownik potwierdził: *„ten błąd zostawiamy zgodnie z moim
   arkuszem (błędna wartość podatku, żeby już nie robić korekty), a resztę od tego
   momentu będziemy już liczyć poprawnie”* — czyli **wyświetlana wartość tej JEDNEJ
   sprzedaży ma pokazywać to, co faktycznie zgłoszono/zapłacono** (wg arkusza), nie
   to, co wylicza silnik z realnych lotów PDF.
2. **Sekcja G (podatek u źródła od dywidend) dla 2022-2024 jest pusta** — źródło
   (snapshot „Vested Dividend Shares”) nie ma rozbicia Gross/Taxes/Fees. Użytkownik
   zgodził się na odtworzenie tych kwot przy założeniu **35% u źródła** (jak w 2025).
3. **Fałszywy alarm salda w kolejce konfliktów** (`/imports`: „2879,70” vs „2888,66”,
   różnica ~8,96 akcji) — zdiagnozowany PONIŻEJ jako błąd we własnym kodzie kroku 19,
   nie w danych. Prawdziwa rozbieżność to 1,61 akcji (w tolerancji).

---

## Diagnoza #3: dlaczego kontrola salda pokazała fałszywy alarm

`reconcile_holdings()` (krok 19) odejmuje od `SUM(qty_remaining)` ilości z
NIEROZSTRZYGNIĘTYCH konfliktów `withhold_to_cover_sale`, zakładając że taka sprzedaż
jeszcze nie jest zaksięgowana. Ale sprzedaż z 2025-10-27 (784 akcje) **już jest
zaksięgowana** — zarobiona ręcznie przez `/lots/sell` w kroku 13.6, długo przed
istnieniem przycisku „Zatwierdź jako sprzedaż” na `/imports`. Jej `import_conflicts`
wiersz (`wtc:2025-10-27:784.0:4153.15`) nigdy nie został oznaczony `resolved=1`, bo
powstał dopiero przy PONOWNYM imporcie tego samego wyciągu, już PO fakcie.

Efekt: `qty_remaining` już poprawnie odzwierciedla konsumpcję 784 akcji (lot #14 ma
`Pozostało=11,0121` z `Ilość=19,4948` — 8,4827 już zjedzone przez tę sprzedaż), a
`reconcile_holdings` odejmuje 784 **drugi raz**. Policzone ręcznie na żywych danych
(`SUM(qty_remaining)` wszystkich lotów = 2887,05, wyciąg mówi 2888,66 → **różnica
1,61 akcji**, dobrze w tolerancji 2,0) — bez podwójnego odejmowania nie ma żadnego
alarmu.

**Naprawa:** przed odjęciem ilości z nierozstrzygniętego konfliktu `withhold_to_cover_sale`,
sprawdzić czy w `sales` już istnieje wiersz z pasującą datą i ilością (epsilon) — jeśli
tak, nie odejmować (już odzwierciedlone w `qty_remaining`). Dodatkowo: `import_statement`
przy każdym imporcie oznacza taki konflikt `resolved=1` automatycznie, gdy wykryje
pasującą, już istniejącą sprzedaż — żeby banner na `/imports` nie wisiał tam wiecznie.

---

## #1: zgłoszona wartość sprzedaży — nowy mechanizm, nie edycja lotów

**Dlaczego nie przeliczać lotów:** koszt 7500,66 zł z arkusza to POMYŁKA (nieaktualna
suma, patrz krok 19) — nie odpowiada żadnej realnej kombinacji zakupów. Podmiana
`sale_allocations`/`lots` żeby "wyszło" 7500,66 wymagałaby zafałszowania rzeczywistego
kosztu konkretnych, prawdziwych zakupów — zepsułoby to FIFO dla przyszłych sprzedaży
i portfel. Zamiast tego: **nowa, osobna warstwa „zgłoszono w PIT-38”** na poziomie
sprzedaży, widoczna OBOK (nie zamiast) prawdziwego śladu FIFO.

Dokładne wartości z arkusza (odczytane bez zaokrągleń wyświetlania):
`Zbycie akcji = 17631,723312 PLN`, `Wydałem na akcje = 7500,66 PLN` →
`Różnica = 10131,063312 PLN`, `Podatek 19% = 1924,902029 PLN`.

**Zmiany:**
1. Migracja `db.py`: `sales` dostaje `reported_revenue_pln`, `reported_cost_pln`,
   `reported_note` (wszystkie nullable REAL/TEXT). `reported_income_pln`/
   `reported_tax_pln` liczone w locie (nie duplikować w schemacie) — ta sama formuła
   co silnik (`income = revenue - cost`, `tax = max(0, income) * pl_capital_gains_tax_pct`),
   tylko na nadpisanych liczbach.
2. `web.py`: nowa trasa `POST /sales/<id>/report` (formularz: przychód, koszt,
   notatka) — wzór z `/lots/sell` (WRITE_LOCK, redirect z komunikatem). `/sales`
   dostaje formularz „Zgłoszona wartość (jeśli różni się od wyliczenia)” per wiersz.
3. `/sales` i `tax/policy.py::compute_all_policies` (oraz `/pit38` + eksporty
   CSV/XLSX): gdy sprzedaż ma `reported_tax_pln` ustawione, UŻYWA go w agregacie
   zamiast wyliczenia z `sale_allocations` — inne sprzedaże w tym samym roku (jeśli
   kiedyś będą) liczą się normalnie. Ślad FIFO per lot zostaje **niezmieniony i nadal
   widoczny** — pokazuje realną historię, z wyraźną adnotacją „silnik wyliczyłby X zł,
   zgłoszono Y zł — patrz notatka” gdy się różnią.
4. Po wdrożeniu: ustawiam `reported_revenue_pln=17631.723312`,
   `reported_cost_pln=7500.66` na sprzedaży #1 przez ten formularz (nie ręczny SQL).

## #2: sekcja G 2022-2024 — odtworzenie przy założeniu 35%

Dla każdego z 13 lotów `dividend_drip`/`holdings_snapshot` (już istnieją od kroku 19)
dopisujemy wiersz w `dividends`, żeby sekcja G miała z czego liczyć:
- `reinvested_eur = quantity * cost_basis_eur` (to samo, co koszt lotu)
- `gross_eur = reinvested_eur / (1 - finnish_withholding_pct/100)` (ustawienie już w
  configu = 35, to samo co realnie zmierzone w 2025)
- `taxes_eur = gross_eur - reinvested_eur`, `fees_eur = 0`
- `record_date = purchase_date = data lotu` (snapshot nie rozróżnia Record/Purchase Date)
- `notes = "SZACUNEK: brutto/podatek u źródła odtworzone z założenia 35%, źródło nie
  ma rozbicia Gross/Taxes/Fees"`

**Bez duplikowania lotu:** `tax/dividends.py::add_dividend()` domyślnie TWORZY nowy
lot `dividend_drip` (`natural_key` w formacie `drip:...`, inny niż już istniejący
`vested_dividend:...` z kroku 19) — wywołanie go wprost podwoiłoby akcje. Rozszerzam
`add_dividend()` o opcjonalny `reinvested_lot_id: int | None` — gdy podany, funkcja
łączy się z JUŻ ISTNIEJĄCYM lotem zamiast tworzyć nowy (`UPDATE dividends SET
reinvested_lot_id = ?`, żadnego `add_lot()`). `importers/computershare_pdf.py`
przekazuje id lotu, który sam przed chwilą utworzył/znalazł w tej samej pętli.

**Widoczne oznaczenie szacunku w UI** (nie tylko `notes` w bazie): `/dividends` i
sekcja G na `/pit38` dostają badge „szacunek 35%” dla wierszy z niepustym `notes` —
dziś żaden inny wiersz `dividends` go nie ustawia, więc to bezpieczny sygnał bez
nowej kolumny.

---

## Pliki

- `nokia_tracker/db.py` — migracja: kolumny na `sales`.
- `nokia_tracker/tax/lots.py` — bez zmian (koszt/przychód per lot zostają realne).
- `nokia_tracker/tax/dividends.py` — `add_dividend(reinvested_lot_id=...)`.
- `nokia_tracker/tax/policy.py` — `compute_all_policies`: użyj `reported_tax_pln`
  gdy ustawione.
- `nokia_tracker/importers/computershare_pdf.py` — `reconcile_holdings` (nie odejmuj
  już zaksięgowanych Typu B), auto-`resolved=1` gdy pasująca sprzedaż istnieje,
  wiersze `dividends` (35%) w pętli fallbacku dywidend.
- `nokia_tracker/web.py` + `templates/sales.html` — trasa i formularz „zgłoszona
  wartość”; `templates/dividends.html`/`pit38.html` — badge szacunku.
- `docs/PLAN_KROK_20_reported_override.md` — kopia tego planu w repo (jak krok 19).

## Weryfikacja

- TDD (jak w kroku 19): czerwony test przed każdą zmianą.
  - `test_tax_lots.py`/nowy plik: `reported_*` w agregacie policy.
  - `test_computershare_pdf.py`/`_import.py`: brak podwójnego odjęcia w
    `reconcile_holdings`, auto-resolve konfliktu, sekcja-G-dywidenda linkuje
    istniejący lot (liczba lotów `dividend_drip` się NIE zmienia po dodaniu
    wierszy `dividends`).
  - `test_web.py`: `/sales/<id>/report` zapisuje i wpływa na `/pit38` agregat.
- Pełny pakiet testów zielony przed release.
- Po wdrożeniu (0.5.2) i ustawieniu `reported_*` na sprzedaży #1:
  `/pit38?year=2025` sekcja „Polityka” pokazuje 1924,90 zł (nie 1711,41), ślad FIFO
  per lot nadal widoczny z adnotacją o różnicy.
  `/pit38?year=2023`/`2024` mają niezerową sekcję G, wyraźnie oznaczoną jako szacunek.
  `/imports` bez fałszywego banera salda po ponownym imporcie 5 plików.
