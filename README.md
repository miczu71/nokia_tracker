# Nokia Tracker

Home Assistant add-on — osobisty asystent inwestycyjny dla akcji Nokia (NOKIA.HE, Nasdaq Helsinki).

Śledzi kurs, historię, newsy i sentyment (AI), generuje prognozy z weryfikacją trafności, porównuje
Nokię z benchmarkiem (Ericsson, OMXH25), prowadzi portfel oparty na lotach FIFO i pełne rozliczenie
podatkowe (PIT-38: przychody kapitałowe, dywidendy zagraniczne, PIT/ZG) dla pracowniczego planu akcji
Nokii (ESPP + LTI) na podstawie wyciągów Computershare/EquatePlus, i wystawia wszystko do Home
Assistant przez MQTT Discovery — plus pełny web UI na ingressie.

Pełny projekt architektoniczny: [`docs/BLUEPRINT.md`](docs/BLUEPRINT.md).

**Status:** wydanie **0.6.0** — pulpit pokazuje teraz **całe** portfolio, nie tylko akcje
uwolnione: nowy podział „W posiadaniu” (z linią wolne/z ograniczeniem, gdy część świeżo
kupionych akcji ESPP czeka na własne dopasowanie — sprzedaż przed jego uwolnieniem oznacza
utratę dopłaty 50%) / „Zablokowane” (nienabyte dopasowania ESPP i transze LTI, z szacunkową
wartością i najbliższą datą dostępności, plus ostrzeżenie o transzach z minioną datą) /
„Razem”. Naprawiono też przyczynę źródłową: importer parsował datę realnej dostępności akcji
(`Available from` z wyciągu Computershare) od kroku 13, ale nigdzie jej nie zapisywał, więc
harmonogram liczył zaległość wg daty NABYCIA — realnie ~4 tygodnie za wcześnie dla ESPP.
Jedno źródło prawdy w `tax/grants.py` (`unvested_summary`, `restricted_own_summary`) zasila
teraz zarówno pulpit, jak i istniejące sensory MQTT — `sensor.nokia_tracker_next_vest_date`
pokazuje od teraz datę dostępności, nie datę nabycia.

Wcześniej (0.5.0): złotówki tam, gdzie się o nich myśli, i podgląd na żywo przy
wpisywaniu: **pulpit** pokazuje każdą kwotę EUR z drugą linią `≈ X zł` po kursie bieżącym
(jawnie odróżnionym od kursu NBP używanego w rozliczeniu), formularze dodania **lotu**,
**sprzedaży** i **dywidendy** liczą kurs NBP i podatek na żywo pod polami — zanim klikniesz
„Dodaj” — tym samym silnikiem, który potem zapisuje dane, więc podgląd nigdy nie kłamie.
**Dywidendy** dostały jedną matematykę zamiast dwóch (kafelki podsumowania liczone teraz z tych
samych zamrożonych kursów NBP co tabela pod nimi). Nawigacja zwinięta z 11 płaskich linków do
5 sekcji (Pulpit / Portfel / Podatki / Dane / Ustawienia — bez JS rozwijają się natywnie).
Przy okazji: naprawiony błąd na **Grantach** (fantomowe puste wiersze przy niezrealizowanych
transzach), **Ustawienia** dostały brakujące pola stawek podatkowych (dotąd tylko do odczytu),
a **Portfel/Importy/Newsy/Prognozy** oczyszczone z powtórzonej prozy i martwych zapytań.
Wcześniej (0.4.0): pełna szerokość ekranu, **Sprzedaże** jako rejestr transakcji (wiersz na
sprzedaż, rozwijany detal FIFO), **PIT-38** z nagłówkiem „ile wpisać w deklarację” (poz. C +
sekcja G + RAZEM DO ZAPŁATY). Wcześniej (0.3.0): pełna przejrzystość rozliczeń — każda kwota PLN
rozkłada się aż do numeru tabeli NBP, zrealizowane sprzedaże z możliwością cofnięcia, dywidendy
z jednym źródłem prawdy, granty z wyceną bieżącą i zrealizowaną, pulpit z konfigurowalnym
zakresem wykresu (1D–MAX). Wcześniej (0.2.0): rynek, AI, portfel oparty na lotach, import
wyciągów Computershare (przyrostowy, idempotentny), pełny silnik podatkowy PIT-38 (trzy polityki
kosztu, sekcja G, PIT/ZG, symulacja „co jeśli sprzedam teraz", eksport CSV/XLSX/PDF).

## Instalacja

Dodaj repozytorium `https://github.com/miczu71/nokia_tracker` jako źródło add-onów w Home Assistant
Supervisor (Ustawienia → Dodatki → Sklep z dodatkami → ⋮ → Repozytoria), zainstaluj i uruchom dodatek.
Wymaga działającego brokera MQTT (`core-mosquitto` domyślnie).

## Web UI

Dodatek wystawia własny interfejs na ingressie (panel „Nokia Tracker” w bocznym menu HA) — to
**główny sposób interakcji** z dodatkiem, dashboard Lovelace nie jest wymagany. Nawigacja
*(od 0.5.0)* jest zwinięta do 5 pozycji: Pulpit / **Portfel** (Portfel, Loty, Sprzedaże, Granty) /
**Podatki** (Dywidendy, PIT-38) / **Dane** (Importy, Newsy, Prognozy) / Ustawienia — grupy
rozwijają się natywnie bez JS (`<details>`), z JS jako pływające menu.

| Strona | Zawartość |
|---|---|
| **Pulpit** | Kurs (EUR, **od 0.5.0** z linią `≈ X zł` po kursie bieżącym), zmiana dzienna, sesja, trend, RSI, wykres cenowy z konfigurowalnym zakresem (1D/1W/1M/3M/6M/1R/3L/5L/MAX, wybór zapamiętany), karta portfela **(od 0.6.0 w trzech blokach: „W posiadaniu” — ilość/koszt/wartość/P&L/zwrot jak dotąd, plus linia wolne/z ograniczeniem gdy część akcji czeka na własne dopasowanie ESPP; „Zablokowane” — nienabyte dopasowania ESPP i transze LTI z szacunkową wartością i najbliższą datą dostępności, ostrzeżenie o zaległych; „Razem” — suma posiadanych i nadchodzących)**, każda kwota EUR z linią PLN i jawnym rozgraniczeniem od kursu NBP podatkowego, sentyment i briefing AI, rekomendacja AI, prognozy 1w/1m/12m, ostatnie alerty, przycisk „Przeanalizuj teraz” |
| **Portfel** | Stan posiadania — automatycznie z lotów, gdy istnieją (FIFO), z liniami PLN; formularz ręczny zwinięty do `<details>` jako fallback, gdy loty istnieją |
| **Loty** | Trzy polityki kosztu obok siebie z podstawą prawną, formularz dodania lotu i formularz rejestracji sprzedaży — oba *(od 0.5.0)* z podglądem na żywo pod polami (kurs NBP, koszt/przychód/podatek PLN, plan FIFO), zanim klikniesz przycisk; odrzuca daty przyszłe; tabela wszystkich lotów z kursem NBP zamrożonym per lot, link do rozliczenia sprzedaży |
| **Sprzedaże** | Karta „Podsumowanie" z KPI za wybrany rok (przychód/koszt/dochód/podatek/na rękę); rejestr transakcji — jeden wiersz na sprzedaż z kluczowymi kwotami i przyciskiem „Cofnij" *(od 0.5.0 widocznym od razu, nie tylko po rozwinięciu)*, klik rozwija pełne rozbicie FIFO (który lot, ile z niego wzięto, wyprowadzenie kursu NBP nabycia i sprzedaży z linkiem do tabeli, kwoty EUR/PLN) |
| **Granty** | Harmonogram ESPP (Matching Shares) i LTI (RS AWARD, transze pogrupowane per grant) z wyciągów Computershare, pasek kafelków *(od 0.5.0)* niezvestowane/następny vesting, status transz (oczekuje/nabyte/zaległe), **wartość dziś** (bieżąca cena/kurs) i **wartość zrealizowana** (cena i kurs NBP z dnia faktycznej sprzedaży, EUR i PLN) per transza |
| **Dywidendy** | Formularz dodania wypłaty *(od 0.5.0 z podglądem na żywo — kurs NBP, podatek, dopłata w PL — pod polami)*, jedno źródło prawdy z kursem NBP zamrożonym na Record Date, kafelki podsumowania **w PLN** z EUR jako podlinią *(od 0.5.0 — dawniej licznik EUR na kursach bieżących nie zgadzał się z tabelą poniżej)*, historia z kwotami EUR **i** PLN, numerem tabeli NBP i kolumną reinwestycji |
| **Importy** | Upload wyciągu Computershare (PDF), kolejka konfliktów (rozbieżności vs poprzedni import, w tym potwierdzenie realnej sprzedaży Withhold-to-Cover), historia importów |
| **PIT-38** | Karta „Do wpisania w deklarację" (poz. C + sekcja G + kafelek RAZEM DO ZAPŁATY) jako pierwszy ekran; niżej: 3 kafelki polityk kosztu (podstawa prawna w zwiniętym rozbiciu), sekcja G scalona z PIT/ZG (schowana, gdy brak dywidend w roku), symulacja „co jeśli sprzedam teraz" *(od 0.5.0 z wynikiem na żywo bez przeładowania strony)*, ślad obliczeń per lot pogrupowany po dacie sprzedaży, eksport CSV/XLSX (kwoty EUR + numery tabel) / widok do druku |
| **Newsy** | Lista zebranych newsów z ocenami AI (sentyment, wpływ, teza), kolumna źródła *(od 0.5.0)* |
| **Prognozy** | Kafelek trafności historycznej (MAPE) *(od 0.5.0)*, historia prognoz 1w/1m/12m vs zrealizowana cena (EUR) |
| **Ustawienia** | Łańcuch AI (primary/fallback, wybór modelu z listy pobranej z routera), progi alertów, usługa powiadomień, polityka kosztu nabycia, **stawki podatkowe** *(od 0.5.0 — podatek u źródła Finlandia, stawka traktatowa, Belka, domyślny rok podatkowy; dawniej tylko do odczytu)* |

## Odporność na niestabilne źródła

Newsy i AI ciągną z zewnętrznych usług, których dostępność nie jest gwarantowana. Od 0.1.2:

- **GDELT** (`providers/news_gdelt.py`): po wyczerpaniu ponowień na HTTP 429/502/503 źródło
  wchodzi w 6-godzinny cooldown (jeden zapis do cache HTTP w SQLite, przeżywa restart dodatku) —
  kolejne cykle `fetch_news` pomijają je bez sięgania do sieci, aż cooldown wygaśnie samoistnie.
  Znane błędy providera logują się jako `WARNING`, nie jako `ERROR` z tracebackiem.
- **Łańcuch AI** (`ai/provider.py`): każde ogniwo (`local`/`gemini`/`anthropic`) ma circuit breaker
  — po 3 kolejnych porażkach z rzędu jest pomijane przez 30 minut zamiast wywoływane (i ponawiane)
  w każdym cyklu ocen newsów. Po 30 minutach obwód sam się zamyka i ogniwo dostaje kolejną szansę.

## Silnik podatkowy PIT-38

> **To kalkulator pomocniczy, nie doradztwo podatkowe.** Wartości do PIT-38 potwierdź z własnym
> rozliczeniem lub doradcą. Add-on pokazuje **jak** policzył każdą liczbę (rozwijany ślad obliczeń
> per lot na stronie „PIT-38"), żeby dało się to zweryfikować, a nie przyjąć na wiarę.

Kursy walut przelicza się kursem średnim NBP **z ostatniego dnia roboczego poprzedzającego**
zdarzenie (art. 11a ustawy o PIT) — zamrożonym raz na zawsze w momencie zapisu, nigdy nie
przeliczanym ponownie. Loty konsumowane są metodą FIFO. Programy motywacyjne (ESPP, LTI) mają
opodatkowanie odroczone do zbycia (art. 24 ust. 11-12a) — stąd trzy równoległe polityki kosztu
uznanego przy sprzedaży, liczone naraz i pokazywane obok siebie:

| Polityka | Koszt uznany | Uzasadnienie |
|---|---|---|
| **`own_only`** (domyślna) | tylko akcje kupione za własne pieniądze | Za pozostałe nic nie zapłacono, opodatkowanie odroczono do zbycia — nie ma czego odliczyć |
| `own_plus_drip` | własne + reinwestowane dywidendy (DRIP) | DRIP kupuje się za pieniądze już opodatkowane jako dywidenda |
| `all_at_acquisition` | wszystkie loty w wartości z dnia nabycia | Dopuszczalne TYLKO jeśli wartość dokładki/LTI była wykazana jako przychód ze stosunku pracy (PIT-11) |

Sekcja G (dywidendy zagraniczne) liczy łańcuch: podatek pobrany u źródła w Finlandii (35% bez
uproszczonej procedury) → zaliczenie w Polsce ograniczone do stawki traktatowej (15 pp) → Belka
(19%) → dopłata w PL i kwota do odzyskania z fińskiego Vero — **w PLN, na kursie NBP zamrożonym na
dzień wypłaty dywidendy**, nie na kursie bieżącym.

Strona **PIT-38** dodaje symulację „co jeśli sprzedam teraz" (ta sama alokacja FIFO co realna
sprzedaż, żaden zapis do bazy) i eksporty: CSV, XLSX (arkusze: Podsumowanie / Ślad per lot /
Dywidendy) oraz widok do druku (PDF przez przeglądarkę).

### Jak zweryfikować kwotę z PIT-38 krok po kroku (0.4.0)

Żadna kwota w PLN nie jest czarną skrzynką — da się ją rozłożyć aż do numeru tabeli NBP:

1. Otwórz **Sprzedaże** (albo kartę „co jeśli sprzedam teraz" na **PIT-38**) i kliknij interesującą
   Cię sprzedaż w rejestrze, żeby rozwinąć jej detal.
2. Dla każdego skonsumowanego lotu widać: ile z niego wzięto (FIFO — najstarszy pierwszy), cenę
   nabycia i sprzedaży w EUR, oraz **wyprowadzenie obu kursów NBP** w formacie „dzień zdarzenia →
   dzień roboczy poprzedzający (art. 11a) → ostatnia opublikowana tabela → kurs", z linkiem do
   archiwum NBP i do surowego JSON-a API (`api.nbp.pl`) jako źródła zapasowego.
3. Kwoty EUR obok PLN są **pochodną** już zamrożonego PLN (`PLN ÷ kurs`), więc zawsze się zgadzają
   z tym, co zapisano w bazie w momencie zdarzenia — nie przelicza się niczego na nowo.
4. Na dole rozwinięcia: „ile finalnie dostaję" — przychód, koszt, dochód, podatek wg aktywnej
   polityki i kwota na rękę w PLN (oraz orientacyjnie w EUR, po kursie sprzedaży).
5. Eksport CSV/XLSX z **PIT-38** zawiera te same kolumny (EUR, numer tabeli NBP) — to ten sam
   dowód co ekran, nie jego uboższa wersja.

To narzędzie pomocnicze, nie doradztwo podatkowe — powyższe służy weryfikacji liczby, nie zastępuje
konsultacji z doradcą podatkowym.

## Encje MQTT Discovery

Wszystkie encje są pod urządzeniem **Nokia Tracker** i mają prefiks `sensor.nokia_tracker_*`
(potwierdzone na żywym Supervisorze — `object_id` w każdym payloadzie discovery gwarantuje ten
prefiks niezależnie od nazwy encji).

### Rynek

| Encja | Opis |
|---|---|
| `sensor.nokia_tracker_price_eur` | Kurs NOKIA.HE (EUR) |
| `sensor.nokia_tracker_price_pln` | Kurs w PLN (przelicznik po bieżącym `eurpln_rate`) |
| `sensor.nokia_tracker_change_pct_day` | Zmiana dzienna (%) |
| `sensor.nokia_tracker_change_abs_day` | Zmiana dzienna (EUR) |
| `sensor.nokia_tracker_day_high` / `_day_low` | Maks./min. dnia |
| `sensor.nokia_tracker_prev_close` | Poprzednie zamknięcie |
| `sensor.nokia_tracker_volume` | Wolumen |
| `sensor.nokia_tracker_week52_high` / `_week52_low` | Maks./min. 52 tygodni |
| `sensor.nokia_tracker_market_state` | Stan sesji (opisowo) |
| `sensor.nokia_tracker_last_quote_ts` | Znacznik czasu ostatniego notowania |
| `sensor.nokia_tracker_adr_price_usd` | Kurs ADR (NYSE, proxy poza sesją helsińską) |
| `sensor.nokia_tracker_spread_vs_adr` | Rozbieżność kurs vs ADR |
| `binary_sensor.nokia_tracker_market_open` | Czy sesja w Helsinkach jest otwarta |

### Technika i benchmark

| Encja | Opis |
|---|---|
| `sensor.nokia_tracker_sma_20` / `_sma_50` | Średnie kroczące |
| `sensor.nokia_tracker_rsi_14` | RSI (14 okresów) |
| `sensor.nokia_tracker_volatility_30d_pct` | Zmienność 30-dniowa |
| `sensor.nokia_tracker_trend` | Opis trendu (silny wzrost…silny spadek) |
| `sensor.nokia_tracker_ericsson_price` | Kurs Ericsson (ERIC-B.ST) |
| `sensor.nokia_tracker_omxh25_value` | Wartość indeksu OMXH25 |
| `sensor.nokia_tracker_eurpln_rate` | Kurs EUR/PLN (bieżący, prezentacyjny) |
| `sensor.nokia_tracker_rel_perf_1d_vs_omxh25` | Względna siła 1D vs OMXH25 |
| `sensor.nokia_tracker_rel_perf_1m_vs_ericsson` | Względna siła 1M vs Ericsson |
| `sensor.nokia_tracker_beta_60d` | Beta 60-dniowa vs OMXH25 |
| `sensor.nokia_tracker_alpha_verdict` | Werdykt: specyficzne dla spółki / trend rynkowy / mieszane |

### AI — newsy, sentyment, prognozy, rekomendacja

| Encja | Opis |
|---|---|
| `sensor.nokia_tracker_sentiment_score` / `_sentiment_label` | Sentyment newsów 24h |
| `sensor.nokia_tracker_impact_score` | Średni wpływ newsów 24h |
| `sensor.nokia_tracker_news_count_24h` | Liczba newsów w 24h |
| `sensor.nokia_tracker_top_news` | Najważniejszy news (stan); pełna lista 5 najważniejszych z ocenami w atrybucie `items` |
| `sensor.nokia_tracker_daily_briefing` | Etykieta briefingu dziennego; pełny tekst (`text`), wersja TTS (`tts_text`), `key_risks`, `sentiment_avg`, `verdict`, `model`, `generated_at` w atrybutach |
| `sensor.nokia_tracker_ai_recommendation` | Rekomendacja (kup/akumuluj/trzymaj/redukuj/sprzedaj); `reason_pl`, `confidence`, `disclaimer` w atrybutach |
| `sensor.nokia_tracker_forecast_1w_eur` / `_1m_eur` / `_12m_eur` | Prognozy cenowe; `ci_low`, `ci_high`, `confidence`, `model`, `generated_at` w atrybutach |
| `sensor.nokia_tracker_forecast_accuracy_pct` | Trafność ostatnich rozliczonych prognoz (100 − MAPE) |
| `sensor.nokia_tracker_ai_provider_active` | Aktywny provider AI w łańcuchu (local/gemini/anthropic/off) |
| `sensor.nokia_tracker_ai_calls_today` | Liczba wywołań AI dzisiaj (licznik dzienny) |

### Portfel

| Encja | Opis |
|---|---|
| `sensor.nokia_tracker_position_qty` | Ilość posiadanych akcji |
| `sensor.nokia_tracker_avg_cost_eur` | Średni koszt zakupu (EUR/akcję) |
| `sensor.nokia_tracker_cost_basis_eur` / `_cost_basis_pln` | Koszt bazowy pozycji |
| `sensor.nokia_tracker_market_value_eur` / `_market_value_pln` | Wartość rynkowa pozycji |
| `sensor.nokia_tracker_unrealized_pnl_eur` / `_pln` | Niezrealizowany zysk/strata |
| `sensor.nokia_tracker_unrealized_pnl_pct` | Niezrealizowany zysk/strata (%) |
| `sensor.nokia_tracker_total_return_pct` | Całkowity zwrot (z dywidendami) |

### Dywidendy i podatki (kalkulator orientacyjny — patrz klauzula niżej)

| Encja | Opis |
|---|---|
| `sensor.nokia_tracker_dividends_gross_eur` | Suma dywidend brutto |
| `sensor.nokia_tracker_dividends_net_eur` | Suma netto (po podatku u źródła w Finlandii) |
| `sensor.nokia_tracker_withholding_paid_eur` | Podatek pobrany u źródła |
| `sensor.nokia_tracker_pl_tax_due_eur` | Dopłata w Polsce (Belka 19% − zaliczenie do stawki traktatowej) |
| `sensor.nokia_tracker_reclaimable_from_finland_eur` | Kwota do odzyskania z fińskiego Vero (nadpłacone ponad stawkę traktatową) |
| `sensor.nokia_tracker_dividend_yield_on_cost_pct` | Stopa dywidendy na koszcie |

> **Klauzula:** te encje liczą na **bieżących** ustawieniach procentowych (stawka traktatowa, Belka)
> i bieżącym kursie EUR/PLN — orientacyjny podgląd, nie pełne rozliczenie. Wersja liczona na kursie
> NBP zamrożonym na dzień wypłaty (zgodna z art. 11a) jest w grupie „PIT-38 i symulacja" niżej oraz
> na stronie web UI **PIT-38**. To narzędzie pomocnicze, nie doradztwo podatkowe; wartości potwierdź
> z własnym rozliczeniem lub doradcą przed wpisaniem do deklaracji.

### Loty i FIFO

| Encja | Opis |
|---|---|
| `sensor.nokia_tracker_lots_total_qty` | Suma otwartych lotów (wszystkie typy) |
| `sensor.nokia_tracker_lots_open_count` | Liczba otwartych lotów; podział per typ w atrybucie `by_type` |
| `sensor.nokia_tracker_lots_cost_basis_pln` | Koszt bazowy otwartych lotów wg aktywnej polityki kosztu |
| `sensor.nokia_tracker_realized_income_pln` | Zrealizowany dochód ze sprzedaży w bieżącym roku podatkowym |
| `sensor.nokia_tracker_realized_tax_pln` | Podatek od zrealizowanego dochodu (19%, wg aktywnej polityki) |

### Granty ESPP/LTI

| Encja | Opis |
|---|---|
| `sensor.nokia_tracker_unvested_qty` | Suma transz jeszcze nie uwolnionych (status `pending`) |
| `sensor.nokia_tracker_next_vest_date` | **Od 0.6.0:** najbliższa przyszła data DOSTĘPNOŚCI (`Available from` z wyciągu, nie data nabycia `Vesting Date` — realnie ~4 tygodnie później dla ESPP); ilość w atrybucie `next_vest_qty` |

### PIT-38 i symulacja

| Encja | Opis |
|---|---|
| `sensor.nokia_tracker_pit38_income_pln` | Dochód kapitałowy w roku podatkowym wg aktywnej polityki kosztu |
| `sensor.nokia_tracker_pit38_tax_pln` | Podatek 19% od dochodu kapitałowego (poz. C) |
| `sensor.nokia_tracker_pit38_dividend_due_pln` | Dopłata w PL od dywidend (sekcja G), na kursie NBP zamrożonym per wypłata |
| `sensor.nokia_tracker_pit38_reclaimable_pln` | Kwota do odzyskania z fińskiego Vero (sekcja G), w PLN |
| `sensor.nokia_tracker_whatif_sell_all_tax_pln` | Podatek, gdyby dziś sprzedać całą otwartą pozycję po cenie bieżącej — `unknown` bez otwartych lotów/ceny |

## Serwisy

Dodatek nie rejestruje własnych usług Home Assistant (`services.yaml`) — sterowanie odbywa się
przez web UI na ingressie (formularze portfela/dywidend, przycisk „Przeanalizuj teraz”) oraz przez
opcje konfiguracyjne Supervisora.

## Licencja

Do ustalenia.
