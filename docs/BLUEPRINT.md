# Blueprint: HA Add-on `nokia_tracker` — asystent inwestycyjny NOKIA.HE

## Context

Cel: własny add-on HA, który śledzi kurs Nokii na Nasdaq Helsinki (NOKIA.HE), gromadzi lokalną historię, agreguje newsy, ocenia je LLM-em (sentyment + wpływ na kurs), generuje prognozy z weryfikacją ich trafności, porównuje ruchy Nokii z Ericssonem i OMXH25, prowadzi realny portfel (transakcje, P&L) i wystawia to wszystko do HA przez MQTT Discovery + własne UI na ingressie.

**Cel docelowy (wydanie 0.2.0): rozliczenie akcji z urzędem skarbowym.** Użytkownik jest pracownikiem Nokii i korzysta z firmowego planu zakupu akcji (deklarowana kwota miesięcznie/kwartalnie, po roku uwolnienie + **50% akcji dokładanych przez firmę**) oraz z **LTI** (Long Term Incentive, transze uwalniane po 1, 2 i 3 latach). Dochodzą akcje z reinwestowanej dywidendy. Wszystko to trzeba rozliczyć w PIT-38, z kursem NBP z ostatniego dnia roboczego poprzedzającego każde zdarzenie.

Podział na wydania (decyzja użytkownika):
- **0.1.0 — rynek + AI:** kurs, historia, newsy, sentyment, prognozy, rekomendacja, benchmark, alerty, prosty stan posiadania. Schemat bazy **od początku gotowy na loty i granty**, żeby 0.2.0 nie wymagało migracji danych.
- **0.2.0 — podatki:** loty, vesting, FIFO, kursy NBP, PIT-38, importery.

Decyzje podjęte przed planowaniem:
- **Portfel 0.1.0 — świadomie okrojony:** użytkownik wpisuje tylko **stan posiadania** (ilość akcji + średnia cena zakupu). Pełne lotowanie z FIFO → 0.2.0. **Schemat bazy jest już lotowy** (`lots`, `sales`, `grants`, `vests`), a formularz stanu posiadania zapisuje pod spodem jeden syntetyczny lot — dzięki temu 0.2.0 dokłada logikę, nie migrację.
- **Waluta:** konto brokerskie w EUR → **jedna warstwa P&L (EUR)**, PLN wyłącznie jako przelicznik prezentacyjny po kursie bieżącym. Efekt walutowy nie wchodzi do rachunku wyniku (brak historycznych kursów transakcji do trzymania).
- **Dywidendy: pełne rozliczenie podatkowe** (fiński podatek u źródła + polski Belka z zaliczeniem) — patrz sekcja podatkowa.
- **Koszt nabycia — domyślnie „tylko własne"** (wariant konserwatywny, zgodny z dzisiejszym arkuszem użytkownika): akcje podarowane (50% dokładki), LTI i te z dywidendy mają koszt **zero**. Polityka jest konfigurowalna, a UI pokazuje obok siebie skutek pozostałych wariantów wraz z podstawą prawną — add-on liczy, nie doradza.
- **LTI: pełny harmonogram vestingu** — tabela grantów z transzami, sensory `unvested_qty` / `next_vest_date`, automatyczne utworzenie lotu w dniu uwolnienia.
- **Import lotów — PDF-y Computershare jako źródło pierwotne.** Użytkownik dostarczył 5 realnych wyciągów „Plan Holdings Statement" (`/config/akcje_temp/`, administrator: Computershare Investor Services Ireland, plan „Share in Success Plan 2019-2026" + granty LTI „RS AWARD"), po jednym na każdy rok podatkowy 2022–2026 YTD. Rozpoznane empirycznie (2026-07-27, patrz sekcja 3a): format jest w pełni maszynowo generowany i tabelaryczny (`pypdf` `extraction_mode="layout"` daje kolumny gotowe do parsowania regexem — zero OCR). To czyni PDF-y **pierwotnym i najdokładniejszym** źródłem danych — dokładniejszym niż ręczny arkusz użytkownika, który był transkrypcją tych samych zdarzeń (stąd wcześniejsza niezgodność sum). Arkusz służy już tylko jako **drugorzędna weryfikacja krzyżowa**, nie jako źródło importu.
- **LLM:** lokalny **freellmapi** jako primary (OpenAI-compatible), **Gemini z osobnym kluczem** jako fallback, Anthropic opcjonalnie.
- **AI wystawia jawną rekomendację** (kup/akumuluj/trzymaj/redukuj/sprzedaj) z uzasadnieniem, pewnością i disclaimerem; prompt dostaje średnią cenę zakupu, więc rada jest kontekstowa.
- **UI:** pełne, na wzór `fuel_tracker` / `pv_roi_tracker`.
- **Tickery:** Nokia hardcoded jako primary + ERIC-B.ST / ^OMXH25 / EURPLN=X jako benchmarki.

Zasada procesowa (do zapisania trwale w kroku 0): **każdy plan zapisujemy jako plik `.md`**, żeby nie zginął przy kompaktowaniu kontekstu. Ten blueprint trafia dodatkowo do repo jako `docs/BLUEPRINT.md`, a sama zasada do pamięci (`feedback_plans_as_md.md`) i do `/config/CLAUDE.md`.

Fundament do reuse (nie piszemy od zera):
- `addons/fuel_tracker/fuel_tracker/fuel_tracker/publisher.py` — wzorzec MQTT discovery: retained configs, `availability_topic` + LWT, zaległy stan publikowany w `_on_connect`, `unpublish_device()` czyszczące retained configs, `discovery_payloads()` wydzielone do testów.
- `.../settings.py` — typed KV store w SQLite; opcje Supervisora tylko **seedują** brakujące klucze (`INSERT OR IGNORE`), baza ma pierwszeństwo po pierwszym starcie.
- `.../db.py` — migracje na `PRAGMA user_version`, lista `_MIGRATIONS`.
- `.../main.py` — APScheduler (`next_run_time=datetime.now()`), MQTT z usługi Supervisora (`ha_client.get_mqtt_service()`), nocny backup, Flask na ingressie.
- `.../ha_client.py` — Supervisor API (`http://supervisor/core/api`) dla stanów i `notify`.
- `.../main.py::auto_import_share()` — wzorzec folderu podrzucania: `<share>/import/` → przetworzone → `<share>/imported/` z prefiksem czasowym, `try/except` per plik. Ten sam mechanizm obsłuży wgrywanie wyciągów Computershare z folderu, nie tylko przez UI.
- `.../receipts.py` — integracja z Gemini: jawna lista modeli + fallback po wyczerpaniu quoty (`gemini-3.1-flash-lite` → `gemini-2.5-flash-lite`), structured output bez `additionalProperties`.
- `addons/pv_roi_tracker/pv_roi_tracker/requirements.txt` — **pinowanie exact** + waitress; vendorowane statyki (`static/vendor/chart.umd.min.js`).
- `addons/pv_roi_tracker/pv_roi_tracker/pv_roi_tracker/invoice_parser.py` + `invoice_layouts.py` + `invoice_store.py` (`pypdf`) — **trenowalne layouty PDF**, zarezerwowane dla ewentualnego przyszłego brokera innego niż Computershare (krok 16). Wyciągi Computershare **nie tego potrzebują** — są w 100% maszynowo generowane i tabelaryczne, `pypdf` z `extraction_mode="layout"` daje gotowe kolumny bez OCR i bez treningu (zweryfikowane empirycznie na 5 realnych plikach użytkownika, patrz sekcja 3a) — prostszy, deterministyczny parser per sekcja.
- `addons/pv_roi_tracker/pv_roi_tracker/pv_roi_tracker/month_close.py` + `deposit.py` — wzorzec zamykania okresu i self-heal rozjechanych rekordów; przyda się przy zamykaniu roku podatkowego.

---

## 1. Stack technologiczny i źródła danych

### Kontener
| Warstwa | Wybór | Uzasadnienie |
|---|---|---|
| Baza obrazu | `python:3.12-alpine` + `jq`, `tzdata` | identycznie jak oba istniejące add-ony |
| Web | `Flask==3.1.3` + `waitress==3.0.2` | waitress zamiast dev-servera (lekcja z pv_roi 0.32) |
| MQTT | `paho-mqtt==2.1.0` (`CallbackAPIVersion.VERSION2`) | pinowane exact; v1 API znika w paho 3.x |
| Harmonogram | `APScheduler==3.11.3` | |
| HTTP | `requests==2.34.2` | |
| Parsowanie | `beautifulsoup4==4.15.0` (fallback scrape), `feedparser==6.0.11` (RSS) | |
| Import (0.2.0) | `pypdf==6.14.2` (PDF-y brokera, ta sama wersja co pv_roi), `openpyxl==3.1.5` (eksport arkusza) | dochodzą dopiero w 0.2.0 — 0.1.0 nie nosi zbędnych zależności |
| Czas | `python-dateutil`, `zoneinfo` (stdlib) | sesje giełdowe w `Europe/Helsinki` |
| Baza | `sqlite3` (stdlib), migracje `PRAGMA user_version` | `/data/nokia_tracker.db` |
| Front | vendorowany `chart.umd.min.js` (kopia z pv_roi) | zero CDN — CSP/offline-first |

> **Odstępstwo od Twojego założenia — Pandas.** Nie wchodzi do obrazu. `pandas`/`numpy` nie mają wheeli musl na PyPI, więc build na `armv7`/`aarch64` kompilowałby numpy ze źródeł (dziesiątki minut, ryzyko OOM w Supervisorze) i puchnie obraz o ~120 MB. Wszystkie potrzebne statystyki (SMA, EMA, RSI-14, odchylenie standardowe, beta/alfa vs benchmark, regresja liniowa trendu) to ~120 linii czystego Pythona w `indicators.py` — na serii 5 lat dziennych świec (~1300 punktów) liczy się w milisekundach i jest w 100% testowalne. Jeśli kiedyś pojawi się realna potrzeba (backtesty na tickach), dopiszemy `pandas` jako opcjonalny extras w osobnym wydaniu.

### Ceny — warstwowo, z providerami wymiennymi
Research (2026-07): **Twelve Data trzyma XHEL na planach Pro/Venture — darmowy tier nie pokrywa Helsinek.** Alpha Vantage ma 25 req/dobę i niepewne pokrycie nordyckie. Stooq nie ma `nokia.he` (sprawdzone: HTTP 404). Yahoo Finance ma i `NOKIA.HE`, i `^OMXH25`.

| Priorytet | Provider | Symbole | Klucz | Uwagi |
|---|---|---|---|---|
| **1. Primary** | Yahoo Finance v8 chart<br>`query1.finance.yahoo.com/v8/finance/chart/NOKIA.HE` | `NOKIA.HE`, `ERIC-B.ST`, `^OMXH25`, `EURPLN=X`, `NOK` | brak | jedno API na wszystko; `interval=5m&range=1d` (intraday, ~15 min opóźnienia) i `interval=1d&range=5y` (backfill). Nieoficjalne → wymaga `User-Agent`, backoffu na 429 i cache |
| **2. Fallback live** | Finnhub `/quote?symbol=NOK` | ADR NYSE (1:1 do akcji z Helsinek) | free (opcjonalny) | darmowy tier daje **real-time** dla US. Proxy kursu helsińskiego = `NOK_usd / EURUSD`; po sesji w Helsinkach jedyne żywe źródło — stąd sensor `spread_vs_adr` |
| **3. Fallback EOD** | scrape `stockanalysis.com/quote/hel/NOKIA` (bs4) | primary | brak | tylko zamknięcia, za flagą `allow_scrape_fallback` |
| **4. Opcjonalnie płatne** | Twelve Data / EODHD | wszystkie | user key | pole w opcjach; README jawnie mówi, że XHEL wymaga planu płatnego |
| FX (prezentacja) | Yahoo `EURPLN=X` → fallback ECB `eurofxref-daily.xml` | | brak | ECB darmowe, bez klucza, dzienne |
| FX (podatki) | **NBP** `api.nbp.pl/api/exchangerates/rates/a/eur/{data}` | | brak | polskie prawo wymaga kursu **średniego NBP z dnia poprzedzającego** przychód — do przeliczania dywidend nie wolno użyć Yahoo/ECB |

Kontrakt providera: `providers/base.py::QuoteProvider.fetch(symbol, granularity, since) -> list[Candle]`. Zamiana źródła = jedna klasa, zero zmian w resztę systemu.

### Newsy — wszystkie darmowe, deduplikowane
| Źródło | Klucz | Rola |
|---|---|---|
| Oficjalne komunikaty Nokia IR (RSS) | brak | najwyższa waga `source_weight` — raporty, kontrakty, guidance |
| Google News RSS (`news.google.com/rss/search?q=Nokia+Oyj`) | brak | szeroki zasięg, wiele języków |
| GDELT DOC 2.0 API | brak | tonalność globalna, wolumen wzmianek |
| Finnhub `/company-news?symbol=NOK` | free key | newsy finansowe skorelowane z tickerem |
| Kauppalehti / Yle RSS | brak | lokalne fińskie doniesienia, często wyprzedzają anglojęzyczne |
| MarketAux | free key (100/dobę) | opcjonalne, ma gotowy sentyment do porównania z naszym |

Deduplikacja: kanonikalizacja URL (bez `utm_*`, bez fragmentu) + `sha256` znormalizowanego tytułu, `UNIQUE` w SQLite. Lista źródeł w bazie (`news_sources`), edytowalna w UI — dodanie RSS-a nie wymaga wydania nowej wersji.

### AI — łańcuch providerów (lokalny primary → Gemini fallback)
`ai/provider.py`: `analyze(task, prompt, schema, max_tokens) -> dict` + łańcuch z automatycznym przejściem do następnego ogniwa:

| # | Provider | Endpoint / model | Klucz |
|---|---|---|---|
| **1. Primary** | `ai/openai_compat.py` → **freellmapi** (lokalny, `192.168.0.106:3003/v1`) | `POST /v1/chat/completions`, model `gemini-3.5-flash` (1M ctx) | `local_llm_api_key` (osobne pole) |
| **2. Fallback** | `ai/gemini.py` → Google AI | `generativelanguage/v1beta`, `gemini-3.1-flash-lite` → `gemini-2.5-flash-lite` | `gemini_api_key` — **osobne pole, NIE klucz z `llmvision`** |
| **3. Opcjonalnie** | `ai/anthropic_api.py` | Messages API, `claude-haiku-4-5-20251001`, JSON przez tool-use | `anthropic_api_key` |

Żaden klucz nie trafia do repo — wszystkie są polami `password` w opcjach add-onu, więc żyją tylko w `/data/options.json` (poza gitem).

**Router wystawia cztery endpointy — sprawdzone, które są nam użyteczne:**

| Endpoint | Status | Decyzja |
|---|---|---|
| `/v1/chat/completions` | ✅ działa, `json_schema` respektowany | **główna droga** dla obu zadań |
| `/v1/messages` (Anthropic-compatible) | ✅ **działa** — zwrócił poprawny `msg_...` z `x-api-key` | `ai/anthropic_api.py` obsługuje **jednym klientem** i lokalny router, i prawdziwe Anthropic — wystarczy podmiana `base_url`. Oszczędza cały trzeci klient |
| `/v1/embeddings` | ❌ HTTP 503 `No enabled providers for embedding family 'gemini-embedding-001'` | **nie budujemy na tym** semantycznej deduplikacji newsów; zostaje hash tytułu + kanonikalizacja URL. Gdy włączysz rodzinę embeddingów w zakładce Embeddings, dojdzie jako opcjonalne ulepszenie (klastrowanie newsów o tym samym zdarzeniu) |
| `/v1/responses` | nie sprawdzany | pomijamy — `chat/completions` wystarcza, mniej powierzchni na regresje |

**Ustalone empirycznie na Twoim routerze (2026-07-27) — to nie założenia, to zmierzone zachowanie:**
1. `json_schema` **działa**: `gemini-3.5-flash` i `gpt-oss-120b` zwróciły poprawny, polski JSON zgodny ze `strict` schematem.
2. Modele `auto` i `fusion` **nie mają** `response_format` w `supported_parameters` → do wywołań ze schematem **przypinamy konkretny model**, nigdy routera. `ai/openai_compat.py` weryfikuje to przez `GET /v1/models` przy starcie i loguje ostrzeżenie, jeśli wybrany model nie wspiera `response_format`.
3. **`max_tokens` musi być ≥1500.** Przy 300 router zwrócił HTTP 502 `truncated JSON (finish_reason=length)` — tokeny reasoningu (226 u `gpt-oss-120b`) liczą się do budżetu przed treścią odpowiedzi.
4. **Awarie upstreamu to HTTP 502 `provider_error`, nie 429.** Retry musi traktować 502 jako błąd przejściowy (2 próby z backoffem na tym samym modelu → dopiero potem Gemini), inaczej add-on przepala fallback przy chwilowej zadyszce jednego upstreamu.
5. `usage.total_tokens` bywa **większe** niż `prompt + completion` (tokeny myślenia) — licznik w `ai_usage` czyta `total_tokens`, nie sumuje składników.
6. Dostępność modeli w routerze jest zmienna (`available: false` na ~połowie listy) → lista modeli w UI jest pobierana na żywo z `/v1/models`, a nie hardkodowana.

Dwa typy zadań (nie więcej — każde to koszt):
1. **`score_news`** — *batch* do 15 artykułów w JEDNYM wywołaniu → per artykuł: `sentiment` (−1..1), `impact` (0..3), `horizon` (`immediate|weeks|quarters`), `thesis_pl` (1 zdanie), `price_effect_pct_est`, `tags` (`5G|patenty|kontrakt|wyniki|zarząd|makro|konkurencja`).
2. **`daily_analysis`** — 1×/dobę po zamknięciu sesji; kontekst = statystyki serii + wskaźniki + względne zachowanie vs OMXH25/Ericsson + top newsy z ocenami + **Twoja ilość akcji i średnia cena zakupu** → `{forecast_1w, forecast_1m, forecast_12m}` (cena + `ci_low`/`ci_high` + `confidence`), `briefing_pl` (≤600 znaków, pod TTS), `key_risks[]`, `market_vs_company_verdict`, `recommendation` (`kup|akumuluj|trzymaj|redukuj|sprzedaj`) + `recommendation_reason_pl` + `recommendation_confidence`.

> **Rekomendacje z disclaimerem.** Prompt jawnie instruuje model, że to analiza edukacyjna, nie porada inwestycyjna, a UI i briefing TTS dopisują stałą klauzulę. Rekomendacja zawiera odniesienie do Twojej średniej ceny (np. „redukuj — kurs 18% nad Twoim kosztem, sentyment słabnie"), bo bez kontekstu pozycji „trzymaj" nic nie znaczy.

**Kontrola kosztów:** batchowanie, ocenianie wyłącznie nieocenionych artykułów, cache po hashu treści, twardy limit `ai_max_calls_per_day` w opcjach, licznik tokenów w tabeli `ai_usage` → sensor diagnostyczny. Przy Gemini flash-lite realny koszt ≈ 0 zł/dobę.

**Uczciwość prognoz (feature, nie ozdoba):** każda prognoza zapisuje `price_at_creation` i `target_date`. Gdy `target_date` minie, scheduler dopisuje `realized_price` i `error_pct` → sensor `forecast_accuracy_pct` (MAPE z ostatnich N rozliczonych prognoz) + tabela w UI. Bez tego prognozy LLM-a są nieweryfikowalną narracją.

### Caching i rate limiting
- `cache.py` — SQLite-backed HTTP cache (`url → body, etag, fetched_at`), TTL per endpoint. **Przetrwa restart kontenera**, więc restart nie przepala quoty.
- `ratelimit.py` — token bucket per provider, stan w tabeli `api_usage` (provider, dzień, licznik); przy 429 backoff wykładniczy z jitterem, po 3 porażkach circuit breaker → sensor `provider_status`.
- **Świadomość sesji giełdowej** — największa oszczędność quoty: Helsinki handlują 10:00–18:30 `Europe/Helsinki`. Wewnątrz sesji intraday co `poll_interval_minutes` (domyślnie 10), poza sesją tylko ADR/EOD raz na godzinę, w weekend i święta nic. `market.py::is_session_open(now)` + `binary_sensor.nokia_market_open`.

---

## 2. Architektura MQTT i encji (MQTT Discovery)

Jedno urządzenie HA, płaska przestrzeń topików (wzorzec z `publisher.py`):

```
nokia_tracker/availability                     -> "online" | "offline"   (retained, LWT)
nokia_tracker/sensors/<slug>/state             -> skalar lub JSON        (retained)
nokia_tracker/sensors/<slug>/attrs             -> JSON atrybutów         (retained)
nokia_tracker/events/alert                     -> JSON (NIE retained)
homeassistant/sensor/nokia_tracker/<slug>/config
homeassistant/binary_sensor/nokia_tracker/market_open/config
```

### Encje (~40, w 6 grupach)

**Rynek:** `price_eur`, `price_pln`, `change_pct_day`, `change_abs_day`, `day_high`, `day_low`, `prev_close`, `volume`, `week52_high`, `week52_low`, `market_state`, `last_quote_ts`, `adr_price_usd`, `spread_vs_adr`
**Technika:** `sma_20`, `sma_50`, `rsi_14`, `volatility_30d_pct`, `trend` (`silny wzrost|wzrost|bok|spadek|silny spadek`)
**Benchmark:** `ericsson_price`, `omxh25_value`, `rel_perf_1d_vs_omxh25`, `rel_perf_1m_vs_ericsson`, `beta_60d`, `alpha_verdict` (`specyficzne dla spółki` / `trend rynkowy` / `mieszane`)
**AI:** `sentiment_score`, `sentiment_label`, `impact_score`, `news_count_24h`, `top_news` (+attrs: lista 10 newsów z ocenami), `daily_briefing` (+attrs: pełny tekst, `tts_text`, `key_risks`), `forecast_1w_eur`, `forecast_1m_eur`, `forecast_12m_eur`, `forecast_confidence`, `forecast_accuracy_pct`, **`ai_recommendation`** (+attrs: `reason_pl`, `confidence`, `disclaimer`)
**Portfel:** `position_qty`, `avg_cost_eur`, `cost_basis_eur`, `market_value_eur`, `unrealized_pnl_eur`, `unrealized_pnl_pct`, `total_return_pct`
**Dywidendy i podatki:** `dividends_gross_eur`, `dividends_net_eur`, `withholding_paid_eur`, `pl_tax_due_eur`, `reclaimable_from_finland_eur`, `dividend_yield_on_cost_pct`, `next_dividend_date`
**Vesting i podatki (0.2.0):** `unvested_qty`, `next_vest_date`, `next_vest_qty`, `unvested_value_eur`, `vested_ytd_qty`, `tax_year_realized_gain_pln`, `tax_year_tax_due_pln`, `whatif_tax_if_sold_now_pln`, `cost_basis_pln_frozen` (suma zamrożonych kosztów wg polityki)
**Bliźniaki PLN:** `price_pln`, `market_value_pln`, `unrealized_pnl_pln`, `cost_basis_pln`, `dividends_net_pln`, `pl_tax_due_pln` (+ `eurpln_rate` jako osobny sensor kursu)
**Diagnostyka:** `ai_calls_today`, `ai_provider_active` (`freellmapi` / `gemini` / `anthropic` / `off`), `api_quota_left`, `provider_status`, `last_analysis_ts`
**Binary:** `market_open`

> **Przeliczanie na PLN.** Konto jest w EUR, więc EUR to waluta rachunku, a PLN wyłącznie prezentacja po **kursie bieżącym** (`eurpln_rate`). Każdy sensor `*_pln` liczy się w chwili publikacji — nie przechowujemy historycznych kursów transakcji, bo przy koncie walutowym efekt walutowy nie jest częścią wyniku inwestycji. Wyjątek świadomy: `pl_tax_due_pln` używa kursu z dnia poprzedzającego wypłatę dywidendy (tak wymaga polskie prawo podatkowe przy przeliczaniu przychodów zagranicznych) — dlatego ten jeden kurs **jest** zapisywany przy każdej dywidendzie.

### Dwie pułapki HA, które ta struktura obchodzi

1. **Limit 255 znaków na stan encji.** Briefing dzienny i lista newsów NIE mogą być stanem. `daily_briefing` publikuje stan-etykietę (`"2026-07-27 · neutralny"`), a treść leci w `json_attributes_topic`. Dashboard czyta `state_attr('sensor.nokia_daily_briefing','text')`; TTS czyta `state_attr(...,'tts_text')`.
2. **`device_class: monetary` nie współpracuje z `state_class: measurement`** (walidator statystyk HA to odrzuca — udokumentowane w komentarzach `fuel_tracker/publisher.py`). Dlatego: **kursy** = `unit_of_measurement: EUR` **bez** `device_class`, ze `state_class: measurement` (chcemy wykresy long-term). **Wartości portfela** = `device_class: monetary` + `state_class: total`.

### Konkretne payloady discovery

Kurs (`homeassistant/sensor/nokia_tracker/price_eur/config`):
```json
{
  "name": "Price EUR",
  "unique_id": "nokia_tracker_price_eur",
  "state_topic": "nokia_tracker/sensors/price_eur/state",
  "availability_topic": "nokia_tracker/availability",
  "unit_of_measurement": "EUR",
  "state_class": "measurement",
  "suggested_display_precision": 3,
  "icon": "mdi:chart-line",
  "device": {
    "identifiers": ["nokia_tracker"],
    "name": "Nokia Tracker",
    "manufacturer": "Custom",
    "model": "nokia_tracker",
    "sw_version": "0.1.0"
  }
}
```

Briefing z atrybutami (`.../daily_briefing/config`):
```json
{
  "name": "Daily Briefing",
  "unique_id": "nokia_tracker_daily_briefing",
  "state_topic": "nokia_tracker/sensors/daily_briefing/state",
  "json_attributes_topic": "nokia_tracker/sensors/daily_briefing/attrs",
  "availability_topic": "nokia_tracker/availability",
  "icon": "mdi:text-box-outline",
  "device": { "identifiers": ["nokia_tracker"] }
}
```
…a payload atrybutów:
```json
{
  "text": "Nokia zamknęła sesję na 9,15 EUR (+1,2%), wyprzedzając OMXH25 o 0,8 pp. Główny motor: doniesienia o kontrakcie 5G w Indiach (wpływ 2/3). Sentyment 24h +0,34 z 11 artykułów. Ryzyko: presja marżowa w Mobile Networks.",
  "tts_text": "Nokia wzrosła dziś o 1,2 procent do 9 euro 15 centów...",
  "sentiment_avg": 0.34,
  "news_count": 11,
  "verdict": "specyficzne dla spółki",
  "model": "gemini-3.1-flash-lite",
  "generated_at": "2026-07-27T18:40:00+03:00"
}
```

Lista newsów (`.../top_news/attrs`) — stan = tytuł najważniejszego (obcięty do 250 znaków), atrybuty = tablica do renderu w Markdown Card:
```json
{
  "items": [
    {"title": "Nokia wins 5G contract in India", "source": "Reuters",
     "url": "https://...", "published_at": "2026-07-27T09:12:00Z",
     "sentiment": 0.7, "impact": 2, "horizon": "weeks",
     "thesis_pl": "Kontrakt zwiększa backlog Mobile Networks w regionie APAC.",
     "tags": ["kontrakt", "5G"]}
  ]
}
```

Sensor prognozy z pełnym przedziałem (`forecast_1m_eur` — stan skalarny dla wykresu, przedział w atrybutach):
```json
{
  "name": "Forecast 1M",
  "unique_id": "nokia_tracker_forecast_1m_eur",
  "state_topic": "nokia_tracker/sensors/forecast_1m_eur/state",
  "json_attributes_topic": "nokia_tracker/sensors/forecast_1m_eur/attrs",
  "unit_of_measurement": "EUR",
  "state_class": "measurement",
  "icon": "mdi:crystal-ball",
  "availability_topic": "nokia_tracker/availability",
  "device": { "identifiers": ["nokia_tracker"] }
}
```

Alert (nie retained, do konsumpcji przez automatyzacje HA i przez `ha_client.notify`):
```json
{"kind": "sentiment_drop", "severity": "warning",
 "title": "Nagły spadek sentymentu Nokii",
 "message": "Sentyment 24h spadł z +0,31 do -0,42 (11 nowych artykułów). Główna przyczyna: obniżka rekomendacji.",
 "sentiment_before": 0.31, "sentiment_after": -0.42, "fired_at": "..."}
```

Alerty mają **histerezę i anty-spam**: próg + minimalny odstęp czasowy per rodzaj alertu, log w tabeli `alerts_log`. Rodzaje: `sentiment_drop`, `price_breaks_forecast` (kurs wychodzi z `ci_low`/`ci_high`), `price_move_pct` (skok dzienny > próg), `divergence` (Nokia mocno odstaje od OMXH25), `high_impact_news` (`impact == 3`), `portfolio_threshold` (P&L przebija zadany poziom).

---

## 3. Struktura plików add-onu

Repo `miczu71/nokia_tracker`, lokalny klon w `/config/addons/nokia_tracker/` (jak dwa pozostałe: `repository.json` w korzeniu, add-on w podkatalogu).

```
nokia_tracker/                        # repo root
├── repository.json                   # {"name":"Nokia Tracker","url":...,"maintainer":"miczu71"}
├── README.md                         # tabele encji i serwisów (wymóg release'owy)
├── CHANGELOG.md
├── .gitignore                        # __pycache__, .pytest_cache
└── nokia_tracker/                    # <- TEN config.yaml czyta Supervisor
    ├── config.yaml                   # wersja, opcje, schema, ingress 8100
    ├── Dockerfile                    # python:3.12-alpine + jq + tzdata
    ├── run.sh                        # options.json -> ENV (jq), exec python -m
    ├── requirements.txt              # pinowane exact
    ├── pytest.ini
    ├── tests/                        # pytest; fixtures z zapisanymi odpowiedziami API
    │   ├── conftest.py               # tymczasowa baza in-memory
    │   ├── fixtures/                 # yahoo_chart_nokia.json, gdelt.json, rss_*.xml, gemini_score.json
    │   ├── test_indicators.py        # SMA/RSI/beta na znanych seriach
    │   ├── test_portfolio.py         # stan posiadania, P&L, wartość w EUR/PLN
    │   ├── test_tax_lots.py          # FIFO, sprzedaż częściowa, akcje ułamkowe
    │   ├── test_tax_policy.py        # trzy polityki kosztu na tym samym zbiorze lotów
    │   ├── test_tax_dividends.py     # 35% vs 15%, zaliczenie 15 pp, 4 pp dopłaty, odzysk z Vero
    │   ├── test_vesting.py           # ESPP 50% match, transze LTI 1/2/3 lata, auto-lot
    │   ├── test_fx_nbp.py            # cofanie do dnia roboczego: 27.10.2025 -> tabela z 24.10.2025
    │   ├── test_computershare_pdf.py # parsowanie 5 realnych plików z akcje_temp/, idempotencja, suma vs "Assets by plan"
    │   └── test_pit38_regression.py  # MUSI odtworzyć realne rozliczenie użytkownika co do groszy
    │   ├── test_publisher.py         # discovery_payloads() + render_values()
    │   ├── test_providers.py         # parsowanie odpowiedzi, fallback, 429
    │   ├── test_ai.py                # walidacja schematu, fallback modeli
    │   ├── test_forecast_backtest.py
    │   └── test_alerts.py            # histereza, anty-spam
    └── nokia_tracker/                # pakiet Pythona
        ├── __init__.py               # __version__ (BUMP razem z config.yaml)
        ├── main.py                   # migracje, MQTT, scheduler, waitress
        ├── db.py                     # PRAGMA user_version + _MIGRATIONS
        ├── settings.py               # typed KV; opcje tylko seedują
        ├── models.py                 # Candle, NewsItem, Score, Forecast, Trade (NamedTuple/dataclass)
        ├── market.py                 # kalendarz sesji Helsinek, is_session_open()
        ├── indicators.py             # SMA/EMA/RSI/stdev/beta/alfa/trend — czysty Python
        ├── quotes.py                 # orkiestracja providerów -> tabela quotes, backfill
        ├── news.py                   # agregacja, kanonikalizacja URL, dedup
        ├── portfolio.py              # 0.1.0: stan posiadania + P&L; API gotowe na loty
        ├── tax/                      # 0.2.0 — rdzeń rozliczenia z US
        │   ├── lots.py               # FIFO, sale_allocations, qty_remaining
        │   ├── policy.py             # own_only / own_plus_drip / all_at_acquisition
        │   ├── dividends.py          # u źródła FI, zaliczenie PL do stawki traktatowej, odzysk z Vero
        │   ├── vesting.py            # ESPP + LTI: granty, transze, auto-lot w dniu uwolnienia
        │   ├── pit38.py              # raport roczny, sekcja G, PIT/ZG, ślad obliczeń per lot
        │   └── whatif.py             # "co jeśli sprzedam teraz" — podatek + które loty zje FIFO
        ├── importers/
        │   ├── computershare_pdf.py  # PRIMARY: pypdf layout mode, parser deterministyczny per sekcja
        │   ├── xlsx_sheet.py         # eksport arkusza użytkownika — weryfikacja krzyżowa, nie import
        │   └── broker_pdf.py         # generyczne trenowalne layouty — tylko gdy dojdzie inny broker
        ├── analysis.py               # składanie kontekstu dla AI + interpretacja odpowiedzi
        ├── forecasts.py              # zapis prognoz, rozliczanie, MAPE
        ├── alerts.py                 # progi, histereza, anty-spam
        ├── sensors.py                # jedno miejsce: wszystkie wartości sensorów
        ├── publisher.py              # MQTT discovery (port z fuel_tracker)
        ├── ha_client.py              # Supervisor API: stany, notify, mqtt service
        ├── cache.py                  # HTTP cache w SQLite
        ├── ratelimit.py              # token bucket + circuit breaker
        ├── backup.py                 # nocny dump bazy do /share
        ├── web.py                    # Flask API + strony (ingress)
        ├── providers/
        │   ├── base.py               # QuoteProvider / NewsProvider (kontrakty)
        │   ├── yahoo.py  finnhub.py  stockanalysis.py  twelvedata.py
        │   ├── fx_ecb.py  fx_nbp.py
        │   └── news_rss.py  news_gdelt.py  news_finnhub.py  news_marketaux.py
        ├── ai/
        │   ├── provider.py           # abstrakcja + wybór z opcji + fallback
        │   ├── gemini.py  anthropic_api.py  openai_compat.py
        │   ├── prompts.py            # prompty PL + JSON schema (score_news, daily_analysis)
        │   └── usage.py              # licznik wywołań/tokenów, dzienny limit
        ├── static/                   # app.css, app.js, vendor/chart.umd.min.js — ?v=<wersja>
        └── templates/                # base.html + dashboard/news/forecasts/portfolio/settings
```

### Opcje w panelu UI dodatku (`config.yaml`)

```yaml
name: Nokia Tracker
version: "0.1.0"
slug: nokia_tracker
arch: [aarch64, amd64, armv7]
init: false
homeassistant_api: true
services: [mqtt:need]
map: [share:rw]
ingress: true
ingress_port: 8100
panel_icon: mdi:chart-timeline-variant-shimmer
options:
  # --- rynek ---
  poll_interval_minutes: 10
  history_backfill_years: 5
  display_currency_secondary: "PLN"
  allow_scrape_fallback: false
  # --- klucze API (wszystkie opcjonalne; primary działa bez klucza) ---
  finnhub_api_key: ""
  marketaux_api_key: ""
  twelvedata_api_key: ""
  # --- AI: łańcuch providerów, każdy z WŁASNYM kluczem ---
  ai_primary: "local"              # local | gemini | anthropic | off
  ai_fallback: "gemini"            # gemini | anthropic | none
  local_llm_base_url: "http://192.168.0.106:3003/v1"
  local_llm_api_key: ""            # freellmapi-... (wpisywane w panelu, nigdy w repo)
  local_llm_model: "gemini-3.5-flash"   # UWAGA: 'auto'/'fusion' nie wspierają json_schema
  gemini_api_key: ""               # OSOBNY klucz, nie ten z integracji llmvision
  gemini_model: "gemini-3.1-flash-lite"
  anthropic_api_key: ""
  anthropic_model: "claude-haiku-4-5-20251001"
  ai_max_tokens: 4000              # ≥1500 wymagane: tokeny reasoningu liczą się do budżetu
  ai_max_calls_per_day: 40
  ai_news_batch_size: 15
  ai_recommendations_enabled: true
  analysis_time: "19:00"           # dzienna analiza po zamknięciu sesji
  # --- alerty ---
  notify_service: ""               # np. notify.family
  alert_sentiment_drop: 0.5
  alert_price_move_pct: 3.0
  alert_on_forecast_break: true
  alert_min_interval_minutes: 120
  # --- portfel i podatki (loty edytowane w UI, tu tylko wartości startowe) ---
  position_qty: 0.0
  avg_cost_eur: 0.0
  broker_fee_pct: 0.0
  cost_basis_policy: "own_only"    # own_only | own_plus_drip | all_at_acquisition
  espp_match_pct: 50.0             # dokładka firmy do planu zakupu akcji
  finnish_withholding_pct: 35.0    # nierezydent bez uproszczonej procedury; 15% po umowie
  treaty_withholding_pct: 15.0     # stawka traktatowa = maks. zaliczenie w PIT-38
  pl_capital_gains_tax_pct: 19.0   # "Belka"
  vest_reminder_days: 7            # powiadomienie przed uwolnieniem transzy
  tax_year: 0                      # 0 = rok bieżący; do generowania raportu za rok zamknięty
  # --- infra ---
  mqtt_host: "core-mosquitto"
  mqtt_port: 1883
  mqtt_user: ""
  mqtt_password: ""
  log_level: "info"
  backup_share: "/share/nokia_tracker"
  timezone: "Europe/Warsaw"
schema:
  poll_interval_minutes: int
  history_backfill_years: int
  display_currency_secondary: str?
  allow_scrape_fallback: bool
  finnhub_api_key: password
  marketaux_api_key: password
  twelvedata_api_key: password
  ai_primary: "list(local|gemini|anthropic|off)"
  ai_fallback: "list(gemini|anthropic|none)"
  local_llm_base_url: str?
  local_llm_api_key: password
  local_llm_model: str?
  gemini_api_key: password
  gemini_model: str?
  anthropic_api_key: password
  anthropic_model: str?
  ai_max_tokens: int
  ai_max_calls_per_day: int
  ai_news_batch_size: int
  ai_recommendations_enabled: bool
  analysis_time: str
  notify_service: str?
  alert_sentiment_drop: float
  alert_price_move_pct: float
  alert_on_forecast_break: bool
  alert_min_interval_minutes: int
  position_qty: float
  avg_cost_eur: float
  broker_fee_pct: float
  cost_basis_policy: "list(own_only|own_plus_drip|all_at_acquisition)"
  espp_match_pct: float
  finnish_withholding_pct: float
  treaty_withholding_pct: float
  pl_capital_gains_tax_pct: float
  vest_reminder_days: int
  tax_year: int
  mqtt_host: str
  mqtt_port: int
  mqtt_user: str
  mqtt_password: password
  log_level: "list(debug|info|warning|error)?"
  backup_share: str
  timezone: str?
```
Progi alertów, źródła newsów i wybór modelu żyją potem w tabeli `settings` (edytowalne w UI bez restartu add-onu) — opcje Supervisora są tylko wartością startową.

### Schemat bazy (migracja v1 — pełna, także pod 0.2.0)
Rynek i AI: `instruments`, `quotes` (`UNIQUE(instrument_id, ts, granularity)`), `news`, `news_scores`, `news_sources`, `forecasts`, `briefings`.
Podatki i portfel: `lots`, `sales`, `sale_allocations`, `grants`, `vests`, `dividends`, `nbp_rates`, `imports`, `import_conflicts`.
Infra: `settings`, `api_usage`, `ai_usage`, `http_cache`, `alerts_log`.

**Cały schemat podatkowy powstaje już w migracji v1**, mimo że 0.1.0 używa go minimalnie — dzięki temu 0.2.0 dokłada logikę i UI, a nie migrację danych produkcyjnych.

```sql
CREATE TABLE lots (
    id INTEGER PRIMARY KEY,
    acquired_date TEXT NOT NULL,
    lot_type TEXT NOT NULL CHECK(lot_type IN ('own','matched','lti','dividend_drip')),
    quantity REAL NOT NULL,               -- akcje ułamkowe: REAL, nie INTEGER
    price_eur REAL NOT NULL,              -- kurs akcji w EUR w dniu nabycia
    fee_eur REAL NOT NULL DEFAULT 0,
    nbp_rate REAL,                        -- kurs NBP z ostatniego dnia roboczego PRZED nabyciem
    nbp_rate_date TEXT,                   -- realna data tabeli NBP (po cofnięciu przez weekend)
    cost_pln REAL,                        -- zamrożone: price_eur * quantity * nbp_rate
    grant_id INTEGER REFERENCES grants(id),
    source TEXT NOT NULL DEFAULT 'manual',
    qty_remaining REAL,                   -- utrzymywane przez FIFO
    notes TEXT
);
CREATE TABLE sales (
    id INTEGER PRIMARY KEY, sale_date TEXT NOT NULL, quantity REAL NOT NULL,
    price_eur REAL NOT NULL, fee_eur REAL NOT NULL DEFAULT 0,
    nbp_rate REAL, nbp_rate_date TEXT, revenue_pln REAL, notes TEXT
);
CREATE TABLE sale_allocations (            -- FIFO: która sprzedaż zjadła który lot
    id INTEGER PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
    lot_id  INTEGER NOT NULL REFERENCES lots(id),
    quantity REAL NOT NULL, cost_pln REAL NOT NULL, revenue_pln REAL NOT NULL
);
CREATE TABLE grants (
    id INTEGER PRIMARY KEY,
    program TEXT NOT NULL CHECK(program IN ('espp','lti')),
    grant_date TEXT NOT NULL, declared_amount_eur REAL, quantity REAL,
    match_pct REAL NOT NULL DEFAULT 0,     -- ESPP: 50
    notes TEXT
);
CREATE TABLE vests (
    id INTEGER PRIMARY KEY,
    grant_id INTEGER NOT NULL REFERENCES grants(id) ON DELETE CASCADE,
    vest_date TEXT NOT NULL, quantity REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','vested','cancelled')),
    lot_id INTEGER REFERENCES lots(id)     -- wypełniane, gdy scheduler utworzy lot
);
CREATE TABLE dividends (
    id INTEGER PRIMARY KEY, pay_date TEXT NOT NULL,
    gross_per_share_eur REAL, quantity REAL, gross_eur REAL NOT NULL,
    withholding_pct REAL, withholding_paid_eur REAL, net_received_eur REAL,
    nbp_rate REAL, nbp_rate_date TEXT, gross_pln REAL, pl_tax_due_pln REAL,
    reinvested_lot_id INTEGER REFERENCES lots(id),   -- DRIP: jedno zdarzenie, dwa skutki
    notes TEXT
);
CREATE TABLE nbp_rates (date TEXT PRIMARY KEY, rate REAL NOT NULL, effective_date TEXT NOT NULL);
CREATE TABLE imports (                     -- audyt każdego wgrania pliku (idempotentne, narastające eksporty)
    id INTEGER PRIMARY KEY,
    filename TEXT NOT NULL, file_sha256 TEXT NOT NULL,
    period_start TEXT, period_end TEXT, as_of_date TEXT,
    imported_at TEXT NOT NULL DEFAULT (datetime('now')),
    rows_inserted INTEGER NOT NULL DEFAULT 0,
    rows_unchanged INTEGER NOT NULL DEFAULT 0,   -- klucz naturalny już istniał, wartości identyczne
    rows_conflict INTEGER NOT NULL DEFAULT 0     -- klucz naturalny istniał, wartości różne -> patrz import_conflicts
);
CREATE TABLE import_conflicts (            -- nigdy nie nadpisujemy automatycznie przy rozjeździe
    id INTEGER PRIMARY KEY,
    import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,             -- 'lot' | 'vest' | 'dividend'
    natural_key TEXT NOT NULL,             -- np. "purchase:2026-04-27:2026-04-29:34.75"
    existing_json TEXT NOT NULL, incoming_json TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0, resolution TEXT   -- 'kept_existing' | 'applied_incoming' | null
);
```

W 0.1.0 formularz „stan posiadania" zapisuje **jeden lot** `lot_type='own'`, `source='holdings_form'` i przy edycji go nadpisuje. W 0.2.0 ten syntetyczny lot zostaje zastąpiony realną historią z importu.

---

## 3a. Silnik podatkowy — rdzeń wydania 0.2.0

### Podstawa prawna (potwierdzona, nie założona)
| Przepis | Co z niego wynika dla kodu |
|---|---|
| **art. 11a ust. 1–2 ustawy o PIT** | Przychody **i** koszty w walucie obcej przelicza się kursem **średnim NBP z ostatniego dnia roboczego poprzedzającego** dzień uzyskania przychodu / poniesienia kosztu. Każdy lot i każda sprzedaż mają **własny, zamrożony kurs** — nigdy kurs bieżący. |
| **art. 24 ust. 11–12a** | Programy motywacyjne: opodatkowanie **odroczone do odpłatnego zbycia**. Ust. 12a rozciąga to na spółki z krajów z umową o unikaniu podwójnego opodatkowania — Finlandia taką ma, więc ESPP i LTI Nokii się łapią. |
| **FIFO dla papierów tego samego rodzaju** | Sprzedaż konsumuje loty w kolejności nabycia. Loty są **niepodzielne w księgowaniu, ale podzielne ilościowo** (sprzedaż częściowa zjada część lotu) — stąd `sale_allocations` jako tabela łącząca. |
| **PIT-38 sekcja G** | Zagraniczny podatek od dywidend wykazuje się osobno; zaliczenie ograniczone do stawki traktatowej. Strata z akcji **nie** obniża podatku od dywidend — dwa rozdzielne strumienie w kodzie. |

> **Klauzula, która musi być widoczna w UI, README i przy eksporcie:** to kalkulator pomocniczy, nie doradztwo podatkowe. Wartości do PIT-38 potwierdzasz z własnym rozliczeniem lub doradcą. Add-on pokazuje **jak** policzył każdą liczbę (rozwijany ślad obliczeń per lot), żeby dało się to zweryfikować, a nie przyjąć na wiarę.

### Typy lotów i polityka kosztu
`lot_type ∈ {own, matched, lti, dividend_drip}` — mapowanie na Twój arkusz: `własne`, `podarowane`, LTI, `dywidenda`.

Polityka `cost_basis_policy` (domyślnie **`own_only`** — Twój dzisiejszy wariant):

| Polityka | Koszt uznawany | Uzasadnienie |
|---|---|---|
| **`own_only`** (default) | tylko `own` | Za pozostałe nic nie zapłaciłeś, a opodatkowanie odroczono do zbycia → nie ma czego odliczyć. Najwyższy podatek, najmniejsze ryzyko sporu. |
| `own_plus_drip` | `own` + `dividend_drip` | DRIP kupujesz za pieniądze już opodatkowane jako dywidenda. |
| `all_at_acquisition` | wszystkie loty w wartości z dnia nabycia | Dopuszczalne **tylko** jeśli wartość dokładki i LTI była wykazana jako przychód ze stosunku pracy (PIT-11) — inaczej to podwójne odliczenie. |

UI liczy **wszystkie trzy równolegle** i pokazuje je obok siebie z podstawą prawną oraz kwotą różnicy — decyzję podejmujesz świadomie przy generowaniu PIT-38, a nie przez przypadkowe ustawienie.

### ESPP i LTI — model grantów i vestingu
`grants` (`program ∈ {espp, lti}`, data nadania, deklarowana kwota lub liczba akcji, `match_pct` domyślnie 50 dla ESPP) → `vests` (transza: `vest_date`, `quantity`, `status ∈ {pending, vested, cancelled}`).

Scheduler codziennie sprawdza transze z `vest_date <= dzisiaj` i statusem `pending`: **tworzy lot** (`lot_type` = `matched` lub `lti`), dociąga kurs NBP z dnia roboczego poprzedzającego, oznacza transzę jako `vested` i wysyła powiadomienie. Loty powstają automatycznie w dniu uwolnienia — bez tego rok podatkowy zawsze będzie niekompletny.

Sensory vestingu: `unvested_qty`, `next_vest_date`, `next_vest_qty`, `unvested_value_eur` (przy dzisiejszym kursie), `vested_ytd_qty`.

### Kursy NBP
`providers/fx_nbp.py` → `api.nbp.pl/api/exchangerates/rates/a/eur/{YYYY-MM-DD}` (darmowe, bez klucza). Tabela **A**, kurs średni. Kluczowa logika: **cofanie do ostatniego dnia roboczego** — NBP zwraca 404 dla weekendów i świąt, więc szukamy wstecz maks. 10 dni. Twoja sprzedaż z 27.10.2025 użyła kursu z 24.10.2025 (piątek) — dokładnie ten mechanizm.

Kursy trafiają do tabeli `nbp_rates` (`date UNIQUE, rate, effective_date`) i **nigdy nie są przeliczane ponownie** — raz przypisany do lotu kurs jest zamrożony, bo tak wygląda w złożonej deklaracji.

### Produkty wyjściowe
- **Raport PIT-38** per rok podatkowy: przychód (poz. C), koszty, dochód/strata, podatek 19%, a w sekcji G zagraniczny podatek od dywidend z zaliczeniem. Eksport CSV/XLSX + wydruk PDF z pełnym śladem obliczeń per lot.
- **PIT/ZG** — załącznik dla dochodów zagranicznych (Finlandia).
- **Kwota do odzyskania z fińskiego Vero** (nadpłacone ponad stawkę traktatową) — pilnowana osobnym sensorem, bo o niej najłatwiej zapomnieć.
- **Symulacja „co jeśli sprzedam teraz"** — przy dzisiejszym kursie i kursie NBP D-1: ile podatku, które loty FIFO zje sprzedaż. To spina moduł podatkowy z rekomendacją AI: „sprzedaj" znaczy co innego, gdy wiesz, że fiskus weźmie 1 900 zł.

### Dywidendy — trzy warstwy, których broker nie policzy
1. **Podatek u źródła w Finlandii** — 35% dla nierezydenta bez uproszczonej procedury, **15%** przy zadziałaniu procedury traktatowej dla akcji nominee-registered.
2. **Zaliczenie w Polsce ograniczone do stawki traktatowej** — PIT-38 pozwala odliczyć maks. **15 pp**, nie faktycznie pobrane 35%. Przy 19% Belki zostaje **4 pp do dopłaty**.
3. **Nadpłacone 20 pp odzyskuje się z Vero**, nie odlicza w PL.

Wszystkie stawki konfigurowalne. Dywidenda reinwestowana tworzy **jednocześnie** rekord dochodu z dywidendy (opodatkowany) i lot `dividend_drip` (przyszły koszt lub zero, zależnie od polityki) — to jedno zdarzenie o dwóch skutkach podatkowych i najłatwiejsze miejsce na błąd.

### Parser Computershare — rozpoznana struktura źródłowa (2026-07-27, na realnych plikach użytkownika)

Pięć plików w `/config/akcje_temp/`, po jednym na okres `1 Jan RRRR – 1 Jan RRRR+1` (2022→2026 YTD) = **kompletna historia planu**. Ekstrakcja: `pypdf.PdfReader(...).pages[i].extract_text(extraction_mode="layout")` — tryb domyślny sklejał znaki bez separatorów (`0.00 EUR27 Apr 2026`), tryb `layout` daje kolumny rozdzielone spacjami, gotowe do `re.split(r'\s{2,}')` per wiersz. To nie skan — więc **żadnego OCR, żadnych trenowalnych layoutów** — jeden deterministyczny parser na sekcję.

Cztery tabele źródłowe, z których wywodzą się WSZYSTKIE loty i granty (sekcje „Assets by type/plan", „Available", „Locked", „Vested Shares" na stronach 1–5 to **wyłącznie widoki podsumowujące do weryfikacji sumy końcowej — nigdy źródło importu**, inaczej akcje zostaną policzone podwójnie):

| Sekcja PDF | Kolumny | Efekt w bazie |
|---|---|---|
| **Purchases** | Contribution Date, Allocation Date, Trade Date, Contribution Amount (EUR), Fair Market Value, Purchase Price (EUR), Quantity, Residual Amount | `lots` (`lot_type='own'`), klucz idempotencji `(contribution_date, trade_date, quantity)` |
| **Matching Shares** (w sekcji „Restricted stock units" planu ESPP) | Allocation Date, Vesting Date, Available from, Quantity | `grants` (`program='espp'`) + `vests`; auto-lot `lot_type='matched'` w dniu vestingu |
| **Restricted Shares — „RRRR RS AWARD DD-MMM-RRRR"** | Allocation Date, Vesting Date, Available from, Quantity — **wiele transz jednego grantu** (obserwowane: 634/633/633 na 3 lata) | `grants` (`program='lti'`) + wiele `vests`; auto-lot `lot_type='lti'` per transza |
| **Dividend (Reinvested)** | Record Date, Purchase Date, Entitled Quantity, Gross Dividend Payment (EUR), Taxes (EUR), Fees, Dividend Reinvested (EUR), Purchase Price (EUR), Purchased shares, Residual Amount | `dividends` + `lots` (`lot_type='dividend_drip'`, `reinvested_lot_id`); **`withholding_pct` liczone z realnych `Taxes/Gross` per wiersz** (zmierzone: 34,9–35,0% na 5 niezależnych dywidendach), nie z opcji configu — dokładniejsze niż statyczna stawka |
| **Withhold-to-Cover** | Execution Date, Instrument, Quantity, Sale Price, Taxes, Fees, **Net Units** | **Reguła:** `Net Units == Quantity` → zero-efektowe potwierdzenie rozliczenia (potwierdzone na 2 realnych wierszach: 2100=2100, 634=634 — spójne z art. 24 ust. 11, brak podatku do potrącenia przy polskim odroczeniu). `Net Units < Quantity` → **rzadki przypadek, różnica to faktyczna dyspozycja przy vestingu**, wymaga ręcznego potwierdzenia w UI przed zaksięgowaniem jako `sales`, silnik nie księguje automatycznie |

Po imporcie każdego pliku: suma `qty_remaining` per plan/typ lotu **musi zgadzać się** z sekcją „Assets by plan" na stronie 1 tego samego wyciągu (kontrola krzyżowa, nie źródło) — rozbieżność blokuje import z czytelnym komunikatem, zamiast cichej pomyłki.

### Import przyrostowy — regularne, powtarzające się wgrywanie tego samego roku

EquatePlus/Computershare eksportuje zawsze **„1 Jan RRRR – dzisiaj"** — czyli każdy kolejny wyciąg za bieżący rok zawiera wszystkie wcześniej wgrane zdarzenia plus nowe na końcu. To założenie projektowe od początku (5 plików użytkownika = 5 takich narastających eksportów), ale wymaga trzech mechanizmów wprost:

1. **UPSERT z detekcją konfliktu, nie ślepy `INSERT OR IGNORE`.** Dla każdego klucza naturalnego (Purchases: `contribution_date+trade_date+quantity`; vesting: `grant_id+vest_date+quantity`; dywidendy: `record_date+purchase_date+entitled_quantity`):
   - klucz istnieje + wartości identyczne → **no-op** (dokładnie Twój scenariusz: ten sam rok, stare wiersze się powtarzają);
   - klucz nie istnieje → **insert** (nowe zdarzenia od ostatniego wgrania);
   - klucz istnieje + wartości **różne** → **nigdy nie nadpisuj automatycznie**. Wiersz ląduje w `import_conflicts` (nowa tabela) i czeka na Twoje potwierdzenie w UI. To jedyny sposób, żeby cicho nie zgubić sprostowania EquatePlus ani cicho go nie zignorować.

2. **Tabela audytu `imports`**: `(id, filename, file_sha256, period_start, period_end, as_of_date, imported_at, rows_inserted, rows_unchanged, rows_conflict)`. Każde wgranie zostaje w historii — widzisz w UI dokładnie, co dodało ostatnie przesłanie pliku, bez przeszukiwania surowych danych.

3. **Kontrola sumy używa najnowszego pliku per rok, nie ostatnio wgranego.** Reguła: dla danego roku kalendarzowego bierzemy plik z najpóźniejszą `as_of_date` (z tabeli `imports`), nie plik ostatnio przesłany — bo mogłbyś wgrać stary plik ponownie przez pomyłkę i nie chcemy, żeby to zepsuło kontrolę krzyżową.

**Twój konkretny scenariusz — sprawdzony:** wgrywasz w marcu wyciąg „1 Jan 2026 – 15 Mar 2026" (importuje N zdarzeń), potem w czerwcu „1 Jan 2026 – 20 Jun 2026" (zawiera te same N zdarzeń + M nowych). Marcowe wiersze trafiają na te same klucze → no-op, log pokazuje `rows_unchanged=N`. Nowe zdarzenia z Q2 → `rows_inserted=M`. Zero duplikatów, zero ręcznej pracy przy pokrywających się okresach.

**Nazewnictwo:** EquatePlus to portal, przez który pobierasz raport; Computershare Investor Services (Ireland) to podmiot wystawiający sam dokument (widnieje w nagłówku i stopce PDF-a). To ten sam łańcuch, więc jeden parser — moduł nazywamy `computershare_pdf.py` od formatu dokumentu, nie od portalu, bo format może przetrwać zmianę portalu.

**Dwie drogi wgrywania (obie prowadzą do tego samego, idempotentnego importera):**
1. **Upload w UI add-onu** — strona „Importy", drag & drop, natychmiastowy podgląd `rows_inserted / rows_unchanged / rows_conflict` przed zatwierdzeniem.
2. **Folder podrzucania** `/share/nokia_tracker/import/` — kładziesz plik z telefonu czy komputera, scheduler przy najbliższym cyklu go wciąga i przenosi do `/share/nokia_tracker/imported/` z prefiksem czasowym. **Reuse `auto_import_share()` z `fuel_tracker/main.py`** — ten sam sprawdzony wzorzec (`import/` → `imported/`, `try/except` per plik, żeby jeden zepsuty dokument nie zablokował reszty), tylko na PDF-y zamiast CSV.

### Importery (0.2.0)
| Droga | Implementacja | Uwaga |
|---|---|---|
| **PDF-y Computershare** | `importers/computershare_pdf.py`, `pypdf` layout mode, parser deterministyczny per sekcja (opis wyżej) | **Primary.** 5 plików użytkownika = pełna historia 2022–2026 YTD, gotowe do importu bez czekania na kolejne próbki |
| Formularz w UI | strona „Loty" — data, typ, kurs EUR, ilość; kurs NBP dociągany sam | do korekt ręcznych i zdarzeń spoza wyciągów |
| Eksport arkusza użytkownika | `importers/xlsx_sheet.py` | **Zdegradowany do weryfikacji krzyżowej** — nie jest już źródłem importu (patrz wyżej); przydatny jako drugi zestaw oczu na wynik z PDF-ów |
| Raporty z innych brokerów (opcjonalnie) | `importers/broker_pdf.py` na trenowalne layouty (`invoice_parser.py`/`invoice_layouts.py` z `pv_roi_tracker`) | tylko jeśli w przyszłości dojdzie inny broker niż Computershare |

---

## 4. Dashboard HA (propozycja YAML)

Wykorzystuje karty, które już masz z HACS: `apexcharts-card`, `auto-entities`, `mushroom`, `card-mod`.

```yaml
title: Nokia
cards:
  # --- nagłówek: kurs + zmiana + status sesji ---
  - type: horizontal-stack
    cards:
      - type: custom:mushroom-template-card
        primary: "{{ states('sensor.nokia_tracker_price_eur') }} €"
        secondary: >-
          {% set d = states('sensor.nokia_tracker_change_pct_day') | float(0) %}
          {{ '▲' if d > 0 else '▼' }} {{ d | round(2) }} % ·
          {{ 'sesja otwarta' if is_state('binary_sensor.nokia_tracker_market_open','on') else 'sesja zamknięta' }}
        icon: mdi:chart-line
        icon_color: >-
          {{ 'green' if states('sensor.nokia_tracker_change_pct_day')|float(0) > 0 else 'red' }}
        badge_icon: >-
          {{ 'mdi:alert' if states('sensor.nokia_tracker_impact_score')|float(0) >= 2.5 else '' }}
      - type: custom:mushroom-template-card
        primary: "{{ states('sensor.nokia_tracker_market_value_pln') }} zł"
        secondary: >-
          P&L {{ states('sensor.nokia_tracker_unrealized_pnl_eur') }} €
          ({{ states('sensor.nokia_tracker_unrealized_pnl_pct') }} %)
        icon: mdi:briefcase-variant
        icon_color: >-
          {{ 'green' if states('sensor.nokia_tracker_unrealized_pnl_eur')|float(0) > 0 else 'red' }}

  # --- kurs 3M + średnie + benchmark znormalizowany ---
  - type: custom:apexcharts-card
    header:
      show: true
      title: NOKIA.HE — 3 miesiące
      show_states: true
    graph_span: 3month
    span:
      end: day
    yaxis:
      - id: eur
        decimals: 2
        apex_config:
          title: { text: EUR }
      - id: idx
        opposite: true
        decimals: 1
    series:
      - entity: sensor.nokia_tracker_price_eur
        name: Kurs
        yaxis_id: eur
        type: area
        stroke_width: 2
        group_by: { func: last, duration: 1d }
      - entity: sensor.nokia_tracker_sma_20
        name: SMA 20
        yaxis_id: eur
        type: line
        stroke_width: 1
        curve: smooth
        group_by: { func: last, duration: 1d }
      - entity: sensor.nokia_tracker_sma_50
        name: SMA 50
        yaxis_id: eur
        type: line
        stroke_width: 1
        curve: smooth
        group_by: { func: last, duration: 1d }
      - entity: sensor.nokia_tracker_omxh25_value
        name: OMXH25
        yaxis_id: idx
        type: line
        opacity: 0.35
        stroke_width: 1
        group_by: { func: last, duration: 1d }

  # --- prognozy AI z przedziałem ufności ---
  - type: custom:apexcharts-card
    chart_type: rangeBar
    header: { show: true, title: Prognozy AI vs kurs bieżący }
    series:
      - entity: sensor.nokia_tracker_forecast_1w_eur
        name: 1 tydzień
        data_generator: |
          const a = entity.attributes;
          return [[Date.now(), [a.ci_low, a.ci_high]]];
      - entity: sensor.nokia_tracker_forecast_1m_eur
        name: 1 miesiąc
        data_generator: |
          const a = entity.attributes;
          return [[Date.now(), [a.ci_low, a.ci_high]]];
      - entity: sensor.nokia_tracker_forecast_12m_eur
        name: 12 miesięcy
        data_generator: |
          const a = entity.attributes;
          return [[Date.now(), [a.ci_low, a.ci_high]]];

  # --- briefing dzienny (pełny tekst z atrybutu, nie ze stanu) ---
  - type: markdown
    content: >-
      ## 📋 Briefing {{ state_attr('sensor.nokia_tracker_daily_briefing','generated_at') | as_datetime | as_local | as_timestamp | timestamp_custom('%d.%m %H:%M', true) }}

      {{ state_attr('sensor.nokia_tracker_daily_briefing','text') }}

      **Sentyment:** {{ states('sensor.nokia_tracker_sentiment_score') }}
      ({{ states('sensor.nokia_tracker_sentiment_label') }}) ·
      **Werdykt:** {{ states('sensor.nokia_tracker_alpha_verdict') }} ·
      **Trafność prognoz:** {{ states('sensor.nokia_tracker_forecast_accuracy_pct') }} %

      {% for r in state_attr('sensor.nokia_tracker_daily_briefing','key_risks') or [] %}
      - ⚠️ {{ r }}
      {% endfor %}

  # --- rekomendacja AI z disclaimerem ---
  - type: markdown
    content: >-
      {% set rec = states('sensor.nokia_tracker_ai_recommendation') %}
      ## {{ {'kup':'🟢','akumuluj':'🟢','trzymaj':'⚪','redukuj':'🟠','sprzedaj':'🔴'}.get(rec,'⚪') }} {{ rec | upper }}

      {{ state_attr('sensor.nokia_tracker_ai_recommendation','reason_pl') }}

      *Pewność: {{ state_attr('sensor.nokia_tracker_ai_recommendation','confidence') }} ·
      Twoja średnia cena: {{ states('sensor.nokia_tracker_avg_cost_eur') }} € ·
      kurs: {{ states('sensor.nokia_tracker_price_eur') }} €*

      > ⚠️ {{ state_attr('sensor.nokia_tracker_ai_recommendation','disclaimer') }}

  # --- newsy z ocenami AI ---
  - type: markdown
    content: >-
      ## 📰 Newsy ({{ states('sensor.nokia_tracker_news_count_24h') }} / 24 h)

      | | Wpływ | Tytuł | Teza |
      |---|---|---|---|
      {% for n in state_attr('sensor.nokia_tracker_top_news','items') or [] -%}
      | {{ '🟢' if n.sentiment > 0.2 else ('🔴' if n.sentiment < -0.2 else '⚪') }}
      | {{ '●' * (n.impact | int) }}
      | [{{ n.title | truncate(60) }}]({{ n.url }})
      | {{ n.thesis_pl | truncate(80) }} |
      {% endfor %}

  # --- benchmark i technika ---
  - type: glance
    title: Benchmark i wskaźniki
    columns: 4
    entities:
      - sensor.nokia_tracker_rel_perf_1d_vs_omxh25
      - sensor.nokia_tracker_rel_perf_1m_vs_ericsson
      - sensor.nokia_tracker_beta_60d
      - sensor.nokia_tracker_rsi_14
      - sensor.nokia_tracker_volatility_30d_pct
      - sensor.nokia_tracker_trend
      - sensor.nokia_tracker_week52_high
      - sensor.nokia_tracker_week52_low

  # --- portfel (EUR = rachunek, PLN = prezentacja) ---
  - type: entities
    title: Portfel
    entities:
      - sensor.nokia_tracker_position_qty
      - sensor.nokia_tracker_avg_cost_eur
      - sensor.nokia_tracker_cost_basis_eur
      - sensor.nokia_tracker_market_value_eur
      - sensor.nokia_tracker_market_value_pln
      - sensor.nokia_tracker_unrealized_pnl_eur
      - sensor.nokia_tracker_unrealized_pnl_pln
      - sensor.nokia_tracker_total_return_pct
      - sensor.nokia_tracker_eurpln_rate

  # --- dywidendy i podatki ---
  - type: entities
    title: Dywidendy i podatki
    entities:
      - sensor.nokia_tracker_dividends_gross_eur
      - sensor.nokia_tracker_dividends_net_eur
      - sensor.nokia_tracker_withholding_paid_eur
      - sensor.nokia_tracker_pl_tax_due_pln
      - sensor.nokia_tracker_reclaimable_from_finland_eur
      - sensor.nokia_tracker_dividend_yield_on_cost_pct
      - sensor.nokia_tracker_next_dividend_date
    footer:
      type: graph
      entity: sensor.nokia_tracker_dividends_net_eur

  # --- vesting i podatki (dochodzi w 0.2.0) ---
  - type: entities
    title: Vesting i podatek
    entities:
      - sensor.nokia_tracker_unvested_qty
      - sensor.nokia_tracker_next_vest_date
      - sensor.nokia_tracker_next_vest_qty
      - sensor.nokia_tracker_unvested_value_eur
      - sensor.nokia_tracker_tax_year_realized_gain_pln
      - sensor.nokia_tracker_tax_year_tax_due_pln
      - sensor.nokia_tracker_whatif_tax_if_sold_now_pln

  # --- diagnostyka: widoczna tylko gdy coś nie działa ---
  - type: conditional
    conditions:
      - condition: state
        entity: sensor.nokia_tracker_provider_status
        state_not: "ok"
    card:
      type: entities
      title: ⚠️ Diagnostyka
      entities:
        - sensor.nokia_tracker_provider_status
        - sensor.nokia_tracker_ai_provider_active
        - sensor.nokia_tracker_ai_calls_today
        - sensor.nokia_tracker_api_quota_left
        - sensor.nokia_tracker_last_analysis_ts
```

Automatyzacja TTS (do `automations.yaml`, po polsku jak reszta):
```yaml
- alias: "Nokia — poranny briefing głosowy"
  triggers: [{ trigger: time, at: "07:30:00" }]
  conditions:
    - condition: template
      value_template: "{{ state_attr('sensor.nokia_tracker_daily_briefing','tts_text') not in [none,''] }}"
  actions:
    - action: tts.google_translate_say
      data:
        entity_id: media_player.salon
        language: pl
        message: "{{ state_attr('sensor.nokia_tracker_daily_briefing','tts_text') }}"
```

Realne `entity_id` **potwierdzam po pierwszej publikacji** przez `/api/states` — HA składa je z nazwy urządzenia i nazwy encji, a nie z `unique_id` (lekcja spisana przy `fuel_tracker`). Dashboard i automatyzacja lądują w repo dopiero po weryfikacji.

---

## 5. Plan implementacji krok po kroku

Każdy krok = działający add-on + zielone testy + commit. Publiczny release **0.1.0** po kroku 10, **0.2.0** (podatki) po kroku 15.

### Wydanie 0.1.0 — rynek, AI, prosty portfel

| # | Krok | Zakres | Definition of done |
|---|---|---|---|
| **0** | Bootstrap repo + zasada „plan jako md" | GitHub `miczu71/nokia_tracker`, `repository.json`, `.gitignore`, klon w `/config/addons/nokia_tracker`, kopia tego blueprintu do `docs/BLUEPRINT.md`, **memory `feedback_plans_as_md.md` + wpis w `/config/CLAUDE.md`** („każdy plan zapisujemy jako plik .md, żeby nie zginął przy kompaktowaniu") | Add-on widoczny w sklepie HA; zasada utrwalona poza kontekstem sesji |
| **1** | Szkielet | `Dockerfile`, `config.yaml`, `run.sh`, `requirements.txt`, `main.py` (log + pusty scheduler), `db.py` **migracja v1 z pełnym schematem podatkowym**, `settings.py`, `pytest.ini`, `conftest.py` | Add-on startuje, `/data/nokia_tracker.db` ma wszystkie tabele (też `lots`/`grants`/`vests`), `pytest` zielony |
| **2** | Ceny + historia | `providers/base.py`, `providers/yahoo.py`, `cache.py`, `ratelimit.py`, `market.py`, `quotes.py`, backfill 5 lat, `indicators.py` | W bazie ~1300 świec dziennych; test parsuje `fixtures/yahoo_chart_nokia.json`; testy SMA/RSI/beta na seriach o znanym wyniku |
| **3** | MQTT — rynek i technika | `publisher.py` (port z `fuel_tracker`), `sensors.py`, `binary_sensor.market_open`, publikacja co `poll_interval_minutes` | Encje kursu i wskaźników w HA; potwierdzone realne `entity_id` przez `/api/states`; `test_publisher.py` na `discovery_payloads()` |
| **4** | Benchmark + FX | `ERIC-B.ST`, `^OMXH25`, `EURPLN=X`, `providers/fx_ecb.py`, `providers/fx_nbp.py`, `providers/finnhub.py` (ADR + `spread_vs_adr`), beta/alfa | Sensory względnej siły, `eurpln_rate` i wszystkie bliźniaki `*_pln` żyją; fallback FX na ECB przetestowany; NBP zwraca kurs z dnia poprzedzającego dla dowolnej daty |
| **5** | Newsy | `news.py`, `providers/news_rss.py`, `news_gdelt.py`, `news_finnhub.py`, `news_marketaux.py`, kanonikalizacja + dedup, tabela `news_sources` | Newsy wpadają do bazy bez duplikatów; test dedupu na dwóch wariantach tego samego URL-a (`utm_*`) |
| **6** | Warstwa AI + sentyment | `ai/provider.py` (łańcuch local→gemini→anthropic), `ai/openai_compat.py` (**freellmapi, walidacja `response_format` przez `/v1/models`, 502 jako retryable**), `ai/gemini.py`, `ai/anthropic_api.py`, `ai/prompts.py`, `ai/usage.py`, batchowe `score_news` | Oceny w `news_scores`; sensory `sentiment_*`, `top_news`, `ai_provider_active`; testy na zapisanych odpowiedziach obu providerów + przejście na fallback po dwóch 502; twardy dzienny limit działa |
| **7** | Prognozy + rekomendacja + backtest | `analysis.py`, `forecasts.py`, dzienna analiza o `analysis_time`, rozliczanie prognoz po `target_date`, MAPE, `ai_recommendation` z kontekstem średniej ceny | Sensory `forecast_*`, `forecast_accuracy_pct`, `daily_briefing`, `ai_recommendation` (+disclaimer w atrybutach i w TTS); `test_forecast_backtest.py` na sztucznej historii |
| **8** | Smart alerty | `alerts.py`: progi, histereza, anty-spam, `alerts_log`, publikacja na `nokia_tracker/events/alert` + `ha_client.notify` | Alert leci raz, nie w pętli; test histerezy przy oscylacji wokół progu |
| **9** | Portfel, podatki, Web UI | `portfolio.py` (stan posiadania → P&L w EUR i PLN), `tax.py` (FI u źródła / zaliczenie PL / kwota do odzysku), `web.py` + `templates/` + `static/` (waitress, `?v=<wersja>`, `Cache-Control: no-store` na HTML/API, badge wersji): formularz **ilość akcji + średnia cena**, formularz dywidend, wykres kursu, lista newsów z ocenami, historia prognoz vs rzeczywistość, ręczny „przeanalizuj teraz", wybór modelu z listy pobranej z routera | `test_portfolio.py` + `test_tax.py` zielone; UI zweryfikowane Playwrightem — screenshot **i** czysta konsola; disclaimer podatkowy widoczny na stronie dywidend |
| **10** | Dashboard + release 0.1.0 | Weryfikacja `entity_id`, dashboard przez WebSocket API, Playwright, automatyzacja TTS, README z tabelami encji, CHANGELOG, bump `config.yaml` **i** `__init__.py`, **opublikowany** (nie draft) release, aktualizacja add-onu przez Supervisora | Encje żyją po świeżej instalacji z release'u; wersja w `/addons/<slug>/info` == tag |

### Wydanie 0.2.0 — rozliczenie z urzędem skarbowym

| # | Krok | Zakres | Definition of done |
|---|---|---|---|
| **11** | Kursy NBP | `providers/fx_nbp.py`, tabela `nbp_rates`, cofanie do ostatniego dnia roboczego (maks. 10 dni wstecz), zamrażanie kursu przy zapisie | `test_fx_nbp.py`: 27.10.2025 → kurs z tabeli 24.10.2025; święta i weekendy obsłużone; raz zapisany kurs nigdy się nie zmienia |
| **12** | Loty i FIFO | `tax/lots.py`, `tax/policy.py`, `qty_remaining`, `sale_allocations`, trzy polityki kosztu liczone równolegle | `test_tax_lots.py` + `test_tax_policy.py`: sprzedaż częściowa, akcje ułamkowe, sprzedaż przez granicę lotów; trzy polityki dają trzy różne, poprawne kwoty |
| **13** | Import Computershare PDF (przyrostowy, idempotentny) + **walidacja na 5 latach realnych danych** | `importers/computershare_pdf.py` (parser sekcji Purchases/Matching Shares/RS AWARD/Dividend Reinvested/Withhold-to-Cover, layout-mode `pypdf`), UPSERT z detekcją konfliktu, tabele `imports`/`import_conflicts`, strona „Importy" w UI (historia wgrań + kolejka konfliktów do ręcznego rozstrzygnięcia), strona „Loty", `importers/xlsx_sheet.py` jako weryfikacja krzyżowa | `test_computershare_pdf.py` parsuje wszystkie 5 plików bez błędów; **wgranie tego samego pliku dwa razy daje `rows_inserted=0, rows_unchanged=N`**; sztucznie zmodyfikowany duplikat wiersza trafia do `import_conflicts`, nie nadpisuje cicho; suma `qty_remaining` per plan zgadza się z „Assets by plan" **na pliku o najpóźniejszej `as_of_date` per rok**; `test_pit38_regression.py` porównuje wynik z arkuszem użytkownika jako drugą parą oczu |
| **14** | ESPP, LTI, dywidendy | `tax/vesting.py` (granty, transze, auto-lot w dniu uwolnienia, przypomnienia), `tax/dividends.py` (u źródła / zaliczenie / odzysk z Vero), DRIP jako jedno zdarzenie o dwóch skutkach | `test_vesting.py`, `test_tax_dividends.py` zielone; transza uwalnia się sama i tworzy lot z kursem NBP D-1; powiadomienie `vest_reminder_days` przed datą |
| **15** | PIT-38, what-if, release 0.2.0 | `tax/pit38.py` (raport roczny, sekcja G, PIT/ZG, ślad obliczeń per lot), `tax/whatif.py`, eksport CSV/XLSX/PDF, sensory podatkowe, klauzula w UI i README | Raport za zamknięty rok zgadza się z ręcznym rozliczeniem; „co jeśli sprzedam teraz" pokazuje podatek i zjadane loty; PDF ma pełny ślad obliczeń; opublikowany release |
| **16** | Inny broker (opcjonalnie) | `importers/broker_pdf.py` na `pypdf` + trenowalne layouty (port `invoice_parser.py`/`invoice_layouts.py`) — **tylko jeśli** kiedyś dojdzie broker inny niż Computershare | Po jednorazowym wskazaniu pól kolejny PDF tego brokera parsuje się sam |

Cross-cutting w każdym kroku: TDD (testy przed implementacją, zero żywego HTTP w testach — wyłącznie fixture'y), komentarze i nazwy po polsku, `logger.exception` w pętlach schedulera tak, żeby awaria jednego providera nie zabijała pozostałych (wzorzec z `main.py::publish_sensors`).

---

## Weryfikacja

1. **Testy jednostkowe:** `cd /config/addons/nokia_tracker/nokia_tracker && python3 -m pytest` — zielone po każdym kroku.
2. **Providerzy na żywo, poza HA:** `python3 -m nokia_tracker.cli quote NOKIA.HE` i `... news --dry-run` — sprawdza realne API bez publikowania czegokolwiek do HA.
3. **Encje w HA:** po kroku 3 `ha_get_state` / `/api/states` na `sensor.nokia_*` — potwierdzenie realnych `entity_id` **przed** wpisaniem ich do dashboardu i automatyzacji.
4. **MQTT:** `mosquitto_sub -t 'nokia_tracker/#' -v` — retained stan i discovery obecne po restarcie brokera; po zatrzymaniu add-onu `availability` = `offline` (LWT).
5. **AI:** `ai_calls_today` nie przekracza limitu przez dobę; przy `ai_primary: off` add-on działa dalej, sensory AI = `unknown`. Test łańcucha: zły `local_llm_api_key` → `ai_provider_active` przeskakuje na `gemini` i analiza mimo to powstaje. Test walidacji: `local_llm_model: auto` → ostrzeżenie w logu o braku `response_format` (potwierdzone przez `/v1/models`).
6. **Podatki (0.2.0):**
   - dywidendy na ręcznie policzonym przykładzie: 100 € brutto, 35% u źródła → 65 € netto, zaliczenie 15 €, Belka 19 € → **4 € dopłaty w PL** i **20 € do odzyskania z Vero**;
   - **`test_computershare_pdf.py` na wszystkich 5 realnych plikach** z `/config/akcje_temp/`: parsuje bez błędów, suma lotów per plan zgadza się z „Assets by plan" na stronie 1 wyciągu, wyliczone `withholding_pct` per dywidenda mieści się w zmierzonym zakresie 34,9–35,0%;
   - reguła Withhold-to-Cover: `Net Units == Quantity` → zero efektu podatkowego (potwierdzone empirycznie 2× w danych użytkownika); `Net Units < Quantity` → import zatrzymuje się i prosi o ręczne potwierdzenie, nigdy nie księguje automatycznie;
   - **test regresyjny porównuje wynik silnika z arkuszem użytkownika jako drugą parą oczu** — PDF-y są źródłem prawdy, arkusz weryfikacją, więc rozbieżność każe zbadać PDF ponownie, a nie ślepo dopasowywać silnik do arkusza;
   - kursy NBP: `27.10.2025 → tabela 24.10.2025`; zamrożony kurs nie zmienia się po ponownym przeliczeniu raportu;
   - vesting: transza z datą wsteczną tworzy lot przy pierwszym uruchomieniu i **nie duplikuje go** przy kolejnych (idempotencja — dokładnie ten błąd zjadł depozyt w `pv_roi` 0.30.x); import tego samego pliku PDF drugi raz nie tworzy duplikatów lotów;
   - trzy polityki kosztu na tym samym zbiorze lotów dają trzy różne kwoty, każda z widocznym uzasadnieniem prawnym.
7. **UI:** MCP Playwright — screenshot do `/config/playwright/` + `browser_console_messages(error)` puste; badge wersji zgadza się z wydaniem (test cache'u WebView).
8. **Sekrety:** `git log -p | grep -iE "freellmapi-|AIza"` nie znajduje nic — klucze żyją wyłącznie w `/data/options.json`.
9. **Deploy:** opublikowany GH release → `ha_manage_addon` update → `/addons/<slug>/info` pokazuje nową wersję; żaden rebuild lokalny.
10. **Backtest AI:** po ~2 tygodniach `forecast_accuracy_pct` ma rozliczone prognozy — jeśli MAPE jest fatalne, prompty i kontekst wracają do poprawki (dane do tej decyzji zbierane od pierwszego dnia).
