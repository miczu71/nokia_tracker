# Changelog

## [0.8.0] - 2026-08-12

Krok 23 (`docs/PLAN_KROK_23_portfel_kafelki.md`) — karta „Portfel” na pulpicie przebudowana
wizualnie na kafelki. Dotąd karta miała trzy sekcje o różnym wyglądzie („W posiadaniu” jako
siatka statystyk, „Zablokowane” osobno, „Razem” na samym dole — często poza ekranem), liczby
bez separatora tysięcy i ilości z 4-5 miejscami po przecinku.

### Zmieniono
- **Karta „Portfel” jako suma na górze + trzy równorzędne kubełki**: „Wartość całkowita”
  (hero na samej górze, zamiast na dole jako „Razem”) nad trzema kafelkami — „Wolne” (można
  sprzedać), „Z ograniczeniem” (widoczny tylko gdy dotyczy, jak dotąd), „Zablokowane”
  (nienabyte dopasowania ESPP/transze LTI) — każdy z tą samą strukturą (ilość, kwota PLN,
  kwota EUR, linia kontekstowa), plus pasek wyniku (koszt bazowy, niezrealizowany P&L,
  całkowity zwrot, dywidendy netto) pod spodem. Liczby te same co dotąd — zero zmian w
  silniku podatkowym/rynkowym, wyłącznie prezentacja.
- **PLN jako waluta główna w tej karcie** (EUR jako druga linia) — jedyne miejsce w apce
  odpowiadające wprost na „ile to jest warte”; reszta stron zostaje przy EUR-głównym.
- **Separator tysięcy i skrócone ilości**: `money()`/`qty()`/`pct()` (nowy `format.py`,
  zarejestrowane jako filtry Jinja) — „143 618 zł” zamiast „143618”, „2 887,05 akcji” zamiast
  „2887.05134”. Pełna precyzja (4 miejsca) zostaje tam, gdzie liczy się zgodność co do grosza
  z wyciągiem: ostrzeżenie o zaległych transzach, strony Loty/Granty/PIT-38 — bez zmian.

### Dodano
- `portfolio.py::dashboard_buckets()` — składa `position_values()`/`restricted_own_summary()`/
  `unvested_summary()` (już liczone w `web.py::dashboard`) w strukturę trzech kubełków + sumę,
  zastępując ręczną arytmetykę, która wcześniej siedziała inline w widoku.
- `format.py` — `money()`/`qty()`/`pct()`, formatowanie liczb po polsku (separator tysięcy
  U+00A0, przecinek dziesiętny), `None` → „—”, nigdy pythonowy napis „None”.

## [0.6.0] - 2026-08-11

Krok 21 (`docs/PLAN_KROK_21_portfel_calkowity.md`) — całkowite zestawienie portfela na
pulpicie. Dotąd karta „Portfel” pokazywała wyłącznie akcje uwolnione (sumę otwartych lotów),
niewidoczne były ani zablokowane dopasowania ESPP/transze LTI, ani to, że część posiadanych
akcji ma ograniczenie zbycia.

### Dodano
- **Karta „Portfel” na pulpicie w trzech blokach**: „W posiadaniu” (bez zmiany liczb — P&L
  i całkowity zwrot nadal liczone z całej posiadanej pozycji — plus nowa linia wolne/z
  ograniczeniem, gdy część świeżo kupionych akcji własnych czeka na własne dopasowanie ESPP:
  sprzedaż przed jego uwolnieniem oznacza utratę dopłaty 50%), „Zablokowane — jeszcze
  nienabyte” (ilość, wartość szacunkowa EUR/PLN, najbliższa data dostępności, ostrzeżenie
  o transzach z minioną datą dostępności — nie wliczonych w „Razem”), „Razem” (posiadane +
  nadchodzące).
- `tax/grants.py::unvested_summary()` — jedno źródło prawdy dla „ile jest jeszcze
  zablokowane”, dzieli transze `pending` na nadchodzące/zaległe wg daty dostępności.
- `tax/grants.py::restricted_own_summary()` — które z JUŻ POSIADANYCH akcji własnych mają
  ograniczenie zbycia. Reguła wyprowadzona z danych (lot `own` jest ograniczony dokładnie
  wtedy, gdy istnieje transza `pending` z tą samą datą alokacji co data nabycia lotu), nie
  sparsowana z sekcji wyciągu — sama się aktualizuje, gdy dopasowanie zvestuje.

### Naprawiono
- **Data „zaległości” transz ESPP liczona ~4 tygodnie za wcześnie.** Wyciąg Computershare ma
  trzy daty w harmonogramie (Allocation/Vesting/Available from) — akcje realnie wpływają na
  konto w dacie `Available from`, nie `Vesting Date`. Importer parsował `Available from` od
  kroku 13, ale nigdzie go nie zapisywał (kolumna nie istniała) — `list_espp`/
  `list_lti_grouped` liczyły `overdue` po samej dacie nabycia, więc każda sierpniowa transza
  ESPP wyglądała na zaległą, zanim Computershare w ogóle zdążył ją zaksięgować. Migracja `v5`
  (`vests.available_from`) + `backfill_available_from()` uzupełnia kolumnę też na transzach
  dodanych przed tym krokiem, przy ponownym imporcie istniejącego wyciągu.

### Zmiana zachowania (świadoma)
- `sensor.nokia_tracker_next_vest_date` pokazuje teraz datę DOSTĘPNOŚCI (`available_from`),
  nie datę nabycia (`vest_date`) — do wgrania świeżego wyciągu realnie zmieni wartość.
  `sensor.nokia_tracker_unvested_qty` bez zmiany semantyki.

### Techniczne
- `sensors.py::grants_values()` przepisane na delegację do `unvested_summary()` — jedna
  definicja „nienabytego” zamiast dwóch rozjeżdżających się implementacji.
- Migracja bazy `v5` (`ALTER TABLE vests ADD COLUMN available_from`).
- TDD; 602 testy total, wszystkie zielone.

## [0.5.3] - 2026-08-11

Znalezione zaraz po wdrożeniu 0.5.2, przy weryfikacji na żywo po ponownym imporcie
5 plików przez użytkownika: sekcja G 2022-2024 zadziałała poprawnie, ale stary,
nieaktualny konflikt salda w kolejce (`/imports`) nadal wisiał — mimo że świeże
przeliczenie po naprawie z 0.5.2 wykazało, że saldo się teraz zgadza.

### Naprawiono
- **Nieaktualne konflikty salda nie znikały same** — `reconcile_holdings()`
  zapobiegała tylko NOWYM fałszywym alarmom (0.5.2), ale nigdy nie rozwiązywała
  już istniejącego, nierozstrzygniętego konfliktu `balance` z wcześniejszego,
  błędnego przebiegu, nawet gdy kolejny import wykazał, że saldo się teraz zgadza.
  Teraz: gdy świeże przeliczenie mieści się w tolerancji, wszystkie dotychczas
  nierozstrzygnięte konflikty `balance` (z dowolnego wcześniejszego importu)
  zostają automatycznie oznaczone jako rozwiązane.

### Techniczne
- 1 nowy test, TDD.

## [0.5.2] - 2026-08-11

Krok 20 (`docs/PLAN_KROK_20_reported_override.md`) — dokończenie kroku 19 po ponownym
imporcie 5 wyciągów przez użytkownika: naprawiony fałszywy alarm salda, nowy mechanizm
zgłoszonej wartości sprzedaży, odtworzona sekcja G dywidend 2022-2024.

### Naprawiono
- **Fałszywy alarm salda w kolejce konfliktów** — `reconcile_holdings()` (krok 19)
  odejmowała ilość z nierozstrzygniętego konfliktu Withhold-to-Cover Typu B nawet
  gdy ta sprzedaż była już zaksięgowana wcześniej ręcznie (np. przez `/lots/sell`,
  zanim istniał przycisk „Zatwierdź jako sprzedaż") — podwójne odjęcie tej samej
  ilości. `import_statement()` teraz automatycznie oznacza taki konflikt jako
  rozwiązany, gdy wykryje pasującą, już istniejącą sprzedaż.

### Dodano
- **Zgłoszona wartość sprzedaży** (`sales.reported_revenue_pln`/`reported_cost_pln`,
  formularz na `/sales`) — dla sprzedaży, gdzie faktycznie zgłoszona/zapłacona kwota
  różni się od tego, co wyliczyłby silnik z realnych lotów (np. deklaracja już
  złożona wg błędnej sumy z ręcznego arkusza i świadomie NIE jest korygowana).
  Nadpisuje TYLKO agregaty PIT-38 — realny ślad FIFO (`sale_allocations`/`lots`)
  zostaje niezmieniony i nadal widoczny, z wyraźną adnotacją różnicy między
  wyliczeniem silnika a wartością zgłoszoną.
- **Sekcja G dla lat 2022-2024** — dywidendy reinwestowane z tych lat (wyciągi bez
  sekcji „Dividend (Reinvested)" transakcyjnej, uzupełnione w kroku 19 tylko jako
  loty) dostają teraz też wiersz w rejestrze dywidend, z brutto/podatkiem u źródła
  odtworzonym przy założeniu 35% (jak realnie zmierzone w 2025) — wyraźnie oznaczone
  badge'em „szacunek" na `/dywidendy` i „zawiera szacunki" w sekcji G na `/pit38`,
  nie mieszane bez odróżnienia ze zmierzonymi wartościami.

### Techniczne
- `tax/dividends.py::add_dividend()`: nowy `reinvested_lot_id` (linkuje do już
  istniejącego lotu zamiast tworzyć duplikat) i `notes` (nigdy wcześniej nie
  zapisywane, mimo że kolumna istniała od kroku 13).
- 18 nowych testów, TDD. Migracja bazy v4 (`sales.reported_revenue_pln`/
  `reported_cost_pln`).

## [0.5.1] - 2026-08-11

Poprawki silnika FIFO/importera znalezione podczas diagnozy rozbieżności podatku
(krok 19, `docs/PLAN_KROK_19_tax_lot_fixes.md`) — użytkownik zgłosił, że podatek
pokazany dla sprzedaży z 2025-10-27 (1711,41 zł) różnił się o ~200 zł od kwoty
faktycznie zapłaconej. Śledztwo na realnych 5 wyciągach Computershare wykazało, że
główną przyczyną była nieaktualna suma w ręcznym arkuszu użytkownika (nie błąd
add-onu) — ale po drodze znalezione zostały cztery realne, mniejsze błędy w silniku,
naprawione tutaj niezależnie od tamtej sprawy. **Sprzedaż z 2025-10-27 pozostaje
nietknięta** — jej `sale_allocations` mają zamrożony koszt na zawsze z definicji, więc
te poprawki wpływają tylko na przyszłe sprzedaże/dywidendy i na historię 2022-2024.

### Naprawiono
- **FIFO mogło skonsumować lot nabyty tego samego dnia co sprzedaż lub później** —
  `open_lots()`/`record_sale()` filtrują teraz po `acquired_date <= sale_date`
  (`tax/lots.py`). Wcześniej brak filtra pozwalał przypadkowo zjeść świeżo kupiony
  lot zamiast zgłosić brak pokrycia ze starszych.
- **Dywidendy reinwestowane z lat 2022-2024 nigdy nie stawały się lotami ani wpisami
  w rejestrze dywidend** — te wyciągi nie mają sekcji „Dividend (Reinvested)"
  transakcyjnej (pojawia się dopiero od wyciągu 2025); nowy parser zapasowy
  `parse_vested_dividend_shares()` czyta snapshot „Vested Dividend Shares" tylko gdy
  sekcja transakcyjna jest nieobecna w danym wyciągu — tworzy lot `dividend_drip`
  (koszt/FIFO), sekcja G tych lat pozostaje niepełna (źródło nie ma Gross/Taxes/Fees).
- **Realne wpływy ze sprzedaży zamiast ceny×ilości** — `record_sale()` przyjmuje
  opcjonalne `proceeds_eur` (Sale Price w PDF bywa zaokrąglona do 2 miejsc, co przy
  dużych ilościach akcji dawało kilkuzłotowy błąd). Podłączone automatycznie do
  potwierdzenia Withhold-to-Cover Typu B na `/imports` oraz jako opcjonalne pole w
  formularzu `/lots` „Zarejestruj sprzedaż". Rozwinięty ślad FIFO na `/sales`
  (`sale_allocations`) prorata się teraz z tych samych realnych wpływów, nie z
  nominalnej ceny — bez tego suma per-lot rozjeżdżała się z sumą całej sprzedaży.
- **Brak kontroli krzyżowej salda z BLUEPRINT §3a** — nowa `reconcile_holdings()`
  porównuje sumę wszystkich lotów (minus nierozstrzygnięte sprzedaże Typu B, których
  celowo nie księgujemy automatycznie) z liczbą „Shares" na stronie 1 każdego wyciągu.
  Rozjazd > 2 akcje (tolerancja uwzględnia niższą precyzję źródła zapasowego dywidend)
  trafia do kolejki konfliktów na `/imports` jako nowa pozycja, nie blokuje importu.

### Techniczne
- 22 nowe testy (529 → 551), TDD przez cały czas — każdy błąd miał czerwony test
  przed poprawką.

Piąte wydanie: złotówki na pulpicie, podgląd na żywo przed zapisem, jedna
matematyka dywidendowa, nawigacja w 5 sekcjach (krok 18,
`docs/PLAN_KROK_18_ux_pln.md`) — pełny przegląd wszystkich stron pod kątem
uproszczenia wizualnego i funkcjonalnego.

### Dodano — PLN na pulpicie
- Karta „Portfel": ilość, **nowy kafelek Koszt bazowy**, wartość rynkowa,
  niezrealizowany P&L — każda kwota EUR z drugą linią `≈ X zł` po kursie
  bieżącym (Yahoo/ECB, ten sam co reszta pulpitu).
- Linia pod kartą jawnie rozgraniczająca kurs bieżący (prezentacyjny) od kursu
  NBP zamrożonego używanego w rozliczeniu podatkowym (`/dywidendy`, `/pit38`).
- Bez kursu EUR/PLN w bazie: strona nie pokazuje `None` ani pustego `≈`, tylko
  „kurs EUR/PLN niedostępny”.

### Dodano — podgląd na żywo przed zapisem
- Trzy nowe endpointy JSON: `GET /api/preview/lot`, `/api/preview/sale`,
  `/api/preview/dividend` — zero nowej logiki podatkowej, każdy woła istniejący
  silnik (`fx_nbp.rate_for_event`, `tax/whatif.py::simulate_sale`,
  `tax/dividends.py::compute_dividend_tax_pln`), więc podgląd nigdy nie
  rozjeżdża się z tym, co faktycznie zostanie zapisane. Błędy (brak pokrycia,
  brak kursu NBP, data w przyszłości) wracają jako `{ok: false, error}` z
  HTTP 200, nigdy 500.
- `NT.initFormPreview()` (`static/app.js`) — debounce 400 ms, `AbortController`
  na wyścigi, `.preview-box` startuje `hidden` (formularz działa identycznie
  bez JS jak przed 0.5.0).
- Podpięte na `/lots` (dodanie lotu + rejestracja sprzedaży), `/dividends`
  (dodanie wypłaty, w tym linia „powstanie lot” dla reinwestycji DRIP) i
  `/pit38` (symulacja „co jeśli sprzedam teraz” — wynik bez przeładowania
  strony, fallback GET z query params zostaje).

### Naprawiono — jedna matematyka dywidendowa
- `/dywidendy`: kafelki podsumowania liczone teraz sumowaniem wierszy tabeli
  (kurs NBP zamrożony na Record Date), nie osobnym wywołaniem
  `sensors.dividends_values()` na kursach bieżących — usunięta rozbieżność
  między kafelkami a tabelą na tej samej stronie.
- Kafelek „Yield on cost” liczył koszt bazowy z porzuconych ręcznych pól
  ustawień (zawsze zero po imporcie PDF) — pokazywał trwały `—`. Przełączony
  na `portfoliom.position_values_auto()`, tak jak reszta aplikacji.

### Naprawiono — `/granty` fantomowe wiersze
- `<tr><td colspan="N">{{ realized_details(...) }}</td></tr>` renderował się
  dla KAŻDEJ transzy niezależnie od tego, czy miała zrealizowaną sprzedaż —
  pusty wiersz-widmo przy każdej niezrealizowanej transzy. Warunek przeniesiony
  z treści makra na sam wiersz.
- Dołożony pasek kafelków „Niezvestowane / Następny vesting / Ilość w
  następnym vestingu” (dane już liczone dla sensorów MQTT, dotąd nieobecne w UI).
- „Zrealizowano” dostało EUR jako podlinię obok PLN.

### Zmieniono — nawigacja w 5 sekcjach
- 11 płaskich linków → Pulpit / **Portfel** (Portfel, Loty, Sprzedaże, Granty) /
  **Podatki** (Dywidendy, PIT-38) / **Dane** (Importy, Newsy, Prognozy) /
  Ustawienia. Grupa z aktywną stroną domyślnie rozwinięta.
- `<details class="nav-group">` — bez JS rozwija się natywnie w miejscu,
  pełny fallback; `NT.initNavGroups()` dokłada pływające menu (jedno otwarte
  naraz, klik poza/Escape zamyka).

### Zmieniono — przegląd pozostałych stron
- `/sales`: tabela 10→9 kolumn (kurs NBP i cena EUR przeniesione do
  rozwinięcia — już tam były), przycisk „Cofnij” wystawiony w wierszu głównym
  zamiast schowany w rozwinięciu; kafelki podsumowania schowane przy zerze
  sprzedaży w wybranym roku.
- `/pit38`: akapit PIT/ZG (dosłowny alias kafelków sekcji G) zwinięty do
  jednej linii; sekcja G schowana przy zerze dywidend w roku; usunięty
  redundantny przycisk „Pokaż” obok auto-submitującego selektora roku.
- `/imports`: trzy rozwlekłe akapity → jeden + `<details>`; tabela historii
  7→5 kolumn; surowe repry dictów Pythona w kolejce konfliktów sformatowane
  jako `klucz: wartość`; badge „Sprzedaż zaksięgowana” przeniesiony do nagłówka.
- `/settings`: karta „Podatki” dostała 4 brakujące pola (podatek u źródła
  Finlandia, stawka traktatowa, Belka, domyślny rok podatkowy — dotąd tylko
  odczyt); polityka kosztu przez czytelne etykiety zamiast surowych enumów;
  usunięty numer wersji przeciekający do tytułu karty.
- `/portfolio`: formularz ręczny zwinięty do `<details>`, gdy istnieją loty
  (i tak nieaktywny — dawniej dwa akapity prozy mówiące to samo); dołożone
  linie PLN i kafelek „Całkowity zwrot”.
- `/news`: usunięte martwe zapytanie o `horizon`/`tags` (nigdy nierenderowane);
  dołożona kolumna źródła i legenda kropek „Wpływ”; link do pulpitu.
- `/forecasts`: dołożony kafelek trafności historycznej (dawniej tylko na
  pulpicie); ceny z jednostką EUR; kolumna `Model` przeniesiona do `title`.
- `_alloc_detail.html`: etykiety (`LOT_TYPE_LABELS`/`POLICY_LABELS`) importowane
  bezpośrednio z nowego `templates/_macros.html` zamiast niejawnego kontraktu
  na to, że wołający wcześniej je zdefiniował; guard na pustą listę alokacji.

### Dodano — `templates/_macros.html`
- Wspólne `stat()` (kafelek KPI, bajtowo identyczny z dotychczasową ręczną
  wersją), `tax_disclaimer()` (3 warianty ostrzeżenia ⚠️, dawniej 4 kopie w
  4 plikach), `LOT_TYPE_LABELS`, `POLICY_LABELS` — zero duplikacji etykiet
  między `/lots`, `/sales`, `/pit38`, `/portfolio`, `/settings`.

### Bez zmian
- `tax/*.py` — cała logika podatkowa nietknięta; zmiany są prezentacyjne albo
  wołają istniejące, przetestowane funkcje.
- Schemat bazy, eksporty CSV/XLSX, sensory MQTT (`sensors.py`).

### Testy
- 529 testów (516 + 13 nowych): PLN na pulpicie (w tym degradacja bez kursu),
  regresja podgląd-vs-zapis dla sprzedaży i dywidendy, błędy podglądu jako
  `ok: false` nie 500, rekoncyliacja kafelków dywidend z tabelą, kafelek
  „Yield on cost” po imporcie lotów, brak fantomowych wierszy na `/granty`,
  nawigacja renderuje się bez JS, sekcja G schowana bez dywidend.

## [0.4.0] - 2026-07-29

Czwarte wydanie: pełna szerokość ekranu + skondensowane Sprzedaże i PIT-38
(krok 17, `docs/PLAN_KROK_17_ux.md`) — treść wypełnia cały dostępny ekran zamiast
780px kolumny ze scrollbarem, a dwie najgęstsze strony dają odpowiedź w pierwszym
ekranie zamiast rozwlekłej prozy.

### Zmieniono — pełna szerokość layoutu
- `.main` bez limitu `max-width` (było 900px) — treść skaluje się do pełnej
  szerokości okna na każdej stronie.
- Tabele przestały wymuszać `white-space: nowrap` na wszystkich komórkach —
  kolumny tekstowe (`Źródło`, `Podstawa prawna`, `Reinwestycja`, `Uznany w`) łamią
  się zamiast rozpychać tabelę w poziomy scroll; kolumny liczbowe/daty zachowują
  `nowrap` przez `.num`/`.nowrap`.
- `.grid.stats` z `auto-fill` na `auto-fit` — kafelki KPI realnie rozciągają się
  na szerokim ekranie zamiast zostawiać puste ścieżki siatki.

### Zmieniono — `/sales` jako rejestr transakcji
- Z akordeonu (`<details>` per sprzedaż z dwiema pełnymi tabelami) na rejestr:
  jeden wiersz na sprzedaż z kluczowymi kwotami w kolumnach do porównania,
  rozwijany detal FIFO pod wierszem (klik/Enter/Spacja, wyrenderowany
  serwerowo — zero doładowywania JS-em).
- Nowa karta „Podsumowanie {rok}" — 6 kafelków KPI (sprzedaże, przychód, koszt,
  dochód, podatek, na rękę) liczone wg aktywnej polityki kosztu.

### Zmieniono — `_alloc_detail.html` (współdzielony przez `/sales` i „co jeśli
sprzedam teraz" na `/pit38`)
- Kurs sprzedaży pokazywany RAZ nad tabelą alokacji (wcześniej powtarzał się w
  osobnym wierszu prozy przy KAŻDYM locie).
- Jedna tabela alokacji zamiast dwóch — kurs lotu jako kolumna z `ⓘ` (tooltip +
  link do tabeli NBP), cena nabycia/prowizja w `title` komórki „Lot”.
- Polityki kosztu jako jedna linia zamiast drugiej tabeli.

### Zmieniono — `/pit38` skondensowany do nagłówka deklaracji
- Nowa pierwsza karta „Do wpisania w deklarację” — poz. C (wg aktywnej polityki)
  + sekcja G + kafelek **RAZEM DO ZAPŁATY** (nowe pole `report['total_due_pln']`
  w `tax/pit38.py::annual_report()`).
- „Polityka kosztu” jako 3 kafelki z podatkiem i deltą; przychód/koszt/dochód i
  podstawa prawna przeniesione do zwiniętego `<details>` pod kafelkami.
- Sekcja G scalona z PIT/ZG (i tak jest jej pochodną) w jedną kartę.
- „Co jeśli sprzedam teraz”: formularz w jednej linii, wynik jako pasek KPI,
  rozbicie FIFO domyślnie zwinięte (wcześniej zawsze rozwinięte).
- Ślad obliczeń per lot: wiersze pogrupowane wizualnie po dacie sprzedaży.

## [0.3.0] - 2026-07-29

Trzecie wydanie: pełna przejrzystość rozliczeń (krok 16, `docs/PLAN_KROK_16_transparentnosc.md`) —
każda kwota PLN daje się rozłożyć aż do numeru tabeli NBP, dywidendy dostają jedno źródło prawdy,
granty pokazują wartość zamiast samego harmonogramu, a pulpit ma konfigurowalny wykres.

### Dodano — rozbicie FIFO do numeru tabeli NBP
- **Numer tabeli NBP** (`providers/fx_nbp.py`): migracja v3 dokłada `nbp_rates.table_no`; nowe
  `table_urls()` (link do archiwum nbp.pl + do surowego JSON-a `api.nbp.pl` jako zapasowy,
  zweryfikowany empirycznie) i `backfill_table_numbers()` (dogania wiersze sprzed tego kroku,
  nigdy nie dotyka już zamrożonego kursu).
- **`tax/trace.py`** (nowy moduł): `fx_derivation()` — wyprowadzenie kursu w formacie „zdarzenie →
  D-1 (art. 11a) → ostatnia opublikowana tabela → kurs" jako gotowe zdanie po polsku, bez żadnego
  zapytania do NBP (czyta tylko już zamrożone kolumny). `enrich_allocations()` — dokłada do każdej
  alokacji FIFO dane lotu, oba wyprowadzenia kursu, kwoty EUR (pochodne zamrożonego PLN) i które
  polityki kosztu uznają dany lot; współdzielone przez symulację i sprzedaże zrealizowane.
- Karta „co jeśli sprzedam teraz" na **PIT-38**: pełne rozwijane rozbicie per lot zamiast samej
  sumy podatku.
- Strona **Sprzedaże** (nowa, `/sales`): każda zrealizowana sprzedaż rozwijalna do tego samego
  rozbicia; **cofanie sprzedaży** (`tax/lots.py::reverse_sale`) przywraca `qty_remaining` lotom —
  literówka w formularzu sprzedaży już nie wymaga ręcznej edycji SQLite.
- Eksport CSV/XLSX z **PIT-38**: nowe kolumny (koszt/przychód EUR, numer tabeli NBP lotu i
  sprzedaży) — eksport to teraz ten sam dowód co ekran.

### Dodano — dywidendy: jedno źródło prawdy
- `tax/dividends.py::add_dividend()`: reinwestycja (DRIP) jest teraz **opcjonalna** — formularz
  ręczny na **Dywidendy** przechodzi przez tę samą funkcję co import PDF, więc kurs NBP zamrożony
  na Record Date i (opcjonalny) lot DRIP powstają identycznie niezależnie od źródła wpisu.
- Nowa `backfill_missing_dividend_rates()` dogania dywidendy wpisane ręcznie przed tym krokiem.
- Migracja v3 dokłada `dividends.currency`. Historia na **Dywidendy**: waluta, brutto EUR **i**
  PLN, kurs NBP z numerem tabeli, kolumna reinwestycji (ilość/cena/data lub „gotówka”).
- Sensory MQTT dywidend (`dividends_*`) **bez zmian** — świadomie zostają na bieżącym kursie EUR,
  żeby nie zerwać historii encji w HA; PLN na zamrożonym kursie jest tylko w UI/PIT-38.

### Dodano — granty: wartość, nie tylko harmonogram
- `tax/grants.py::valuation()`: dla każdej transzy — wartość **dziś** (bieżąca cena/kurs, część
  wciąż w portfelu) i wartość **zrealizowana** (cena i kurs NBP z dnia faktycznej sprzedaży, część
  skonsumowana przez `sale_allocations`). Transze jeszcze niedopasowane (`reconcile_vesting`)
  pokazują całość jako prognozę, jawnie oznaczoną.
- Strona **Granty**: kolumny „Wartość dziś EUR/PLN” i „Zrealizowano PLN”, rozwijalna lista sprzedaży
  per transza z kursem NBP.

### Dodano — pulpit: konfigurowalny wykres
- `quotes.py::closes_in_range()`/`prune_intraday()`, nowy endpoint `/api/chart?range=`, joby
  schedulera `refresh_intraday_job` (co `poll_interval_minutes`) i `prune_intraday_job` (cron
  3:00) — zakres **1D** ma wreszcie z czego rysować (Yahoo intraday istniało w kodzie, ale nikt go
  nie wołał). Zakresy 1D/1W/1M/3M/6M/1R/3L/5L/MAX, wybór zapamiętany w `localStorage`.

### Dodano — usprawnienia porządkowe
- Formularze lotu/sprzedaży/dywidendy odrzucają daty przyszłe z czytelnym komunikatem (zamiast
  gołego 500 — NBP zwraca HTTP 400 na przyszłe daty).
- Selektor roku na **PIT-38**: lista lat z rzeczywistymi zdarzeniami zamiast pola liczbowego.

### Klauzula
To kalkulator pomocniczy, nie doradztwo podatkowe. Rozbicie do numeru tabeli NBP służy weryfikacji
liczby, nie zastępuje konsultacji z doradcą — patrz README „Jak zweryfikować kwotę z PIT-38”.

## [0.2.0] - 2026-07-29

Drugie wydanie: pełny silnik podatkowy PIT-38 dla pracowniczego planu akcji Nokii (ESPP + LTI),
zbudowany na podstawie realnych wyciągów Computershare użytkownika. Obejmuje kroki 11-15 z
`docs/BLUEPRINT.md` §3a — od kursów NBP po raport gotowy do wpisania w deklarację.

### Dodano — silnik podatkowy
- **Kursy NBP** (`providers/fx_nbp.py`): kurs średni z ostatniego dnia roboczego poprzedzającego
  zdarzenie (art. 11a ustawy o PIT), z cofaniem do 10 dni wstecz przez weekendy/święta. Raz
  zapisany kurs nigdy nie jest przeliczany ponownie.
- **Loty i FIFO** (`tax/lots.py`, `tax/policy.py`): sprzedaż konsumuje loty metodą FIFO
  (`sale_allocations`), z obsługą sprzedaży częściowej i akcji ułamkowych. Trzy polityki kosztu
  (`own_only`/`own_plus_drip`/`all_at_acquisition`) liczone równolegle z tych samych zapisanych
  alokacji, każda z podstawą prawną — strona **Loty** pokazuje wszystkie trzy obok siebie.
- **Import wyciągów Computershare** (`importers/computershare_pdf.py`): parser PDF (layout-mode
  `pypdf`) sekcji Purchases/Matching Shares/RS AWARD/Dividend Reinvested/Withhold-to-Cover.
  Przyrostowy i idempotentny — ten sam wyciąg wgrany drugi raz daje `rows_unchanged`, nigdy
  duplikatów; rozbieżności trafiają do kolejki konfliktów zamiast cichego nadpisania. Strona
  **Importy**: upload, kolejka konfliktów (w tym ręczne potwierdzenie realnej sprzedaży przy
  Withhold-to-Cover typu B), historia wgrań.
- **Granty ESPP/LTI i vesting** (`tax/grants.py`): harmonogram transz z wyciągów, reconciliation
  dopasowujący loty do transz po dokładnej, jednoznacznej ilości (nigdy nie zgaduje przy
  niejednoznaczności), przypomnienia `vest_reminder_days` przed nadchodzącą datą. Strona
  **Granty** (wyłącznie odczyt).
- **Sekcja G — dywidendy w PLN** (`tax/dividends.py::compute_dividend_tax_pln`): łańcuch
  u źródła → zaliczenie traktatowe → Belka → dopłata w PL / odzysk z Vero, liczony na kursie NBP
  zamrożonym na dzień wypłaty (art. 11a) — w odróżnieniu od istniejącego orientacyjnego kalkulatora
  EUR na bieżącym kursie.
- **Raport PIT-38** (`tax/pit38.py::annual_report`): poz. C (trzy polityki kosztu naraz), sekcja G,
  PIT/ZG, ślad obliczeń per lot (kurs NBP i data osobno dla kosztu lotu i przychodu sprzedaży).
- **„Co jeśli sprzedam teraz"** (`tax/whatif.py::simulate_sale`): symulacja sprzedaży na tej samej
  alokacji FIFO co realna sprzedaż (`tax/lots.py::_plan_fifo`, wydzielona z `_allocate_fifo` bez
  zmiany zachowania), bez żadnego zapisu do bazy.
- Strona **PIT-38**: selektor roku, wszystko powyższe w jednym miejscu, formularz what-if
  (GET/read-only), widok do druku (`?print=1`, PDF przez przeglądarkę), eksport CSV i XLSX
  (`openpyxl`, arkusze Podsumowanie/Ślad per lot/Dywidendy).
- 12 nowych encji MQTT: 5× loty i FIFO, 2× granty (`unvested_qty`, `next_vest_date`), 5× PIT-38 i
  symulacja.

### Klauzula
To kalkulator pomocniczy, nie doradztwo podatkowe. Wartości do PIT-38 potwierdź z własnym
rozliczeniem lub doradcą — add-on pokazuje pełny ślad obliczeń per lot właśnie po to, żeby dało
się to zweryfikować, a nie przyjąć na wiarę.

## [0.1.2] - 2026-07-28

Odporność na niestabilne zewnętrzne źródła po przeglądzie logów produkcyjnych 0.1.1 — dwa
powtarzalne błędy w każdym cyklu `fetch_news` (co 30 min), żaden nie psuł danych, ale marnowały
czas i zaśmiecały logi tracebackami.

### Naprawiono — GDELT (HTTP 429) i router LLM (401/502) bez samoczynnego powrotu
- **GDELT** (`providers/news_gdelt.py`): zmierzone empirycznie z zewnątrz add-onu — 429 to blokada
  na poziomie IP, nie efekt zbyt szybkich ponowień (curl co 6s, powyżej deklarowanego limitu 1/5s,
  i tak dostawał 429). Dodano cooldown: po wyczerpanych ponowieniach (429/502/503) źródło zapisuje
  znacznik w cache HTTP (SQLite, przeżywa restart) i kolejne cykle `fetch_news` pomijają je bez
  sięgania do sieci przez 6h, zamiast bić głową w tę samą blokadę co 30 min.
- **`news.py::aggregate()`**: znane błędy providerów (`QuoteProviderError`) logują się teraz jako
  `WARNING` z krótkim opisem, nie jako `ERROR` z pełnym tracebackiem — realne, nieoczekiwane błędy
  (`logger.exception`) zostają czytelne w logach zamiast tonąć w szumie.
- **Łańcuch AI** (`ratelimit.py` + `ai/provider.py`): istniejący circuit breaker
  (`is_circuit_open`/`record_failure`/`record_success`) był liczony, ale nigdzie nie używany do
  pomijania ogniw — `analyze()` wołało martwe ogniwo (router LLM zwracający naprzemiennie 401/502
  na upstreamie mimo poprawnego klucza, zmierzone na żywo) w każdym cyklu. Teraz po 3 kolejnych
  porażkach ogniwo jest pomijane przez 30 minut, po czym obwód sam się zamyka.

### Zmieniono
- Domyślny `local_llm_model`: `gemini-3.5-flash` → `gemini-3.1-flash-lite` — pod tym samym
  ładunkiem (`score_news`, 15 newsów, pełny schemat) zmierzone 2,5× szybciej (4,4s vs 11s) i 2×
  mniej tokenów (1785 vs 3885), przy tej samej jakości ocen (15/15) i innej trasie upstreamu
  routera (mniej podatnej na obserwowane 401/502).

## [0.1.1] - 2026-07-28

Poprawka błędu widocznego na żywo tuż po 0.1.0 + nowe niezależne źródło ceny.

### Naprawiono — zamrożona cena (price_eur i pochodne)
- Yahoo Finance czasem zwraca najnowszą dzienną świecę z `close: null` (jeszcze niedomknięta) —
  parser (`providers/yahoo.py`) po prostu ją odrzucał zamiast sięgnąć po `meta.regularMarketPrice`
  z tej samej odpowiedzi. Efekt: `price_eur` (i pochodne: `change_pct_day`, `ericsson_price`,
  `omxh25_value`, `eurpln_rate`, `rel_perf_1d_vs_omxh25`, `rel_perf_1m_vs_ericsson`, `beta_60d`,
  `alpha_verdict`, `sma_20/50`, `rsi_14`, `trend`, `last_quote_ts`) potrafiły zamrozić się na
  wiele dni mimo poprawnie działającego pollera co `poll_interval_minutes`.
- Fix: dla **ostatniego** punktu serii, gdy `close` jest puste, podstawiana jest
  `meta.regularMarketPrice` (ts zostaje bucketem dnia, jak dotychczas — bez tworzenia duplikatu
  wiersza). Dziury w środku serii (prawdziwe braki danych, np. święta) nadal pomijane bez zmian.

### Dodano — Avanza jako dodatkowe, niezależne źródło żywej ceny
- Nowy `providers/avanza.py`: publiczne, bezkluczowe API Avanzy (`_api/market-guide/stock/{id}`),
  używane wyłącznie do odświeżania bieżącej ceny instrumentu głównego (nie zastępuje Yahoo jako
  źródła historii/backfillu/benchmarków). Zero nowych zależności w `requirements.txt`.
- Nowa `quotes.refresh_live_price()`: częściowy `UPDATE` samego `close`, zachowujący
  `open`/`high`/`low`/`volume` zebrane przez Yahoo dla tego samego dnia (nie zeruje `day_high`/
  `day_low`/`volume`).
- Nowa opcja `avanza_live_price_enabled` (domyślnie włączona) — wyłącznik awaryjny bez przebudowy
  obrazu, gdyby ten nieoficjalny endpoint kiedyś zmienił kształt lub zablokował ruch. Awaria Avanzy
  nigdy nie przerywa reszty publikacji sensorów (osobny `try/except` w `main.py`).

## [0.1.0] - 2026-07-28

Pierwsze wydanie: śledzenie rynku, warstwa AI, prosty portfel, pełny web UI.

### Rynek i technika
- Kurs NOKIA.HE (Yahoo Finance, backfill 5 lat), cache HTTP w SQLite, rate limiting z circuit breakerem.
- Wskaźniki: SMA 20/50, RSI 14, zmienność 30-dniowa, opis trendu.
- Świadomość sesji giełdowej Helsinki (`binary_sensor.market_open`) do oszczędzania zapytań poza sesją.

### Benchmark i FX
- Ericsson (ERIC-B.ST), OMXH25, ADR (NYSE proxy poza sesją), beta/alfa 60-dniowa.
- Kursy EUR/PLN (prezentacyjne, ECB fallback) i NBP (pod rozliczenie podatkowe w 0.2.0).

### Newsy i sentyment AI
- Agregacja z RSS (Nokia IR, Google News, Kauppalehti/Yle), GDELT, Finnhub, MarketAux; dedup po kanonikalizacji URL + hashu tytułu.
- Łańcuch providerów AI: lokalny `freellmapi` (primary) → Gemini (fallback) → Anthropic (opcjonalnie), z walidacją `response_format`, retry na HTTP 502 i twardym dziennym limitem wywołań.
- Batchowa ocena newsów: sentyment, wpływ, horyzont, teza, tagi.

### Prognozy i rekomendacja AI
- Dzienna analiza po zamknięciu sesji: prognozy 1 tydzień / 1 miesiąc / 12 miesięcy z przedziałem ufności.
- Rekomendacja AI (kup/akumuluj/trzymaj/redukuj/sprzedaj) kontekstowa względem średniej ceny zakupu, z jawnym disclaimerem.
- Backtest trafności prognoz (MAPE) po rozliczeniu każdej prognozy w `target_date`.

### Smart alerty
- 5 rodzajów: spadek sentymentu, gwałtowny ruch kursu, przebicie przedziału prognozy, rozbieżność vs OMXH25, news o wysokim wpływie.
- Histereza i anty-spam (minimalny odstęp per rodzaj alertu), publikacja przez MQTT i `notify`.

### Portfel i dywidendy
- Prosty stan posiadania (ilość + średni koszt) z P&L w EUR i PLN.
- Kalkulator podatku od dywidend: podatek u źródła (Finlandia) → zaliczenie do stawki traktatowej w Polsce → Belka 19% → kwota do odzyskania z fińskiego Vero.
- Schemat bazy od startu przygotowany na loty/granty/vesting (0.2.0) — brak migracji danych przy przejściu na pełne rozliczenie.

### Web UI
- Flask + waitress na ingressie HA, 6 stron: Pulpit, Portfel, Dywidendy, Newsy, Prognozy, Ustawienia.
- Wykres cenowy 90 dni, przycisk „Przeanalizuj teraz”, wybór modelu AI z listy pobranej na żywo z routera.
- Poprawnie działa pod ingress reverse proxy (WSGI `SCRIPT_NAME` middleware + `url_for()` wszędzie — statyki, linki, przekierowania POST).

### MQTT Discovery
- ~55 sensorów + 1 binary_sensor pod jednym urządzeniem „Nokia Tracker”, `object_id` w każdym payloadzie gwarantuje stabilne `entity_id` niezależnie od nazwy encji.

### Znane ograniczenia (dochodzą w 0.2.0)
- Rozliczenie PIT-38 (FIFO, loty, ESPP/LTI vesting, import PDF Computershare) — jeszcze nie zaimplementowane.
- Kalkulator dywidend liczy na bieżących ustawieniach procentowych, nie na zamrożonym kursie NBP z dnia poprzedzającego wypłatę.
- Dashboard Lovelace nie jest dostarczany — web UI dodatku na ingressie jest głównym interfejsem.
