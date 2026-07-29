# Nokia Tracker 0.3.0 — pełna przejrzystość rozliczeń + konfigurowalny pulpit

## Context

`nokia_tracker` 0.2.0 policzy poprawny PIT-38, ale **nie pozwala zweryfikować, skąd wzięła się liczba**. Silnik FIFO (`tax/lots.py::_plan_fifo`) zna komplet szczegółów każdej alokacji, lecz UI wyrzuca je po drodze:

- `/pit38` „co jeśli sprzedam teraz" ma w `whatif_result.lots_consumed` gotowe rozbicie per lot — i **w ogóle go nie renderuje** (`templates/pit38.html:132-156` pokazuje tylko sumę per polityka).
- Ślad zrealizowanych sprzedaży (`pit38.py::_sale_trace`) to płaska tabela **tylko za jeden rok podatkowy**, bez grupowania po sprzedaży, bez kwot w EUR, bez numeru tabeli NBP.
- Kursy NBP są zamrożone prawidłowo (art. 11a), ale nigdzie nie widać **wyprowadzenia** „zdarzenie → D-1 → ostatnia opublikowana tabela" ani linku do konkretnej tabeli. `nbp_rates` nie zapisuje numeru tabeli, choć API go zwraca.
- Dywidendy mają **dwa źródła prawdy**: import PDF (`add_dividend` — kurs NBP zamrożony na dacie, lot DRIP tworzony) i ręczny formularz (`web.py:195-220` — surowy INSERT, bez kursu, bez DRIP, bez `natural_key`). Historia nie pokazuje waluty ani reinwestycji.
- Wykres na pulpicie to sztywne 90 ostatnich zamknięć bez osi X (`web.py:115`, `static/app.js:11`).
- `/grants` to czysty harmonogram — bez aktualnej wartości i bez wartości z dnia sprzedaży dla transz już skonsumowanych.

Cel 0.3.0: **każda kwota w PLN daje się rozłożyć na czynniki pierwsze aż do numeru tabeli NBP**, dywidendy mają jedno źródło prawdy, a pulpit i granty pokazują wartości, nie tylko daty.

## Decyzje podjęte

| Temat | Decyzja |
|---|---|
| Zakres 1D na wykresie | Dodajemy job intraday (pełne 1D) |
| Dywidendy | Ujednolicenie na `add_dividend` + PLN; **sensory MQTT zostają na EUR bez zmian** (żeby nie zerwać historii w HA) |
| Link NBP | Numer tabeli + link nbp.pl (dla człowieka) + link api.nbp.pl (zapasowy, zweryfikowany) |
| Podział wydań | Jedno wydanie **0.3.0** |

---

## 1. Warstwa danych — migracja v3 + numer tabeli NBP

**`nokia_tracker/db.py`** — dopisz migrację v3 do `_MIGRATIONS` (nie ruszaj v1/v2):

```sql
ALTER TABLE nbp_rates ADD COLUMN table_no TEXT;
ALTER TABLE dividends ADD COLUMN currency TEXT NOT NULL DEFAULT 'EUR';
```

**`nokia_tracker/providers/fx_nbp.py`**:
- `rate_on_or_before()` → zwraca **3-krotkę** `(rate, effective_date, table_no)`; `no` z odpowiedzi API (zweryfikowane empirycznie: `{"no":"142/A/NBP/2026","effectiveDate":"2026-07-24","mid":4.3257}`).
- `rate_for_event()` — ta sama zmiana sygnatury, logika D-1 bez zmian.
- Nowa `table_urls(table_no, effective_date) -> dict`:
  - `nbp` = `https://nbp.pl/archiwum-kursow/tabela-nr-{slug}-z-dnia-{effective_date}/` gdzie `slug` = `table_no.lower().replace("/", "-")`
  - `api` = `https://api.nbp.pl/api/exchangerates/rates/a/eur/{effective_date}/?format=json` ← **zweryfikowane, zwraca dokładnie ten kurs**
  - Uwaga: strony `nbp.pl` nie dało się zweryfikować z tego środowiska (1 KB odpowiedzi — render JS albo blokada bota), dlatego link API jest podawany zawsze obok.
- Nowa `backfill_table_numbers(conn) -> int` — dla wierszy `nbp_rates` z `table_no IS NULL` dociąga numer po `effective_date`. **Nigdy nie dotyka `rate`** — kurs pozostaje zamrożony, dopisujemy wyłącznie metadaną.

**Wywołujący do zaktualizowania** (rozpakowanie 3-krotki): `tax/lots.py::add_lot`, `tax/lots.py::backfill_missing_rates`, `tax/lots.py::record_sale`, `tax/dividends.py::add_dividend`, `tax/whatif.py::simulate_sale`.

**`nokia_tracker/main.py`** — dopisz `backfill_table_numbers` do istniejącego joba `backfill_nbp_rates` (cron 6:15, main.py:374).

---

## 2. Pełne rozbicie FIFO — nowy moduł `tax/trace.py`

Jeden moduł, z którego korzystają **wszystkie trzy** widoki (symulacja, zrealizowane sprzedaże, eksporty) — żeby symulacja i rzeczywistość nie mogły się rozjechać, dokładnie jak `_plan_fifo` jest współdzielone przez `record_sale` i `simulate_sale` dzisiaj.

```python
def fx_derivation(conn, event_date, kind) -> dict
```
Zwraca komplet wyprowadzenia kursu do wyświetlenia:
`event_date` + dzień tygodnia PL → `d_minus_1` + dzień tygodnia → `effective_date` (faktyczna tabela) + dzień tygodnia → `table_no` → `rate` → `urls` → `explanation_pl` (gotowe zdanie, np. *„sprzedaż 27.10.2025 (pon) → dzień roboczy poprzedzający: 26.10.2025 (nd, brak tabeli) → ostatnia opublikowana: 24.10.2025 (pt), tabela 142/A/NBP/2025, kurs 4,3257"*).
Dane bierze z już zamrożonych kolumn (`nbp_rate`, `nbp_rate_date`, `table_no`) — **zero nowych zapytań do NBP przy renderowaniu strony**.

```python
def enrich_allocations(conn, allocations, sale_ctx, cfg) -> dict
```
Dla każdej alokacji z `_plan_fifo` (lub z `sale_allocations`) dokłada:

| Pole | Wyliczenie |
|---|---|
| `lot_price_eur`, `lot_fee_eur`, `lot_quantity` | z `lots` |
| `qty_taken` | z alokacji |
| `cost_eur` | `cost_pln / lot_nbp_rate` — pochodna zamrożonego PLN, gwarantuje spójność z pokazaną kwotą PLN |
| `cost_pln` | zamrożone, bez zmian |
| `lot_fx` | `fx_derivation(acquired_date, "nabycie")` |
| `sale_price_eur`, `fee_share_eur` | z `sales` / kontekstu symulacji |
| `revenue_eur` | `qty_taken * sale_price_eur - fee_share_eur` |
| `revenue_pln` | zamrożone, bez zmian |
| `sale_fx` | `fx_derivation(sale_date, "sprzedaż")` |
| `income_pln` | `revenue_pln - cost_pln` |
| `counted_in` | lista polityk, które uznają koszt tego lotu (z `taxpolicy.POLICIES`) |

Sumy zbiorcze: `revenue_eur/pln`, `cost_eur/pln`, `income_pln`, `tax_pln` per polityka oraz **„ile finalnie otrzymuję"**:
- `net_pln` = `revenue_pln - tax_pln` (wg aktywnej polityki)
- `net_eur` = `revenue_eur - tax_pln / sale_nbp_rate` — **oznaczone w UI jako prezentacyjne**: podatek płacisz w PLN, przeliczenie na EUR służy tylko porównaniu z wpływem brokerskim.

**`tax/whatif.py::simulate_sale`** — dodaj `lots_consumed_detailed = trace.enrich_allocations(...)` obok istniejącego `lots_consumed` (zachowaj stary klucz, korzystają z niego testy `test_tax_whatif.py`).

---

## 3. Symulacja PIT-38 — rozbicie na ekranie

**`templates/pit38.html`**, karta „Co jeśli sprzedam teraz" (obecnie linie 132-156):

Pod tabelą polityk dodaj **rozwijaną sekcję** `<details open>` „Z których lotów idzie sprzedaż" — jeden wiersz `<tr>` na lot + zagnieżdżony wiersz z wyprowadzeniem kursu:

```
Lot #12 · nabyty 2024-03-15 · typ: własne
  ├ bierzemy 40,0000 z 120,0000 dostępnych (FIFO — najstarszy pierwszy)
  ├ cena nabycia 3,4200 EUR/akcję + prowizja 0,90 EUR (udział: 0,30 EUR)
  ├ kurs nabycia:  nabycie 15.03.2024 (pt) → D-1 = 14.03.2024 (czw)
  │                → tabela 52/A/NBP/2024 [nbp.pl] [api] → 4,3011
  ├ koszt:         136,80 EUR  →  588,39 PLN
  ├ kurs sprzedaży: sprzedaż 29.07.2026 (śr) → D-1 = 28.07.2026 (wt)
  │                → tabela 144/A/NBP/2026 [nbp.pl] [api] → 4,3242
  ├ przychód:      178,40 EUR  →  771,44 PLN
  └ dochód:        183,05 PLN   (uznany w: własne / własne+DRIP / wszystkie)
```

Pod spodem **podsumowanie „ile finalnie dostaję"**: przychód EUR/PLN, koszt EUR/PLN, dochód PLN, podatek PLN wg aktywnej polityki, **na rękę PLN** i **na rękę EUR (prezentacyjnie)**.

Formatowanie: reużyj istniejących klas `.table`, `.table-wrap`, `.subcard`, `.muted`, `.num` z `static/app.css` — bez nowego systemu stylów.

---

## 4. Zrealizowane sprzedaże — nowa strona `/sales`

**`nokia_tracker/web.py`** — nowa trasa `GET /sales`:
- lista sprzedaży z `sales` (wszystkie lata, filtr `?year=` opcjonalny), najnowsze pierwsze;
- każda sprzedaż w `<details>` — nagłówek: data, ilość, cena EUR, przychód EUR/PLN, podatek wg aktywnej polityki;
- po rozwinięciu: **dokładnie ten sam blok rozbicia co w symulacji** (ten sam makro Jinja, wydzielone do `templates/_alloc_detail.html` i `{% include %}` z obu stron — jedno źródło formatowania).

**`templates/base.html`** — dodaj „Sprzedaże" do nawigacji między „Loty" a „Granty".
**`templates/lots.html`** — link „zobacz rozliczenie sprzedaży" pod formularzem sprzedaży.

**Cofanie sprzedaży** (usprawnienie porządkowe — patrz §8): `POST /sales/<id>/delete` z potwierdzeniem, w jednej transakcji przywraca `qty_remaining` lotów i kasuje `sale_allocations` + `sales`. Dziś literówka w formularzu sprzedaży trwale konsumuje loty i nie ma jak tego odkręcić inaczej niż edycją SQLite.

---

## 5. Dywidendy — jedno źródło prawdy, waluta, reinwestycja

**`nokia_tracker/tax/dividends.py`**:
- `add_dividend()` — DRIP staje się **opcjonalny**: `purchased_shares`/`purchase_price_eur`/`purchase_date` z domyślnymi `None`; przy braku wartości nie tworzy lotu, `reinvested_lot_id` zostaje `NULL`. Zachowaj idempotencję po `natural_key`.
- Nowa `backfill_missing_dividend_rates(conn) -> int` — lustrzana do `lots.py::backfill_missing_rates`: uzupełnia `nbp_rate`/`nbp_rate_date`/`gross_pln` tam, gdzie są `NULL` (czyli dla dywidend wpisanych dotąd ręcznie). **Nigdy nie nadpisuje już zamrożonego kursu.**

**`nokia_tracker/web.py`**:
- `POST /dividends` przechodzi przez `taxdiv.add_dividend()` zamiast surowego INSERT-a.
- `GET /dividends` — `backfill_missing_dividend_rates()` + `compute_dividend_tax_pln()` jako źródło kwot (tak jak `/pit38`), zamiast `taxm.compute_dividend_tax()`.

**`templates/dividends.html`**:
- Formularz: dodaj sekcję „Reinwestycja (DRIP)" — data zakupu, cena EUR/akcję, liczba kupionych akcji (wszystkie opcjonalne, z podpowiedzią „zostaw puste, jeśli dywidenda wypłacona gotówką").
- Historia — nowe kolumny: **waluta** (`EUR`), brutto EUR **i** brutto PLN, kurs NBP + numer tabeli z linkami, u źródła %, dopłata PL (PLN), do odzysku z Vero (PLN), **reinwestycja**: `12,3456 akcji @ 3,42 EUR (2024-08-01)` z linkiem do lotu, albo „gotówka".
- Usuń nieaktualny disclaimer („to dochodzi w wydaniu 0.2.0" — linie 10-16); zastąp tym samym tekstem co na `/lots` i `/pit38`.

**Sensory MQTT (`sensors.py::dividends_values`) zostają bez zmian** — nadal liczą w EUR na bieżących stawkach. Świadomie: zmiana zerwałaby historię encji `dividends_*` w HA. Rozjazd „UI w PLN zamrożonym / sensory w EUR bieżącym" udokumentuj w README.

**⚠ Do decyzji użytkownika (nie ruszam bez zgody):** `add_dividend()` zapisuje **Record Date** do kolumny `pay_date` (docstring to przyznaje) i na tej dacie zamraża kurs. Art. 11 ustawy o PIT wiąże przychód z **datą postawienia do dyspozycji** (data wypłaty), nie z Record Date — dla Nokii dzieli je zwykle kilka dni, co potrafi zmienić i kurs, i rok podatkowy przy dywidendzie na przełomie roku. Poprawka wymaga rozdzielenia kolumn `record_date`/`pay_date` i **przeliczenia już zamrożonych kursów**, więc zgłaszam to jako osobną decyzję, a nie cichą zmianę.

---

## 6. Pulpit — konfigurowalny zakres wykresu + dane intraday

**`nokia_tracker/quotes.py`** — nowa `closes_in_range(conn, instrument_id, granularity, since) -> list[tuple[str, float]]` zwracająca `(ts, close)`, nie same liczby (potrzebna oś czasu).
Nowa `prune_intraday(conn, keep_days=60) -> int` — retencja świec 5-minutowych.

**`nokia_tracker/main.py`**:
- Nowy job `refresh_intraday_job` co `poll_interval_minutes` (obok istniejącego `publish_sensors`, main.py:369). Yahoo obsługuje intraday (`providers/yahoo.py:65-66`: `interval=5m, range=1d`), więc historia śróddzienna narasta dzień po dniu bez backfillu.
- Nowy job `prune_intraday` — cron dziennie ~3:00.

**`nokia_tracker/web.py`** — nowy endpoint `GET /api/chart?range=<r>`:
`1d` → intraday; `1w|1m|3m|6m|1y|3y|5y|max` → daily. Zwraca `{"granularity": ..., "points": [[ts, close], ...]}`. Nagłówek `no-store` łapie się automatycznie (`_no_cache` obsługuje `application/json`).
`GET /` przestaje wstrzykiwać `chart_closes_json` z `[-90:]` — podaje tylko domyślny zakres.

**`static/app.js`** — `initPriceChart` przyjmuje id kanwy + domyślny zakres:
- pasek przycisków zakresów nad wykresem, aktywny podświetlony;
- kliknięcie → `fetch` na `/api/chart` → `chart.data` podmienione + `chart.update()` (bez rekonstrukcji obiektu Chart);
- oś X **włączona**, z formatowaniem zależnym od zakresu (godzina dla 1d, dzień dla ≤3m, miesiąc/rok wyżej);
- tooltip z pełną datą i kursem;
- wybór zapamiętany w `localStorage` (`nt.chart.range`).

**`templates/dashboard.html`** — kontener przycisków + `NT.initPriceChart("price-chart", {url: "{{ url_for('chart_api') }}", range: "3m"})`.

---

## 7. Granty — wartość aktualna i wartość z dnia sprzedaży

**`nokia_tracker/tax/grants.py`** — nowa `valuation(conn, current_price_eur, current_eurpln) -> dict[int, dict]` (klucz: `vest_id`):

Dla transzy z przypisanym `lot_id` (te rozwiązane przez `reconcile_vesting`):
- **część otwarta** (`lots.qty_remaining`): `qty × current_price_eur` → EUR, `× current_eurpln` → PLN, oznaczone „wycena bieżąca";
- **część skonsumowana**: `SELECT` po `sale_allocations` dla tego `lot_id` z `JOIN sales` → per sprzedaż: data, ilość, cena EUR z dnia sprzedaży, **zamrożony kurs NBP tej sprzedaży**, wartość EUR i PLN. To odpowiada wprost na „dla tych, które zostały pokryte przez sprzedaż, chcę wartość z dnia sprzedaży" — i jest to wartość *faktycznie zrealizowana*, nie szacunek.

Dla transz bez `lot_id` (`pending` / nierozwiązane przez reconcile): wartość szacunkowa `quantity × current_price_eur` **wyraźnie oznaczona jako prognoza**, nie realizacja.

**`nokia_tracker/web.py::grants_get`** — dociągnij bieżącą cenę i kurs EUR/PLN dokładnie tak jak robi to `portfolio_get` (web.py:149-153: `quotes.latest_quote` dla `ids["primary"]` i `ids["eurpln"]`).

**`templates/grants.html`**:
- ESPP i LTI: nowe kolumny „Wartość dziś (EUR / PLN)" i „Zrealizowano (EUR / PLN)";
- transze pokryte sprzedażą → `<details>` z listą sprzedaży, każda z kursem NBP i linkiem do tabeli (ten sam makro `_alloc_detail.html`);
- podsumowanie per grant LTI: suma wartości bieżącej + suma zrealizowanej;
- **usuń nieaktualny disclaimer** (linie 11-15: „Nie tworzy lotów; auto-tworzenie lotów… to osobny krok (scheduler)" — krok 14 to już zrobił przez `reconcile_vesting`).

---

## 8. Usprawnienia porządkowe (propozycja własna)

| # | Rzecz | Dlaczego |
|---|---|---|
| 8.1 | **Cofanie sprzedaży** — `POST /sales/<id>/delete` (§4) | Dziś literówka trwale konsumuje loty; jedyny ratunek to ręczna edycja SQLite |
| 8.2 | **Walidacja dat w przyszłość** w formularzach lotu / sprzedaży / dywidendy | `rate_for_event` na przyszłej dacie → NBP zwraca 400 → `QuoteProviderError` → 500 zamiast komunikatu |
| 8.3 | **Selektor roku podatkowego z listy lat mających dane** zamiast `<input type=number>` (`pit38.html:12-17`) | Dziś łatwo wpisać rok bez żadnych zdarzeń i patrzeć na zera |
| 8.4 | **Eksporty CSV/XLSX rozszerzone** o kolumny EUR, `table_no`, wyprowadzenie kursu + nowy arkusz „Sprzedaże" z pełnym rozbiciem | Eksport ma być tym samym dowodem co ekran, nie uboższą wersją |
| 8.5 | **Aktualizacja nieaktualnych disclaimerów** na `/dividends` i `/grants` | Obie strony opisują stan sprzed kroków 14-15 |
| 8.6 | **README + CHANGELOG**: tabele encji/tras, sekcja „Jak zweryfikować kwotę z PIT-38 krok po kroku" | Zgodnie z checklistą wydań — release notes z tabelami encji |
| 8.7 | **⚠ Token GitHub jawnie w `.git/config`** repozytorium add-onu (`origin` zawiera `ghp_…`) | Wycieka przy każdym `git remote -v` i w każdym backupie `/config`. Proponuję zmienić remote na czyste HTTPS i pchać przez `gh` / `GITHUB_PERSONAL_ACCESS_TOKEN` z ENV; **token warto unieważnić w GitHubie** |

---

## 9. Testy

Repo ma 442 testy; wzorzec: `tests/conftest.py` daje fixture `conn` na `tmp_path` po `dbm.migrate`.

Nowe pliki / rozszerzenia:
- `tests/test_fx_nbp.py` — `table_no` z odpowiedzi API, `table_urls()`, `backfill_table_numbers()` **nie zmienia `rate`**.
- `tests/test_tax_trace.py` (nowy) — `fx_derivation()` dla dnia roboczego, poniedziałku (D-1 = niedziela → tabela z piątku) i po święcie; `enrich_allocations()`: `cost_eur × rate == cost_pln` (spójność), suma `revenue_pln` alokacji == `sales.revenue_pln`, `counted_in` zgodne z `taxpolicy.POLICIES`.
- `tests/test_db.py` — migracja v3 idempotentna, `user_version == 3`.
- `tests/test_tax_dividends_pln.py` — `add_dividend` bez DRIP nie tworzy lotu; `backfill_missing_dividend_rates` uzupełnia tylko `NULL`-e.
- `tests/test_quotes.py` — `closes_in_range` (granice zakresu), `prune_intraday` nie rusza świec dziennych.
- `tests/test_web.py` — `/sales` (200, rozbicie w treści), `/api/chart` dla każdego zakresu, `POST /sales/<id>/delete` przywraca `qty_remaining`, `/grants` z wyceną.
- `tests/test_tax_grants.py` — `valuation()`: lot otwarty, lot częściowo sprzedany, lot w całości sprzedany, transza bez `lot_id`.

---

## 10. Kolejność wykonania

1. **Skopiuj ten plan** do `/config/addons/nokia_tracker/docs/PLAN_KROK_16_transparentnosc.md` (zasada: plan trafia do repo przed pierwszą linią kodu).
2. Migracja v3 + `fx_nbp` (`table_no`, `table_urls`, backfill) + aktualizacja wywołujących → testy.
3. `tax/trace.py` + `whatif` → testy.
4. `templates/_alloc_detail.html` + rozbicie na `/pit38`.
5. `/sales` + cofanie sprzedaży + nawigacja.
6. Dywidendy (moduł → web → szablon) + backfill kursów.
7. `/grants` — `valuation()` + szablon.
8. Wykres: `closes_in_range`, `/api/chart`, joby intraday + retencja, `app.js`, `dashboard.html`.
9. Usprawnienia 8.2-8.6, README + CHANGELOG.
10. Bump `nokia_tracker/config.yaml` **i** `nokia_tracker/nokia_tracker/__init__.py` na `0.3.0`.

---

## Weryfikacja

**Testy jednostkowe** (musi przejść komplet, nie tylko nowe):
```bash
cd /config/addons/nokia_tracker/nokia_tracker && python -m pytest -q
```

**Weryfikacja empiryczna kursu NBP** — na realnym locie z bazy sprawdź, że wyświetlone wyprowadzenie zgadza się ze źródłem:
```bash
curl -s "https://api.nbp.pl/api/exchangerates/rates/a/eur/<effective_date>/?format=json"
# `no` i `mid` muszą zgadzać się co do znaku z tym, co pokazuje /pit38
```

**Wydanie** (zgodnie z checklistą add-onów, bez lokalnego rebuildu):
1. push do `miczu71/nokia_tracker`, **published** release `0.3.0` (nie draft), body z tabelami encji/tras;
2. `ha_manage_addon` → `update` na slugu z hash-prefiksem → weryfikacja wersji przez `/info`;
3. **NIE** cykl uninstall→remove_repository→add_repository→install — kasuje SQLite z realnymi danymi z importu PDF.

**Weryfikacja UI (Playwright, self-review przed pokazaniem)** — dla każdego ekranu screenshot **i** `browser_console_messages(error)`, zrzuty do `/config/playwright/`:
- `/pit38` — symulacja z realną ilością: rozbicie per lot widoczne, linki do tabel NBP klikalne, „na rękę PLN/EUR" się zgadza;
- `/sales` — rozwinięcie sprzedaży pokazuje te same liczby co eksport CSV;
- `/dividends` — waluta, kurs NBP, kolumna reinwestycji;
- `/grants` — wartość bieżąca i wartość z dnia sprzedaży;
- `/` — przełączanie 1d/1w/1m/3m/6m/1y/max przerysowuje wykres, oś X ma daty, wybór przeżywa odświeżenie strony;
- **na telefonie**: badge wersji w nav pokazuje `0.3.0` (potwierdzenie, że WebView nie serwuje cache'u).
