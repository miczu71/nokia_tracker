# Nokia Tracker 0.5.0 — PLN na pulpicie, podgląd na żywo przy wpisywaniu, przegląd wszystkich stron

## Context

`nokia_tracker` 0.4.0 (krok 17) naprawił szerokość layoutu i skondensował `/sales` + `/pit38`.
Zostały trzy problemy, które zgłosił użytkownik, i jeden dług, który wyszedł przy audycie:

1. **Pulpit nie mówi w złotówkach.** Konto jest w EUR, ale zobowiązanie podatkowe i intuicja
   użytkownika są w PLN. `portfolio.py:86-97` **już liczy** `market_value_pln`, `cost_basis_pln`,
   `unrealized_pnl_pln` na każdym żądaniu — i oba szablony (`dashboard.html`, `portfolio.html`)
   je wyrzucają do kosza. Trzeba je tylko wyrenderować.

2. **Wpisywanie dywidend i transakcji jest „na ślepo".** Wpisujesz datę i kwotę, klikasz
   „Dodaj", i dopiero po przeładowaniu widzisz, jaki kurs NBP się zamroził i ile z tego wyszło
   w PLN. Jak coś jest nie tak — nie ma jak cofnąć (nie ma route'u DELETE ani dla lotu, ani dla
   dywidendy). To jest odwrotność przejrzystości, którą krok 16 wprowadził w `/sales`.

3. **Nawigacja ma 11 płaskich linków** — na telefonie zawija się na dwie linie.

4. **Audyt wykazał realną niespójność danych, nie tylko kosmetykę** (szczegóły niżej,
   „Znaleziska audytu"): strona `/dywidendy` pokazuje **dwie różne odpowiedzi podatkowe
   40 pikseli od siebie** — kafelki liczone przez `sensors.dividends_values()` po kursach
   bieżących w EUR, a tabela pod nimi przez `compute_dividend_tax_pln()` po kursach NBP
   zamrożonych. Do tego kafelek „Yield on cost" u każdego użytkownika po imporcie pokazuje
   na stałe `—`, bo liczy się z porzuconych pól ręcznych.

**Cel 0.5.0:** złotówki widoczne tam, gdzie się o nich myśli; każdy formularz pokazuje wynik
**zanim** zapiszesz; jedna nawigacja zamiast jedenastu; jedna matematyka dywidendowa zamiast dwóch.

**Decyzje użytkownika** (potwierdzone przed planem):
- PLN na pulpicie: **druga linia pod kwotą EUR** (`≈ 20 105 zł`), po kursie bieżącym Yahoo/ECB,
  z jawnym oznaczeniem, że to NIE kurs NBP z tabeli podatkowej;
- wpisywanie: problemem jest **brak podglądu wyniku** — nie liczba pól, nie domyślne wartości,
  nie rozrzucenie po stronach. Formularze zostają tam, gdzie są, w obecnym kształcie;
- nawigacja: **grupowanie w 5 sekcji**, strony zostają (żadnego scalania `/portfolio` czy `/sales`);
- zakres: **pełny przegląd wszystkich stron**, nie tylko trzy powyższe punkty.

---

## Krok 0 — utrwalić plan w repo

Skopiować ten plik do `docs/PLAN_KROK_18_ux_pln.md` (konwencja repo: `PLAN_KROK_12..17`)
**przed** pierwszą zmianą w kodzie.

---

## Krok 1 — fundament: `templates/_macros.html` (nowy plik)

Cztery rzeczy są dziś skopiowane po szablonach. Zanim cokolwiek przebudujemy, wyciągamy je
do jednego miejsca — inaczej każda kolejna zmiana to znowu edycja w czterech plikach.

```jinja
{% macro stat(label, value, unit=None, cls='', sub=None) %}...{% endmacro %}
{% macro tax_disclaimer(variant='default') %}...{% endmacro %}
{% set LOT_TYPE_LABELS = {...} %}
{% set POLICY_LABELS = {...} %}
```

- `stat()` zastępuje ~25 ręcznie klepanych kafelków w `dashboard/sales/pit38/dividends/portfolio`.
  **⚠️ Twardy wymóg:** makro musi emitować `<span class="stat-value">{{v}}<span class="stat-unit">{{u}}</span></span>`
  **bez żadnej białej znaku między spanami** — `tests/test_web.py:379` porównuje ten HTML bajt
  w bajt (`<span class="stat-value">38<span class="stat-unit">PLN</span></span>`). Użyć
  `{%- -%}` konsekwentnie.
- `sub=` to nowa linia pod wartością — nośnik dla `≈ X zł` z kroku 2.
- `tax_disclaimer()` zwija 4 kopie ostrzeżenia (`lots.html:16-21`, `pit38.html:30-35`
  bajtowo identyczne; `dividends.html:10-15` i `sales.html:15-19` to warianty).
  **Musi nadal zawierać frazy `kalkulator pomocniczy` i `nie doradztwo podatkowe`** —
  pinowane przez `test_lots_page_empty_state:186`, `test_dividends_page_shows_disclaimer:766`,
  `test_pit38_page_empty_state_shows_disclaimer:912`.
- `LOT_TYPE_LABELS`/`POLICY_LABELS` importowane przez `{% from "_macros.html" import ... %}`
  w `lots.html`, `sales.html`, `pit38.html`, `settings.html`, `portfolio.html` i **w samym
  `_alloc_detail.html`** — dziś partial ma niejawny kontrakt na kontekst wołającego
  (komentarz `_alloc_detail.html:6-7` sam to przyznaje) i wywala się `UndefinedError`, jeśli
  ktoś go włączy z nowej strony. Etykiety `Tylko własne` / `Własne + dywidenda` /
  `Wszystkie w wartości nabycia` **muszą zostać dosłowne** (`test_lots_page_shows_three_policies_comparison:268`,
  `test_pit38_page_shows_three_policies_and_section_g:915`).

CSS: dodać `.stat-sub { font-size: 12px; color: var(--muted); font-weight: 500 }`.

---

## Krok 2 — PLN na pulpicie (główna prośba #1)

**`web.py::dashboard` (:126-156)** — dołożyć do kontekstu kurs i jego metadane:

```python
eurpln_row = quotes.latest_quote(conn, ids["eurpln"], granularity="daily")
fx_info = {"rate": eurpln_row["close"] if eurpln_row else None,
           "ts": eurpln_row["ts"] if eurpln_row else None,
           "source": eurpln_row["source"] if eurpln_row else None}
```
`values["eurpln_rate"]` już istnieje (`sensors.py:143`) i jest tym samym kursem — `fx_info`
dokłada tylko `ts`/`source`, żeby dało się napisać skąd i z kiedy.

**`templates/dashboard.html`** — karta „Portfel" (:51-80), przez `stat(..., sub=...)`:

| kafelek | linia główna | `sub` |
|---|---|---|
| Ilość | `position.position_qty` | — |
| Wartość rynkowa | `market_value_eur` EUR | `≈ {{ market_value_pln }} zł` |
| Niezrealizowany P&L | `unrealized_pnl_eur` EUR | `≈ {{ unrealized_pnl_pln }} zł` |
| Całkowity zwrot | `total_return_pct` % | — |
| **Koszt bazowy** (nowy) | `cost_basis_eur` EUR | `≈ {{ cost_basis_pln }} zł` |

Wszystkie trzy `_pln` **już są** w `position` (`portfolio.py:94-96`) — zero zmian w Pythonie.
Kafelek „Koszt bazowy" przenosi na pulpit jedyną daną, którą `/portfolio` miał w nadmiarze.

Karta „Kurs" (:16-18) dostaje `sub` = `≈ {{ price_eur * fx_info.rate }} zł`.

Linia dywidend (:77-78) — dołożyć PLN z kroku 4: `Dywidendy netto: X EUR · Y zł (podatek)`.

Pod kartą Portfel jedna linia `.muted`:
> `EUR/PLN {{ '%.4f'|format(fx_info.rate) }} · {{ fx_info.source }} · {{ fx_info.ts[:16] }} — kurs bieżący, nie tabela NBP używana w rozliczeniu podatkowym`

To zdanie jest **wymagane**, nie ozdobne: aplikacja ma teraz dwa równoległe kursy (bieżący
Yahoo/ECB do prezentacji, NBP D-1 zamrożony do podatku) i nic w UI ich dziś nie rozróżnia
poza prozą na `/grants`. Gdy `fx_info.rate` jest `None` — pokazać `kurs EUR/PLN niedostępny`
i pominąć wszystkie linie `sub` (bez `≈ None zł`).

---

## Krok 3 — podgląd na żywo w formularzach (główna prośba #2)

Rdzeń wydania. **Zero nowej logiki podatkowej** — trzy endpointy JSON, każdy woła funkcję,
która już istnieje i jest przetestowana.

### 3a. Endpointy w `web.py`

| Endpoint | Parametry (query) | Silnik (istniejący) |
|---|---|---|
| `GET /api/preview/lot` | `acquired_date, quantity, price_eur, fee_eur` | `fx_nbp.rate_for_event` + `taxtrace.fx_derivation` |
| `GET /api/preview/sale` | `sale_date, quantity, price_eur, fee_eur` | **`taxwhatif.simulate_sale`** (`tax/whatif.py:26`) |
| `GET /api/preview/dividend` | `pay_date, gross_eur, withholding_pct, quantity` | `fx_nbp.rate_for_event` + `taxdiv.compute_dividend_tax_pln` |

Kluczowe własności:
- **`simulate_sale` to gotowy silnik podglądu sprzedaży** — jego docstring (`tax/whatif.py:8-11`)
  wprost mówi „`conn` służy wyłącznie do ODCZYTU, żadnego INSERT/UPDATE/commit", i używa tej
  samej `_plan_fifo()`, którą woła `record_sale()`. Podgląd nie może się rozjechać z zapisem.
- Dla dywidendy: zbudować dict `{"gross_pln": gross_eur * rate, "withholding_pct": ...}`
  i podać do `compute_dividend_tax_pln` — jego docstring (`tax/dividends.py:139`) potwierdza,
  że przyjmuje `dict` tak samo jak `sqlite3.Row`.
- Wszystkie trzy zwracają wspólny kształt: `{ok, nbp_rate, nbp_rate_date, explanation_pl,
  table_urls, lines: [{label, value, unit, emphasis}], error}`. `explanation_pl` bierze się
  z `taxtrace.fx_derivation` — ta sama proza, którą `/sales` pokazuje po fakcie.
- Błędy (`InsufficientLotsError`, `CostBasisMissingError`, data w przyszłości, brak kursu NBP)
  → HTTP 200 z `{ok: false, error: "..."}`, **nigdy 500**. Formularz ma ostrzegać, nie padać.
- Walidacja daty przyszłej: wyciągnąć istniejące `_is_future_date` (`web.py:72`) i użyć w
  endpointach — dziś ta sama reguła jest sprawdzana dopiero w POST.
- `rate_for_event` robi `INSERT OR IGNORE` do `nbp_rates` (cache kursów publicznych).
  `pit38_get:665` woła `simulate_sale` bez `WRITE_LOCK` — trzymamy ten sam wzorzec dla spójności.
- `_no_cache` (`web.py:117`) już obejmuje `application/json` → `no-store` gratis.

### 3b. `static/app.js` — `NT.initFormPreview(formId, endpoint, boxId)`

Dokładać do istniejącego IIFE (`window.NT`), bez nowych zależności:
- delegowany `input`/`change` na formularzu, **debounce 400 ms**;
- nie strzela, dopóki nie ma kompletu pól wymaganych przez endpoint;
- `fetch(endpoint + "?" + new URLSearchParams(...))`, `AbortController` na wyścigi;
- renderuje do `.preview-box`: kurs NBP + data + `explanation_pl` (ta sama treść co potem
  na `/sales`), listę `lines`, a przy `ok:false` — `.preview-box.error` z komunikatem;
- **degraduje się czysto**: `.preview-box` startuje jako `hidden`; bez JS formularz działa
  dokładnie jak dziś. Żadnej blokady przycisku „Dodaj" na podstawie podglądu.

CSS: `.preview-box` (ramka `--grid`, tło `--page`, `font-size:13px`), `.preview-box.error`
(`--bad`), `.preview-line`, `.preview-line.emphasis` (pogrubione, `--ink`).

### 3c. Podpięcie w szablonach

- `dividends.html` — `.preview-box` pod formularzem „Dodaj wypłatę dywidendy" (:55-92).
  Pokazuje: kurs NBP na Record Date + wyprowadzenie D-1, brutto PLN, podatek u źródła,
  Belka, **dopłata w PL** (emphasis), do odzyskania z Vero. Jeśli wypełniono pola DRIP —
  linia „powstanie lot: X akcji @ Y EUR z {{ data }}", bo dziś ten lot pojawia się
  **niespodziewanie i nie da się go usunąć**.
- `lots.html` — `.preview-box` pod „Dodaj lot" (kurs NBP, koszt EUR, **koszt PLN**) oraz
  pod „Zarejestruj sprzedaż" (plan FIFO: które loty i po ile zostaną skonsumowane, przychód
  PLN, podatek wg aktywnej polityki, **na rękę PLN**). Podgląd sprzedaży jest tu ważniejszy
  niż podgląd zakupu — sprzedaży też nie da się cofnąć z tej strony.
- `pit38.html` — formularz „Co jeśli sprzedam teraz" (:145-160) **przechodzi na ten sam
  mechanizm**: dziś przeładowuje całą stronę przez query params. Zostawiamy działający
  fallback bez JS (`whatif_qty`/`whatif_price` w route zostają), ale z JS wynik pojawia się
  bez przeładowania. `test_pit38_page_whatif_query_params_render_result:983` i
  `..._insufficient_lots_shows_error_not_500:995` sprawdzają ścieżkę serwerową — nie ruszamy jej.

---

## Krok 4 — jedna matematyka dywidendowa (znalezisko audytu, priorytet)

**To jest błąd danych, nie kosmetyka.** `dividends.html` pokazuje dziś dwie niezgodne odpowiedzi:

| element | funkcja | kurs | waluta |
|---|---|---|---|
| kafelki `:21-51` | `sensors.dividends_values` (`sensors.py:241`) | **bieżący** | EUR |
| wiersze tabeli `:95-139` | `taxdiv.compute_dividend_tax_pln` (`web.py:238`) | **NBP zamrożony na Record Date** | PLN |
| `/pit38` Sekcja G | `tax/pit38.py::_section_g` | NBP zamrożony | PLN |

Fix w `web.py::dividends_get` (:223-254) — **~5 linii, zero nowych zapytań do NBP**, bo
`items` już zawiera wynik `compute_dividend_tax_pln` per wiersz:

```python
totals = {
    "gross_pln": sum(i["gross_pln"] or 0 for i in items),
    "gross_eur": sum(i["gross_eur"] for i in items),
    "withholding_paid_pln": sum(i.get("withholding_paid_pln") or 0 for i in items),
    "pl_tax_due_pln": sum(i.get("pl_tax_due_pln") or 0 for i in items),
    "reclaimable_from_finland_pln": sum(i.get("reclaimable_from_finland_pln") or 0 for i in items),
}
```
Usunąć wywołanie `sensors.dividends_values` z tej trasy (sensory MQTT zostają bez zmian —
`sensors.py` nietknięty).

**Przy okazji znika trzeci błąd:** `cost_basis_eur = cfg["position_qty"] * cfg["avg_cost_eur"]`
(`web.py:246` i identycznie `:139`) liczy z **porzuconych pól ręcznych**, które po imporcie PDF
są zerami — więc `dividend_yield_on_cost_pct` jest `None` i kafelek „Yield on cost"
(`dividends.html:45-49`) pokazuje `—` **na stałe u każdego użytkownika**. Zastąpić przez
`portfoliom.lots_based_position_values(...)["cost_basis_eur"]`, które liczy z realnych lotów.

Kafelki na `/dywidendy` przechodzą na PLN (`gross_pln`, `u źródła`, `dopłata w PL`,
`do odzyskania`) z `sub` w EUR — odwrotnie niż na pulpicie, bo to strona podatkowa.
Testy `test_dividends_post_computes_tax_and_stores_row:721` pinują `"400.00"`, `"16.00"`,
`"80.00"` z **wierszy tabeli**, nie z kafelków — zmiana kafelków ich nie ruszy.

---

## Krok 5 — nawigacja w 5 sekcjach

`templates/base.html` (:15-31) + `static/app.js` + `app.css`.

| sekcja | zawiera |
|---|---|
| **Pulpit** | `/` |
| **Portfel ▾** | Portfel · Loty · Sprzedaże · Granty |
| **Podatki ▾** | Dywidendy · PIT-38 |
| **Dane ▾** | Importy · Newsy · Prognozy |
| **Ustawienia** | `/settings` |

- Mapowanie `active` → grupa jako dict w `base.html`; grupa zawierająca aktywną stronę dostaje
  `.nav-group.active` i jest **domyślnie rozwinięta** (żeby nie zgubić kontekstu po przejściu).
- `NT.initNavGroups()` — klik/Enter/Escape, `aria-expanded`, zamykanie po kliknięciu poza.
  **Bez `:hover`** — na dotyku hover nie istnieje. Wzorzec dokładnie jak `initRowToggles()`.
- **Fallback bez JS:** grupa renderowana jako `<details class="nav-group">` — bez skryptu
  rozwija się natywnie. Nawigacja nie może zależeć od JS.
- `.nav-version` zostaje (`test_base_template_versions_static_assets:34` pinuje `?v=` na
  statykach, nie sam badge — ale badge to wymóg CLAUDE.md dot. cache mobilnego).
- `@media print` — dodać `.nav-group[open] { display: none }` (nav i tak jest ukryty).

---

## Krok 6 — przegląd stron (znaleziska audytu, w kolejności wartości)

Zmiany prezentacyjne. **`tax/*.py` nietknięte**, schemat bazy nietknięty.

**`/grants`** — trzy rzeczy:
1. **Fantomowe wiersze (błąd):** `grants.html:95` i `:134` emitują `<tr><td colspan="8">{{ realized_details(...) }}</td></tr>`
   **bezwarunkowo**. Gdy transza nie ma zrealizowanej części, makro zwraca białe znaki i
   renderuje się **pusty wiersz dla każdej transzy** — przy ~10 transzach LTI podwaja to
   wysokość tabeli niczym. Owinąć `<tr>` w `{% if %}`, nie treść makra.
2. Dołożyć pasek kafelków: `unvested_qty`, `next_vest_date`, `next_vest_qty` — **już liczone**
   przez `sensors.py::grants_values()` na potrzeby MQTT, ale strona *o vestingu* ich nie ma.
3. `Zrealizowano` jest PLN-only, choć `realized_value_eur` istnieje w `tax/grants.py::valuation`
   i jest wyrzucane. Dodać jako `sub`.
   Zachować `Brak grantów ESPP` / `Brak grantów LTI` / `zaległe — sprawdź wyciąg` / `niedopasowane`.

**`/pit38`** — najwyższa strona w apce (16 kafelków + 4 tabele + 2 formularze):
- akapit `pit_zg` (:134-138) to **dosłowny alias** kafelków Sekcji G nad nim
  (`tax/pit38.py:98-102`: `foreign_income_pln = section_g["gross_pln"]`). Zwinąć do jednej
  linii `.muted` — **ciąg `PIT/ZG` musi zostać** (`test_...:915`);
- Sekcja G i „Ślad obliczeń" renderują się przy zerze danych (4 zerowe kafelki + „0 dywidend").
  Owinąć w `{% if report.section_g.dividend_count %}` / `{% if report.sale_trace %}`;
- selektor roku ma `onchange="this.form.submit()"` **i** przycisk „Pokaż" (:16-21) — usunąć
  przycisk. **Zachować `<option value="2023"` dosłownie** (`test_...:948` pinuje składnię atrybutu);
- `RAZEM DO ZAPŁATY` i `Sekcja G` zostają bez zmian.

**`/sales`**:
- kafelki podsumowania renderują 6 zer, gdy nie ma sprzedaży — owinąć w `{% if totals.count %}`;
- **przycisk „Cofnij tę sprzedaż" jest schowany w rozwiniętym wierszu** — jedyna destrukcyjna
  akcja w całej aplikacji, dostępna dopiero po kliknięciu chevronu. Wystawić jako `.btn.small`
  w wierszu głównym. **Formularz musi zostać prawdziwym `<form action="/sales/<id>/delete">`** —
  `test_sales_delete_...:341` dopasowuje to regexem;
- tabela 10 kolumn → 8: `Kurs NBP` i `Cena EUR` są **już** w rozwinięciu (`_alloc_detail.html:9-15`);
- `Podsumowanie {{ year }}` i bajtowy HTML kafelka — **nie ruszać** (patrz krok 1).

**`/imports`** — najwięcej prozy w apce: trzy akapity (`:10-16`, `:30-34`, `:84-88`, ~15 linii)
na stronę, której cała interakcja to jeden file picker i dwa przyciski; `:84-88` powtarza
regułę z `:13-15`. Zwinąć do jednego akapitu + `<details>`. Tabela historii 7→5 kolumn
(trzy kolumny dat + `Bez zmian`, na który nikt nie reaguje). **Surowe repry dictów Pythona**
w `<td class="muted">{{ c.existing }}</td>` (:70-71) — sformatować w route jako `klucz: wartość`.
Badge „Sprzedaż zaksięgowana" przenieść z ciała karty (:39) do `page-head` jak wszędzie indziej.
Kartę konfliktów renderować tylko przy `conflicts`. Zachować `Zatwierdź jako sprzedaż`,
`/imports/conflicts/{id}/confirm-sale`, `Brak historii importów`, `Brak nierozwiązanych konfliktów`.

**`/settings`** — `settings_post` (`web.py:588-604`) zapisuje **12 z 27** kluczy z
`SETTINGS_TYPES`. Nieedytowalne są m.in. `finnish_withholding_pct`, `treaty_withholding_pct`,
`pl_capital_gains_tax_pct`, `tax_year` — czyli **stawki, z których liczy się każda kwota PLN
w aplikacji**; `dividends.html:63` pokazuje „domyślne 35%" bez żadnej możliwości zmiany.
Dodać kartę „Podatki" z tymi czterema polami (scalając dzisiejszą jednopolową kartę, której
tytuł do tego przecieka wersję do UI: `"Podatki (0.2.0 — schemat gotowy…)"`, :82).
`cost_basis_policy` przez `POLICY_LABELS` zamiast surowych enumów.
**⚠️ `test_settings_checkbox_unchecked_when_omitted:789` robi `html.count("checked") == 0`
na całej stronie** — żadna nowa klasa CSS, atrybut ani polski tekst na `/settings` nie może
zawierać podciągu `checked`.

**`/portfolio`** — strona jest w ~75% duplikatem karty Portfel z pulpitu (te same dane, ta sama
funkcja: `position_values_auto` tylko deleguje do `lots_based_position_values`), a jej druga
karta **sama o sobie pisze, że jest nieaktywna** (:47-49) i mimo to zapisuje do bazy wartości,
które psuły „Yield on cost" (krok 4). Ponieważ scalanie stron zostało odrzucone: zwinąć
ręczny formularz do `<details>` z jednym zdaniem zamiast dwóch akapitów (:13-18 i :46-55 mówią
to samo), dodać linie `≈ zł` i „Całkowity zwrot", `<code>own_only</code>` (:16) → `POLICY_LABELS`.
**Zachować `aktywne źródło`** — to jedyny sentinel odróżniający dwa stany strony
(`test_...:117` i `:140`), oraz `value="150.0"`/`value="8.75"` w polach formularza (`test_...:97`).

**`/news`** — `web.py:543,551` pobiera i parsuje `horizon` + `tags`, których szablon nie używa
(martwa robota na każdym żądaniu). Usunąć. Dodać legendę do `Wpływ` (`'●' * n` bez objaśnienia)
i kolumnę źródła. Link z karty „Sentyment i briefing" na pulpicie tutaj — dziś agregat jest
na pulpicie, szczegół tu, bez żadnego połączenia.

**`/forecasts`** — 8 kolumn, w tym `Model` (metadana debugowa); ceny **bez jednostki**, choć
pulpit te same wartości opisuje `EUR`. Trafność historyczna — jedyna liczba, po którą się tu
przychodzi — jest **na pulpicie**, nie tutaj. Dodać kafelek, dodać `EUR`, zwęzić do 6 kolumn.

**`_alloc_detail.html`** — najlepiej zaprojektowany plik z tej grupy; jedyne zmiany: import
etykiet z `_macros.html` (krok 1) zamiast niejawnego kontraktu na kontekst, oraz `{% if detail.allocations %}`
przed `<thead>`. **`explanation_pl` musi renderować się dokładnie raz na sprzedaż** —
`test_alloc_detail_renders_sale_fx_once:400` robi `html.count(...) == 1`.

---

## Krok 7 — testy

Baza: **516 testów** (w tym `tests/test_web.py`: 80). Wszystkie muszą przejść.
Lista fraz nienaruszalnych jest rozsiana po krokach wyżej i **musi być sprawdzona przed
commitem** — trzy asercje są nietypowo kruche:
1. `test_sales_page_shows_year_totals:379` — bajtowy HTML kafelka (dyktuje kształt makra `stat()`);
2. `test_settings_checkbox_unchecked_when_omitted:789` — zero wystąpień `checked` na `/settings`;
3. `test_dividends_post_with_drip_creates_lot:744` — `"gotówka" not in html`, przechodzi tylko
   dlatego, że `dividends.html:76` pisze „wypłacona **gotówką**" (ą ≠ a). Nie zmieniać tej etykiety
   na mianownik przy przebudowie formularza.

Nowe testy:
- `test_dashboard_shows_pln_alongside_eur` — pulpit zawiera `zł` i wartość ≈ `market_value_eur × kurs`;
- `test_dashboard_labels_current_rate_not_nbp` — obecna fraza odróżniająca kurs bieżący od NBP;
- `test_dashboard_omits_pln_when_no_fx_rate` — brak kursu → brak `≈`, brak `None`, strona 200;
- `test_preview_lot_returns_nbp_rate_and_cost_pln`;
- `test_preview_sale_matches_recorded_sale` — **regresja kluczowa**: podgląd dla (qty, cena, data)
  daje ten sam `tax_pln` co realny `POST /lots/sell` z tymi samymi danymi;
- `test_preview_sale_insufficient_lots_returns_ok_false_not_500`;
- `test_preview_dividend_matches_stored_row` — podgląd = to, co potem trafia do wiersza tabeli;
- `test_preview_rejects_future_date`;
- `test_dividends_totals_use_frozen_nbp_not_current_rate` — **regresja na krok 4**: suma kafelków
  == suma kolumn PLN w tabeli pod nimi;
- `test_dividends_yield_on_cost_uses_lots_not_manual_settings` — po imporcie lotów kafelek nie jest `—`;
- `test_grants_no_phantom_rows_for_unrealized_vests` — liczba `<tr>` == liczba transz;
- `test_nav_groups_render_without_js` — `<details class="nav-group">` + wszystkie 11 linków obecne w HTML;
- `test_pit38_section_g_hidden_when_no_dividends`.

Uruchomienie: `cd /config/addons/nokia_tracker/nokia_tracker && python -m pytest -q`

---

## Krok 8 — weryfikacja UI (Playwright, przed pokazaniem)

Konwencja repo — self-review zanim cokolwiek trafi do akceptacji:
1. `persist-install chromium` jeśli brak `/usr/bin/chromium-browser`; `--disable-gpu`.
2. Zrzuty do `/config/playwright/` (`filename: "playwright/<nazwa>.jpg"`) dla `/`, `/dividends`,
   `/lots`, `/grants`, `/settings` w **1920×1080** i **390×844**.
3. **Dowody, nie „na oko":**
   - pulpit: `≈` i `zł` obecne w karcie Portfel (`browser_evaluate`);
   - podgląd: wpisać komplet pól na `/lots` → `.preview-box` przestaje być `hidden` i zawiera
     kurs NBP; wpisać ilość większą niż stan → `.preview-box.error`, **strona się nie przeładowuje**;
   - nawigacja: przy 390px pasek mieści się w **jednej linii** (`scrollHeight` ≤ ~48px);
   - brak scrolla poziomego: `.table-wrap` ma `scrollWidth <= clientWidth` przy 1920px.
4. `browser_console_messages(error)` **po każdym zrzucie** — screenshot sam w sobie nie wystarcza.

---

## Krok 9 — wydanie 0.5.0

1. Bump **obu** plików: `nokia_tracker/config.yaml` (`version: "0.5.0"`) **i**
   `nokia_tracker/nokia_tracker/__init__.py` (`__version__ = "0.5.0"`). Supervisor czyta ten
   pierwszy; badge w nawigacji i `?v=` na statykach czytają drugi.
2. `CHANGELOG.md` — sekcja `[0.5.0]`; `README.md` — sekcje Features/Entities zaktualizowane
   (nowe endpointy `/api/preview/*`, PLN na pulpicie, grupowana nawigacja).
3. Commit + push na `main` do `github.com/miczu71/nokia_tracker`.
4. **Opublikowany** (nie draft) release `0.5.0` przez `gh release create`, z rozpisanymi
   zmianami — nie auto-generowane body. Zweryfikować, że `config.yaml` na `main` == tag.
5. Odświeżenie sklepu: `homeassistant.update_entity` na `update.nokia_tracker_update`, poll ~1 min,
   potem `ha_manage_addon` → `update` na slugu z hash-prefiksem; weryfikacja przez `/info`.

### ⚠️ Czego NIE robić przy deployu

**Nie używać cyklu `uninstall → remove_repository → add_repository → install`.** Ten add-on
trzyma realne dane z importu PDF w SQLite, a ten cykl je **kasuje** — zdarzyło się to
2026-07-28. Deploy wyłącznie przez opublikowany release + update.

Cache frontu jest już obsłużony (`no-store` na HTML/JSON w `web.py:117-121`, statyki z
`?v={{ version }}`, badge wersji w nawigacji) — bump wersji unieważnia CSS/JS sam z siebie.

---

## Pliki

| Plik | Zakres |
|---|---|
| `docs/PLAN_KROK_18_ux_pln.md` | nowy — kopia tego planu (krok 0) |
| `templates/_macros.html` | **nowy** — `stat()`, `tax_disclaimer()`, `LOT_TYPE_LABELS`, `POLICY_LABELS` |
| `web.py` | 3 endpointy `/api/preview/*`; `fx_info` w `dashboard`; totals PLN + fix cost basis w `dividends_get`; czyszczenie `news_page`; formatowanie konfliktów; 4 pola podatkowe w `settings_post` |
| `static/app.js` | `initFormPreview()`, `initNavGroups()` |
| `static/app.css` | `.stat-sub`, `.preview-box(.error)`, `.preview-line(.emphasis)`, `.nav-group`, druk |
| `templates/base.html` | nawigacja w 5 sekcjach (`<details>` + JS) |
| `templates/dashboard.html` | linie `≈ zł`, kafelek Koszt bazowy, linia kursu, link do `/news` |
| `templates/dividends.html` | kafelki PLN, `.preview-box`, `_macros` |
| `templates/lots.html` | dwa `.preview-box`, `_macros` |
| `templates/pit38.html` | podgląd bez przeładowania, zwinięty `pit_zg`, guardy na pustkę |
| `templates/sales.html` | guard pustki, delete w wierszu głównym, 10→8 kolumn |
| `templates/grants.html` | fix fantomowych wierszy, kafelki vestingu, EUR przy zrealizowanych |
| `templates/imports.html` | proza→`<details>`, 7→5 kolumn, badge do `page-head` |
| `templates/settings.html` | karta Podatki (4 pola), `POLICY_LABELS`, bez wersji w tytule |
| `templates/portfolio.html` | `<details>` na ręczny, linie `≈ zł`, `POLICY_LABELS` |
| `templates/news.html`, `forecasts.html` | legenda/źródło; kafelek trafności, `EUR`, 8→6 kolumn |
| `templates/_alloc_detail.html` | import etykiet z `_macros`, guard pustki |
| `tests/test_web.py` | 13 nowych testów |
| `config.yaml`, `__init__.py`, `CHANGELOG.md`, `README.md` | wydanie 0.5.0 |

**Bez zmian:** `tax/*.py` (cała logika podatkowa — zmiany są prezentacyjne albo wołają
istniejące funkcje), `sensors.py` (sensory MQTT bez zmian; usuwamy tylko `dividends_values`
ze ścieżki webowej), `portfolio.py`, `providers/*`, schemat bazy, eksporty CSV/XLSX.

---

## Uwaga poboczna (nie część zadania, zgłoszona już w kroku 17 i wciąż aktualna)

`git remote -v` w `/config/addons/nokia_tracker` ma **wklejony token GitHub w URL-u**
(`https://x-access-token:ghp_…@github.com/…`). Leży jawnym tekstem w `.git/config` i wycieka do
outputu każdej komendy gita — łącznie z outputem, który trafia do transkryptu tej sesji.
Token należy **unieważnić** na GitHubie i przestawić remote na czysty URL +
`GITHUB_PERSONAL_ACCESS_TOKEN` z env / `gh auth`. Mogę to zrobić w osobnym kroku.
