# E8 — Ślad „skąd ta liczba" (nokia_tracker 0.24.0)

## Context

`nokia_tracker` jest na **0.23.0** (E7 wydane i zweryfikowane 2026-08-23). Zostaje
**ostatni etap roadmapy v3** — E8, opisany w `docs/ROADMAP_V3.md:431-441`:

> Rozciągnięcie istniejącego `tax/trace.py` ze stron podatkowych na **stan konta i
> kalkulator**. Każda kwota klikalna → rozwinięcie ze składnikami i źródłem: który lot,
> który wiersz PDF, która tabela NBP z jakiego dnia.
>
> **Kryterium twarde:** suma składników w rozwinięciu == wyświetlana kwota, co do grosza.

Problem, który to zamyka, jest nazwany w Context roadmapy: **brak zaufania do liczb**.
E1/E2 dały niezmienniki, E7 dał uzgodnienie z wyciągiem — ale kwota na ekranie nadal jest
liczbą bez rodowodu. Dziś ślad istnieje **wyłącznie** na `/pit38` i `/sales`
(`templates/_alloc_detail.html`, `tax/trace.py::enrich_allocations`), czyli dokładnie NIE
tam, gdzie użytkownik patrzy na co dzień — na Stanie konta i w kalkulatorze wypłaty.

### Trzy ustalenia empiryczne sprzed implementacji

Roadmapa zakłada „który wiersz PDF". Sprawdzone w schemacie:

1. **Nie ma klucza obcego do importu.** `lots`/`vests`/`dividends` nie mają `import_id`
   (`db.py:101-158`). Mają `source` (`manual` | `pdf_import` | `holdings_snapshot`) i
   `natural_key`, który **jest** tożsamością wiersza PDF —
   `purchase:{contribution_date}:{trade_date}:{quantity}`,
   `espp_vest:…`, `lti_vest:…`, `dividend:…` (`importers/computershare_pdf.py:395-424`).
2. **Nazwa pliku i okres wyciągu** są dostępne wyłącznie przez `statement_snapshots`
   (E7, migracja v12) → `imports.filename`. Dopasowanie idzie po `natural_key` w
   `snapshot_json`, nie po FK.
3. **`statement_snapshots` na produkcji jest puste** — wszystkie importy są sprzed 0.23.0
   (odnotowane w `ROADMAP_V3.md:394-401`). Ślad do konkretnego pliku wypełni się dopiero
   przy najbliższym re-imporcie.

**Decyzja (zatwierdzona):** `source` + `natural_key` pokazywane **zawsze** (działa na
dzisiejszej produkcji), nazwa pliku i okres **doklejane tylko przy dopasowaniu** w
`statement_snapshots`. Zero migracji, zero fałszywej pewności. Brak pliku renderuje się
jako jawne „plik wyciągu nieznany (import sprzed 0.23.0)", nigdy jako pusty ślad.

### Decyzje zatwierdzone

| Pytanie | Wybór |
|---|---|
| Zakres | **Pełny** — każda kwota na `/` i `/wyplata` (20 pozycji, tabela niżej) |
| Mechanizm UI | **Serwerowe `<details>`** — zero nowego JS, klawiatura i dotyk out-of-the-box |
| Ślad do PDF | **`natural_key` zawsze + plik gdy jest** — bez migracji |

---

## Odstępstwo od litery roadmapy (świadome)

Roadmapa mówi „`tax/trace.py` (uogólnienie)". **Nie uogólniam `tax/trace.py`** — powstaje
nowy moduł najwyższego poziomu `breakdown.py`, a `tax/trace.py` zostaje **nietknięty** i
jest przez niego konsumowany (`fx_derivation` dla nogi NBP).

Powód: `tax/` to beton (zasada z `ROADMAP_V3.md:54`), a rozbicia E8 komponują
`portfolio.py`, `cash.py`, `advisor.py`, `tax/grants.py`, `tax/pit38.py` i `tax/whatif.py`
naraz — to warstwa **nad** silnikami, nie w środku silnika podatkowego. Wsadzenie jej do
`tax/` odwróciłoby zależności. Wzorzec jest już ustalony przez E4 (`cash.py`), E5
(`account_events.py`) i E7 (`reconcile.py`): **model odczytu jako osobny moduł najwyższego
poziomu, zero zapisu**. To ta sama klasa odstępstwa, którą E7 zrobił przy „gotówce per
plan" — do odnotowania w `ROADMAP_V3.md` przy oznaczaniu etapu jako wydany.

---

## Architektura

### `nokia_tracker/breakdown.py` (nowy, model odczytu, zero zapisu)

Trzy zamrożone dataklasy — jeden kształt dla wszystkich 20 rozbić:

```python
@dataclass(frozen=True)
class Source:
    kind: str            # 'lot' | 'sale' | 'vest' | 'dividend' | 'nbp' | 'statement' | 'manual' | 'quote'
    label: str           # zdanie po polsku
    ref: str | None      # natural_key / numer tabeli NBP / nazwa pliku
    href: str | None     # /lots#lot-42 albo link do tabeli NBP

@dataclass(frozen=True)
class Component:
    label: str
    value: float | None  # w jednostce Breakdown; None = wiersz informacyjny (nie wchodzi do sumy)
    detail: str | None   # "12,3456 szt. × 3,8200 EUR × 4,3125"
    sources: tuple[Source, ...] = ()

@dataclass(frozen=True)
class Breakdown:
    key: str
    label: str
    amount: float | None
    unit: str            # 'zł' | 'EUR' | 'szt.' | '%'
    formula: str         # jednolinijkowe wyjaśnienie po polsku
    components: tuple[Component, ...]
    shown: float         # kwota wyświetlana na stronie
    recomputed: float    # kwota odtworzona ze składników
    note: str | None = None
```

**Niezmiennik domykania — `abs(shown - recomputed) <= 0.01`, egzekwowany przy budowaniu,
nie tylko w teście.** Dwa domykacze, obie ścieżki rzucają `BreakdownNotClosedError`:

- `close_sum(shown, components)` — `recomputed = sum(c.value for c in components if c.value is not None)`.
  Reszta w przedziale `(0, 0.01]` doklejana jako **jawny składnik „zaokrąglenie"**; reszta
  większa = wyjątek. Nigdy cichy dryf.
- `close_formula(shown, recomputed)` — dla kwot iloczynowych (podatek = podstawa × stawka,
  koncentracja = wartość / suma). Budowniczy **przelicza kwotę ze składników, które
  pokazuje**, i porównuje z liczbą silnika. To nie jest ozdobnik — to realny test
  krzyżowy: rozjazd między tym, co pokazujemy, a tym, co silnik policzył, staje się
  wyjątkiem.

Bez tego rozróżnienia kryterium twarde roadmapy („suma składników == kwota") byłoby
puste dla siedmiu kwot iloczynowych — jedyny sposób, żeby je „domknąć" sumą, to pokazać
wynik jako jedyny składnik, czyli udawać ślad.

**`BreakdownCtx`** — budowany raz na żądanie, żeby `/` nie robiło N+1:
- indeks lotów (`{id: row}`) z jednego `SELECT * FROM lots`,
- indeks wyciągów `{natural_key: (filename, as_of_date, period_start, period_end)}`
  zbudowany raz z wszystkich `statement_snapshots` (na produkcji dziś **pusty** — patrz
  ustalenie 3),
- cache wyprowadzeń FX per `lot_id` (ten sam wzorzec co `lot_fx_cache`,
  `tax/trace.py:149`).

**`provenance(ctx, table, row_id) -> tuple[Source, ...]`** — jedno miejsce, gdzie powstaje
noga „skąd to jest w bazie": `source` + `natural_key` zawsze, `statement` gdy indeks ma
dopasowanie. Noga NBP idzie przez **istniejące** `tax/trace.py::fx_derivation`
(niezmienione), które już zwraca `table_no`, `urls` i `explanation_pl`.

### Warstwa widoków

- `views/account.py::account_view()` → dokłada `traces: dict[str, Breakdown]`
- `views/withdrawal.py::withdrawal_view()` → dokłada `result["traces"]`

Budowniczy dostają **liczby, które widok i tak już policzył** (wynik `dashboard_buckets`,
`ledger`, `engine`), nigdy nie wołają silników drugi raz. Zero nowej matematyki —
jedynym wyjątkiem jest przeliczenie w `close_formula`, które istnieje właśnie po to, żeby
silnik skonfrontować.

**Degradacja:** widok łapie `BreakdownNotClosedError` per kwota i pomija ten jeden ślad
(kwota renderuje się jak dziś, bez `<details>`). Rozjazd nie wywala strony — ląduje jako
finding w `integrity.py`. Lekcja z 0.22.1: niezłapany wyjątek w widoku = gołe 500 na
stronie, która jest celem całej roadmapy.

### Szablony

- `_macros.html`: `traced(bd)` jako makro `{% call %}` (owija dowolną zawartość wołającego
  w `<details class="trace">`, a przy `bd is none` renderuje samą zawartość bez zmian) +
  `trace_body(bd)` renderujące tabelę składników, linię `formula` i listę źródeł.
  Makro woła makro — **nie** `{% include %}`, żeby nie wpaść w pułapkę kontekstu, którą
  `_alloc_detail.html:7-10` obchodzi jawnym importem etykiet.
- `account.html`, `withdrawal.html`: owinięcie istniejących kwot. **Bez zmiany ani jednego
  formatowania liczby.**
- `app.css`: `.trace`, `.trace-mark` (ⓘ), `.trace-body`, `summary { list-style: none }`,
  `.grid.stats { align-items: start }` żeby rozwinięcie rosło w dół zamiast rozpychać
  wiersz. `.trace-body` jest `no-print` — ślad jest ekranowy, inaczej wydruk `/` puchnie
  dwudziestokrotnie.

Cache-busting **już działa** — `base.html:8,91` serwuje `app.css`/`app.js` z
`?v={{ version }}`, więc bump 0.23.0 → 0.24.0 unieważnia CSS sam z siebie.

**Zero nowego JS.** `<details>`/`<summary>` daje klawiaturę, czytniki ekranu i dotyk za
darmo — dlatego to jest lepsze niż dzisiejszy `<abbr title="…">` w `_alloc_detail.html:44`,
gdzie całe wyprowadzenie D-1 → tabela NBP jest **niedostępne na telefonie**.

---

## Kwoty objęte śladem (20)

### `/` Stan konta (11)

| klucz | kwota (miejsce w szablonie) | rodzaj | składniki |
|---|---|---|---|
| `portfel.total` | `account.html:42` wartość całkowita | suma | wartość pozycji + wartość nienabytych transz |
| `portfel.free` | `:56` kubełek Wolne | suma | wartość pozycji − wartość z ograniczeniem |
| `portfel.restricted` | `:65` kubełek Z ograniczeniem | suma | per lot ESPP z `restricted_own_lots` (data, ilość, do kiedy) |
| `portfel.locked` | `:77` kubełek Zablokowane | suma | per nienabyta transza (grant, data vestu, ilość) |
| `portfel.cost_basis` | `:112` Koszt bazowy | suma | per lot: ilość × cena × kurs NBP lotu + prowizja, z `fx_derivation` i `natural_key` |
| `portfel.unrealized` | `:115` Niezrealizowany P&L | suma | wartość rynkowa + (−koszt bazowy) |
| `portfel.total_return` | `:121` Całkowity zwrot | formuła | (P&L + dywidendy netto) / koszt bazowy |
| `portfel.dividends_net` | `:123` Dywidendy netto | suma | per wypłata (`tax/dividends.py::payouts`), brutto − podatek u źródła; nota: kurs bieżący, nie NBP |
| `cash.broker_balance` | `:24,:149` Saldo u brokera | suma | jeden ręczny odczyt: data, wiek, `source='manual'` |
| `cash.sale_proceeds` | `:154` Wpływy ze sprzedaży | suma | per sprzedaż: `revenue_pln` + kurs NBP sprzedaży + numer tabeli |
| `cash.tax_outstanding` | `:155` Podatek do zapłaty | suma | podatek należny (PIT-38) + każda wpłata z `tax_payments` ze znakiem minus |

Przepadek ESPP (`account.html:68-69`) jest częścią noty kubełka „Z ograniczeniem", nie
osobnym kafelkiem — ślad dostaje na `/wyplata` jako `wyplata.forfeit`.

### `/wyplata` Kalkulator (9)

| klucz | kwota | rodzaj | składniki |
|---|---|---|---|
| `wyplata.quantity` | `withdrawal.html:66` Ilość akcji | formuła | dla kierunku „target": wynik bisekcji + iteracje/tolerancja; dla „quantity": wejście użytkownika |
| `wyplata.gross_eur` | `:67` Brutto | formuła | ilość × cena EUR |
| `wyplata.revenue_pln` | (w `result`) Przychód PLN | suma | per lot z `lots_consumed_detailed` + kurs sprzedaży + tabela NBP |
| `wyplata.cost_fifo` | `:68` Koszt FIFO | suma | per lot: koszt PLN, kurs nabycia, `natural_key` |
| `wyplata.income` | (w `result`) Dochód roku | suma | dochód tej sprzedaży + już zrealizowany dochód roku |
| `wyplata.usable_loss` | `:75` Wykorzystana strata | suma | per rok z `tax_loss_carryforward` |
| `wyplata.tax` | `:69` Podatek | formuła | (dochód roku − strata) × stawka |
| `wyplata.net` | `:70` Na rękę | suma | przychód PLN + (−podatek) |
| `wyplata.forfeit` | `:88-89` Przepadek ESPP | suma | per transza dopasowania |

Koncentracja przed/po (`:96-97`) **świadomie poza zakresem** — to wskaźnik pochodny od
`portfel.total`, który ślad już ma; osobne rozbicie powielałoby tę samą listę lotów.

---

## Kroki (TDD, jeden commit na krok)

0. **Skopiować ten plan do repo** jako `docs/PLAN_E8_slad.md` — reguła „plan jako plik .md",
   przed pierwszą linią kodu.
1. `breakdown.py`: dataklasy, `close_sum`/`close_formula`, `BreakdownNotClosedError`,
   `BreakdownCtx`, `provenance()`. **Testy przed implementacją**, w tym: reszta ≤ 1 gr →
   składnik „zaokrąglenie"; reszta > 1 gr → wyjątek; lot ręczny / `pdf_import` bez
   snapshotu / `pdf_import` z dopasowanym snapshotem → trzy różne kształty `Source`.
2. Budowniczy `/wyplata` (9). Testy przed implementacją.
3. Budowniczy `/` (11). Testy przed implementacją.
4. `views/withdrawal.py` + `views/account.py` emitują `traces`, z łapaniem
   `BreakdownNotClosedError` per kwota.
5. `_macros.html` (`traced`, `trace_body`) + `app.css`.
6. Wpięcie w `account.html` i `withdrawal.html`.
7. `integrity.py`: nowy niezmiennik `breakdown_not_closed` (warning) — rozjazd składników
   ląduje na karcie „Spójność danych" na `/dane`, nie ginie.
8. Wydanie 0.24.0 + weryfikacja na produkcji + `ROADMAP_V3.md`/`CHANGELOG.md`.

**Migracji brak** — E8 nic nie zapisuje do bazy. To jedyny etap roadmapy v3 bez migracji.

---

## Kryteria twarde

1. **Kwoty renderują się bajtowo identycznie.** Wszystkie istniejące asercje w
   `test_web_account.py` (14) i `test_web_wyplata.py` (14) przechodzą **bez zmiany ani
   jednej** — ten sam wymóg, który E3 i E7 spełniły.
2. **Domykanie ≤ 0,01 zł na każdym rozbiciu**, egzekwowane wyjątkiem przy budowaniu.
3. **`git diff --stat` nie dotyka `tax/`.** `test_tax_*.py` zielono przed i po (beton).
4. **Zero nowej matematyki finansowej** — budowniczy komponują liczby, które widok już ma.
5. Rozjazd nie wywala strony: kwota degraduje się do dzisiejszego renderu, finding trafia
   do `integrity.py`.

## Ryzyka

| Ryzyko | Mitygacja |
|---|---|
| `/` liczy 11 rozbić na każde wejście | `BreakdownCtx` budowany raz (jeden `SELECT` lotów, jeden indeks wyciągów, cache FX). Zmierzyć czas renderu `/` przed i po, podać liczbę. |
| `<details>` w kafelku `.stat` / `.pf-bucket` psuje layout na 390 px | `align-items: start` + Playwright na 390 i 1920, porównanie z istniejącymi zrzutami 0.23.0 w `/config/playwright/` |
| Domykanie odsłoni realny rozjazd na produkcyjnych danych | To **feature**, precedens E7 (tolerancja z danych odsłoniła 1,61 akcji). Degradacja + finding, nie 500. |
| Ślad do PDF pusty na całej produkcji | Oczekiwane i jawnie komunikowane („plik wyciągu nieznany"). Wypełni się po re-imporcie. |

---

## Weryfikacja

1. `python -m pytest` w `/config/addons/nokia_tracker/nokia_tracker` — zielono.
   Punkt odniesienia **1283 testy** na 0.23.0, oczekiwany wzrost do ~1330.
2. `python -m pytest tests/test_tax_*.py` — zielono, beton nietknięty.
3. `git diff --stat` — potwierdzić brak zmian w `tax/`.
4. Wydanie: bump `nokia_tracker/config.yaml` **i** `nokia_tracker/nokia_tracker/__init__.py`
   na `0.24.0`, push, **published** GitHub release, weryfikacja wersja == tag, potem
   update przez Supervisor (`ha_manage_updates`) z backupem przed.
5. **Playwright na produkcji**, 390 px i 1920 px, zrzuty do `/config/playwright/`:
   `/` i `/wyplata` (oba kierunki) — screenshot **i** `browser_console_messages(error)`.
   Rozwinąć co najmniej trzy ślady i sprawdzić, że suma składników zgadza się z nagłówkiem.
6. **Test empiryczny celu roadmapy:** wziąć „Podatek do zapłaty" ze Stanu konta, rozwinąć,
   ręcznie zsumować składniki — musi wyjść ta sama liczba co w kafelku, co do grosza.
7. `/dane` — karta „Spójność danych": nowy niezmiennik `breakdown_not_closed` milczy na
   czystych danych.
8. Oznaczyć E8 jako WYDANE w `docs/ROADMAP_V3.md` (z odnotowaniem odstępstwa
   `breakdown.py` zamiast uogólnienia `tax/trace.py`) i domknąć roadmapę v3.
