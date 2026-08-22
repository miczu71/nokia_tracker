# nokia_tracker E6 — dwukierunkowy kalkulator wypłaty (0.22.0)

## Context

`nokia_tracker` jest na **0.21.1**; etapy E1–E5 roadmapy v3 (`docs/ROADMAP_V3.md`) są wydane
i zweryfikowane na produkcji. Kolejny etap to **E6 — kalkulator wypłaty**, czyli jedyny etap
realizujący wprost nazwany cel projektu:

> „zrobić szybkie wyliczenia ile wypłacę w danym miesiącu, uwzględniając podatek i symulacje lotów"

Fundament jest już położony: E3 dał warstwę `views/`, E4 księgę gotówki, E5 ekran `/` z przyciskiem
„Policz wypłatę", który dziś prowadzi do `/plan` z jawnym TODO w kodzie
(`templates/account.html:186-188`). E6 podmienia ten link na realny kalkulator.

**Zakres:** jeden ekran `/wyplata`, przełącznik kierunku.
- **Kierunek A** — „potrzebuję X zł netto" → ile akcji, z których lotów, jaki podatek, co przepada.
  To **jedyna nowa matematyka w całej roadmapie v3**: bisekcja po ilości akcji nad istniejącym
  `tax/lots.py::_plan_fifo`.
- **Kierunek B** — „mam N akcji" → brutto, opłaty, koszt FIFO, podatek, na rękę, przepadek.
  Silnik już istnieje (`tax/whatif.py::simulate_sale`); praca jest prezentacyjna.

**Bez migracji bazy.** E6 nie dotyka schematu.

## Decyzje z wywiadu (2026-08-22)

| Pytanie | Wybór |
|---|---|
| Definicja „na rękę" | **Pełny model roczny** — dochód tej sprzedaży + zrealizowany dochód roku (`taxpolicy.compute_all_policies`) − netowanie stratą z lat ubiegłych (`taxlosses.available_for_year`). Ta sama zasada, którą już stosuje `optimize_sale_timing`. |
| Opłaty | **Ożywić `broker_fee_pct`** — dziś martwa opcja; zostaje domyślną wartością pola opłaty w kalkulatorze, edytowalną. Zero zmian w `tax/` i w zapisanych sprzedażach. |
| Wynik kierunku A | **Ilość ułamkowa (dokładna) + druga linia: zaokrąglone w górę do pełnej akcji** z przeliczonym netto. |
| Nawigacja | **`Wypłata` w grupie „Portfel"** obok „Plan"; `/plan` nietknięty; przycisk na `/` przełączony na `/wyplata`. Bez 7. pozycji top-level (pasek już zawija na 390 px — 0.21.1). |

## Znaleziska sprawdzone empirycznie przed planem

Trzy rzeczy potwierdzone czytaniem kodu, nie założone:

1. **`broker_fee_pct` jest martwe.** Zadeklarowane w `settings.py:48,103` i `main.py:105`,
   `grep` nie znajduje ani jednego użycia w wyliczeniu. Kalkulator jest pierwszym konsumentem.
2. **`restricted_own_lots(today=…)` NIE reaguje na przyszłą datę.** Filtruje po `v.status =
   'pending'` (`tax/grants.py:321`) — fakcie z bazy, nie porównaniu dat; `today` wpływa tylko na
   `open_lots(as_of=today)`. Skutek: `forfeit_for_quantity(today=<przyszłość>)` **zawyża przepadek**
   o loty, które do tej daty byłyby już wolne. `exit_plan` obchodzi to ręcznie, nakładając cutoff po
   `free_until` (`advisor.py:470-473`); **`optimize_sale_timing` NIE** — jego scenariusz „2 stycznia"
   (`advisor.py:346`) ma dziś ten błąd. To znane ryzyko dla E6, bo kalkulator ma wybór miesiąca sprzedaży.
3. **Dług z E3 do spłaty tutaj.** `/plan?timing_qty=…` przy niewystarczających lotach rzuca
   niezłapany `InsufficientLotsError` → gołe 500, w OBU trasach (HTML i JSON). E3 świadomie odłożył
   to do E6 (`views/plan.py` docstring, `ROADMAP_V3.md` §E3).

**Monotoniczność bisekcji — sprawdzona analitycznie:**
`net(q) = revenue(q) − 0.19·max(0, base + revenue(q) − cost(q) − loss_avail)`.
Pochodna to `0.81·revenue' + 0.19·cost'` w obszarze opodatkowanym i `revenue'` poza nim — obie
dodatnie dla każdej polityki kosztu. Funkcja jest ściśle rosnąca, więc bisekcja jest poprawna
i zbieżna; wyczerpanie straty daje załamanie nachylenia, nie utratę monotoniczności.
**Roadmapa i tak wymaga jawnego testu na progu** — nie zwalniamy się z niego tą analizą.

---

## Kroki

### Krok 0 — plan do repo
Skopiować ten plik jako `docs/PLAN_E6_wyplata.md` przed pierwszą linią kodu (reguła
`feedback_plans_as_md`). Commit osobny.

### Krok 1 — wspólny silnik podatku rocznego (refaktor, zero zmiany liczb)
Arytmetyka „dochód roku + dochód scenariusza − strata z lat ubiegłych" żyje dziś w **dwóch
kopiach**: `advisor.py:335-344` (`optimize_sale_timing`) i `advisor.py:489-499` (`exit_plan`).
Kalkulator byłby trzecią. Wydzielić w **`tax/whatif.py`** (nie `tax/policy.py` — `losses` importuje
`policy`, byłby cykl; `whatif` nie ma żadnego importera wewnątrz `tax/`):

```python
def annual_tax_breakdown(conn, cfg, year, sale_income_pln, policy=None) -> dict   # wersja z bazą
def _annual_tax(base_income_pln, loss_available_pln, sale_income_pln, tax_rate) -> dict  # czysty rdzeń
```

Zwraca `{tax_without_loss_pln, usable_loss_pln, income_after_loss_pln, tax_with_max_loss_pln}` —
dokładnie klucze, których używają dziś obaj konsumenci. Czysty rdzeń jest osobno, bo bisekcja
z kroku 3 woła go dziesiątki razy i nie może za każdym razem uderzać w bazę.

Przepiąć `optimize_sale_timing` i `exit_plan` na helper. **Kryterium twarde: `test_advisor.py`
i `test_tax_*.py` zielono bez zmiany ani jednej asercji** (wzorzec `_apply_policies` z kroku 26).

### Krok 2 — przepadek poprawny dla przyszłej daty
Dodać `as_of` do `advisor.forfeit_for_quantity(...)`: przed wywołaniem `forfeit_for_allocations`
wyzerować `match_rate` dla lotów z `free_until <= as_of` — dokładnie ten cutoff, który
`exit_plan:470-473` liczy dziś inline. Wydzielić go jako `_effective_match_rates(restricted, as_of)`
i przepiąć **oba** miejsca, żeby nie było dwóch kopii reguły.

**To naprawia znalezisko 2 — czyli zmienia liczby w scenariuszu „2 stycznia" na `/plan`.**
Zmiana jest w stronę prawdy (dziś przepadek jest zawyżony), ale jest zmianą zachowania:
osobny commit, jawny wpis w CHANGELOG, **checkpoint z porównaniem `delta_forfeit_pln` przed/po
na produkcyjnych danych**.

### Krok 3 — `solve_for_net()` (jedyna nowa matematyka)
W `tax/whatif.py`:

```python
class TargetUnreachableError(Exception): ...

def solve_for_net(conn, cfg, target_net_pln, price_eur, *, fee_pct=0.0,
                  sale_date=None, tol_pln=0.01, max_iter=64) -> dict
```

- Snapshot **raz**: `taxlots.open_lots(conn, as_of=sale_date)`, `fx_nbp.rate_for_event`,
  `base_income_pln`, `loss_available_pln`. `_plan_fifo` nie mutuje kandydatów (czyta wyłącznie
  `qty_remaining`), więc jeden snapshot wystarcza na wszystkie iteracje — bez kopiowania.
- `_net_for(qty)`: `fee_eur = fee_pct/100 · qty · price_eur` → `_plan_fifo` → `revenue_pln`,
  `cost_pln` wg aktywnej polityki → `_annual_tax(...)` z kroku 1 → `net = revenue − tax`.
  **Zero nowej matematyki FIFO i zero nowej matematyki podatku** — obie z istniejących funkcji.
- Bisekcja `lo=0`, `hi=Σ qty_remaining`. Cel powyżej `_net_for(hi)` → `TargetUnreachableError`
  z maksymalną osiągalną kwotą w komunikacie (nie ciche przycięcie). Brak zbieżności w `max_iter`
  → też wyjątek, nigdy przybliżenie po cichu (wymóg roadmapy).
- Finalne `simulate_sale(conn, cfg, quantity, price_eur, fee_eur, sale_date)` **jeden raz**,
  po znalezieniu ilości — daje `lots_consumed_detailed` (ślad NBP, hak pod E8) tą samą drogą,
  którą chodzi kierunek B. Oba kierunki kończą w tej samej funkcji.
- `whole_shares`: `math.ceil(quantity)` + ponowne `_net_for` na tej liczbie (jeśli mieści się
  w dostępnych lotach; inaczej `None` z powodem).

**Testy (TDD, przed implementacją):** round-trip `solve_for_net(X)` → `simulate_sale(wynik)`
w granicach ±1 zł; wynik nigdy > dostępnej ilości; cel nieosiągalny → wyjątek; **skonstruowany
przypadek z progiem wyczerpania straty z lat ubiegłych** — monotoniczność i zbieżność jawnie
asercjonowane; ilość ułamkowa (loty produkcyjne są ułamkowe, np. 154,663115); `fee_pct > 0`
obniża netto o właściwą kwotę.

### Krok 4 — `views/withdrawal.py`
`withdrawal_view(conn, cfg, direction, …) -> dict` — **jeden kształt wyniku dla obu kierunków**,
żeby szablon miał jeden blok renderujący, nie dwa:

`quantity`, `gross_eur/pln`, `fee_pln`, `cost_fifo_pln`, `income_pln`, `usable_loss_pln`,
`tax_pln`, **`net_pln`**, `forfeit_qty`, `forfeit_value_pln`, `concentration_before/after`,
`lots_consumed_detailed`, `whole_shares` (tylko kierunek A), `timing` (`optimize_sale_timing`
dla znalezionej ilości — porównanie z 2 stycznia jako karta pomocnicza).

Składane z istniejących klocków: `taxwhatif.simulate_sale`, `taxwhatif.solve_for_net`,
`advisorm.forfeit_for_quantity` (z `as_of` z kroku 2), `advisorm.concentration`,
`portfoliom.dashboard_buckets`, `market_context.latest_price_and_rate`.
Wzorzec modułu: `views/account.py` (E5) i `views/cash.py` (E4).

**Przepadek pokazywany OSOBNO, nie odejmowany od „na rękę"** — to utrata akcji, nie przepływ
gotówki; zszycie tych dwóch liczb byłoby dokładnie tym błędem, przed którym ostrzega E4
(„gotówka" ≠ „przychód podatkowy"). Osobny wiersz „całkowity koszt decyzji" wolno pokazać
jako sumę informacyjną, jawnie opisaną.

### Krok 5 — strona `/wyplata`
- **Trasa** `wyplata_get` w `web/routes_plan.py` (rodzina planera trzyma się razem) + podgląd
  JSON `GET /api/preview/wyplata`, reużywający `NT.initFormPreview` z `static/app.js:143`
  (ten sam mechanizm co trzy istniejące podglądy — zero nowego JS).
- **`templates/withdrawal.html`** — przełącznik kierunku, pole kwoty/ilości, cena (domyślnie
  bieżąca), opłata (domyślnie z `broker_fee_pct`), **wybór miesiąca sprzedaży**; wynik jako
  `grid stats` + rozwinięcie śladu FIFO (`_alloc_detail.html`, już istnieje). Mobile-first,
  `data-label` na tabelach od razu (lekcja z E5 — `cash.html` miał tę lukę).
- **Nawigacja**: `('wyplata_get', 'wyplata', 'Wypłata')` do `NAV_GROUPS['portfel']` w
  `templates/base.html:38`, zaraz po „Plan".
- **`templates/account.html:186-188`**: `href` na `url_for('wyplata_get')`, komentarz TODO usunięty.
- Cache-busting wg `CLAUDE.md`: bez nowych statyk, ale szablon dostaje `version` jak reszta.
- **Nowy test rozwiązywalności `url_for`** dla nowego endpointu (E3 zostawił ten test w
  `test_web_routing.py` — dopisać, nie tworzyć drugiego).

### Krok 6 — spłata długu z E3
`views/plan.py::timing_scenario` łapie `(taxlots.InsufficientLotsError,
taxlots.CostBasisMissingError)` i zwraca `(result, error)` jak `exit_scenario`. Obie trasy
(`plan_get`, `preview_sale_timing`) renderują komunikat zamiast 500. Testy na obu ścieżkach.

### Krok 7 — wydanie 0.22.0
Wg `feedback_ha_addon_release` + `feedback_release_notes`: bump `nokia_tracker/config.yaml`
**i** `nokia_tracker/__init__.py`, CHANGELOG z tabelą tras/funkcji, README (sekcje Features
i Entities), **published** GitHub release, weryfikacja `wersja == tag`, potem update przez
Supervisor (`ha_manage_updates` z backupem — **nigdy** cykl rebuild z
`reference_supervisor_git_addon_rebuild`, ta baza ma realne dane).
Aktualizacja `docs/ROADMAP_V3.md`: E6 → WYDANE + wynik weryfikacji.

---

## Pliki

| Plik | Zmiana |
|---|---|
| `tax/whatif.py` | +`annual_tax_breakdown`/`_annual_tax`, +`solve_for_net`, +`TargetUnreachableError` |
| `advisor.py` | `optimize_sale_timing`/`exit_plan` na wspólny helper; `forfeit_for_quantity(as_of=)`; `_effective_match_rates` |
| `views/withdrawal.py` | **nowy** — składanie obu kierunków |
| `views/plan.py` | `timing_scenario` łapie wyjątki (dług E3) |
| `web/routes_plan.py` | `/wyplata` + `/api/preview/wyplata`; obsługa błędu w `plan_get`/`preview_sale_timing` |
| `templates/withdrawal.html` | **nowy** |
| `templates/base.html`, `templates/account.html` | nawigacja + `href` przycisku |
| `tests/test_tax_whatif_solve.py`, `tests/test_views_withdrawal.py`, `tests/test_web_wyplata.py` | **nowe** |
| `tests/test_advisor.py`, `test_web_plan.py`, `test_web_routing.py` | rozszerzenia |

**Bez migracji.** `db.py` nietknięty.

## Ryzyka

| Ryzyko | Mitygacja |
|---|---|
| Kroki 1 i 2 dotykają `tax/`/`advisor.py` — „beton" | `test_tax_*.py` zielono **przed i po**; krok 1 bez zmiany ani jednej asercji |
| Krok 2 zmienia liczby na `/plan` | Osobny commit, checkpoint z porównaniem przed/po na realnych danych, jawny CHANGELOG |
| Bisekcja rozjeżdża się na progu straty | Jawny test na skonstruowanym progu; brak zbieżności = wyjątek, nie przybliżenie |
| Przepadek zszyty z „na rękę" | Osobne wiersze; test asercjonujący, że `net_pln` nie zawiera przepadku |
| Wydajność (bisekcja × `open_lots`) | Snapshot raz, czysty rdzeń podatku, `simulate_sale` raz na końcu |

## Weryfikacja

1. `pytest` — wszystko zielono; oczekiwane ~1134 → ~1170.
2. `test_tax_*.py` i `test_advisor.py` zielono; krok 1 bez zmiany asercji (`git diff` dowodem).
3. **Kryteria twarde roadmapy:** `solve_for_net(X)` → `simulate_sale(wynik)` w ±1 zł od X;
   wynik ≤ dostępnej ilości; brak zbieżności → jawny błąd.
4. **Trzy realne scenariusze policzone ręcznie** (arkusz) vs wynik narzędzia — checkpoint E6
   z roadmapy. Kandydaci: cel netto poniżej wartości jednego lotu, cel wymagający kilku lotów
   przez granicę ograniczenia ESPP, cel nieosiągalny.
5. Po instalacji przez Supervisor: **Playwright na 390 px i 1920 px** — `/wyplata` (oba kierunki),
   `/` (nowy `href`), `/plan` (regresja po kroku 2 i 6), screenshot **i** konsola bez błędów,
   pliki do `/config/playwright/`.
6. **Test empiryczny celu roadmapy:** „potrzebuję X zł netto w listopadzie" → odpowiedź w
   ≤ 3 kliknięciach od `/`, liczba zgodna z ręcznym wyliczeniem.
