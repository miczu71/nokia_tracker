# Nokia Tracker

Home Assistant add-on — osobisty asystent inwestycyjny dla akcji Nokia (NOKIA.HE, Nasdaq Helsinki).

Śledzi kurs, historię, newsy i sentyment (AI), generuje prognozy z weryfikacją trafności, porównuje
Nokię z benchmarkiem (Ericsson, OMXH25), prowadzi prosty portfel i rozliczenie podatku od dywidend,
i wystawia wszystko do Home Assistant przez MQTT Discovery — plus pełny web UI na ingressie. Docelowo
(0.2.0): rozliczenie akcji z pracowniczego planu (ESPP/LTI) i dywidend z polskim urzędem skarbowym
(PIT-38), na podstawie wyciągów Computershare/EquatePlus.

Pełny projekt architektoniczny: [`docs/BLUEPRINT.md`](docs/BLUEPRINT.md).

**Status:** wydanie **0.1.0** — rynek, AI, prosty portfel, web UI. Rozliczenie podatkowe PIT-38
(0.2.0) w budowie.

## Instalacja

Dodaj repozytorium `https://github.com/miczu71/nokia_tracker` jako źródło add-onów w Home Assistant
Supervisor (Ustawienia → Dodatki → Sklep z dodatkami → ⋮ → Repozytoria), zainstaluj i uruchom dodatek.
Wymaga działającego brokera MQTT (`core-mosquitto` domyślnie).

## Web UI

Dodatek wystawia własny interfejs na ingressie (panel „Nokia Tracker” w bocznym menu HA) — to
**główny sposób interakcji** z dodatkiem, dashboard Lovelace nie jest wymagany:

| Strona | Zawartość |
|---|---|
| **Pulpit** | Kurs, zmiana dzienna, sesja, trend, RSI, wykres cenowy 90 dni, karta portfela, sentyment i briefing AI, rekomendacja AI, prognozy 1w/1m/12m, ostatnie alerty, przycisk „Przeanalizuj teraz” |
| **Portfel** | Formularz stanu posiadania (ilość akcji + średni koszt zakupu) |
| **Dywidendy** | Formularz dodania wypłaty dywidendy (przelicza podatek u źródła/PL/odzysk na bieżąco), historia i podsumowanie, klauzula podatkowa |
| **Newsy** | Lista zebranych newsów z ocenami AI (sentyment, wpływ, teza) |
| **Prognozy** | Historia prognoz 1w/1m/12m vs zrealizowana cena, trafność (MAPE) |
| **Ustawienia** | Łańcuch AI (primary/fallback, wybór modelu z listy pobranej z routera), progi alertów, usługa powiadomień, polityka kosztu nabycia (0.2.0) |

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

> **Klauzula:** kalkulator dywidend liczy na **bieżących** ustawieniach procentowych (stawka
> traktatowa, Belka), nie na zamrożonym kursie NBP z dnia poprzedzającego wypłatę wymaganym przez
> pełne rozliczenie PIT-38 — to dochodzi w wydaniu 0.2.0. To narzędzie pomocnicze, nie doradztwo
> podatkowe; wartości potwierdź z własnym rozliczeniem lub doradcą przed wpisaniem do deklaracji.

## Serwisy

Dodatek nie rejestruje własnych usług Home Assistant (`services.yaml`) — sterowanie odbywa się
przez web UI na ingressie (formularze portfela/dywidend, przycisk „Przeanalizuj teraz”) oraz przez
opcje konfiguracyjne Supervisora.

## Licencja

Do ustalenia.
