# Changelog

## [0.14.0] - 2026-08-16

Krok 30 (`docs/PLAN_KROK_30_dywidendy.md`), pierwsza fala z Roadmapy v2
(`docs/ROADMAP.md`) — kalendarz i prognoza dywidend.

Rozpoznanie na realnych danych produkcyjnych przy planowaniu obaliło dwie przesłanki
pierwotnego zapisu w roadmapie: Nokia płaci **kwartalnie**, nie raz w roku (realne
record date 2023-2026 potwierdzają rytm), i harmonogram ogłoszeń dostał **nową tabelę**
zamiast pól w ustawieniach (decyzja użytkownika — WZA uchwala kwotę roczną w 4 ratach,
chce wpisać cały harmonogram naraz). Oba odstępstwa udokumentowane wprost w
`docs/ROADMAP.md`.

### Dodano
- **Migracja v10: `dividend_schedule`** — ogłoszony harmonogram WZA. Klucz naturalny
  `(fiscal_year, instalment)`, trzy poziomy pewności (`dates_confirmed` odróżnia
  potwierdzoną datę od samej zapowiedzianej kwoty), `matched_dividend_id` łączy ratę
  z realną wypłatą po jej zaimportowaniu.
- **Nowy moduł `dividend_outlook.py`** (nie `tax/dividends.py::forecast()` — kolizja
  nazwy z prognozami CENOWYMI, `forecasts.py`/`forecast_1w_eur`/...). `per_share_history()`
  liczy stawkę na akcję jako medianę ostatnich 4 REALNYCH wypłat, wykluczając wiersze
  odtworzone z „Vested Dividend Shares” (`taxdiv.is_estimated()`, nowa funkcja dzielona
  z `tax/pit38.py`) — te mają w polu ilości akcje kupione z reinwestycji, nie bazę
  uprawnioną, więc naiwne liczenie dałoby stawkę ~150× za wysoką. Poniżej 4 realnych
  wypłat silnik nie zgaduje — zero zdarzeń szacowanych, tylko jawny powód.
- `entitled_base()`/`qty_on()` — dywidenda liczona od akcji FAKTYCZNIE posiadanych
  (wolne + z ograniczeniem zbycia), nie od zablokowanych transz ESPP/LTI. `calendar()`
  scala raty z harmonogramu z zdarzeniami szacowanymi (rata ogłoszona zawsze wypiera
  szacowaną w tym samym kwartale), podatek liczy ISTNIEJĄCYM łańcuchem sekcji G
  (`compute_dividend_tax`/`compute_dividend_tax_pln`), zero nowej matematyki podatkowej.
- **`/dividends`**: karta „Kalendarz” (wykres + tabela zdarzeń, badge
  potwierdzona/zapowiedziana/szacowana, horyzont `?lata=1|3|5`), karta „Ogłoszony
  harmonogram” (formularz na 4 raty naraz, bez walidacji dat przyszłych — to sens tej
  tabeli), karta „Założenia prognozy” (stawka z pasmem niepewności, liczba wypłat
  rocznie, ile realnych/szacunkowych wypłat wzięto pod uwagę).
- 3 nowe sensory MQTT: `next_dividend_date` (+ atrybuty), `dividend_next_12m_gross_eur`,
  `dividend_next_12m_net_pln` — nazwy świadomie omijają `forecast_*`.

### Zweryfikowano
- Na realnych danych produkcyjnych: stan posiadania skoczył ~24× (119,66 → 2 888,66
  akcji) przez vesting ESPP/LTI między ostatnią realną wypłatą a dziś — dokładnie
  sytuacja, po którą ta fala powstała. `dividends.quantity`/`gross_eur` mają dwie różne
  semantyki zależnie od pochodzenia wiersza, potwierdzone przez bezpośrednie
  sparsowanie 5 realnych wyciągów Computershare przed implementacją.

60 nowych testów (904 → 964), TDD przez cały krok.

## [0.13.1] - 2026-08-16

Naprawa realnego błędu znalezionego na żywo, tego samego dnia co 0.13.0 — pierwsze prawdziwe
pytanie zadane przez `/asystent` na produkcji nie rozpoznało intencji, mimo poprawnego klucza i
działającego routera.

### Naprawiono
- **`CHAT_INTENT_SCHEMA` łamał się na Gemini structured output** — `params.*` używały wzorca
  `"type": [X, "null"]` (nullable union) do wyrażenia opcjonalnych parametrów. Gemini (zarówno
  bezpośrednio przez `ai/gemini.py`, jak i przez router freellmapi routing na Google) zwracał
  HTTP 400 `"Proto field is not repeating, cannot start list"` DOKŁADNIE na tej ścieżce —
  `response_schema` Gemini nie wspiera typu jako listy. Skutek na produkcji: `local` i `gemini`
  padały jednakowo (ten sam schemat, ten sam backend), `anthropic` (trzecie ogniwo) też odpadał —
  ale z zupełnie innego, niezwiązanego powodu (wyczerpane środki na koncie, nie błąd kodu) —
  więc każde pytanie kończyło się jako `intent="inne"`.
- Żaden inny schemat w `ai/prompts.py` nigdy nie używał nullable-union — wzorzec był unikalny dla
  tego jednego schematu, wprowadzony w 0.13.0 bez sprawdzenia na żywym Gemini przed wydaniem.
  Naprawione powrotem do sprawdzonego wzorca: pojedynczy typ + pominięcie z `required` (jak
  opcjonalne pole w `SCORE_NEWS_SCHEMA`) — model po prostu pomija parametr, którego pytanie nie
  zawiera, zamiast wpisywać `null`.

904 testy (bez zmiany liczby — poprawka schematu, nie nowej funkcji).

## [0.13.0] - 2026-08-16

Krok 29 (`docs/PLAN_KROK_29_asystent.md`), szósta fala z `docs/ROADMAP.md` — Asystent: czat nad
własnymi danymi. Ostatnia fala funkcjonalna z pierwotnej roadmapy; 1.0.0 zostaje zarezerwowane
(zgodnie z pierwotną decyzją) na wydanie po jednym pełnym sezonie rozliczeniowym na tym silniku.

### Dodano
- **Strona `/asystent`** + pole szybkiego pytania na pulpicie — pytanie w naturalnym języku
  polskim, trójstopniowo: AI #1 rozpoznaje intencję (jeden z 11 tematów, `CHAT_INTENT_SCHEMA`),
  Python liczy odpowiedź ISTNIEJĄCYM, już przetestowanym silnikiem (zero nowej matematyki), AI #2
  (opcjonalna, `ai_chat_narration_enabled`) ubiera policzone liczby w zdanie po polsku. Liczby
  renderuje szablon z wyniku silnika, nigdy tekst modelu — halucynacja kwoty strukturalnie
  niemożliwa. Wymuszone przez `ai/provider.py::analyze()`, który obsługuje tylko ustrukturyzowany
  JSON, bez pętli tool-calling.
- **11 intencji**, każda deleguje do jednej istniejącej funkcji: podatek ze sprzedaży
  (`tax/whatif.py::simulate_sale`), ile mogę sprzedać (`tax/lots.py`/`tax/grants.py`), vesting
  (`tax/grants.py::vesting_timeline`), ile zarobiłem (`portfolio.py`/`tax/policy.py`), dywidendy
  w roku i PIT za rok (`tax/pit38.py::annual_report`), koszt sprzedaży teraz i kiedy sprzedać
  (`advisor.py`), porównanie z benchmarkiem (`sensors.py::results_values`), straty z lat
  ubiegłych (`tax/losses.py`), koncentracja majątku (`advisor.py::overview`). Walidacja paramów
  przed silnikiem, uczciwa porażka na `InsufficientLotsError`/`CostBasisMissingError` zamiast
  zmyślonej liczby, nieznana intencja z modelu → „inne”, nigdy wyjątek do użytkownika.
- **Karta „Stan AI”** na `/ustawienia` + pasek nad `/asystent` (`ai/status.py`) — domknięcie długu
  z roadmapy 0.8.1: circuit breaker i liczniki wywołań istniały od kroku 6/7, ale nie miały
  żadnego konsumenta w UI. Per ogniwo: wywołania/tokeny dziś, limit i ile zostało, stan obwodu,
  ostatni błąd; plus osiągalność lokalnego routera freellmapi.
- 3 nowe ustawienia: `ai_chat_enabled`, `ai_chat_narration_enabled`, `ai_max_calls_per_day_local`.
- Migracja bazy v9: `chat_log` (log pytań/intencji/odpowiedzi, przycinany do ostatnich 200).

### Naprawiono
- **Dzienny limit AI był globalny, nie per ogniwo** — `ai/provider.py::analyze()` sprawdzał
  wspólną pulę RAZ, przed całą pętlą łańcucha, więc wyczerpanie limitu płatnego `gemini`/
  `anthropic` blokowało też darmowy lokalny router `freellmapi`, mimo osobnego klucza i osobnych
  pieniędzy. Teraz limit sprawdzany per ogniwo, w pętli — wyczerpanie jednego pozwala przejść do
  następnego.

### Bez zmian (celowo)
- Zero nowych sensorów MQTT i zero zmian w `tax/*.py` — czat i status AI są warstwą UI nad
  istniejącymi silnikami i licznikami.
- Statystyki panelu admina routera freellmapi (`/api/health`, `/api/analytics/summary`) —
  zmierzone empirycznie: wymagają osobnej sesji e-mail+hasło, klucz Bearer używany przez
  `/v1/chat/completions` ich nie otwiera. Kod mimo to próbuje tym kluczem i degraduje czysto do
  braku danych, bez dokładania kolejnego sekretu do opcji dodatku.

904 testy (831→904, TDD przez wszystkie 8 podkroków).

## [0.12.0] - 2026-08-16

Krok 28 (`docs/PLAN_KROK_28_ux_mobile.md`), piąta fala z `docs/ROADMAP.md` — UX/mobile
+ wykresy. Retrofit wszystkich istniejących stron pod mobile-first + trzy nowe wizualizacje
Chart.js, w sześciu podkrokach (28.1–28.6), każdy osobno zweryfikowany testami i Playwright.

### Dodano
- **Globalny przełącznik waluty PLN/EUR** w nagłówku (zapamiętany w `localStorage`) — nowy
  `cur_block()` w `_macros.html` renderuje obie waluty naraz, CSS przełącza widoczny wariant;
  wykresy (`/wyniki`) przerysowują się bez przeładowania strony (`nt:currency-change`).
- **Tabele → karty poniżej 430px** — `<table>` zostaje w DOM (CSS-only), etykiety z `data-label`
  przez `::before`; zastosowane na Lotach, Sprzedażach, Grantach, Dywidendach, Newsach.
- **Globalny selektor roku podatkowego** w nagłówku — zastąpił trzy zdublowane kopie na
  `/pit38`, `/sales`, kreatorze (`@app.context_processor`).
- **Trzy nowe wykresy**: donut trzech kubełków portfela (pulpit), słupki dywidend rok po roku,
  waterfall Poz. C na `/pit38` (przychód→koszt→dochód→strata odliczona [informacyjnie]→
  podatek→na rękę — suma zgodna z silnikiem co do grosza).
- **„Dziś warto wiedzieć"** na pulpicie — 0-3 zdania deterministyczne (bez AI): zmiana kursu,
  najbliższy vesting, sygnał podatkowy (tylko gdy jest i dostępna strata, i zysk w tym roku).
- Sortowanie kolumn (Loty, Dywidendy, Newsy), „Pokaż więcej" na Newsach (limit 50→200), sticky
  pasek z ceną/wartością portfela na pulpicie, jednolite stany puste (`empty_state()`), szkielety
  ładowania wykresów, widok do druku na `/wyniki` i `/plan`.

### Naprawiono
- **Kontrfaktyczny benchmark OMXH25** (`/wyniki` i sensor MQTT
  `benchmark_omxh25_counterfactual_pln`) liczył się w EUR (przepływy z `build_xirr_cashflows`
  są EUR, OMXH25 zarejestrowany jako `currency="EUR"`) i publikował się bez konwersji pod
  jednostką/nazwą „PLN" od kroku 25 (0.9.0) — realna wartość EUR podpisana jako PLN, zawyżenie/
  zaniżenie o rząd kursu EUR/PLN (~4x). Znalezione empirycznie przy budowie przełącznika waluty.

### Bez zmian (celowo poza falą)
- Oś czasu vestingu z pierwotnego planu — okazała się już istnieć (`.tl-rail`/`.tl-dot` z kroku 26).
- Filtrowanie rocznego na `/wyniki`/`/grants`/`/dividends`/`/lots` — pierwotny szkic planu to
  zakładał, ale roadmapa mówiła tylko o konsolidacji trzech ISTNIEJĄCYCH selektorów w jeden.

831 testów (od 820 na starcie fali, +11 nowych dla `dashboard_insights.py`, TDD).

## [0.11.0] - 2026-08-16

Krok 27 (`docs/PLAN_KROK_27_straty_kreator.md`), czwarta fala z `docs/ROADMAP.md` — Podatki:
straty z lat ubiegłych i kreator rozliczenia rocznego. Dwie realne luki: silnik podatkowy znał
dochód ujemny (`income_pln < 0`), ale strata po prostu znikała (`tax_pln = max(0, ...)` zerował
podatek bez zapisania faktu, że strata w ogóle wystąpiła) — art. 9 ust. 3-3a ustawy o PIT pozwala
odliczyć ją w kolejnych 5 latach; i rozliczenie roczne nie miało żadnego śladu „co już sprawdziłem"
poza pamięcią użytkownika.

### Dodano
- **Straty z lat ubiegłych** (`tax/losses.py`) — silnik liczy straty per rok per polityka kosztu
  (trzy niezależne salda — `own_only`/`own_plus_drip`/`all_at_acquisition` dają trzy różne historie
  strat), pilnuje 5-letniego okna odliczeń i limitu z art. 9 ust. 3-3a (całość jednorazowo dla strat
  do 5 mln zł, 50%/rok gdy odliczenie już rozłożone na raty). Nigdy nie nadpisuje bezwarunkowo
  wiersza straty, który ma już zarejestrowane odliczenia — korekta danych zmniejszająca stratę
  poniżej już wykorzystanej kwoty zgłasza konflikt do ręcznej decyzji, nie ciszej nadpisuje.
- **`/pit38/kreator`** — checklista rozliczenia rocznego, samosprawdzająca się z bazy (import
  wyciągu, rozstrzygnięte konflikty, saldo zgodne z wyciągiem — blokują zamknięcie roku; sekcja G
  bez szacunków, decyzja o odliczeniu straty — informacyjne), formularz zapisu odliczenia straty per
  pozycja, zamknięcie/odblokowanie roku (migawka kwoty do zapłaty, nie zamrożenie samych danych —
  dopisanie brakującej transakcji po zamknięciu wciąż możliwe, tylko widocznie oznaczone jako
  rozjazd ze snapshotem).
- **Karta „Straty z lat ubiegłych"** na `/pit38` — dostępna strata, odliczone w tym roku, podatek po
  odliczeniu, link do kreatora. `total_due_pln` liczy się teraz WYŁĄCZNIE z jawnie zarejestrowanych
  odliczeń (kreator), nigdy z automatycznego maksimum — strata dostępna to nie strata użyta.
- **Optymalizator momentu sprzedaży** na `/plan` — „sprzedaż dziś vs 2 stycznia następnego roku":
  różnica podatku (z uwzględnieniem dostępnej straty), różnica przepadku dopasowania ESPP,
  rekomendacja kierunku decyzji liczona deterministycznie (nie przez AI).
- 2 nowe sensory MQTT: `loss_available_pln`, `loss_used_this_year_pln`.
- Migracja bazy v8: `tax_loss_carryforward`, `tax_loss_deductions`, `tax_year_closed`.

### Bez zmian
- Podstawa prawna (art. 9 ust. 3-3a ustawy o PIT) opisana w kodzie jako stan wiedzy z planowania,
  nie cytat z Dziennika Ustaw — do potwierdzenia na aktualnym tekście ustawy przed pierwszym realnym
  użyciem (żaden z lat 2023-2026 w danych produkcyjnych nie jest dziś stratny w żadnej z trzech
  polityk, więc funkcja nie miała jeszcze okazji zadziałać na realnych pieniądzach).

820 testów (od 793 na starcie fali).

## [0.10.0] - 2026-08-15

Krok 26 (`docs/PLAN_KROK_26_doradca.md`), trzecia fala z `docs/ROADMAP.md` — Doradca planu
pracowniczego: jedyna część roadmapy, której nie da się kupić w narzędziu premium, bo żadne z nich
nie zna specyfiki polskiego ESPP/LTI. Cztery pytania, na które dodatek dotąd nie odpowiadał wprost:
ile tracę sprzedając dziś, kiedy co wpada, ile da mi regularna wpłata, czy nie mam za dużo w jednym
koszyku, który jest jednocześnie moim pracodawcą.

### Dodano
- **Strona „Plan"** (grupa „Portfel") — cztery karty:
  - **„Ile tracę, sprzedając dziś"** — dopisuje KWOTĘ do ostrzeżenia, które dotąd (od kroku 21)
    było samym zdaniem bez liczby. Przepadek dopasowania ESPP liczony proporcjonalnie do
    sprzedanych sztuk (mianownik = oryginalna ilość lotu), z tabelą per lot i (gdy pokrycie
    pozwala) nogą podatkową sprzedaży całego ograniczonego pakietu.
  - **„Harmonogram vestingu"** — oś czasu oczekujących transz (kafelki: w tym kwartale / w tym
    roku / w przyszłym roku, zaległe pokazywane osobno, nigdy zsumowane po cichu), pozioma szyna
    z kropkami na desktopie, pionowa lista na telefonie.
  - **„Planer ESPP"** — wpłata EUR/mc × liczba miesięcy × cena → akcje własne, akcje dopasowania,
    wartość na koniec, podatek wg aktywnej polityki kosztu; podgląd na żywo pod formularzem, trzy
    chipy scenariusza cenowego (bieżąca / −20% / +20%) zamiast suwaka, którym nie da się trafić na
    telefonie.
  - **„Ryzyko koncentracji"** — udział akcji pracodawcy (wartość rynkowa + oczekujące dopasowania)
    w łącznym majątku vs konfigurowalny próg ostrzeżenia; puste, dopóki „Reszta majątku" na
    Ustawieniach jest zerem (żeby nikomu, kto nic nie wpisał, nie wyskoczyło fałszywe 100%).
- 3 nowe sensory MQTT: `forfeit_value_pln`, `concentration_pct`, `vest_this_year_qty`.
- 2 nowe opcje/ustawienia: `other_net_worth_pln` (reszta majątku w PLN, zwykłe pole liczbowe —
  świadomie NIE encja HA), `concentration_alert_pct` (próg ostrzeżenia, domyślnie 25%).
- `tax/grants.py::restricted_own_lots()` (surowe fakty per ograniczony lot — ile dopasowania ESPP
  na nim wisi, `restricted_own_summary()` przepisane na delegację do niej, wyjście bit-w-bit
  identyczne) i `vesting_timeline()` (oś czasu, funkcja siostrzana do `unvested_summary()`, nie jej
  rozszerzenie — żeby nie ruszać kontraktu z trzema produkcyjnymi konsumentami).
- Nowy moduł `advisor.py` — `forfeit_summary()`/`forfeit_for_quantity()`/`forfeit_for_allocations()`
  (ta ostatnia przyjmuje kształt `simulate_sale()["lots_consumed"]` — hak dla przyszłego what-if na
  `/pit38`, bez nowej matematyki), `espp_plan()` (czysta, karmi syntetyczne loty do
  `tax/lots.py::_plan_fifo()` zamiast podrabiać dane w bazie), `concentration()`, `overview()`
  (jeden kompozytor dla strony `/plan` I sensora MQTT — nigdy dwóch różnych liczb dla tego samego
  faktu).
- `tax/whatif.py::_apply_policies()` — pętla trzech polityk kosztu wydzielona z `simulate_sale`
  bez zmiany zachowania, żeby planer ESPP liczył podatek DOKŁADNIE tą samą matematyką.
- **Brak migracji bazy** — świadomie: wszystkie cztery funkcje wyprowadzalne z istniejących danych
  (`lots`/`grants`/`vests` + jeden wiersz w tabeli KV `settings`).

### Zweryfikowano na realnych danych przed wdrożeniem
Trzy transze dopasowania ESPP (29,24 + 28,99 + 17,37 = 75,60606 szt.) implikują 151,20 akcji
własnych kupionych na tych datach, a ograniczonych jest tylko 142,7294 — różnica to ślad znanej
częściowej sprzedaży z 2025-10-27. Przewidziano ręcznie przed wdrożeniem: przepadek ≈ 71,37 szt.
(nie 75,60606 — gdyby reguła proporcjonalna nie zadziałała, obie liczby byłyby równe), wartość
≈ 587 EUR. Zgodność przewidywania z tym, co pokazała strona po deployu, to najostrzejszy sygnał, że
mianownik proporcji (`lots.quantity`, nie `qty_remaining`) i wykrycie częściowej sprzedaży działają
poprawnie.

## [0.9.0] - 2026-08-14

Krok 25 (`docs/PLAN_KROK_25_wyniki.md`), druga fala z `docs/ROADMAP.md` — wyniki: XIRR, TWR,
atrybucja zysku, kontrfaktyczny benchmark. Dotąd jedyną miarą zwrotu był punktowy
`unrealized_pnl_pct`/`total_return_pct` — zero miary zwrotu w czasie i zero rozbicia na to,
co go faktycznie napędza.

### Dodano
- **Strona „Wyniki"** (grupa „Portfel") — XIRR na wpłatach własnych (gotówka realnie
  wydana na akcje `own`; dopasowanie ESPP/LTI wchodzi tylko do wartości końcowej jako
  darmowy przypływ) obok TWR (neutralizuje moment wpłat, jedyna miara uczciwie
  porównywalna z indeksem), atrybucja zysku na pięć składników (zmiana kursu akcji /
  dopłata ESPP / akcje LTI / dywidendy gotówka+DRIP / efekt walutowy EUR-PLN —
  sumujące się co do grosza z zyskiem całkowitym, efekt walutowy liczony jako reszta
  z definicji), krzywa wartości portfela (PLN) na wykresie razem z kontrfaktycznym
  OMXH25 („gdyby te same wpłaty poszły w indeks"), tabela zwrotu rok po roku.
- 4 nowe sensory MQTT: `xirr_own_pct`, `twr_pct`, `fx_effect_pln`,
  `benchmark_omxh25_counterfactual_pln`.
- `analytics/` (nowy pakiet) — `history.py::rebuild()` (dzienna rekonstrukcja wartości
  portfela BEZ sieci, z lotów/alokacji sprzedaży/`quotes`/gęstych `nbp_rates`,
  materializowana w nowej tabeli `portfolio_history`), `returns.py::xirr()`/`twr()`
  (Newton + bisekcja awaryjna, czysty Python — bez numpy, `xirr()` zweryfikowany na
  referencyjnym przykładzie z dokumentacji Excela, 37.3%), `attribution.py::decompose()`,
  `benchmark.py::counterfactual()`/`counterfactual_series()`.
- `fx_nbp.backfill_range()` — gęsta seria kursów NBP (prerequisite krzywej wartości),
  kluczowana `effective_date` zamiast leniwego cache per zdarzenie podatkowe.
- Migracja bazy `v7` (`portfolio_history`).
- Dwa nowe joby schedulera: `backfill_nbp_range_job` (5:00, inkrementalny — pełny
  backfill 5 lat tylko przy pierwszym uruchomieniu) i `rebuild_portfolio_history_job`
  (5:30, po NBP).

### Naprawiono (znalezione w trakcie TDD, przed wdrożeniem)
- Krzywa wartości i tabela rok-po-roku na `/wyniki` były błędnie zagnieżdżone pod
  warunkiem „mam dzisiejszą cenę" w pierwszej wersji trasy, mimo że liczą się wyłącznie
  z `portfolio_history` (materializowanej nocnym jobem, niezależnej od bieżącego pollu
  cenowego) — strona pokazywałaby pusty wykres/tabelę przez cały dzień do najbliższego
  odświeżenia ceny, mimo że dane historyczne już były dostępne. Złapane testem przed
  wdrożeniem, nie na produkcji.

### Techniczne
- TDD przez cały krok — każdy moduł testowany na realnych obliczeniach, nie mockach.
  Kryterium akceptacji atrybucji: suma 5 składników == zysk całkowity z dokładnością do
  grosza, sprawdzane na scenariuszu mieszanym (own+matched+lti+DRIP+dywidenda gotówkowa,
  cena i FX oba w ruchu).
- `format.py`'s `money`/`qty`/`pct` (dotąd tylko karta „Portfel" na pulpicie) rozszerzone
  na `/wyniki` — druga strona pokazująca liczby zagregowane, nie wymagająca bajtowej
  zgodności z wyciągiem.
- 54 nowe testy (645 → 699), zero zmian w istniejących encjach/silniku podatkowym.

## [0.8.1] - 2026-08-14

Krok 24 (`docs/PLAN_KROK_24_backup.md`), pierwsza fala z nowej `docs/ROADMAP.md` — kopia
zapasowa i przywracanie danych. Dodatek nie miał dotąd żadnego sposobu na eksport własnych
danych ani ochronę przed znanym scenariuszem: cykl przeinstalowania add-onu (używany np. przy
odtwarzaniu ze starszego kodu, patrz `reference_supervisor_git_addon_rebuild`) czyści `/data`.

### Dodano
- **Strona „Kopia zapasowa"** (grupa „Dane") — eksport pełnego zrzutu bazy jako ZIP
  (`nokia.db` przez spójny `Connection.backup()`, `manifest.json` z wersją/schematem/licznikami
  wierszy, czytelne CSV sześciu tabel: loty/sprzedaże/alokacje/granty/transze/dywidendy).
- **Przywracanie z podglądem różnicy przed zapisem** — wgranie ZIP-a pokazuje ile wierszy
  przybędzie/zniknie/zostanie bez zmian per tabela, dopiero jawne potwierdzenie zapisuje.
  Ten sam kontrakt „nigdy nie nadpisuj w ciemno" co importer Computershare. Kopia z nowszego
  schematu niż obsługiwany przez zainstalowane wydanie jest odrzucana z czytelnym komunikatem
  (starszy schemat jest dozwolony — dociąga go `migrate()` po przywróceniu).
- **Nocny auto-snapshot** (4:00) do `/share/nokia_tracker/backup/nokia_YYYY-MM-DD.zip`,
  rotacja ostatnich 14 dni.
- `backup.py` — `export_zip()`/`restore_preview()`/`restore_apply()`, testowalne bez
  Flaska/schedulera na prawdziwych plikach SQLite. `db.SCHEMA_VERSION` (nowa stała, `PRAGMA
  user_version` docelowe po pełnym `migrate()`) jako jedno źródło prawdy zamiast duplikowania
  liczby migracji w kolejnych modułach.

### Techniczne
- TDD przez cały krok — 13 nowych testów (6 `backup.py` + 7 tras `/dane`), 645 total
  (632 → 645), wszystkie napisane i obserwowane jako czerwone przed implementacją.
- Zero zmian w istniejących encjach MQTT czy silniku podatkowym/portfelowym.

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
