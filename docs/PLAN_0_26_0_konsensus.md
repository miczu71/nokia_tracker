# nokia_tracker 0.26.0 — konsensus analityków obok prognozy AI

## Context

Dziś aplikacja pokazuje na `/rynek` **wyłącznie prognozy AI** (karta „Prognozy", 1w/1m/12m
z przedziałem ufności, `templates/market.html:104-131`). Nie ma żadnego niezależnego punktu
odniesienia — ani ceny docelowej analityków, ani konsensusu rekomendacji. Grep po
`price_target|cena docelowa|analityk|analyst|konsensus|consensus|upside|fair value` przez
cały kod, szablony i migracje: **zero trafień** poza AI-owym słowem `recommendation`
(werdykt `kup|akumuluj|trzymaj|redukuj|sprzedaj` w tabeli `briefings`, nie cena).

Efekt: prognoza AI jest nieosadzona. Nie wiadomo, czy model mówi coś odkrywczego, czy
powtarza rynkowy konsensus — a przy 12-miesięcznym horyzoncie to różnica między informacją
a szumem.

Funkcja jest już **zapisana w backlogu projektu**, świadomie odłożona:
`docs/ROADMAP.md:343` — *„Kalendarz wyników kwartalnych + konsensus analityków (Finnhub free)
obok prognozy AI, z backtestem »kto miał rację« — użytkownik wybrał czat zamiast tego;
**wraca, jeśli prognozy AI okażą się słabe**."* Ten dokument tę pozycję odmraża, z jedną
zmianą wobec pierwotnego zapisu: **źródłem nie jest Finnhub** (patrz niżej — endpoint
`/stock/price-target` jest płatny, a darmowy tier nie obejmuje Helsinek).

**Zamierzony efekt:** cena docelowa analityków jest w aplikacji równoprawnym obywatelem
prognozy AI — widoczna obok niej, przeliczona na realny portfel, podana AI jako wsad,
i rozliczana tą samą miarą trafności (MAPE), żeby po roku dało się odpowiedzieć na pytanie
„kto trafia lepiej".

---

## Ustalenia empiryczne (zweryfikowane na żywo 2026-08-24, przed napisaniem planu)

### Źródło danych — Yahoo `quoteSummary`

`GET https://query2.finance.yahoo.com/v10/finance/quoteSummary/NOKIA.HE?modules=financialData,recommendationTrend`

Realna odpowiedź, waluta **EUR** (`financialCurrency: "EUR"`), notowanie helsińskie:

| Pole | Wartość |
|---|---|
| `currentPrice` | 8,722 |
| `targetLowPrice` | 4,65 |
| `targetMeanPrice` | 10,32455 |
| `targetMedianPrice` | 10,125 |
| `targetHighPrice` | 18,00 |
| `numberOfAnalystOpinions` | 22 |
| `recommendationKey` | `hold` |
| `recommendationTrend` | 4 miesiące wstecz, `{strongBuy, buy, hold, sell, strongSell}` |

**Kontrola krzyżowa (niezależne źródło):** `stockanalysis.com/quote/hel/NOKIA/forecast/`
podaje 4,65 / 10,32 / 10,13 / 18,00 przy 23 analitykach i ratingu „Hold" — **zgodne co do
centa**. To nie jest fallback teoretyczny, tylko sprawdzony.

⚠️ **Uwaga na symbol:** ADR `NOK` zwraca zupełnie inne liczby (8,5 / 15,02 / 21,0 przy
9 analitykach) — to targety dla notowania nowojorskiego. Portfel użytkownika to akcje
helsińskie z Computershare, więc **jedynym poprawnym symbolem jest `NOKIA.HE`**. Test musi
to zabezpieczać jawnie.

### Ryzyko techniczne — handshake cookie+crumb

`quoteSummary` (v10) **wymaga** pary cookie+crumb, w odróżnieniu od `chart` (v8), którego
aplikacja używa dziś bez żadnej autoryzacji (`providers/yahoo.py:31`). Zweryfikowana
sekwencja (każdy krok sprawdzony osobno):

1. `GET https://fc.yahoo.com` → **HTTP 404, ale ustawia cookies A1/A3**.
   404 jest tu oczekiwany i **nie wolno go traktować jako awarii**.
2. `GET https://query2.finance.yahoo.com/v1/test/getcrumb` z tymi cookies → crumb (11 znaków).
   Bez cookie zwraca `{"error":{"code":"Unauthorized","description":"Invalid Cookie"}}`.
3. `GET quoteSummary?...&crumb=<crumb>` z tymi samymi cookies → HTTP 200.
   Ten sam crumb **bez** cookie → HTTP 401 `Invalid Crumb`.

Wniosek: cookie i crumb to nierozdzielna para, trzymana w `requests.Session()` w pamięci
procesu. Wygasa — obsługa 401 to **ponowny handshake i jedna powtórka**, nie zwykły retry
(`ratelimit.backoff_retry` z `retryable_statuses` tego nie załatwi, bo nie odświeży sesji).

---

## Decyzje (zatwierdzone przez użytkownika)

| Pytanie | Wybór |
|---|---|
| Zakres | **Wszystkie cztery**: karta na `/rynek`, przełożenie na portfel, wsad dla AI, rozliczanie MAPE |
| Źródło | **Yahoo (główne) + stockanalysis.com (fallback)** |

---

## Projekt

### 1. Provider — `providers/yahoo_analyst.py` (nowy)

Osobny moduł, **nie** rozszerzenie `providers/yahoo.py`: inny host (`query2` vs `query1`),
inny model autoryzacji (crumb vs brak), inny kontrakt zwrotny (`AnalystConsensus`, nie
`list[Candle]`). Klasa `QuoteProvider` (`providers/base.py:14`) tu nie pasuje i nie należy
jej naginać.

Reużywa istniejącą infrastrukturę bez zmian — to kanoniczny kształt z `providers/yahoo.py:100-114`:
- `cache.get/set(conn, url, ttl)` (`cache.py:9,24`) — TTL **6 h** (konsensus zmienia się
  rzadko; przeżywa restart kontenera, więc restart nie przepala limitu)
- `ratelimit.backoff_retry(fn, provider="yahoo_analyst")` (`ratelimit.py:110`) — retry 429/502
  + circuit breaker
- `timeout=15`, ten sam `_USER_AGENT`
- błąd → `QuoteProviderError` (jak Yahoo/Finnhub, bo API jest udokumentowane w kształcie)

Nowy dataclass w `models.py`: `AnalystConsensus(as_of, low, mean, median, high, n_analysts,
rating, currency, source, trend)`.

### 2. Fallback — `providers/stockanalysis.py` (nowy)

Parsowanie tabeli „Price Target" z `stockanalysis.com/quote/hel/NOKIA/forecast/`
(`beautifulsoup4==4.15.0` już jest w `requirements.txt`, nowa zależność zerowa).

**Bramkowany istniejącym, dziś nieużywanym ustawieniem `allow_scrape_fallback`.** To
ustawienie jest już rozprowadzone end-to-end (`config.yaml:29,97` → `run.sh:10` → `main.py:78`
→ `settings.py:16,76`), ale **nie ma ani jednego konsumenta** — jest zarezerwowane i puste.
Ta funkcja daje mu pierwszego konsumenta, dokładnie zgodnie z nazwą, bez dodawania nowego
ustawienia. Domyślnie `false` → użytkownik włącza świadomie.

Awaria obu źródeł → karta pokazuje **„brak danych"** z wiekiem ostatniego znanego odczytu,
nigdy starej liczby udającej świeżą (wzorzec `broker_cash` z E4).

### 3. Migracja v14 (`db.py`) — dwie zmiany

`SCHEMA_VERSION` = 13 dziś (`db.py:416`); dopisanie jednego stringa do `_MIGRATIONS`
(`db.py:412`) daje v14. Mechanizm: `PRAGMA user_version`, applier `db.py:434`.

**a) Nowa tabela `analyst_targets`** — dzienny snapshot, append-only, `UNIQUE(as_of_date, source)`
jako UPSERT:
```
id, as_of_date, fetched_at, low_eur, mean_eur, median_eur, high_eur,
n_analysts, rating, currency, source ('yahoo' | 'stockanalysis'),
trend_json (recommendationTrend jako JSON, NULL dla fallbacku)
```
To jedyne źródło prawdy dla karty, portfela i promptu AI. Historia snapshotów daje
„jak konsensus się przesuwał" bez dodatkowej pracy.

**b) `forecasts.source TEXT NOT NULL DEFAULT 'ai'`** + `UPDATE forecasts SET source='ai'`
dla istniejących wierszy.

**To jest wymóg poprawności, nie ozdobnik.** Bez tej kolumny wstawienie wiersza konsensusu
do `forecasts` psuje trzy istniejące zachowania:
- `sensors.py:210` bierze `ORDER BY created_at DESC LIMIT 1` per horyzont → wiersz konsensusu
  z `horizon='12m'` **przesłoniłby prognozę AI 12m** na `/rynek` i na sensorze MQTT
- `forecasts.py:45 accuracy_pct()` uśrednia po ostatnich 10 rozliczonych bez filtra →
  „Trafność historyczna" na `/rynek` po cichu zmieniłaby znaczenie na mieszankę AI+analitycy
- `analysis.py:48` podaje tę trafność do promptu AI → AI dostawałaby ocenę cudzych prognoz
  jako własną

### 4. Rozliczanie MAPE — reużycie, nie druga implementacja

Wiersz-lustro w `forecasts` z `source='consensus'`, `horizon='12m'`, `model='consensus:yahoo'`,
`predicted_price=mean`, `ci_low=low`, `ci_high=high`, `target_date = dziś + 365 dni`.

**Zapisywany tylko przy ZMIANIE konsensusu** wobec ostatniego wiersza-lustra (nie codziennie
— 365 wierszy/rok zalałoby `accuracy_pct` i zrobiłoby z rewizji analityków szum). Rewizja
targetu to sygnał; jej brak nie jest.

Dzięki temu `forecasts.py:23 settle_due()` (odpalane nocno, `main.py:573`) rozlicza konsensus
**bez żadnej zmiany** — ta sama funkcja, ten sam MAPE. Zero nowej matematyki, zgodnie z zasadą
projektu.

Zmiany w 5 miejscach czytających `forecasts` (kompletna lista, zgrepowana):

| Plik:linia | Zmiana |
|---|---|
| `sensors.py:210` | `+ AND source = 'ai'` |
| `alerts.py:70` | `+ AND source = 'ai'` (alert `price_breaks_forecast`) |
| `forecasts.py:45` | `accuracy_pct(conn, n=10, source='ai')` — nowy parametr, domyślnie `'ai'` |
| `analysis.py:48` | bez zmiany kodu (korzysta z nowego defaultu) |
| `web/routes_rynek.py:104` | **bez filtra** — pokazuje oba, o to chodzi; `forecasts.html` dostaje kolumnę „Źródło" |

### 5. Prezentacja

**a) Karta „Konsensus analityków" na `/rynek`**, bezpośrednio pod kartą „Prognozy"
(`templates/market.html:104-131`). Pasek zakresu niska–średnia–wysoka z zaznaczoną dzisiejszą
ceną, liczba analityków, rating, dystans do średniej w %, wiek danych i źródło.
Dane składa `views/market.py:14 market_view()` (nowy klucz `consensus`) — warstwa `views/`
pozostaje `url_for()`-free zgodnie z docstringiem `views/market.py:1-7`.

**b) Karta „Portfel w scenariuszach analityków" na `/` (Stan konta)**.
**Zero nowej matematyki:** `portfolio.position_values_auto(conn, cfg, price_eur, eurpln_rate)`
(`portfolio.py:47`) przyjmuje cenę jako parametr — wystarczy wywołać ją trzy razy dodatkowo
z `low`/`mean`/`high` zamiast dzisiejszej ceny. Ten sam kurs EUR/PLN co reszta strony
(`views/account.py:51`).
Opisane jawnie jako **scenariusz cenowy, nie prognoza** — spójnie z dyscypliną
disclaimerów w `market.html:98`.

**c) Wsad dla AI** — `analysis.py:75 _build_context()` dostaje `analyst_consensus`,
`ai/prompts.py:84 daily_analysis_prompt()` dokleja jedno zdanie kontekstu i **prosi model
o jawne odniesienie się do konsensusu** („moja prognoza 12m jest niżej/wyżej niż konsensus
analityków, bo…"). Schemat odpowiedzi (`ai/prompts.py:57 DAILY_ANALYSIS_SCHEMA`) **bez zmian**
— uzasadnienie mieści się w istniejącym `recommendation_reason_pl`. Rozszerzanie schematu
oznaczałoby migrację `briefings` i ryzyko na trzech providerach AI naraz; nie warto.

**d) Sensory MQTT** — `publisher.py:93-105` + `sensors.py`: `analyst_target_mean_eur`
(state) z atrybutami `low`/`high`/`median`/`n_analysts`/`rating`/`as_of`. Jeden sensor,
nie pięć.

### 6. Harmonogram

Nowy job w `main.py` — **raz dziennie**, o `analysis_time` minus 30 min (przed analizą AI,
żeby prompt dostał świeży konsensus tego samego dnia). Nie w `publish_sensors()`
(co 10 min = 144 zbędne strzały dziennie w dane, które zmieniają się raz na tygodnie).
Job trzyma `db.WRITE_LOCK` (`db.py:21`) jak pozostałe — **uwaga: lock jest niereentrantny**,
patrz ostrzeżenie `ai/copilot.py:24-27`.

---

## Pliki

**Nowe:** `providers/yahoo_analyst.py`, `providers/stockanalysis.py`, `analyst.py`
(orkiestracja: fetch → fallback → zapis snapshotu → warunkowy wiersz-lustro; wzorzec
`quotes.py` jako jedyny writer), `tests/test_yahoo_analyst.py`,
`tests/test_stockanalysis.py`, `tests/test_analyst.py`.

**Zmieniane:** `db.py` (migracja v14), `models.py` (`AnalystConsensus`), `forecasts.py`
(parametr `source`), `sensors.py` (filtr + nowe sensory), `alerts.py` (filtr),
`analysis.py` + `ai/prompts.py` (kontekst), `views/market.py`, `views/account.py`,
`templates/market.html`, `templates/account.html`, `templates/forecasts.html`
(kolumna „Źródło"), `publisher.py`, `main.py` (job), `backup.py` (`_CSV_TABLES` +=
`analyst_targets`), `README.md` + `CHANGELOG.md`, `docs/ROADMAP.md:343` (pozycja
z backlogu → zrealizowana).

**Nietykane:** cały `tax/` (beton — ta funkcja nie dotyka podatków ani FIFO).

---

## Testy (TDD, przed implementacją)

Punkt odniesienia: **1340 testów** na 0.25.0, wszystkie muszą zostać zielone.

- Fixture z **realnej** odpowiedzi Yahoo (jak `tests/fixtures/yahoo_chart_nokia_5d.json`) —
  zapisana z dzisiejszego, zweryfikowanego strzału
- Parsowanie: brakujące pola → `None`, nie wyjątek; `numberOfAnalystOpinions: 0` → „brak danych"
- **Handshake:** 401 → ponowny handshake + jedna powtórka; drugi 401 → `QuoteProviderError`
- **404 z `fc.yahoo.com` NIE jest awarią** (osobny test — to najbardziej kontrintuicyjny
  fragment całej sekwencji)
- Fallback: Yahoo pada → `allow_scrape_fallback=1` → stockanalysis; `=0` → brak danych, cisza
- **Symbol:** test broniący `NOKIA.HE` przed podmianą na `NOK` (inne liczby, inna waluta)
- Migracja v14: istniejące wiersze `forecasts` dostają `source='ai'`; **`accuracy_pct()` przed
  i po migracji zwraca tę samą liczbę** (kryterium twarde — inaczej zmieniliśmy istniejący
  wskaźnik po cichu)
- Wiersz-lustro powstaje **tylko** przy zmianie konsensusu; `settle_due()` rozlicza go tak
  samo jak AI-owy
- `sensors.forecast_values()` nie widzi wierszy konsensusu (regresja z §3b)
- Scenariusze portfela: `position_values_auto` z ceną `mean` daje dokładnie tę samą liczbę,
  co ręczne przeliczenie `qty × mean × kurs`

---

## Weryfikacja

1. `pytest` — 1340 + nowe, zielono; `test_tax_*.py` bez zmian (beton nietknięty,
   `git diff --stat` nie dotyka `tax/`)
2. `integrity.check_all()` — zero pęknięć po migracji v14
3. **Eksport ZIP przed migracją** (`GET /dane/eksport.zip`) — reguła roadmapy dla każdej migracji
4. Release wg `feedback_ha_addon_release`: bump `nokia_tracker/config.yaml` + `__init__.py`,
   **published** GitHub release, weryfikacja wersja == tag, update przez Supervisor z backupem
5. **Playwright na produkcji** (390 px i 1920 px, screenshot **i** konsola, do `/config/playwright/`):
   `/rynek`, `/`, `/forecasts`
6. **Test empiryczny na żywych danych:** liczby na karcie muszą zgadzać się co do centa
   z bezpośrednim strzałem `curl` do Yahoo wykonanym w tej samej chwili — wzorzec z E4/E7/E8
   („realne dane łapią błędy, których testy nie łapią")
7. Świadome wyłączenie Yahoo (zła nazwa hosta w cache) → sprawdzenie, że fallback wchodzi
   przy `allow_scrape_fallback=1`, a przy `=0` karta mówi „brak danych" zamiast pokazać
   nieaktualną liczbę

---

## Ryzyka

| Ryzyko | Mitygacja |
|---|---|
| Yahoo zmieni mechanizm crumb → funkcja milknie | Fallback stockanalysis (zweryfikowany, zgodny co do centa); awaria degraduje do „brak danych", nigdy do złej liczby |
| Wiersz konsensusu psuje istniejące liczby AI | `source` w migracji v14 + filtr w 4 miejscach + test „`accuracy_pct` przed == po" |
| Zły symbol (`NOK` zamiast `NOKIA.HE`) → liczby z innej giełdy i waluty | Dedykowany test; symbol brany z `instruments`, nie z literału w nowym module |
| Scraping stockanalysis łamie się przy redesignie | Domyślnie **wyłączony** (`allow_scrape_fallback=0`); parser milknie, nie rzuca |
| Użytkownik weźmie cenę docelową za obietnicę | Jawny disclaimer „scenariusz cenowy, nie prognoza" + widoczna liczba analityków i rozrzut 4,65–18,00, który sam w sobie mówi, jak bardzo analitycy się nie zgadzają |

---

## Wersja

**0.26.0** — pierwszy krok po roadmapie v3 (0.18.0–0.24.1, w całości wydana) i po E9 (0.25.0).
1.0.0 nadal zarezerwowane.
