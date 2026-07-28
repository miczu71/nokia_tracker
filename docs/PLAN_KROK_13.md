# Nokia Tracker — Krok 13: import PDF Computershare (loty/granty/dywidendy)

## Context

Krok 12 (silnik FIFO + 3 polityki kosztu) jest zamknięty i wdrożony (0.1.2, 317 testów). Następny
etap roadmapy z `docs/BLUEPRINT.md` §3a/§5 to parser wyciągów Computershare „Plan Holdings
Statement" (5 realnych plików w `/config/akcje_temp/`, 2022→2026 YTD) — pierwsze realne dane
zasilające silnik lotów zbudowany w kroku 12, i podstawa pod raport PIT-38 (krok 15).

Przed napisaniem kodu wydobyłem tekst layout-mode (`pypdf`) ze wszystkich 5 plików i **precyzyjnie
zweryfikowałem strukturę kolumn przez pomiar pozycji znaków** (nie zgadywanie) — blueprint sam
przyznaje, że jego opis sekcji powstał z częściowego oglądu tych samych plików tego samego dnia, więc
potraktowałem go jako hipotezę do zweryfikowania, nie pewnik. Dwa istotne ustalenia zmieniają zakres
względem litery blueprintu:

1. **Kolumny „Purchases" potwierdzone dokładnie** (poprzez dopasowanie pozycji nagłówka do wiersza
   danych, nie samą kolejność czytania tekstu — tryb `default` pypdf okazał się MYLĄCY, bo kolejność
   emisji etykiet w strumieniu PDF nie odpowiada fizycznej kolejności kolumn): **Allocation Date,
   Contribution Date, Trade Date, Settlement Date, Contribution Amount (EUR), Residual Amount
   Previous (EUR), Fees (EUR), Fair Market Value (EUR), Purchase Price (EUR), Purchased Shares
   (ilość), Residual Amount finalne (EUR)** — 4 daty + 7 wartości liczbowych, zawsze w tej kolejności.
   Klucz naturalny z blueprintu (`contribution_date+trade_date+quantity`) mapuje się jednoznacznie na
   kolumny 2+3+10. `lots.acquired_date` = **Trade Date** (dzień faktycznego wykonania zakupu — decyzja
   projektowa, blueprint nie precyzował którą z 4 dat użyć).
2. **Sekcja „Withhold-to-Cover" ma DWA różne kształty w realnych danych, nie jeden jak zakładał
   blueprint:**
   - **Typ A** (z etykietą instrumentu "Nokia Share", `Net Units == Quantity`) — potwierdzone na 2
     wierszach (634=634, 2100=2100) — zero-efektowe potwierdzenie odroczenia podatku, zgodnie z
     blueprintem. Tylko log, brak zapisu do bazy.
   - **Typ B, ODKRYTY DOPIERO PRZY POMIARZE** (bez etykiety instrumentu, kolumny: Execution Date /
     Quantity / Sale Price / **Sale Proceeds** / Taxes / Fees / **Net proceeds** w EUR) — znaleziony w
     realnym pliku: **784 akcje sprzedane za 4 153.15 EUR netto**. To NIE jest zero-efektowe
     potwierdzenie — to prawdziwa, gotówkowa sprzedaż, którą silnik FIFO musi znać, żeby dochód/podatek
     zrealizowany się zgadzał. Blueprintowa reguła „Net Units < Quantity → ręczne potwierdzenie" nie
     wystarczy do wykrycia tego przypadku (Typ B nie ma w ogóle kolumny "Net Units"), więc rozpoznanie
     idzie po KSZTAŁCIE wiersza (obecność "Sale Proceeds"/"Net proceeds" zamiast "Instrument"/"Net
     Units"), nie po porównaniu dwóch liczb. **Typ B zawsze trafia do kolejki ręcznego potwierdzenia
     (`import_conflicts`), nigdy nie księguje się automatycznie** — użytkownik może to ręcznie wpisać
     przez istniejący formularz `/lots/sell` (krok 12) po zobaczeniu szczegółów w kolejce.

**Ważne ustalenie o prywatności (sprawdzone przed pisaniem planu):** repo `miczu71/nokia_tracker` na
GitHubie jest **publiczne** (`gh repo view` → `PUBLIC`). Te 5 plików PDF zawiera prawdziwe dane
osobowe użytkownika (imię, nazwisko, adres, User ID, historia transakcji finansowych). **Nie wolno
ich committować do repo** — ani jako pliki testowe, ani jako wyekstrahowany tekst. Testy jednostkowe
parsera będą działać na ręcznie napisanych, syntetycznych fixture'ach tekstowych (odzwierciedlających
dokładnie zmierzoną strukturę, z fikcyjnym imieniem/adresem), a weryfikacja „na wszystkich 5
realnych plikach" (wymagana literą blueprintu) będzie osobnym testem **bramkowanym istnieniem
`/config/akcje_temp/`** (`pytest.mark.skipif`) — działa lokalnie na tej maszynie, nigdy nic nie
commituje, pomijany na świeżym klonie/w CI.

**Zawężenie kontroli krzyżowej „Assets by plan":** blueprint wymaga, żeby suma `qty_remaining` per typ
lotu zgadzała się z podsumowaniem na stronie 1 wyciągu. Ten krok tworzy tylko loty `own` (z Purchases)
i `dividend_drip` (z Dividend Reinvested) — loty `matched`/`lti` powstają dopiero w kroku 14
(scheduler wg dat vestingu). Pełna rekoncyliacja całego portfela nie jest więc możliwa do końca w tym
kroku. Zamiast tego: **walidacja wewnętrznej arytmetyki każdego wiersza** przy parsowaniu (np. Gross
Dividend − Taxes − Fees ≈ Dividend Reinvested + Residual) jako sanity-check łapiący błędy
pozycjonowania kolumn, a pełna kontrola krzyżowa wraca w kroku 14, kiedy istnieją już wszystkie typy
lotów.

Dobra wiadomość: **schemat bazy nie wymaga żadnej migracji** — `lots`/`grants`/`vests`/`dividends`/
`imports`/`import_conflicts` istnieją od migracji v1 (krok 1), a `BACKUP_SHARE` (`/share/nokia_tracker`)
jest już eksportowane w `run.sh`, tylko nieużywane.

## 1. Zależności

`requirements.txt`: dodać `pypdf==6.14.2` (dokładnie ta wersja jest zainstalowana i przetestowana w
tym środowisku). `openpyxl` (eksport arkusza użytkownika, zdegradowany do weryfikacji krzyżowej wg
blueprintu) **poza zakresem tego kroku** — brak pliku xlsx w `/config/akcje_temp/`, dodamy tylko jeśli
użytkownik dostarczy taki plik.

## 2. `importers/computershare_pdf.py` — parser (funkcje czyste, bez zapisu do bazy)

Strategia: **dopasowanie po kształcie wiersza (regex), nie po rekonstrukcji nagłówka** — nagłówki są
zawijane na 3-4 linie w kolejności, która w tekście bywa PO danych (nie przed), a tytuły sekcji
("Purchases", "Withhold-to-Cover") potrafią wystąpić w dowolnym miejscu względem swoich wierszy.
Każdy typ wiersza ma unikalny, stały kształt (liczba dat + liczba wartości z/bez sufiksu EUR/PLN),
więc rozpoznanie nie zależy od kontekstu (tytułu/nagłówka) w ogóle.

| Funkcja | Rozpoznaje | Efekt |
|---|---|---|
| `extract_layout_text(pdf_bytes) -> str` | cienki wrapper na `PdfReader(BytesIO(...)).pages[i].extract_text(extraction_mode="layout")`, sklejone wszystkie strony | — |
| `parse_document_meta(text) -> dict` | `period_start`/`period_end` z linii `DATE - DATE`, `as_of_date` z `as of DATE` | do tabeli `imports` |
| `parse_purchases(text) -> list[dict]` | 4 kolejne daty + 7 wartości (5 z EUR) | → `lots` (own) |
| `parse_matching_shares(text) -> list[dict]` | `Matching Shares` + 3 daty + ilość + PLN | → `grants`(espp) + `vests` |
| `parse_rs_award(text) -> list[dict]` | `\d{4}\s+RS\s+AWARD\s+...` + 3 daty + ilość + PLN | → `grants`(lti) + `vests`, wiele transz per grant |
| `parse_dividends(text) -> list[dict]` | 2 daty + ilość + 7 wartości (5 z EUR) | → `dividends` + `lots`(dividend_drip) |
| `parse_withhold_to_cover(text) -> tuple[list, list]` | **Typ A** (Instrument+Net Units) i **Typ B** (Sale Proceeds/Net proceeds) osobno | Typ A: log; Typ B: `import_conflicts` |

Linie-adnotacje bez wartości informacyjnej dla importu (kurs PLN/EUR nad wierszem Purchases, kod
referencyjny nad wierszem Dividend/Withhold) — pomijane przez regex (nie pasują do żadnego kształtu).

## 3. `tax/grants.py` (nowy moduł)

`add_grant(conn, program, grant_date, quantity, natural_key, ...)` i
`add_vest(conn, grant_id, vest_date, quantity, natural_key, status='pending')` — idempotentne po
`natural_key`, ten sam wzorzec co `tax/lots.py::add_lot`. Klucze naturalne:
- ESPP grant: `espp_grant:{allocation_date}:{quantity}` (dopasowanie grantu do jednej transzy matchu)
- ESPP vest: `espp_vest:{allocation_date}:{vest_date}:{quantity}`
- LTI grant: `lti_grant:{participation_description}` (etykieta typu „2025 RS AWARD 07-JUL-2025" jest
  już naturalnie unikalna — Computershare sam ją nadaje)
- LTI vest: `lti_vest:{participation_description}:{vest_date}:{quantity}`

## 4. `tax/dividends.py` (rozszerzenie istniejącego modułu)

Dodać `add_dividend(conn, record_date, purchase_date, entitled_quantity, gross_eur, taxes_eur,
fees_eur, reinvested_eur, purchase_price_eur, purchased_shares, ...)`: liczy `withholding_pct` z
realnych `taxes/gross` (per wiersz, dokładniejsze niż stała z configu), zamraża kurs NBP na
**Record Date** (dzień uzyskania przychodu, art. 11a) przez `fx_nbp.rate_for_event`, wstawia
`dividends` + woła `taxlots.add_lot(..., lot_type='dividend_drip', natural_key='drip:...')`, linkuje
`reinvested_lot_id`. Istniejące `compute_dividend_tax()` pozostaje bez zmian.

## 5. Orkiestracja importu — `import_statement()` w `computershare_pdf.py`

`import_statement(conn, pdf_bytes, filename) -> dict` (raport `rows_inserted/unchanged/conflict`):
1. SHA-256 pliku, wstawienie wiersza `imports` (idempotencja na poziomie pliku: ten sam plik drugi raz
   → wszystkie klucze naturalne już istnieją → same `rows_unchanged`, zero duplikatów).
2. Dla każdego sparsowanego wiersza (lot/grant+vest/dividend): sprawdź `natural_key` w bazie —
   brak → insert (przez `add_lot`/`add_grant`/`add_vest`/`add_dividend`); istnieje + wartości
   identyczne → `rows_unchanged++`; istnieje + wartości różne → wiersz do `import_conflicts`
   (nigdy nie nadpisuj), `rows_conflict++`.
3. Withhold-to-Cover Typ B → zawsze `import_conflicts` (entity_type='withhold_to_cover_sale').
4. Sanity-check arytmetyki per wiersz (patrz Context) — log warning przy niezgodności, nie blokuje
   importu (to pomoc diagnostyczna, nie twardy gate — pełna kontrola krzyżowa wraca w kroku 14).

## 6. Scheduler + folder podrzucania (reuse `fuel_tracker`)

`main.py`: odczyt `BACKUP_SHARE` (już eksportowane w `run.sh`), nowy job `auto_import_pdf_share()`
— **port `auto_import_share()` z `fuel_tracker/main.py`** (ten sam wzorzec: `<share>/import/*.pdf` →
`import_statement()` → przenieś do `<share>/imported/<timestamp>_<nazwa>`, `try/except` per plik, żeby
jeden zepsuty PDF nie zablokował reszty).

## 7. Web UI — strona „Importy"

`web.py`: `GET /imports` (historia importów z tabeli `imports` + kolejka nierozwiązanych
`import_conflicts` + formularz uploadu), `POST /imports/upload` (multipart, plik w pamięci przez
`BytesIO`, bez zapisu na dysk), `POST /imports/conflicts/<id>/resolve` (oznacza rozwiązany +
notatka — użytkownik sam księguje sprzedaż typu B przez istniejący `/lots/sell`, ta trasa tylko
zamyka wpis w kolejce). Nowy `templates/imports.html`, wpis w nawigacji.

## 8. Testy (TDD, zero PII w repo)

| Plik | Zawartość |
|---|---|
| `tests/test_computershare_pdf.py` | `parse_*` na **ręcznie napisanych syntetycznych stringach tekstowych** odzwierciedlających dokładnie zmierzony kształt (w tym oba typy Withhold-to-Cover, wielotranszowy RS AWARD, adnotacje-śmieci do zignorowania) |
| `tests/test_computershare_pdf_real_files.py` | `@pytest.mark.skipif(not Path("/config/akcje_temp").is_dir())` — pełny pipeline na 5 realnych plikach: parsuje bez błędów, sumy się zgadzają wewnętrznie, drugi import tego samego pliku daje `rows_inserted=0` |
| `tests/test_tax_grants.py` | `add_grant`/`add_vest` idempotentność, wielotranszowy grant LTI |
| `tests/test_computershare_pdf_import.py` | `import_statement` na słownikach syntetycznych (bez PDF): insert/unchanged/conflict, Typ B zawsze konflikt |
| `tests/test_web.py` (rozszerzenie) | upload przez multipart z `import_statement` zamockowanym (jak `test_analyze_now`) — bez potrzeby generowania realnego PDF-a w teście |

## 9. Weryfikacja end-to-end

1. `pytest` zielony (unit + real-files gated test lokalnie).
2. Commit + push (kilka commitów jak w kroku 12: parser → grants.py → dividends ledger → import
   orchestration → scheduler+UI).
3. Deploy live (ten sam cykl uninstall→remove_repository→add_repository→install→start, bez bumpa
   wersji).
4. `ha_manage_addon` proxy `GET /imports` (Playwright/Ingress pozostaje trwale zablokowany — patrz
   pamięć z poprzedniej sesji).
5. **Rekomendowane, do potwierdzenia przy akceptacji planu:** zaimportować realne 5 plików z
   `/config/akcje_temp/` do PRODUKCYJNEJ bazy na żywo (przez `ha_manage_addon` proxy upload) — to nie
   jest zanieczyszczenie danymi testowymi jak w kroku 12, tylko właściwe, docelowe wypełnienie
   portfela realną historią 2022-2026, potrzebne pod raport PIT-38 w kroku 15. Alternatywa: zostawić
   to użytkownikowi do zrobienia samodzielnie przez UI.

## Ryzyka

- **Format PDF może się zmienić** (nowy layout Computershare) — parser oparty o kształt regex, nie o
  konkretne teksty nagłówków, więc drobne zmiany formatowania liczb/dat są bardziej odporne niż
  parsowanie po pozycji nagłówka; nowy layout tabeli i tak wymagałby przeglądu.
- **Withhold-to-Cover Typ B pozostaje ręczny** — świadome ograniczenie zakresu (bezpieczniejsze niż
  zgadywanie, które loty/grant należy skonsumować automatycznie).
- **Brak pełnej kontroli krzyżowej do czasu kroku 14** — udokumentowane wprost, nie ukrywane.
