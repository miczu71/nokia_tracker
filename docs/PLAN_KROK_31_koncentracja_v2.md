# Krok 31 — Ryzyko koncentracji v2 (`nokia_tracker` 0.15.0)

## Context

`nokia_tracker` jest na **0.14.0** (wydane 2026-08-16, live, krok 30 — kalendarz i
prognoza dywidend). Następna pozycja Roadmapy v2 (`docs/ROADMAP.md:280-286`) to
**0.15.0 / krok 31 — Ryzyko koncentracji v2**: dwa niezależne dodatki do istniejącej
karty „Ryzyko koncentracji” na `/plan` (z kroku 26, `advisor.py::concentration()`):

1. Punkt odniesienia 10–15% majątku w jednej spółce — standard branżowy
   (BofA Private Bank, zacytowany w `ROADMAP.md:254`), obok własnego progu użytkownika
   (`concentration_alert_pct`, domyślnie 25%).
2. Planer systematycznego wyjścia — „sprzedawaj N akcji miesięcznie/kwartalnie przez
   K okresów” → symulacja rok po roku (podatek z uwzględnieniem dostępnej straty z lat
   ubiegłych, przepadająca dopłata ESPP, końcowa koncentracja).

**Zero migracji bazy** — `other_net_worth_pln`/`concentration_alert_pct` już istnieją
(`settings.py:58-59,105-106`, sześć miejsc zaseedowanych w kroku 26), zero nowych
ustawień. **Zero nowych sensorów MQTT** — roadmapa nie wymienia żadnych dla tej fali
(w przeciwieństwie do kroku 26/30).

Decyzje podjęte przy planowaniu (nie relitygować):
- Planer to funkcja **informacyjna/eksploracyjna** (nic nie zapisuje do bazy), jak
  `espp_plan`/`optimize_sale_timing` — ten sam kontrakt.
- Netowanie stratą z lat ubiegłych w planerze bierze WYŁĄCZNIE **realną**
  `tax/losses.py::available_for_year()` — świadomie NIE licząc strat generowanych
  wewnątrz samego planu między jego własnymi latami (ten sam kompromis co już
  istniejący `optimize_sale_timing`, opisany w jego docstringu — to kontynuacja
  istniejącej zasady, nie nowa decyzja).
- Cena i kurs EUR/PLN płaskie na całym horyzoncie planera — ten sam, uczciwie
  udokumentowany kompromis co `espp_plan`/`optimize_sale_timing`.
- Daty okresów przez `dateutil.relativedelta` (już przypięty w `requirements.txt`,
  dotąd nieużywany) — precyzyjniejsze niż ręczna arytmetyka miesięcy, zero nowej
  zależności.

## A. Benchmark branżowy

W `advisor.py`: stałe modułowe `CONCENTRATION_BENCHMARK_LOW_PCT = 10.0`,
`CONCENTRATION_BENCHMARK_HIGH_PCT = 15.0` (źródło w komentarzu: BofA Private Bank).
`concentration()` (`advisor.py:222-241`) dostaje dwa nowe klucze w zwracanym słowniku:
`benchmark_low_pct`, `benchmark_high_pct` — czysta stała doklejona do wyniku, zero
nowej logiki, zero nowego parametru funkcji.

W `templates/plan.html` (`:197-221`) na `.conc-bar-wrap` drugi element —
`<div class="conc-bar-benchmark" style="--bm-low:{{ conc.benchmark_low_pct }}%;
--bm-high:{{ conc.benchmark_high_pct }}%"></div>` (pasek/zakres pod głównym paskiem)
+ zdanie `.muted` pod kafelkami: „Standard branżowy: 10–15% majątku w jednej spółce
(BofA Private Bank)” z linkiem do źródła. `static/app.css`: `.conc-bar-benchmark`
jako nakładka `position:absolute` z `left/width` liczonymi z custom properties.

## B. Planer systematycznego wyjścia — `advisor.exit_plan()`

### B.1 Sygnatura i dane wejściowe

```python
exit_plan(conn, cfg, shares_per_period: float, frequency: str, num_periods: int,
          price_eur: float, eurpln_rate: float | None = None,
          start_date: str | None = None) -> dict
```

`frequency`: `"monthly"` (krok 1 mies.) lub `"quarterly"` (krok 3 mies.) — inny string
→ `ValueError`. Guardy jak w `espp_plan`: `shares_per_period<=0`, `num_periods<=0`,
`price_eur<=0` → `ValueError`.

### B.2 Symulacja lotów bez zapisu do bazy

Lokalna, **mutowalna** kopia `taxlots.open_lots(conn, as_of=start_date)`
(`[dict(row) for row in ...]`) — `_plan_fifo` (`tax/lots.py:212`) sam NIE mutuje
`candidates`, więc po każdym okresie ręcznie odejmuję przydzieloną ilość z lokalnych
kopii po `lot_id`, zanim wywołam `_plan_fifo` dla kolejnego okresu. To **trzeci**
konsument `_plan_fifo` po `simulate_sale`/`espp_plan`.

**Brak pokrycia z góry:** jeśli `shares_per_period * num_periods` przekracza sumę
`qty_remaining` wszystkich kandydatów → `taxlots.InsufficientLotsError` przed
pierwszą iteracją (fail fast, zasada „nie zgaduj, gdy pokrycia brakuje” z reszty
modułu — bez tego planer zwróciłby ostatnie okresy z cichym niedoborem).

### B.3 Daty okresów

`_period_date(start_date, i, frequency)` — nowy prywatny helper, `relativedelta(months=i)`
lub `relativedelta(months=3*i)` dodane do `start_date`. Test na `start_date` z 31 dnia
miesiąca (`relativedelta` sam przycina do ostatniego dnia miesiąca docelowego —
zachowanie biblioteki, pinowane testem, nie reimplementowane ręcznie).

### B.4 Podatek per okres i per rok

Dla każdego okresu: `plan = taxlots._plan_fifo(candidates, shares_per_period, price_eur,
0.0, eurpln_rate)` (gdy `eurpln_rate is None`, pomijam całą gałąź PLN/podatku dla tego
okresu, jak `espp_plan`), `revenue_pln = sum(a["revenue_pln"] for a in plan)`.

Okresy grupowane po `year = int(sale_date[:4])`. Na koniec, per rok:
`combined_income_pln = base_income_pln(rok) + Σ income_pln okresów tego roku w planie`,
gdzie `base_income_pln` = realny dochód z `taxpolicy.compute_all_policies(conn, cfg,
year=rok)[active_policy]["income_pln"]` (0, gdy rok nie ma jeszcze żadnych realnych
sprzedaży — przyszłe lata planu). `usable_loss_pln = min(taxlosses.available_for_year(
conn, cfg, rok, policy=active_policy)["total_remaining_pln"], max(0, combined_income_pln))`
— **ta sama formuła co `optimize_sale_timing._scenario()`**, skopiowana świadomie (nie
wydzielona do współdzielonej funkcji w tym kroku — dwa wywołania to za mało, żeby
uzasadnić abstrakcję; jeśli pojawi się trzeci konsument, wydzielić wtedy).
`tax_pln(rok) = round(income_after_loss_pln * tax_rate, 2)`.

### B.5 Przepadek dopłaty ESPP per okres

Per okres: `rates_by_lot_id = {item["lot_id"]: item["match_rate"] for item in
grantsm.restricted_own_lots(conn, today=sale_date)}` (realne, znane z góry daty
vestingu — to legalne źródło faktu, bo lokalna symulacja NIE zmienia rzeczywistej
tabeli `lots`) → `forfeit_for_allocations(plan, rates_by_lot_id)` — ten sam hak z
kroku 26, zero nowej matematyki przepadku.

### B.6 Koncentracja przed/po

`concentration_before` = `conc` z `overview()` (już liczona). `concentration_after`:
`employer_value_pln_after = employer_value_pln_before - Σ(sprzedane_szt × price_eur ×
eurpln_rate) - Σ(przepadłe_szt × price_eur × eurpln_rate)` (sprzedane akcje znikają z
kubełka „wolne”, przepadłe nigdy nie zawibrują do kubełka „zablokowane”) →
`concentration(employer_value_pln_after, cfg["other_net_worth_pln"],
cfg["concentration_alert_pct"])`. Gdy `eurpln_rate is None`, `concentration_after` =
`None` (nie da się przeliczyć wartości PLN).

### B.7 Kształt wyniku

```python
{
  "frequency", "shares_per_period", "num_periods", "start_date",
  "periods": [{"period", "sale_date", "year", "quantity",
               "revenue_pln"|None, "forfeit_qty", "forfeit_value_pln"|None}],
  "years": [{"year", "income_pln", "usable_loss_pln", "tax_pln"}],  # None-owe pola gdy brak eurpln_rate
  "totals": {"shares_sold", "revenue_pln"|None, "tax_pln"|None,
             "forfeit_qty", "forfeit_value_pln"|None, "net_proceeds_pln"|None},
  "concentration_before", "concentration_after",
}
```

## C. Strona `/plan` — piąta karta

`web.py::plan_get()` (`:968-1027`): trzy nowe opcjonalne GET-param (`exit_qty`,
`exit_freq`, `exit_periods`), wołanie `advisorm.exit_plan(...)` w `try/except
ValueError` symetrycznie do bloku ESPP (`:992-1000`) — puste parametry = brak karty
wyniku, nie błąd. Nowa `GET /api/preview/exit-plan` (`web.py`, wzorzec
`preview_sale_timing` `:1066-1098`): kontrakt `{ok:true, lines:[...]}` /
`{ok:false, error}`, zawsze HTTP 200, zero zapisu.

`templates/plan.html`: piąta karta „Planer systematycznego wyjścia” — formularz GET +
`.preview-box` (wzorzec ESPP), tabela rok-po-roku (rok · dochód · wykorzystana strata ·
podatek), kafelki `totals`, para kafelków koncentracja przed/po, akapit zastrzeżeń
(cena/kurs płaskie, strata z lat ubiegłych bez interakcji między latami planu — patrz
§ decyzje).

## Pliki

**Zmienione:** `nokia_tracker/advisor.py` (`exit_plan`, `_period_date`, stałe
benchmarku, `concentration()` +2 klucze), `nokia_tracker/web.py` (`/plan` GET params,
`/api/preview/exit-plan`), `nokia_tracker/templates/plan.html`, `static/app.css`,
`CHANGELOG.md`, `README.md`, `nokia_tracker/__init__.py` → `0.15.0`, `config.yaml`
(wersja addonu).

**Nowe:** `docs/PLAN_KROK_31_koncentracja_v2.md`, rozszerzenia
`tests/test_advisor.py`, `tests/test_web.py`.

## Plan testów (TDD — czerwone przed zielonym)

`tests/test_advisor.py`:
- `concentration()`: `benchmark_low_pct==10.0`, `benchmark_high_pct==15.0` zawsze obecne.
- `exit_plan`: prosty scenariusz bez FX (same akcje, `eurpln_rate=None` → `revenue_pln`/
  `tax_pln` wszędzie `None`, `quantity`/`forfeit_qty` policzone); z FX i jedną transzą
  straty z lat ubiegłych z `tests/test_tax_losses.py::_loss_year`-owym wzorcem →
  `usable_loss_pln>0`, `tax_pln` niższy niż bez straty; okresy przecinające granicę
  roku (`start_date` w listopadzie, `frequency="monthly"`, `num_periods=3`) → dwa
  wpisy w `years`; przepadek maleje między okresami w miarę realnego uwalniania
  vestingu (dwie transze `pending` na różnych datach); brak pokrycia →
  `InsufficientLotsError` **przed** jakąkolwiek mutacją stanu; `concentration_after.pct`
  < `concentration_before.pct` po sprzedaży z dodatnim `other_net_worth_pln`;
  `frequency="quarterly"` → odstęp dokładnie 3 miesiące (`relativedelta` na 31.01 →
  30.04, nie `ValueError`); funkcja nic nie zapisuje (`SELECT COUNT(*) FROM lots`
  przed/po identyczne, `qty_remaining` realnych lotów nietknięte); zły `frequency` →
  `ValueError`; guardy `shares_per_period<=0`/`num_periods<=0`/`price_eur<=0`.

`tests/test_web.py`: `/plan` z `exit_qty`/`exit_freq`/`exit_periods` renderuje kartę;
puste parametry → strona bez błędu (jak ESPP); `/api/preview/exit-plan` zwraca `lines`
z HTTP 200 na dobrym inpucie, `ok:false` na złym, **nic nie zapisuje** (`COUNT(*) FROM
lots` przed/po); dwa nowe klucze benchmarku widoczne na `/plan` w HTML (`10` i `15` w
treści karty koncentracji).

## Weryfikacja

1. `tests/test_advisor.py` i `tests/test_web.py` zielone; reszta suity (w tym
   `test_tax_*.py`) bez regresji — `exit_plan` czyta `tax/`, nie zapisuje.
2. Ręczne przeliczenie jednego scenariusza na realnych danych produkcyjnych (loty +
   transze vestingu z produkcji) przed wdrożeniem, porównanie z tym, co pokaże strona.
3. Playwright na realnym URL-u ingressu (1920px + 390px + tryb ciemny), screenshot
   **i** `browser_console_messages(error)`.
4. Sweep PII na diffie przed pushem (repo publiczne).
5. Wdrożenie: push → `gh release create v0.15.0` (`isDraft:false`) →
   `homeassistant.update_entity` na `update.nokia_tracker_update` → poll `ha_get_addon`
   aż `version_latest=="0.15.0"` → `ha_manage_addon(action="update")` na slugu
   `5f59858c_nokia_tracker`. Nigdy cyklu uninstall/remove_repository/add_repository/install.
6. `README.md` (opis piątej karty) i `CHANGELOG.md` w tym samym wydaniu.

## Ryzyka i pułapki

1. Podwójne liczenie podatku przy kilku okresach w tym samym roku — sumować
   `income_pln` PRZED zastosowaniem stawki/straty, nie liczyć podatku per okres osobno.
2. `relativedelta` na końcu miesiąca (31 → 28/29/30) — test jawnie na `start_date`=31.
3. Dzielenie przez zero: `num_periods`, `price_eur`, `total_net_worth_pln` przy
   `concentration_after` (te same guardy co `concentration()`/`espp_plan` już mają).
4. Karta na `/plan` nie może wywalić całej strony, gdy `exit_qty` puste/błędne —
   `try/except ValueError` jak przy ESPP, nigdy 500.
5. `_period_date`/pętla muszą operować na LOKALNEJ kopii lotów — pomyłka z realnym
   `open_lots(conn, ...)` wewnątrz pętli po cichu zresetowałaby symulację do stanu
   bazy przy każdym okresie (żaden test tego nie złapie, jeśli test ma tylko jeden
   otwarty lot — pilnować scenariusza z co najmniej dwoma).
6. `forfeit_for_allocations` woła się na `plan` danego okresu (symulowana alokacja),
   NIE na realnym `simulate_sale` — pomylenie źródeł da przepadek policzony z
   niewłaściwych ilości.

## Pierwszy krok implementacji

Ten dokument, commit osobno. Potem czerwone testy w `tests/test_advisor.py`, dopiero
potem `advisor.py::exit_plan()`.
