# nokia_tracker — rozbieżność podatku od sprzedaży 2025 (1711 zł vs ~1911-1925 zł zapłacone)

## Context

Add-on pokazywał podatek od jedynej sprzedaży (2025-10-27, 784 akcje) = **1711,41 zł**.
Ręczne wyliczenie użytkownika w arkuszu (wgranym do `/config/akcje_temp/` w trakcie tej
sesji, poza repo) dało ~200 zł więcej i **tyle zostało faktycznie zapłacone**. Zadanie:
znaleźć źródło różnicy.

Diagnoza: ślad FIFO z żywego add-onu (`/sales`, `/lots`, `/pit38/export.csv`)
skonfrontowany wiersz-po-wierszu z (a) 5 realnymi wyciągami Computershare
(`/config/akcje_temp/*.pdf`, `pypdf` layout mode) i (b) arkuszem użytkownika.

---

## Główny wniosek (potwierdzony liczbowo)

**To nie add-on się myli — arkusz użytkownika ma nieaktualną sumę.**

Komórka „Wydałem na akcje” w arkuszu = **7500,66 zł**. Ale zsumowanie WSZYSTKICH
wierszy „własne” w tym samym arkuszu (14 wierszy, 2022–2025) daje **8397,03 zł**.
Różnica = 896,37 zł = dokładnie trzy ostatnie wiersze zakupów (23.04, 22.05, 24.06.2025,
razem 906,25 zł) — dopisane do tabeli transakcji, ale nigdy niewłączone do sumy użytej
w polu „Podatek 19%”. Skumulowana suma po przedostatnim wierszu (28.04.2025) = 7490,79 zł,
prawie dokładnie tyle, ile wpisano jako finalne „Wydałem” — silny dowód, że pole zostało
wpisane ręcznie w kwietniu/maju 2025 i nigdy odświeżone.

| | przychód | koszt | dochód | podatek |
|---|---|---|---|---|
| arkusz jak zapisany (co dało ~1911-1925 zł zapłacone) | 17 631,72 | 7 500,66 | 10 131,06 | **1 924,90** |
| arkusz, suma WSZYSTKICH wierszy „własne” | 17 631,72 | 8 397,03 | 9 234,69 | 1 754,59 |
| ...i przychód netto po prowizji 8,32 EUR (jak w PDF) | 17 596,49 | 8 397,03 | 9 199,46 | **1 747,90** |
| add-on po korekcie lotów (patrz niżej) | 17 596,49 | 8 394,68 | 9 201,81 | **1 747,08** |

Poprawiony arkusz i poprawiony add-on zgadzają się **co do 82 groszy**. Metodologia
(polityka „tylko własne”) była cały czas ta sama po obu stronach — różniła się tylko
kompletność sumowania w arkuszu.

**Decyzja użytkownika (2026-08-11): zapłacony podatek (~1911-1925 zł) traktujemy jako
finalny i prawidłowy — korekty deklaracji NIE robimy.** Naprawiamy silnik, żeby każda
kolejna sprzedaż/dywidenda była liczona poprawnie od teraz. Sprzedaży z 2025-10-27
**nie cofamy i nie przeliczamy** — jej `sale_allocations` zostają nietknięte (te wiersze
mają zamrożony koszt na zawsze z definicji, patrz `tax/lots.py` docstring), więc nie
wymaga to żadnego specjalnego zabezpieczenia w kodzie — wystarczy pominąć krok
„przeksięgowanie sprzedaży” z listy napraw.

---

## Osobne, realne błędy w add-onie (mniejsza skala, naprawiamy niezależnie)

Te dotyczą kompletności rejestru lotów i mechaniki FIFO — nie tłumaczą głównej różnicy
(to zrobił arkusz), ale są prawdziwymi błędami wpływającymi na **przyszłe** sprzedaże i
na sekcję G/PIT-38 lat 2022-2024, które dziś są puste.

### A. Wyciągi 2023 i 2024 nie mają sekcji „Transactions” (Dividend Reinvested)

Ta sekcja pojawia się po raz pierwszy w wyciągu 2025. W latach 2022-2024 akcje z
reinwestowanej dywidendy widnieją wyłącznie jako wiersze `Vested Dividend Shares` w
tabeli podsumowującej — parser **świadomie je pomija**
(„już poprawnie pokryte przez parse_dividends" — założenie fałszywe dla lat bez sekcji
transakcyjnej). Skutek: **`/pit38?year=2024` pokazuje 0 dywidend**, mimo że w wyciągu
jest ich 7 (i podobnie w latach 2022-2023). Plik: `importers/computershare_pdf.py:150-175`.

### B. FIFO może skonsumować lot nabyty tego samego dnia co sprzedaż (lub później)

`open_lots()` nie filtruje po dacie względem sprzedaży. W tej konkretnej sprzedaży
(2025-10-27) skonsumowało to 8,4827 z 19,4948 akcji lotu #14 — zakupu wykonanego **tego
samego dnia**. To nie jest sama w sobie zła decyzja podatkowa (dzień nabycia = dzień
zbycia bywa dopuszczalny), ale mechanizm jest przypadkowy — `record_sale` powinien
jawnie decydować, nie po prostu sięgać po najbliższy wolny lot niezależnie od daty.
Pliki: `tax/lots.py:95-101` (`open_lots`), `tax/lots.py::record_sale`.

### C. Brak kontroli krzyżowej salda z BLUEPRINT §3a

BLUEPRINT wymaga: „suma `qty_remaining` musi zgadzać się z «Assets by plan» —
rozbieżność blokuje import z czytelnym komunikatem". W kodzie jest tylko
`_check_dividend_arithmetic` z komentarzem „pełna kontrola krzyżowa wraca w kroku 14" —
nigdy nie wróciła. Plik: `importers/computershare_pdf.py:307`.

### D. Przychód liczony z ceny×ilości zamiast z realnych wpływów

Wyciąg (Sell (Shares)): Sale Proceeds 4161,47 EUR, Fees 8,32 → Net 4153,15 EUR.
Silnik liczy `784 × 5,31 − 8,32 = 4154,72 EUR` — cena 5,31 w PDF jest zaokrąglona do
2 miejsc, więc mnożenie wprowadza ~1,57 EUR błędu. Dla przyszłych sprzedaży z
Type-B Withhold-to-Cover (realna gotówkowa sprzedaż, patrz BLUEPRINT §3a) parser już
wyciąga `sale_proceeds` — warto go móc przekazać zamiast przeliczać.

---

## Plan naprawy (tylko silnik — bez ruszania sprzedaży z 2025-10-27)

1. **`tax/lots.py` — FIFO nie sięga w przyszłość.**
   `open_lots(conn, as_of=None)` + filtr `acquired_date <= sale_date` w `record_sale`
   i `_allocate_fifo`/`_plan_fifo`. `tax/whatif.py::simulate_sale` przekazuje
   `as_of=dziś`. Test: sprzedaż w dniu X nie może skonsumować lotu z datą nabycia > X.

2. **`importers/computershare_pdf.py` — `Vested Dividend Shares` jako źródło zapasowe.**
   Nowa `parse_vested_dividend_shares()`; używana **tylko** dla wyciągów bez sekcji
   `Dividend (Reinvested)` (2022-2024), `source='holdings_snapshot'`,
   `natural_key = "divshare:<data>:<qty>"`, `notes` z adnotacją o precyzji 0,01 (dane
   z podsumowania, nie z tabeli transakcyjnej).

3. **`importers/computershare_pdf.py` — kontrola krzyżowa salda (BLUEPRINT §3a).**
   Nowa `reconcile_holdings(conn, parsed, as_of)`: `SUM(qty_remaining)` vs sekcja
   „Assets by plan"/„Shares" tego samego wyciągu. Rozbieżność > 0,01 → wiersz w
   `import_conflicts` (`entity_type='balance'`) + baner na `/imports`. Nie blokuje
   twardo (dane produkcyjne już istnieją), ale nie da się przeoczyć przy przyszłych
   importach.

4. **`tax/lots.py::record_sale` — opcjonalne realne wpływy.**
   Nowy param `proceeds_eur`; gdy podany, `revenue_pln = (proceeds_eur - fee_eur) * rate`.
   Podłączyć do `/lots/sell` (pole opcjonalne) i do potwierdzenia Type-B konfliktu
   (`/imports/conflicts/<id>/confirm-sale`), gdzie `sale_proceeds` już jest sparsowane.

5. **Backfill danych 2022-2024** (dywidendy + loty DRIP z sekcji A) — uzupełnia
   rejestr, żeby przyszłe sprzedaże/PIT-38 miały kompletną historię. **Nie wywołuje
   `reverse_sale`, nie dotyka `sales`/`sale_allocations` z 2025-10-27** — te zostają
   dokładnie takie, jak są, zgodnie z decyzją użytkownika.

6. **Wersja + release** wg `feedback_ha_addon_release` / skill `release`
   (bump `nokia_tracker/config.yaml` **i** `__init__.py`, opublikowany GH release,
   deploy przez `update_entity` + `ha_manage_addon(action="update")` — **nie**
   uninstall/reinstall, w bazie są realne dane podatkowe).

## Korekta w trakcie implementacji (kontrola salda, punkt 3)

Pierwsza wersja `reconcile_holdings` (SUM tylko `own`/`matched`/`dividend_drip`,
tolerancja 0,01) dała **fałszywe alarmy na realnych 5 plikach** — uruchomienie testu
`test_import_all_five_real_files_covers_the_784_share_sale` po dodaniu kontroli ujawniło
2 nieoczekiwane konflikty `balance`. Zdiagnozowane empirycznie na tych samych plikach:

1. **Loty `lti` (RS Award) TEŻ wchodzą do „Shares"** — raz zvestowane (Withhold-to-Cover
   Typ A) przechodzą z bucketu „Restricted Shares" do zwykłego „Shares", tak samo jak
   dopasowania ESPP. Pierwotne założenie (LTI zostaje osobno) było błędne — potwierdzone
   liczbowo: suma WSZYSTKICH typów (3671,05) minus nierozstrzygnięta sprzedaż Typu B
   (784) ≈ „Shares" z najnowszego wyciągu (2888,66), z dokładnością do ~1,6 akcji.
2. **Nierozstrzygnięte sprzedaże Typu B trzeba odjąć** — Computershare już pokazuje je
   jako sprzedane w swoim saldzie, nasza baza świadomie nie księguje ich automatycznie.
3. **Tolerancja podniesiona z 0,01 do 2,0 akcji** — `parse_vested_dividend_shares` (punkt
   2, dywidendy 2022-2024) ma udokumentowaną precyzję ~0,01/wiersz, która kumuluje się
   przez lata (zmierzone na realnych danych: 0,01-0,03 akcji rozjazdu na rok).

Poprawiona wersja: zero fałszywych alarmów na pełnej sekwencji 5 plików (tylko
oczekiwany 1 konflikt Typu B), zweryfikowane `test_computershare_pdf_real_files.py`
(15/15 zielone, gated na `/config/akcje_temp`). Jeden pre-istniejący test
(`test_import_statement_full_pipeline_on_real_files_reimport_gives_zero_inserted`)
zaktualizowany, żeby importować pełną historię przed testowanym plikiem zamiast samego
najnowszego pliku na pustej bazie — na pustej bazie kontrola salda słusznie zgłosiłaby
brak czterech lat lotów, to nie regresja.

## Druga korekta w trakcie implementacji (punkt 4, realne wpływy)

Pierwsza wersja `record_sale(proceeds_eur=...)` nadpisywała tylko `sales.revenue_pln`
(sumę na poziomie całej sprzedaży) — ale rozwinięty ślad FIFO na `/sales`
(`sale_allocations.revenue_pln`, per lot) nadal liczył się z nominalnego `price_eur`,
bo `_allocate_fifo`/`_plan_fifo` nigdy nie dostawały `proceeds_eur`. Efekt: `/sales`
pokazywał INNĄ liczbę (124,00) niż faktycznie zapisana w `sales.revenue_pln` (130,00) —
złapane własnym testem web (`test_lots_sell_post_uses_optional_real_proceeds_over_price_times_quantity`),
nie ręczną inspekcją. Naprawione przekazaniem `effective_price_eur = gross_eur / quantity`
(zamiast nominalnego `price_eur`) do `_allocate_fifo`, żeby `SUM(sale_allocations.revenue_pln)`
zawsze zgadzało się z `sales.revenue_pln` — dodany osobny test na ten niezmiennik.

## Weryfikacja

- TDD: punkty 1–4 mają test napisany i czerwony przed implementacją
  (`tests/test_tax_lots.py`, `tests/test_computershare_pdf.py`, `tests/test_web.py`).
- Test regresyjny na realnych danych (gated `skipif` na `/config/akcje_temp` — PDF-y i
  arkusz mają PII, repo jest publiczne, więc **arkusz użytkownika też nigdy nie trafia
  do repo/fixture'ów**, tylko syntetyczne dane jak dotąd).
- Po deployu: `/sales` pokazuje **identyczny wynik dla sprzedaży #1** jak dziś
  (1711,41 zł, own_only) — to jest oczekiwane i celowe, nie regresja.
- `/pit38?year=2023` i `/pit38?year=2024` pokazują niezerowe dywidendy.
- `/imports` bez ostrzeżenia o saldzie po ponownym imporcie wszystkich 5 PDF-ów.
- Pełny pakiet testów (obecnie 529) zielony.
