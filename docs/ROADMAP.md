# Roadmapa rozwoju `nokia_tracker` — 0.8.1 → 1.0.0

## Context

`nokia_tracker` jest od 0.2.0 **feature-complete wobec `docs/BLUEPRINT.md`** — wszystkie 15
zaplanowanych kroków wydane, plus kroki 16–23 (przejrzystość FIFO, rejestr sprzedaży, uproszczenie
UI, push/digest, kafelki portfela). Stan na 0.8.0: 632 testy, 27 tras web, ~60 encji MQTT, silnik
FIFO + PIT-38 z trzema politykami kosztu, importer Computershare, łańcuch AI z circuit breakerem.
Blueprint jest wyczerpany — zostaje w nim tylko opcjonalny krok 16 (inny broker), którego nie ma po co
robić.

Ta roadmapa odpowiada na pytanie „co dalej”, na podstawie:
1. **realnych luk w kodzie** potwierdzonych czytaniem źródeł (nie domysłów) — brak jakiejkolwiek
   miary zwrotu w czasie (`grep -i "xirr\|twr"` → zero trafień), brak rozliczania strat z lat
   ubiegłych (`grep -i "strat"` w `tax/` → jedno trafienie, w komentarzu), brak historii wartości
   portfela (żadnej tabeli snapshotów), brak eksportu/kopii danych (`/pit38/export.*` to jedyne
   eksporty, obejmują sam PIT-38, nie bazę),
2. **benchmarku funkcji z narzędzi klasy premium** (sekcja „Co mają inni”),
3. **decyzji użytkownika** podjętych w tej sesji: wszystkie cztery fale w kolejce, zakres pozostaje
   **wyłącznie Nokia** (bez rozrostu w wielo-instrumentowy portfel), AI rozwijane w kierunku
   **czatu nad własnymi danymi**.

Cel końcowy: 1.0.0 zarezerwowane na „feature complete + sprawdzone w boju” — po fali czatu
i po jednym pełnym sezonie rozliczeniowym na nowym silniku.

---

## Co mają inni (benchmark premium) — i czego nam brakuje

| Funkcja | Kto to ma | Stan w `nokia_tracker` |
|---|---|---|
| IRR/XIRR (zwrot ważony pieniądzem) i TWR (ważony czasem) | Sharesight, IBKR PortfolioAnalyst, Getquin | **brak** — jest tylko `unrealized_pnl_pct` i `total_return_pct` (punktowe) |
| Krzywa wartości portfela w czasie | wszyscy | **brak** — żadnej historii, tylko wykres ceny akcji |
| Atrybucja zwrotu (kurs / dywidendy / FX) | Sharesight, IBKR | **brak** — a dla inwestora rozliczającego się w PLN efekt EUR/PLN to często większa część wyniku niż kurs akcji |
| Porównanie z benchmarkiem przy **tych samych** przepływach | Sharesight, Getquin | częściowo — jest `rel_perf_*` i `beta_60d` dla samej akcji, nie dla portfela |
| Kalendarz i prognoza dywidend | DivvyDiary, Snowball Analytics | częściowo — jest rejestr wypłat, brak prognozy i kalendarza ex-div |
| Kalendarz wyników + konsensus analityków | Koyfin, Simply Wall St, Seeking Alpha | **brak** (backlog — użytkownik wybrał czat zamiast tego) |
| Wycena fundamentalna (DCF/fair value, mnożniki vs peers) | Simply Wall St, Koyfin | **brak** (backlog) |
| Raporty podatkowe gotowe do deklaracji | Sharesight (AU/NZ/UK/CA) | **mocna strona** — PIT-38 + PIT/ZG + ślad do numeru tabeli NBP; brak strat z lat ubiegłych |
| Planowanie sprzedaży pod podatek | Sharesight, Parqet | częściowo — jest „co jeśli sprzedam wszystko”, brak „ile sprzedać, żeby…” i porównania rok bieżący vs styczeń |
| Obsługa planów pracowniczych (ESPP/RSU) — koszt utraty dopłaty, harmonogram | **nikt tego nie robi dobrze** (Sharesight/Getquin traktują to jak zwykły zakup) | częściowo — jest ostrzeżenie tekstowe o ograniczeniu zbycia; brak wyceny „ile tracę sprzedając dziś” |
| Ryzyko koncentracji (akcje pracodawcy vs reszta majątku) | Getquin, Parqet (dywersyfikacja) | **brak** |
| Czat/asystent nad własnym portfelem | najnowsze wersje Getquin/Simply Wall St | **brak** — AI robi tylko scoring newsów, briefing i rekomendację |
| Eksport/kopia własnych danych | wszyscy | **brak** — a historia tego dodatku zna dwa przypadki wyczyszczenia SQLite przy przeinstalowaniu |

**Wniosek:** przewaga tego dodatku nie leży w tym, w czym premium jest dobre (ładne wykresy,
fundamenty), tylko w dwóch rzeczach, których premium **nie umie**: polskie rozliczenie akcji
pracowniczych i dostęp do własnych, surowych danych z wyciągu. Roadmapa świadomie dokłada
brakującą warstwę analityczną (bo bez niej nie da się odpowiedzieć „czy mi się to opłaca”),
ale nie próbuje gonić Koyfina.

---

## Fale

### 0.8.1 (krok 24) — kopia zapasowa, przywracanie, zdrowie danych  · ~1 dzień

Najtańsza fala, robiona pierwsza, bo chroni realne dane podatkowe użytkownika (5 lat wyciągów,
zaksięgowane sprzedaże, dywidendy) — a historia tego dodatku zna dwa wyczyszczenia `/data`
przy cyklu przeinstalowania (patrz `reference_supervisor_git_addon_rebuild`).

- `GET /dane/eksport.zip` — pełny zrzut: `nokia.db` (przez `sqlite3.Connection.backup()`, spójny
  bez zatrzymywania schedulera) + `manifest.json` (wersja, `schema_version`, data, sumy kontrolne)
  + czytelne CSV kluczowych tabel (`lots`/`sales`/`sale_allocations`/`dividends`/`grants`/`vests`).
- `POST /dane/import` — przywrócenie z takiego ZIP-a, z **podglądem różnicy przed zapisem**
  (ile lotów/sprzedaży/dywidend przybędzie/zniknie) i wymuszonym potwierdzeniem. Nigdy nie
  nadpisuje w ciemno — ten sam kontrakt co importer PDF.
- Nocny auto-snapshot do `${BACKUP_SHARE}/backup/nokia_YYYY-MM-DD.zip` z rotacją (ostatnie 14) —
  `/share` jest już zamontowane (`map: share:rw`) i używane przez `auto_import_pdf_share`.
- Karta „Stan systemu” na `/ustawienia`: ostatni udany fetch per provider, stan circuit breakerów,
  zużycie limitu AI (`ai_usage`), liczba nierozstrzygniętych konfliktów, data ostatniej kopii.

**Pliki:** nowy `backup.py`, trasy w `web.py`, job w `main.py`, `templates/settings.html`.
**Ryzyko:** import cudzej/starszej bazy — blokada po `schema_version` z `manifest.json`,
migracja w górę dozwolona, w dół odrzucana.

---

### 0.9.0 (krok 25) — Wyniki: XIRR, krzywa wartości, atrybucja, benchmark  · ~3–4 dni

Największa merytoryczna dziura. Wszystko liczone **wstecz z danych, które już są** — nie wymaga
czekania na nowe pomiary.

**Warunek wstępny — gęsta seria kursów NBP.** `nbp_rates` (PK `date`) jest dziś rzadka: zawiera
tylko daty, o które ktoś zapytał przy zdarzeniu podatkowym. Do dziennej krzywej w PLN potrzeba
serii ciągłej. NBP API obsługuje zakresy (`/api/exchangerates/rates/a/eur/{od}/{do}/`, max 367 dni
na żądanie) → backfill 5 lat = ~6 żądań, jednorazowo + nocne domykanie. Rozszerzenie
`providers/fx_nbp.py` o `backfill_range()`; istniejące `rate_on_or_before()` (semantyka „ostatnia
opublikowana tabela”) działa po zagęszczeniu bez zmian.

**Nowy pakiet `analytics/`:**
- `history.py::rebuild()` — dzienna rekonstrukcja stanu portfela od pierwszego lotu do dziś:
  ilość = loty nabyte do dnia D minus `sale_allocations` do dnia D; wycena = ostatnie znane
  `quotes.close` ≤ D; PLN po kursie NBP z D. Materializowane w nowej tabeli `portfolio_history`
  (migracja v6), przeliczane po każdej zmianie danych i nocnym jobem. ~1300 wierszy na 5 lat.
- `returns.py::xirr()` — Newton + bisekcja awaryjna, czysty Python (bez numpy — `BLUEPRINT` §1
  świadomie wyklucza pandas/numpy z powodu musl/armv7). Dwa warianty, oba pokazywane obok siebie:
  **XIRR na własnych wpłatach** (przepływy = tylko gotówka realnie wydana na loty `own`; akcje
  `matched`/`lti`/`dividend_drip` wchodzą jako darmowy przypływ — to pokazuje prawdziwą stopę
  zwrotu planu pracowniczego, zwykle absurdalnie wysoką i to jest poprawne) oraz **TWR**
  (neutralizuje moment wpłat — jedyna miara uczciwie porównywalna z indeksem).
- `attribution.py::decompose()` — rozbicie całkowitego zysku w PLN na:
  (a) zmiana kursu akcji, (b) dopłata ESPP 50%, (c) akcje LTI, (d) dywidendy netto (gotówka + DRIP),
  (e) **efekt walutowy EUR/PLN**. Kryterium akceptacji jest twarde: test sprawdza, że suma
  komponentów równa się całkowitemu zyskowi z dokładnością do groszy — inaczej rozbicie jest
  ozdobnikiem, nie liczbą.
- `benchmark.py::counterfactual()` — „gdyby te same wpłaty (co do dnia i kwoty) poszły w OMXH25 /
  Ericssona”: dzienne notowania obu są już backfillowane 5 lat w `quotes`.

**UI — nowa strona `/wyniki`:** krzywa wartości (obszar, przełącznik EUR/PLN, zakresy jak na
pulpicie), kafelki XIRR/TWR/zysk całkowity, wykres słupkowy atrybucji, krzywa benchmarku na tym
samym wykresie, tabela zwrotów rok po roku. Plus 4 nowe sensory MQTT
(`xirr_own_pct`, `twr_pct`, `portfolio_value_pln_history` jako atrybut, `fx_effect_pln`).

**Pułapka do przetestowania jawnie:** dni bez notowania (święta giełdowe w Helsinkach ≠ dni wolne
NBP) — krzywa musi używać „ostatniej znanej” wartości po obu stronach niezależnie, nie zerować się.

---

### 0.10.0 (krok 26) — Doradca planu pracowniczego  · ~3 dni

Jedyna część roadmapy, której **nie da się kupić** — i najbliższa realnej decyzji użytkownika.

- **„Ile tracę, sprzedając dziś”** — dla każdego lotu `own` z ograniczeniem zbycia (reguła już
  istnieje: `tax/grants.py::restricted_own_summary()` — lot `own` jest ograniczony wtedy, gdy
  istnieje transza `pending` z tą samą datą alokacji) policz wartość **przepadającej dopłaty 50%**
  po cenie bieżącej: „sprzedaż tych 142,73 akcji dziś kosztuje Cię dodatkowo X zł utraconego
  dopasowania, uwalnianego za N dni”. Dziś jest tylko zdanie ostrzegawcze bez kwoty.
- **Harmonogram vestingu jako oś czasu** — poziomy timeline transz ESPP/LTI (nie tabela), z wyceną
  bieżącą per transza, sumami „co wpada w tym kwartale / roku”, wyróżnieniem zaległych. Dane w
  całości z `unvested_summary()`; to praca prezentacyjna, nie silnikowa.
- **Planer ESPP** — „wpłacam X EUR/mc przez N miesięcy przy cenie P (suwak: bieżąca, ±20%, własna)”
  → ile akcji własnych, ile dopasowanych, wartość na koniec, podatek przy sprzedaży wg aktywnej
  polityki. Zbudowany na istniejącym `tax/whatif.py::simulate_sale` (i wydzielonym z niego
  `_plan_fifo`), nie na nowej matematyce.
- **Ryzyko koncentracji** — „akcje pracodawcy to X% Twojego majątku”, gdzie „reszta majątku” to
  albo liczba w ustawieniach, albo (lepiej) **encja HA** wskazana w nowej opcji `net_worth_entity`
  — dodatek ma `homeassistant_api: true` i gotowy `ha_client.py`, więc odczyt stanu encji to kilka
  linii. Ostrzeżenie zapala się powyżej progu (domyślnie 25%), z jawnym kontekstem: to jednocześnie
  Twój pracodawca, więc ryzyko kursu i ryzyko dochodu są skorelowane.

**UI:** nowa strona `/plan`. Sensory: `forfeit_value_pln`, `concentration_pct`, `vest_this_year_qty`.

---

### 0.11.0 (krok 27) — Podatki: straty z lat ubiegłych + kreator rozliczenia  · ~3 dni

- **Straty z lat ubiegłych (art. 9 ust. 3 ustawy o PIT).** Dziś silnik w ogóle nie zna tego pojęcia
  — rok stratny po prostu daje podatek 0 i strata przepada z pola widzenia. Nowa tabela
  `tax_loss_carryforward` (rok powstania, kwota, kwoty odliczone w kolejnych latach), reguła:
  odliczenie w ciągu 5 kolejnych lat, w jednym roku maksymalnie 50% straty — albo jednorazowo do
  5 000 000 zł, z resztą rozliczaną w pozostałych latach tego okresu przy tym samym limicie 50%.
  **Brzmienie przepisu potwierdzić przy implementacji na aktualnym tekście ustawy** (ten projekt ma
  zasadę potwierdzania podstawy prawnej, nie zakładania jej — patrz sekcja „Podstawa prawna” w
  BLUEPRINT §3a). Strata liczona **per polityka kosztu** — trzy polityki dają trzy różne historie
  strat, więc kolumna musi być trzykrotna, inaczej przełączenie polityki po latach da bzdurę.
  Wynik wchodzi do `tax/pit38.py::annual_report` jako osobna pozycja + do widoku „ile wpisać”.
  Numery pozycji formularza zmieniają się rocznie — **nie hardkodować**, opisywać nazwą pozycji.
- **Kreator rozliczenia rocznego** — `/pit38/kreator`: lista kroków ze stanem zapisanym per rok
  (wgraj wyciąg → rozstrzygnij konflikty → sprawdź saldo vs „Assets by plan” → zweryfikuj sekcję G
  → sprawdź straty z lat ubiegłych → wyeksportuj → przepisz do deklaracji → oznacz rok jako
  zamknięty). Każdy krok wie sam, czy jest spełniony (odpytuje bazę), a nie polega na ptaszku
  klikniętym ręcznie. Rok „zamknięty” blokuje przypadkowe zmiany wstecz (miękko, z możliwością
  odblokowania) — wzorzec sprawdzony w `pv_roi_tracker` (`reconciled month freeze`), ale
  **z wyciągniętą lekcją z tamtego incydentu**: zamknięcie roku nie może zamrażać danych
  wprowadzonych *po* zamknięciu jako „nieistotnych” (patrz `project_pv_roi_audit_0_35_4_0_35_5` —
  tak właśnie zepsuło się `consumed_kwh`). Zamrażamy **liczby raportu**, nie prawo do dopisania
  brakującej transakcji.
- **Optymalizator momentu sprzedaży** — „sprzedaję N akcji dziś vs 2 stycznia”: różnica podatku,
  wpływ na wykorzystanie strat, wpływ na przepadek dopłaty ESPP (spina się z krokiem 26).

---

### 0.12.0 (krok 28) — UX/mobile + wykresy  · ~3 dni

Retrofit istniejących stron (nowe strony z fal 25–27 powstają od razu mobile-first — to warunek
ukończenia każdej z nich, nie osobna praca).

1. **Globalny przełącznik waluty PLN/EUR** w nagłówku, zapamiętany — dziś PLN jest walutą główną
   wyłącznie na karcie „Portfel” (świadoma decyzja z kroku 23), co po dołożeniu `/wyniki` i `/plan`
   przestanie się bronić. Ustawienie sterowane jednym filtrem, bez dublowania szablonów.
2. **Tabele → karty poniżej 430 px** (Loty, Sprzedaże, Granty, Dywidendy, Newsy) — dziś przewijają
   się poziomo, co na telefonie w WebView Companiona jest nieużywalne.
3. **Globalny selektor roku podatkowego** — dziś każdy widok ma własny.
4. **Nowe wykresy**: krzywa wartości (z 0.9.0), słupki dywidend rok po roku, oś czasu vestingu
   (z 0.10.0), **waterfall PIT-38** (przychód → koszt → dochód → strata z lat ubiegłych → podatek →
   na rękę), donut trzech kubełków portfela. Chart.js jest już wpięty lokalnie
   (`static/chart.umd.min.js`) — bez nowych zależności.
5. **„Dziś warto wiedzieć”** — trzy zdania na górze pulpitu: największa zmiana, najbliższe zdarzenie
   (vesting/dywidenda), sygnał podatkowy. Liczone deterministycznie, nie przez AI.
6. Sortowanie i filtrowanie kolumn, paginacja newsów, sticky nagłówek z ceną i wartością portfela,
   spójne stany puste, szkielety przy ładowaniu wykresu, widok do druku dla `/wyniki` i `/plan`.

Cache-busting jest już poprawny (`?v={{ version }}` + `no-store` na HTML/API + badge wersji w
nawigacji) — nowe statyki muszą trzymać ten sam wzorzec, zgodnie z regułą z `CLAUDE.md`.

---

### 1.0.0 (krok 29) — Asystent: czat nad własnymi danymi  · ~3–4 dni

Ostatnia fala, bo ma sens dopiero gdy jest o czym rozmawiać (wyniki, plan, straty).

**Kluczowa decyzja architektoniczna:** `ai/provider.py::analyze()` obsługuje wyłącznie
ustrukturyzowany JSON wg schematu — **nie ma pętli tool-calling** i nie zamierzamy jej dorabiać.
Czat działa więc trójstopniowo i to jest zaleta, nie kompromis:

1. **AI #1 — rozpoznanie intencji.** Pytanie użytkownika → JSON wg schematu
   `{intent: enum, params: {...}}`. Intencje: `podatek_ze_sprzedazy`, `ile_moge_sprzedac`,
   `kiedy_vesting`, `ile_zarobilem`, `dywidendy_w_roku`, `koszt_sprzedazy_teraz`,
   `porownanie_z_benchmarkiem`, `inne`.
2. **Python — obliczenie.** Wywołanie istniejącego silnika: `tax/whatif.py::simulate_sale`,
   `tax/pit38.py::annual_report`, `tax/grants.py::unvested_summary`, `analytics/*`. Zero nowej
   matematyki w tej fali.
3. **AI #2 — sformułowanie odpowiedzi po polsku** na podstawie policzonego JSON-a, z instrukcją
   „nie zmieniaj liczb”. **Liczby i tak renderuje Jinja z wyniku silnika**, nie tekst modelu —
   odpowiedź AI jest warstwą narracyjną wokół tabelki, więc halucynacja kwoty jest strukturalnie
   niemożliwa, a nie tylko „mało prawdopodobna”.

Dodatkowo: tylko odczyt (żadna intencja nie zapisuje do bazy), limit dzienny przez istniejące
`ai_max_calls_per_day` + `ratelimit.py`, historia rozmowy w `chat_log` (do wglądu i do debugowania
złych rozpoznań intencji), degradacja bez AI — `intent: inne` lub brak providera daje deterministyczną
odpowiedź „nie wiem, ale to jest na stronie X”. Klauzula „nie jest to doradztwo podatkowe” pod każdą
odpowiedzią, jak wszędzie indziej.

**UI:** nowa strona `/asystent` + pole szybkiego pytania na pulpicie.

---

## Backlog (świadomie poza falami)

- Kalendarz wyników kwartalnych + konsensus analityków (Finnhub free) obok prognozy AI, z backtestem
  „kto miał rację” — użytkownik wybrał czat zamiast tego; wraca, jeśli prognozy AI okażą się słabe.
- Kalendarz i prognoza dywidend (ex-div, przewidywany roczny dochód).
- Wycena fundamentalna (mnożniki vs Ericsson, prosty DCF).
- Obsługa splitu/konsolidacji akcji — dziś FIFO by się na tym wywrócił; Nokia nie ma tego w planach,
  ale to znana bomba zegarowa: warto mieć chociaż wykrycie („cena zmieniła się o >40% w jednej sesji
  bez newsa — sprawdź split”).
- Klastrowanie newsów o tym samym zdarzeniu (embeddingi) — zablokowane, bo `/v1/embeddings` na
  routerze zwraca 503; odblokuje się samo, gdy użytkownik włączy rodzinę embeddingów.
- Inny broker niż Computershare (krok 16 z BLUEPRINT) — nadal bez powodu.

---

## Kolejność i zależności

```
0.8.1 (kopia)  ──►  niezależna, robiona pierwsza (ochrona realnych danych)
0.9.0 (wyniki) ──►  wymaga gęstej serii NBP;  daje dane dla wykresów w 0.12.0 i dla czatu
0.10.0 (plan)  ──►  wymaga tylko istniejących grants/vests + whatif
0.11.0 (podatki) ─►  niezależna od 0.9/0.10;  optymalizator momentu sprzedaży spina się z 0.10.0
0.12.0 (UX)    ──►  wymaga 0.9.0 i 0.10.0 (bo rysuje ich dane)
1.0.0 (czat)   ──►  wymaga wszystkich (odpowiada na pytania o wyniki, plan i podatki)
```

0.10.0 i 0.11.0 są wzajemnie niezależne — kolejność między nimi można odwrócić, jeśli zbliża się
termin rozliczenia rocznego.

---

## Krytyczne pliki

| Fala | Nowe | Modyfikowane |
|---|---|---|
| 0.8.1 | `backup.py` | `web.py`, `main.py`, `templates/settings.html` |
| 0.9.0 | `analytics/{history,returns,attribution,benchmark}.py`, `templates/results.html` | `db.py` (migracja v6 `portfolio_history`), `providers/fx_nbp.py` (`backfill_range`), `sensors.py`, `publisher.py`, `web.py`, `static/app.js` |
| 0.10.0 | `advisor.py`, `templates/plan.html` | `tax/grants.py` (rozszerzenie, nie przepisanie), `ha_client.py` (odczyt encji majątku), `config.yaml` (`net_worth_entity`, `concentration_warn_pct`), `web.py` |
| 0.11.0 | `tax/losses.py`, `templates/wizard.html` | `db.py` (migracja v7), `tax/pit38.py`, `tax/policy.py`, `web.py` |
| 0.12.0 | — | wszystkie `templates/*.html`, `static/app.css`, `static/app.js`, `templates/_macros.html` |
| 1.0.0 | `ai/chat.py`, `templates/assistant.html` | `ai/prompts.py`, `db.py` (migracja v8 `chat_log`), `web.py` |

Do ponownego użycia, nie pisania od nowa: `format.py` (`money`/`qty`/`pct`), `templates/_macros.html`
(`stat()`, `tax_disclaimer()`), `tax/whatif.py::_plan_fifo`, `tax/grants.py::unvested_summary` /
`restricted_own_summary`, `portfolio.py::dashboard_buckets`, `db.WRITE_LOCK`,
`web.py::_IngressPrefixMiddleware` (każda nowa trasa **musi** używać `url_for()`), `ratelimit.py`.

---

## Weryfikacja

Dla każdej fali, w tej kolejności:

1. **TDD** — testy przed implementacją, każdy nowy moduł. Punkt odniesienia: 632 testy na 0.8.0.
   Kryteria twarde, nie „przechodzi”: suma komponentów atrybucji == całkowity zysk co do grosza;
   XIRR na znanym przepływie zgodny z arkuszem; krzywa wartości na dzień `T` zgodna z
   `position_values()` liczonym niezależnie; strata z lat ubiegłych nigdy nie przekracza limitu 50%
   i nie przeżywa 5 lat.
2. **Cała sekcja `tax/` traktowana jak beton** — każda zmiana w niej wymaga uruchomienia
   `test_tax_*.py` przed i po, zielono w obu punktach (wzorzec z kroku 15: refaktor `_plan_fifo`
   zweryfikowany nietkniętą suitą).
3. **Sprawdzenie na realnych danych przed wdrożeniem** — policzyć oczekiwane liczby ręcznie
   z produkcyjnych sensorów/wyciągów i porównać z tym, co pokaże strona (wzorzec z kroku 21:
   100,0200 zaległych i 142,7294 ograniczonych przewidziane przed deployem i trafione co do cyfry).
4. **Playwright na realnym URL-u ingressu** (1920 px + 390 px + tryb ciemny), screenshot **i**
   `browser_console_messages(error)` — obie rzeczy, screenshot sam nie wystarcza. Ścieżka
   `ws_command: "supervisor/api"` → `ingress_session` → `document.cookie` działa (potwierdzone
   w kroku 21/23), próbować jej **przed** sięganiem po zastępczy proxy GET przez `ha_manage_addon`.
5. **Wdrożenie bezpieczną ścieżką** (add-on trzyma realne dane podatkowe): push → `gh release create`
   z potwierdzeniem `isDraft: false` → `homeassistant.update_entity` na `update.nokia_tracker_update`
   → poll `ha_get_addon` aż `version_latest` się zgodzi → `ha_manage_addon(action="update")`.
   **Nigdy** cyklu uninstall/remove_repository/add_repository/install — on kasuje SQLite.
6. **Sweep PII na diffie przed każdym pushem** — repo jest publiczne, a te fale dotykają danych
   z realnych wyciągów (imię, adres, ID pracownika). Reguła z kroku 13, po realnym near-miss.
7. Po każdej fali: aktualizacja `README.md` (tabele encji + opis stron) i `CHANGELOG.md` w tym samym
   wydaniu — dryf dokumentacji zdarzył się już raz (kroki 12–14 były live i nieudokumentowane).

---

## Pierwszy krok implementacji

Skopiować ten dokument do repo jako `docs/ROADMAP.md` (zasada: plan żyjący tylko w transkrypcie ginie
przy kompaktowaniu), a potem dla każdej fali osobny `docs/PLAN_KROK_<n>_<slug>.md` w momencie jej
rozpoczęcia — dokładnie tak, jak wyglądają kroki 12–23.
