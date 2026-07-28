# Changelog

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
