# Nokia Tracker — Krok 12: loty i FIFO (silnik podatkowy 0.2.0)

## Context

`nokia_tracker` ma wydane 0.1.2 (rynek, AI, alerty, prosty portfel, web UI na ingressie), 287 testów
zielonych, add-on działa na żywo. Roadmapa z `docs/BLUEPRINT.md` §5 przewiduje teraz wydanie 0.2.0 —
pełne rozliczenie PIT-38 dla pracowniczego planu akcji Nokii (ESPP + LTI + DRIP).

Rozpoznanie stanu przed planowaniem:

- **Krok 11 (kursy NBP) jest w ~90% zrobiony przy okazji kroku 4.** `providers/fx_nbp.py` z
  `rate_on_or_before()`, tabela `nbp_rates`, 6 testów na realnym fixture — wszystko istnieje.
  Ale: `grep` pokazuje **zero wywołań produkcyjnych** tej funkcji, a semantyka jest o jeden dzień
  za późna względem art. 11a ustawy o PIT. Przepis wymaga kursu z ostatniego dnia roboczego
  **poprzedzającego** zdarzenie; `rate_on_or_before(conn, "2025-10-27")` zwróci kurs z 27.10, jeśli
  NBP tego dnia publikował, a blueprint wprost wymaga wyniku 24.10 (piątek). To jedyna realna luka
  kroku 11 i domykamy ją tutaj, bo dopiero teraz pojawia się pierwszy konsument.
- **Krok 12 (loty i FIFO) nie jest zaczęty.** Schemat bazy (`lots`, `sales`, `sale_allocations`,
  `grants`, `vests`, `dividends`) istnieje od migracji v1, więc zero migracji danych — jest gdzie
  pisać, nie ma czym pisać.

Cel etapu: działający, przetestowany silnik lotów FIFO z zamrożonymi kursami NBP i trzema
politykami kosztu liczonymi równolegle, plus minimalna warstwa widoczna (strona „Loty" + 5 encji
MQTT), żeby etap dało się zweryfikować na żywo tak jak każdy poprzedni — a przy okazji żeby
istniało ręczne wprowadzanie lotów, którym w kroku 13 zweryfikujemy parser PDF-ów Computershare
przeciwko czemuś, co sami wpisaliśmy.

Zakres uzgodniony z użytkownikiem: **silnik + minimalne UI/encje**, wdrożenie na żywo bez
publikowanego release'u (`config.yaml` zostaje na `0.1.2`, publikowane 0.2.0 dopiero po kroku 15).
Branch: `main`.

## Krok 0 — plan do repo

Skopiować ten plik do `/config/addons/nokia_tracker/docs/PLAN_KROK_12.md` jako **pierwszy** commit,
zanim powstanie jakikolwiek kod (zasada z `feedback_plans_as_md`).

## 1. Refaktor `tax.py` → pakiet `tax/`

Blueprint adresuje moduły jako `tax/lots.py`, `tax/policy.py`, `tax/dividends.py`, `tax/pit38.py`,
a dziś istnieje płaski moduł `nokia_tracker/tax.py`. Kolizja nazw — zamieniamy na pakiet **teraz**,
póki plik ma 36 linii i dwóch importerów:

- `tax.py` → `tax/dividends.py` (treść bez zmian).
- `tax/__init__.py` re-eksportuje `compute_dividend_tax` z `tax/dividends.py`.

Dzięki temu `from . import tax as taxm` + `taxm.compute_dividend_tax(...)` w `sensors.py:16` i
`web.py:18` oraz `tests/test_tax.py` działają **bez żadnej zmiany** — refaktor jest niewidoczny na
zewnątrz i zielone testy to potwierdzają.

## 2. Kurs NBP D-1 (domknięcie kroku 11)

`providers/fx_nbp.py` — nowa funkcja obok istniejącej, nie zamiast niej:

```python
def rate_for_event(conn, event_date: str) -> tuple[float, str] | None:
    """Kurs wg art. 11a: ostatni dzień roboczy POPRZEDZAJĄCY zdarzenie."""
    prev = (date.fromisoformat(event_date) - timedelta(days=1)).isoformat()
    return rate_on_or_before(conn, prev)
```

`rate_on_or_before()` zostaje nietknięte (jedno zapytanie zakresowe, cache w `nbp_rates`,
zamrożenie przez `INSERT OR IGNORE`) — dokładamy tylko warstwę semantyczną.

Fixture do testu: jednorazowo pobrać realny zakres
`https://api.nbp.pl/api/exchangerates/rates/a/eur/2025-10-16/2025-10-26/?format=json` i zapisać jako
`tests/fixtures/nbp_eur_range_2025_10.json`. W testach zero żywego HTTP (zasada cross-cutting
z blueprintu).

## 3. `tax/lots.py` — loty i alokacja FIFO

Kluczowe funkcje:

| Funkcja | Rola |
|---|---|
| `add_lot(conn, acquired_date, lot_type, quantity, price_eur, fee_eur=0, source='manual', natural_key=None, grant_id=None, notes=None)` | Wstawia lot, ustawia `qty_remaining = quantity`, zamraża kurs przez `fx_nbp.rate_for_event` i liczy `cost_pln`. **Idempotentne po `natural_key`** — istniejący klucz zwraca id istniejącego lotu bez wstawiania (to jest dokładnie ten błąd, który zjadł depozyt w `pv_roi` 0.30.x; kroki 13–14 na tym stoją). |
| `record_sale(conn, sale_date, quantity, price_eur, fee_eur=0)` | Zapisuje `sales` z własnym zamrożonym kursem D-1 i `revenue_pln`, po czym konsumuje loty FIFO. |
| `allocate_fifo(conn, sale_id, quantity)` | Loty z `qty_remaining > 0` w kolejności `acquired_date, id`; per alokacja wiersz w `sale_allocations` z proporcjonalnym `cost_pln` i `revenue_pln`; dekrementacja `qty_remaining`. |
| `open_lots(conn)` / `lots_summary(conn, cfg)` | Odczyt dla UI i sensorów: sumy per `lot_type`, `qty_remaining`, koszt uznany wg aktywnej polityki. |
| `backfill_missing_rates(conn)` | Uzupełnia `nbp_rate`/`nbp_rate_date`/`cost_pln`/`revenue_pln` tam, gdzie są NULL. |

Decyzje projektowe, które trzeba utrzymać:

- **`sale_allocations.cost_pln` przechowuje surowy koszt nabycia** (proporcjonalny wycinek
  `lots.cost_pln`), **nie** koszt po polityce. Polityka jest filtrem nakładanym dopiero przy
  raportowaniu — inaczej nie da się policzyć trzech polityk równolegle z tych samych zapisanych
  danych, czego wprost wymaga blueprint §3a.
- **Zamrożony kurs nigdy się nie przelicza.** `add_lot`/`backfill` piszą `nbp_rate` tylko gdy jest
  NULL. Test tego pilnuje.
- **Brak pokrycia = wyjątek, nie ujemne `qty_remaining`.** Sprzedaż większa niż suma otwartych lotów
  podnosi `InsufficientLotsError`; transakcja wycofana w całości.
- **Akcje ułamkowe** — porównania z epsilonem `1e-9`, żeby resztki po podziale float nie zostawiały
  „lotów-widm" z `qty_remaining = 3e-16`.
- **NBP niedostępne nie blokuje zapisu.** `add_lot` łapie `QuoteProviderError`/`None`, zapisuje lot
  z `nbp_rate = NULL`, a `backfill_missing_rates` (nowy job schedulera) domyka to później. Ten sam
  mechanizm obsłuży masowy import PDF-ów w kroku 13, gdzie kilkadziesiąt lotów naraz odpytywałoby
  NBP synchronicznie w trakcie requestu HTTP.

## 4. `tax/policy.py` — trzy polityki równolegle

```python
POLICIES = {
    "own_only":           {"own"},
    "own_plus_drip":      {"own", "dividend_drip"},
    "all_at_acquisition": {"own", "matched", "lti", "dividend_drip"},
}
```

- `recognized_cost_pln(allocations, policy)` — suma `cost_pln` alokacji, których `lot_type` należy
  do zbioru polityki.
- `compute_all_policies(conn, cfg, year=None)` → dict `polityka → {revenue_pln, cost_pln,
  income_pln, tax_pln, legal_basis_pl, delta_vs_default_pln}`. Podatek = `pl_capital_gains_tax_pct`
  (domyślnie 19%) od dochodu; **dochód ujemny → podatek 0 i strata pokazana wprost** (strata z akcji
  nie miesza się ze strumieniem dywidendowym — blueprint §3a, PIT-38 sekcja G).
- `legal_basis_pl` to stały tekst per polityka, ten sam co w tabeli blueprintu — UI ma pokazywać
  uzasadnienie obok kwoty, nie samą kwotę.

Ustawienie `cost_basis_policy` (już istnieje w `settings.py:41` i `config.yaml:106`, domyślnie
`own_only`) wybiera tylko, która polityka zasila sensory — UI zawsze pokazuje wszystkie trzy.

## 5. Encje MQTT (5 nowych)

`sensors.py` — nowa funkcja `lots_values(conn, cfg)`, wołana w `main.py::publish_sensors` obok
istniejących. Wpisy w `publisher.py::_ENTITIES`:

| slug | Nazwa (musi zawierać jednostkę) | Uwagi |
|---|---|---|
| `lots_total_qty` | „Lots Total Qty" | suma `qty_remaining` |
| `lots_open_count` | „Lots Open Count" | `has_attrs=True` — rozbicie per `lot_type` |
| `lots_cost_basis_pln` | „Lots Cost Basis PLN" | koszt uznany wg aktywnej polityki |
| `realized_income_pln` | „Realized Income PLN" | dochód ze sprzedaży w bieżącym roku podatkowym |
| `realized_tax_pln` | „Realized Tax PLN" | 19% od powyższego, 0 przy stracie |

`object_id` w discovery (dodane w kroku 7) gwarantuje `entity_id = sensor.nokia_tracker_<slug>`
niezależnie od `name` — mimo to nazwy z jednostką, bo tak wygląda reszta encji.

## 6. Web UI — strona „Loty"

`web.py`: trasy `lots_get` (`GET /lots`), `lots_post` (dodanie lotu), `sale_post` (rejestracja
sprzedaży). Nowy `templates/lots.html`, wpis w nawigacji `templates/base.html` między „Portfel"
a „Dywidendy".

Zawartość strony:
1. **Tabela lotów** — data nabycia, typ (`własne`/`podarowane`/LTI/`dywidenda`), ilość,
   `qty_remaining`, cena EUR, kurs NBP + data kursu, koszt PLN, źródło. Lot bez kursu dostaje
   badge „kurs do uzupełnienia".
2. **Formularz dodania lotu** — data, typ, ilość, cena EUR, prowizja.
3. **Formularz sprzedaży** — data, ilość, cena EUR, prowizja; po zapisie pokazuje, które loty
   FIFO zostały zjedzone i w jakiej części.
4. **Porównanie trzech polityk** obok siebie: przychód, koszt uznany, dochód, podatek, podstawa
   prawna, różnica względem `own_only`.
5. **Klauzula** — kalkulator pomocniczy, nie doradztwo podatkowe (ten sam ton co disclaimer na
   stronie dywidend).

Twarde wymagania odziedziczone z kroku 9, których nie wolno złamać:
- wszystkie linki, akcje formularzy i statyki przez `{{ url_for(...) }}`, przekierowania jako
  `redirect(url_for(...))` — inaczej ingress rozjedzie ścieżki;
- każda trasa POST owinięta `db.WRITE_LOCK`;
- każda trasa otwiera własne `db.get_conn()`.

## 7. Scheduler

`main.py`: nowy job `backfill_nbp_rates` (dziennie, np. 06:15) wołający
`tax.lots.backfill_missing_rates` — całe ciało pod tym samym `db.WRITE_LOCK` co pozostałe joby
(wzorzec z kroku 6, po incydencie „database is locked").

## 8. Testy (TDD — testy przed implementacją)

| Plik | Co pokrywa |
|---|---|
| `tests/test_fx_nbp.py` (rozszerzenie) | `rate_for_event("2025-10-27")` → kurs z tabeli z `effectiveDate = 2025-10-24`; okno zapytania kończy się na 2025-10-26, nie 27 |
| `tests/test_tax_lots.py` (nowy) | sprzedaż częściowa jednego lotu; sprzedaż przez granicę lotów (2 alokacje); akcje ułamkowe bez resztek-widm; sprzedaż > stan → wyjątek i brak zmian w bazie; `natural_key` dwa razy → jeden lot; zamrożony kurs nie zmienia się przy ponownym `backfill`; kolejność FIFO po `acquired_date`, nie po `id` |
| `tests/test_tax_policy.py` (nowy) | ten sam zbiór lotów daje trzy różne, poprawne kwoty; strata → podatek 0; `delta_vs_default_pln` zgadza się z różnicą |
| `tests/test_web.py` (rozszerzenie) | `/lots` na pustej bazie i na bazie z danymi (łapie błędy Jinja2, których pusta strona nie łapie); POST lotu i POST sprzedaży → redirect z prefiksem ingressu |
| `tests/test_sensors.py`, `tests/test_publisher.py` (rozszerzenie) | `lots_values()` na znanym zbiorze; discovery zawiera 5 nowych encji z `object_id` |

Baseline: 287 zielonych. Oczekiwane po etapie: ~325+.

## 9. Weryfikacja end-to-end

1. `cd /config/addons/nokia_tracker/nokia_tracker && python3 -m pytest` — wszystko zielone.
2. Commit + push na `main` (osobne commity: plan, refaktor pakietu, silnik, UI, sensory).
3. Deploy na żywo cyklem dla add-onów z repo git bez zmiany wersji (`reference_supervisor_git_addon_rebuild`):
   `ha_manage_addon` uninstall → `remove_repository` → `add_repository` → install → start.
4. `ha_search` / `ha_get_state` — 5 nowych `sensor.nokia_tracker_lots_*` / `*_realized_*` istnieje
   z poprawnymi `entity_id`; logi Supervisora bez błędów.
5. Playwright: świeży `ingress_session` (per-reinstall, wg `project_nokia_tracker`), zrzut strony
   „Loty" do `/config/playwright/nokia_lots.jpg` **oraz** `browser_console_messages(error)` pusty.
6. Test funkcjonalny na żywo w UI: dodać dwa loty (`own` po różnych datach), sprzedać ilość
   przekraczającą pierwszy lot, sprawdzić że FIFO zjadło oba we właściwej kolejności, kursy NBP są
   z dnia poprzedzającego, a trzy polityki pokazują trzy różne kwoty. Po weryfikacji usunąć dane
   testowe.
7. Bez bumpa wersji i bez release'u — `config.yaml`/`__init__.py` zostają na `0.1.2`.

## Ryzyka

- **Refaktor `tax.py` → `tax/`** dotyka dwóch importerów produkcyjnych. Mitygacja: `__init__.py`
  zachowuje dokładnie to samo API, `test_tax.py` zostaje niezmieniony jako test regresji.
- **NBP w trakcie requestu HTTP** — formularz dodania lotu odpytuje zewnętrzne API. Mitygacja:
  zapis z `nbp_rate = NULL` + backfill, nigdy 500 na stronie.
- **Float przy akcjach ułamkowych** — mitygacja: epsilon w porównaniach + test na 0,3333 akcji.
- **Strona „Loty" i ingress** — najczęstsze źródło regresji w tym add-onie. Mitygacja: `url_for()`
  wszędzie + test POST-redirect + kontrola konsoli w Playwrighcie.
