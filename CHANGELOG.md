# Changelog

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
