# Krok 28 — UX/mobile + wykresy (`nokia_tracker` 0.12.0)

## Context

`nokia_tracker` jest na **0.11.0** (wydane 2026-08-16, live, 820 testów, slug
`5f59858c_nokia_tracker`). Fale 0.8.1–0.11.0 zamknięte. Następna pozycja roadmapy
(`docs/ROADMAP.md:174-195`) to **0.12.0 / krok 28 — UX/mobile + wykresy** — retrofit
wszystkich istniejących stron (nowe strony z fal 25–27 miały być mobile-first od razu,
ale nie były w pełni domknięte pod tym kątem — patrz §1).

Sześć niezależnie weryfikowalnych podkroków (28.1–28.6), każdy osobny commit,
Playwright po każdym większym kroku (1920px + 390px + dark), nie tylko na końcu.

## 0. Ustalenia ze zwiadu po kodzie (nie z domysłu)

- **Formatowanie pieniędzy**: `format.py::money()` jest celowo używany tylko na
  `dashboard.html`/`results.html`/`plan.html` (wartości zagregowane). Strony podatkowe
  (`lots`, `sales`, `grants`, `pit38`, `_alloc_detail`) celowo zostają przy
  `'%.Nf'|format(...)` — bajtowa zgodność z wyciągiem/PIT-38, **nie dotykać**.
- **Dual-currency w danych — trzy poziomy dojrzałości**:
  - *Tier A (już policzone obie waluty)*: `portfolio.py::dashboard_buckets()` — każdy
    kubełek ma `value_eur` **i** `value_pln`; `analytics/history.py::rebuild()` —
    `portfolio_history` ma `market_value_eur` **i** `market_value_pln` per dzień.
    Czysty toggle wyświetlania, zero zmian w silniku.
  - *Tier B (PLN-only dziś, EUR tanie do dodania)*: `advisor.py` (krok 26) —
    `forfeit_value_pln`, `timeline.buckets.*.value_pln`, `timing_result.*.forfeit_value_pln`,
    `conc.employer_value_pln` — liczone jako `qty × cena_eur × kurs_nbp`; cena EUR i data
    są już pod ręką w miejscu liczenia, więc dodanie `_eur` obok `_pln` to jedna linia,
    nie nowa logika. Wymaga zmiany `advisor.py` + rozszerzenia `test_advisor.py`.
  - *Tier C (świadomie tylko PLN, poza toggle)*: `attribution.py::decompose()` —
    „efekt walutowy EUR/PLN" jest jednym ze składników rozbicia; widok w EUR
    fałszowałby dekompozycję (nie da się pokazać kwoty w walucie, której zmiana jest
    jednym z wyjaśnianych czynników). Wszystkie strony podatkowe (Tier C też z
    definicji, patrz wyżej). Wykluczenie udokumentowane komentarzem w kodzie, tym
    samym wzorcem co istniejący komentarz w `format.py:1-8`.
  - **Odkryty realny brak**: `docs/ROADMAP.md:110-111` (krok 25) obiecywał na
    `/wyniki` „przełącznik EUR/PLN" przy krzywej wartości — nigdy nie zaimplementowany
    (`grep -i eur templates/results.html` → zero trafień, wszystko tylko `_pln`).
    Krok 28.1 domyka ten dług, globalnie zamiast lokalnie dla jednego wykresu.
- **`localStorage`**: jedyny istniejący precedens to `nt.chart.range`
  (`static/app.js:25-26,88`, użyty w `initPriceChart`). Wzorzec do powielenia dla
  `nt.currency` i (28.3) `nt.tax_year`.
- **Rok podatkowy**: dziś każda strona ma własny formularz `?year=` (`pit38.html:12-24`,
  `sales.html:17-27`, `wizard.html:10-16`); `results.html`/`grants.html`/`dividends.html`/
  `lots.html` nie mają w ogóle selektora (pokazują cały zakres). `_pit38_report_for_request`
  (`web.py:1247-1253`) już ma fallback `request.args.get("year") or cfg["tax_year"] or now().year`
  — wzorzec do uogólnienia.
- **Wykresy — wzorzec do powielenia**: canvas w template + `NT.initXxxChart(id, dane)`
  w `static/app.js`, kolory przez `cssVar("--series-N")`, `responsive:true`,
  `maintainAspectRatio:false`. Dwa istniejące przykłady: `initPriceChart`
  (`static/app.js:20-94`, fetch z API) i `initValueChart` (`static/app.js:249-282+`,
  dane wstrzyknięte `|tojson` z serwera). Nowe wykresy idą drugą drogą (dane już są
  policzone server-side w danym request, nie potrzeba osobnego API endpointu).
- **`_macros.html`**: tylko `stat()` (`:30-38`, białe znaki między `value`/`unit`
  pinowane przez `test_web.py:379` — **nie ruszać formatu**) i `tax_disclaimer()`
  (`:51-69`, warianty pinowane testami). Zero komponentu empty-state/skeleton dziś —
  wzorzec to ad hoc `<p class="muted">Brak ...</p>`.
- **`base.html`**: `<nav>` = brand + `.nav-links` (linki + `NAV_GROUPS` dropdowny) +
  `.nav-version`. Global toggle/selector slotują się między `.nav-links` i
  `.nav-version`. Cache-busting już poprawny (`?v={{version}}`, `_no_cache` w
  `web.py:139-142` na `text/html`/`application/json`) — nowe statyki trzymają wzorzec.
- **Print**: `@media print` już istnieje (`app.css:354-361`) i już działa na
  `/pit38?print=1`. Krok 28.6 tylko rozszerza `print_mode` na `/wyniki` i `/plan`
  (dodać przycisk „Widok do druku" + `request.args.get("print")=="1"` w tych routach),
  nie buduje niczego od zera.

## 1. Krok 28.1 — Globalny przełącznik waluty PLN/EUR

**Decyzja architektoniczna**: nie ma jednego Jinja *filtra* wybierającego walutę (to
wymagałoby przekazywania preferencji do każdego renderu server-side i unieważniałoby
cache), tylko jeden **macro + jeden globalny toggle sterowany klient-side** — dane
(Tier A i B) są już renderowane w obu walutach do DOM, JS przełącza widoczność. Brak
JS = domyślnie EUR (obecne zachowanie zachowane, konwencja "EUR primary" z kroku 23).

- Nowy macro `dual_money(value_eur, value_pln, unit_eur='EUR', unit_pln='zł')` w
  `_macros.html`, renderujący oba spany: `<span class="cur cur-eur">…</span><span
  class="cur cur-pln">…</span>`. Zastępuje istniejący `pf_money()` z `dashboard.html:9-15`
  (ten sam kontrakt, przenosi się do wspólnego miejsca — zero duplikacji między
  dashboard/wyniki/plan).
- `advisor.py`: dodać `forfeit_value_eur`, `value_eur` w kubełkach timeline,
  `forfeit_value_eur` w `timing_result`, `employer_value_eur` w `conc` — Tier B, patrz
  §0. TDD: rozszerzyć `test_advisor.py` przed zmianą kodu.
- `analytics/attribution.py` i strony podatkowe: **bez zmian**, `dual_money` tam nie
  wchodzi (Tier C, komentarz w kodzie wyjaśniający dlaczego).
- `base.html`: przycisk `<button id="currency-toggle" class="btn-toggle">EUR/PLN</button>`
  między `.nav-links` a `.nav-version`. Inline skrypt w `<head>` (przed CSS/JS, żeby
  uniknąć FOUC) czyta `localStorage.getItem('nt.currency')` i ustawia
  `document.documentElement.dataset.currency = 'eur'|'pln'` **przed pierwszym paintem**.
  `static/app.js::initCurrencyToggle()` podpina klik: flip + zapis do localStorage +
  ustawienie atrybutu (bez przeładowania strony — czysty CSS toggle).
- `app.css`: `html[data-currency="eur"] .cur-pln{display:none}` i odwrotnie (`.cur-eur`
  domyślnie widoczny gdy atrybut brak — zgodne z „no-JS = EUR").
- **Test Playwright**: kliknięcie toggle na `/`, `/wyniki`, `/plan` w 1920px i 390px,
  sprawdzić że wartości faktycznie się zamieniają (nie tylko że przycisk istnieje).

## 2. Krok 28.2 — Tabele → karty poniżej 430px

Dotyczy: `lots.html` (2 tabele, 8 kolumn łącznie), `sales.html` (1, 5 kol.),
`grants.html` (3 tabele, 12 kol. łącznie), `dividends.html` (1, 5 kol.), `news.html`
(1, 2 kol.). Dziś żadna nie ma `.table-wrap` (klasa istnieje w CSS od dawna,
`app.css:134`, ale nieużywana w tych szablonach — przewijają się poziomo bez wrappera
albo w ogóle nie mieszczą się na 390px).

- **Nie usuwać `<table>`** — media query `@media (max-width: 430px)` przełącza
  wyświetlanie: `.table thead{display:none}`, `.table tr{display:block; ...karta...}`,
  `.table td{display:flex; justify-content:space-between}` z `content: attr(data-label)`
  na pseudo-elemencie `::before` (wymaga dodania `data-label="…"` do każdego `<td>` w
  tych 5 szablonach — mechaniczna, ale rozległa zmiana, 5 plików).
  Ten wzorzec (CSS-only card flip, żadnego nowego JS) zachowuje istniejące
  sortowanie/filtrowanie (28.6) bez duplikowania znaczników.
- Owinąć każdą z tych tabel w `.table-wrap` (już zdefiniowane, tylko nieużywane) dla
  szerokości pośrednich (430–900px), żeby zamiast łamania layoutu był świadomy scroll.
- **Test**: Playwright 390px na wszystkich pięciu stron, screenshot + sprawdzić że
  żaden element nie wychodzi poza viewport (`browser_evaluate` scrollWidth vs
  clientWidth = 0 różnicy).

## 3. Krok 28.3 — Globalny selektor roku podatkowego

- Jeden `<select>` w `base.html` (obok przycisku waluty), lista lat z
  `_years_with_data()` (`web.py:1273-1285`, już istnieje, przenieść wywołanie do
  kontekstu współdzielonego przez wszystkie route'y zamiast tylko pit38/wizard).
  Wybór zapisywany do `localStorage nt.tax_year` **i** wysyłany jako `?year=` przy
  nawigacji (progresywne ulepszenie — bez JS formularz nadal działa przez per-stronowe
  selecty, które zostają jako fallback tam gdzie już są: `pit38.html`, `sales.html`,
  `wizard.html`; global selector dodatkowo dokłada rok do stron, które go dziś nie mają:
  `results.html`, `grants.html`, `dividends.html`, `lots.html`).
- `web.py`: wspólna funkcja `_resolve_tax_year(request, cfg)` zastępująca zduplikowaną
  logikę w `sales_get`/`_pit38_report_for_request` — jeden punkt prawdy, zero zmiany
  zachowania dla stron, które już to miały (regression-safe, testy istniejące muszą
  przejść bez zmian).
- **Ryzyko**: `results.html`/`grants.html` dziś pokazują dane bez filtra rocznego
  (całościowo) — dodanie selektora zmienia domyślne zachowanie tych stron (pokażą
  domyślnie bieżący rok zamiast wszystkiego). To jest **zmiana zachowania**, nie tylko
  UI — do potwierdzenia przy implementacji, czy `/wyniki` (krzywa wartości) w ogóle
  powinna filtrować się rocznie (krzywa ma sens ciągła). Decyzja: **rok filtruje tylko
  tabelę „zwroty rok po roku" i wykres atrybucji na `/wyniki`, nie krzywą wartości**
  (ta zostaje zawsze pełna, z zakresami czasowymi jak dziś — `chart_ranges` to inny,
  niezależny mechanizm). `grants.html`/`dividends.html`/`lots.html` dostają selektor
  jako **dodatkowy filtr opcjonalny** (domyślnie "wszystkie lata", nie zawężają się
  automatycznie) — zero zmiany domyślnego zachowania tam, gdzie dziś nie ma selektora.

## 4. Krok 28.4 — Nowe wykresy Chart.js

Wszystkie idą wzorcem `initValueChart` (dane `|tojson` z serwera, nie osobne API):

- **Słupki dywidend rok/rok** na `dividends.html` — `NT.initDividendBarChart(id, data)`,
  dane z istniejącego `dividends` query (agregacja po roku, już liczona gdzieś do
  sumy — sprawdzić czy jest gotowa funkcja czy trzeba dopisać `groupby` w Pythonie,
  zero nowej logiki podatkowej).
- **Oś czasu vestingu** na `plan.html` — pozioma oś (nie tabela), dane z
  `unvested_summary()` (już istnieje z kroku 26/`advisor.py`), renderowana jako
  Chart.js horizontal bar/scatter po datach transz.
- **Waterfall PIT-38** na `pit38.html` — przychód → koszt → dochód → strata z lat
  ubiegłych → podatek → na rękę. Chart.js nie ma natywnego typu "waterfall" — budować
  jako stacked bar z przezroczystym segmentem "offset" (znany trik, zero nowej
  zależności, zgodnie z `BLUEPRINT §1` wykluczającym nowe biblioteki).
- **Donut trzech kubełków portfela** na `dashboard.html` — dane już policzone przez
  `dashboard_buckets()` (wolne/ograniczone/zablokowane), tylko brakuje wizualizacji.
- **Test twardy**: dla waterfallu — suma wysokości segmentów (bez offsetów) musi
  odpowiadać faktycznemu `tax_pln`/`income_pln` z `annual_report()`, żeby wykres nie
  kłamał liczbowo (ten sam standard co `attribution.py` w kroku 25: suma komponentów
  = całość, co do grosza).

## 5. Krok 28.5 — „Dziś warto wiedzieć" na pulpicie

Nowa funkcja `dashboard_insights.py::today_worth_knowing(conn, ...)` (albo metoda w
istniejącym `portfolio.py`, do zdecydowania przy implementacji którędy dane już płyną
do `web.py::dashboard`), **deterministyczna, bez AI**:

1. Największa zmiana — dziś vs wczoraj na `quotes` (już pobierane dla wykresu ceny).
2. Najbliższe zdarzenie — min(najbliższy vesting z `unvested_summary()`, najbliższa
   znana data dywidendy z rejestru wypłat) — cokolwiek bliżej w czasie.
3. Sygnał podatkowy — jeśli dostępna strata z lat ubiegłych (krok 27,
   `loss_available_pln`) i jest zysk do końca roku, jedno zdanie o możliwości
   optymalizacji; inaczej pomijane (brak sygnału = brak zdania, nie sztuczne wypełnianie).

Renderowane jako 1-3 zdania nad kartą Portfel na `dashboard.html`. **TDD obowiązkowe**
(to jest logika, nie markup) — fixtury: brak nadchodzących zdarzeń, brak straty
dostępnej, wszystkie trzy sygnały naraz.

## 6. Krok 28.6 — Polish

- **Sortowanie/filtrowanie kolumn**: czysty JS (`static/app.js`), atrybut
  `data-sort-key` na `<th>`, klik przełącza `asc/desc`, żadnej zmiany server-side.
  Zakres: te same 5 tabel co 28.2.
- **Paginacja newsów**: `news.html` — dziś ładuje wszystko naraz (sprawdzić skalę przy
  implementacji — jeśli lista jest krótka w praktyce, paginacja może być "load more"
  zamiast stron, taniej w implementacji).
- **Sticky header** z ceną/wartością portfela — CSS `position: sticky` na istniejącym
  elemencie nagłówka ceny na dashboardzie, zero nowego markupu.
- **Spójne stany puste**: nowy macro `empty_state(message, icon=None)` w
  `_macros.html`, zastępuje ad hoc `<p class="muted">` w miejscach gdzie dziś
  występuje (bez zmiany treści komunikatów — testy pinują treść, `_macros.html`
  wywołanie musi wyrenderować identyczny tekst, patrz `test_lots_page_empty_state`
  i pozostałe pinowane testy z §0).
- **Szkielety ładowania wykresów**: prosty CSS `.chart-skeleton` (pulsująca ramka)
  pokazywany do czasu `Chart.js` init (JS zdejmuje klasę po narysowaniu wykresu) —
  nowy wzorzec, nic do powielenia.
- **Widok do druku dla `/wyniki` i `/plan`**: powielić dokładnie wzorzec z `pit38.html`
  (`print_mode=request.args.get("print")=="1"`, przycisk „Widok do druku"), zero
  nowego CSS (istniejący `@media print` już ogólny, ewentualne doprecyzowania tylko
  jeśli screenshot pokaże złamany layout).

## 7. Kolejność i zależności między podkrokami

```
28.1 (waluta)   ── niezależny, robiony pierwszy (najbardziej przekrojowy — nav/macros)
28.2 (karty)    ── niezależny od 28.1
28.3 (rok)      ── niezależny, ale współdzieli miejsce w nav z 28.1 — robić po 28.1
                   żeby nie przepisywać base.html dwa razy
28.4 (wykresy)  ── wymaga danych z 25/26/27 (już są), niezależny od 28.1-28.3
28.5 (insights) ── wymaga 28.4? NIE — czysta logika, niezależny
28.6 (polish)   ── wymaga 28.2 (sort/filtr działa na tabelach z 28.2) i 28.4
                   (skeleton potrzebuje wykresów) — robiony na końcu
```

Realna kolejność implementacji: **28.1 → 28.3 → 28.2 → 28.4 → 28.5 → 28.6** (waluta i
rok razem, bo oba w nav; potem karty; wykresy i insighty równolegle konceptualnie ale
sekwencyjnie w tej sesji; polish na końcu bo zależy od reszty).

## 8. Krytyczne pliki

| Podkrok | Nowe | Modyfikowane |
|---|---|---|
| 28.1 | — | `_macros.html` (`dual_money`), `base.html`, `app.css`, `app.js`, `advisor.py`, `test_advisor.py`, `dashboard.html`, `results.html`, `plan.html` |
| 28.2 | — | `lots.html`, `sales.html`, `grants.html`, `dividends.html`, `news.html`, `app.css` |
| 28.3 | — | `base.html`, `web.py` (`_resolve_tax_year`), `app.js`, `results.html`, `grants.html`, `dividends.html`, `lots.html` |
| 28.4 | — | `app.js` (4 nowe `initXxxChart`), `dividends.html`, `plan.html`, `pit38.html`, `dashboard.html`, `web.py` (kontekst danych) |
| 28.5 | `dashboard_insights.py` | `dashboard.html`, `web.py`, `test_dashboard_insights.py` (nowy) |
| 28.6 | — | `app.js`, `app.css`, `_macros.html` (`empty_state`), `news.html`, `results.html`, `plan.html`, `web.py` |

## 9. Weryfikacja (per podkrok, ten sam standard co krok 25-27)

1. TDD dla logiki (28.1 Tier B advisor, 28.4 waterfall sanity-check, 28.5 insights) —
   testy przed kodem.
2. `tax/` nietknięte w tej fali — jeśli którykolwiek podkrok dotknie `tax/*.py`,
   czerwona flaga, zatrzymać się i przemyśleć (roadmapa nie przewiduje zmian
   silnikowych tutaj, tylko `advisor.py`, który jest już poza `tax/`).
3. Cała istniejąca suita (820 testów) zielona po każdym podkroku, nie tylko na końcu —
   szczególnie testy pinujące markup (`test_web.py::test_stat_macro_*`, warianty
   `tax_disclaimer`, empty-state teksty).
4. Playwright po 28.1, 28.2, 28.3 i 28.4 osobno (nie czekać do końca fali) — 1920px +
   390px + dark, screenshot **i** `browser_console_messages(error)`. Krok 28.5/28.6
   mogą być zweryfikowane razem z finalnym przejściem przed wydaniem.
5. Sweep PII na diffie przed pushem (repo publiczne).
6. README.md + CHANGELOG.md w tym samym wydaniu.
7. Wdrożenie: bump `config.yaml` → push → `gh release create` (published) →
   `homeassistant.update_entity` na `update.nokia_tracker_update` → poll `ha_get_addon`
   → `ha_manage_addon(action="update")`. **Nigdy** cyklu uninstall/reinstall.

## 10. Co świadomie zostaje poza tą falą

- Realny endpoint eksportu/API dla wykresów (28.4 używa danych już policzonych w
  request, nie nowego `/api/*` — zgodnie z BLUEPRINT §1, minimalizm zależności).
- Zmiana domyślnej waluty (EUR jako primary zostaje domyślne bez JS — zgodność wstecz).
- Migracja bazy — ta fala jest czysto prezentacyjna + jedno rozszerzenie `advisor.py`
  (Tier B), zero nowej tabeli.
