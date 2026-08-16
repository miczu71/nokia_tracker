# Krok 30 / 0.14.0 — Kalendarz i prognoza dywidend (`nokia_tracker`)

## Context

`docs/ROADMAP.md` (roadmapa v2, zatwierdzona w poprzedniej turze) stawia falę 0.14.0 jako pierwszą
po asystencie. Rozpoznanie na realnych danych produkcyjnych pokazało, że fala jest **bardziej
wartościowa niż zakładała roadmapa, ale oparta na dwóch błędnych przesłankach**, które trzeba
poprawić w tym samym kroku.

**Dlaczego to ma sens teraz:** stan posiadania użytkownika skoczył ze 119,66 akcji (kwiecień 2026)
do 2 888,66 (lipiec 2026) przez vesting ESPP/LTI. Ostatnia realna dywidenda wyniosła ~4,78 EUR
brutto. Najbliższa wypadnie na ~2 887 akcji uprawnionych → ~115 EUR, czyli **~24× więcej**. Dziś
nic w aplikacji tego nie pokazuje — historia dywidend jest tylko wsteczna.

### Ustalenia empiryczne z tej sesji (potwierdzone, nie założone)

1. **Nokia płaci kwartalnie, nie raz w roku.** Realne record date: 2023-02-20/05-15/08-14/11-13,
   2024-02-19/05-03/08-01/10-31, 2025-05-01/07-25/10-24, 2026-01-30/04-24.
   `docs/ROADMAP.md` twierdzi „Nokia zwykle wypłaca raz w roku" — **do poprawienia**.
2. **Dywidenda należy się tylko od akcji faktycznie posiadanych** (wolne + z ograniczeniem), nie od
   zablokowanych transz. Dowód co do cyfry: wypłata 2026-01-30 miała `entitled_quantity` =
   **61,4916**, dokładnie równe `shares_total` z wyciągu na koniec 2025 (61,491555). Dziś
   `shares_total` = 2 888,66 ≈ pulpitowe „wolne 2 744,32 + z ograniczeniem 142,73" = 2 887,05,
   podczas gdy „zablokowane 1 266,00" jest wyłączone.
3. **`dividends.quantity` ma DWIE semantyki** — to największe ryzyko poprawności całej fali.
   Wiersze transakcyjne (`parse_dividends`) mają tam bazę uprawnioną (~61). Wiersze odtworzone z
   „Vested Dividend Shares" (`importers/computershare_pdf.py:589-625`) mają tam **liczbę akcji
   kupionych z reinwestycji** (~0,19), a `gross_eur` jest odtworzone jako
   `quantity × cost_basis_eur / (1 − 0,35)`. Dla nich `gross_eur/quantity` to **cena akcji**, nie
   stawka dywidendy → naiwne liczenie stawki daje ~150× za dużo. Odróżnia je **niepuste `notes`** —
   ten sam sygnał, którego już używa `tax/pit38.py:68`.
4. **Realnych wypłat transakcyjnych jest tylko 5** z 18 wierszy w bazie. Stawki: 0,03999 / 0,03999 /
   0,02999 / 0,02992 / 0,03995. Mediana z ostatnich 4 = **0,035 EUR/kwartał → 0,14 EUR/rok**, co
   zgadza się z realnie ogłoszoną roczną dywidendą Nokii. Metoda się waliduje na danych.
5. `dividends.pay_date` przechowuje **record date** (mylna nazwa kolumny). Nie ma nigdzie kolumny
   ex-date ani daty ogłoszenia.

### Decyzje użytkownika (wiążące)

- Ogłoszony harmonogram → **nowa tabela (migracja v10) + formularz**, nie pola w ustawieniach.
  Powód wybrany przez użytkownika: WZA uchwala kwotę roczną płatną w 4 kwartalnych ratach — chce
  wpisać cały harmonogram naraz, z historią ogłoszeń.
- Kalendarz musi odróżniać „potwierdzoną" od „szacowanej".

---

## Odstępstwa od roadmapy (świadome, do udokumentowania w commicie)

| Roadmapa mówi | Robimy | Dlaczego |
|---|---|---|
| „Nokia zwykle wypłaca raz w roku" | kwartalnie, 4 raty | obalone realnymi record date (ustalenie 1) |
| „Zero nowej tabeli" | migracja v10 | decyzja użytkownika z tej sesji |
| „Nowa funkcja w `tax/dividends.py` (`forecast()`)" | nowy moduł `dividend_outlook.py` | (a) `tax/dividends.py` to moduł księgi + łańcucha podatkowego, zamraża kursy NBP — projekcja czytająca `vests`/`lots`/kurs bieżący tam nie należy (ta sama argumentacja co `advisor.py:1-10`); (b) **nazwa `forecast` jest zajęta** przez prognozy CENOWE: tabela `forecasts`, `forecasts.py`, `sensors.forecast_values`, encje `forecast_1w_eur`, ustawienie `alert_on_forecast_break`, trasa `/forecasts` |

Trzy poziomy pewności zamiast dwóch (`potwierdzona` / `zapowiedziana` / `szacowana`) — WZA uchwala
kwotę, ale upoważnia zarząd do decyzji o dacie każdej raty osobno, więc przez większość roku kwota
jest znana, a data orientacyjna. Przy dwóch etykietach musielibyśmy kłamać w jedną ze stron.

---

## Zakres

### 1. Migracja v10 — `dividend_schedule`

```sql
CREATE TABLE dividend_schedule (
    id INTEGER PRIMARY KEY,
    fiscal_year INTEGER NOT NULL,
    instalment INTEGER NOT NULL,
    record_date TEXT NOT NULL,
    payment_date TEXT,
    ex_date TEXT,
    gross_per_share_eur REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'EUR',
    dates_confirmed INTEGER NOT NULL DEFAULT 0,
    announced_on TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'announced' CHECK(status IN ('announced','cancelled')),
    matched_dividend_id INTEGER REFERENCES dividends(id),
    notes TEXT,
    UNIQUE(fiscal_year, instalment)
);
CREATE INDEX idx_dividend_schedule_record ON dividend_schedule(record_date);
```

Klucz naturalny `(fiscal_year, instalment)` — ponowne wysłanie formularza UPSERT-uje (data
orientacyjna staje się potwierdzoną), nie duplikuje.

**Dopasowanie ogłoszenia do realnej wypłaty:** `reconcile_schedule(conn, today=None)`, wzorowane
wprost na `tax/grants.py::reconcile_vesting` włącznie z jego kontraktem „dopasuj tylko gdy
jednoznaczne, inaczej zostaw i bądź szczery": (1) dokładne `pay_date == record_date`, (2) okno ±5
dni gdy dokładnie jeden kandydat, (3) inaczej `NULL`, nigdy zgadywania. Dopasowane raty znikają z
kalendarza przyszłości.

**W tym samym commicie, bo inaczej cicho psuje istniejącą funkcję:**
`backup.py::_CSV_TABLES` (~linie 32-46) hardkoduje listę tabel i kolumn — nowa tabela bez wpisu tam
przetrwa w `.db` wewnątrz ZIP-a, ale zniknie z CSV i z liczników manifestu, **bez żadnego błędu**.
Dodatkowo `restore_preview` (~129-131) robi `SELECT id FROM {table}` po `_CSV_TABLES`, a
`_check_schema_compatible` (~108) odrzuca tylko backupy *nowsze* — więc backup z v9 wywali
`sqlite3.OperationalError` na `/dane`. Potrzebny guard po `sqlite_master`.

### 2. Silnik projekcji — nowy `nokia_tracker/dividend_outlook.py`

Nowa funkcja w `tax/dividends.py` (jedna definicja „to szacunek", współdzielona z PIT-38):
```python
def is_estimated(row) -> bool:   # CZYSTA
```
`tax/pit38.py:68` przechodzi na delegację do niej (zachowanie bajtowo identyczne, pilnowane przez
istniejące testy PIT-38).

```python
def per_share_history(conn, lookback: int = 4) -> dict
def entitled_base(conn, today: str | None = None) -> dict
def qty_on(base: dict, record_date: str) -> float          # CZYSTA
def calendar(conn, cfg, years_ahead=3, eurpln_rate=None, today=None) -> dict
```

- `per_share_history` **wyklucza wiersze `is_estimated()`** (ustalenie 3) i raportuje
  `excluded_estimated_count`. Punkt centralny = **mediana** z ostatnich 4 realnych wypłat (odporna
  na kwartał 0,03), pasmo min–max obok („0,035 EUR/akcję, zakres 0,03–0,04 z 4 wypłat"). Kadencja =
  mediana odstępów między realnymi record date, przyciągnięta do najbliższej z {91, 182, 365} →
  `payments_per_year` ∈ {4, 2, 1}; na realnych danych daje 4.
- **Kontrakt uczciwości:** `sufficient = (liczba realnych wypłat >= 4)`. Poniżej — `per_share_eur =
  None`, tekstowy `reason`, i silnik emituje **zero** zdarzeń szacowanych. Nigdy zmyślonej daty.
  (Na dziś: 5 realnych wypłat, czyli ledwo wystarcza — warto to pokazać na ekranie.)
- `entitled_base`: `held_qty` = suma `qty_remaining` z `tax/lots.py::open_lots(conn)` (loty
  z ograniczeniem SĄ posiadane → wchodzą; zablokowane transze to `vests`, nie loty → wypadają —
  dokładnie ustalenie 2). Przyszłe przyrosty = transze `pending` z
  `tax/grants.py::vesting_timeline`, po `effective_date` (`COALESCE(available_from, vest_date)` —
  późniejsza z dat, konserwatywnie). Transze zaległe wyłączone i raportowane osobno jako
  `overdue_excluded_qty` (uniknięcie podwójnego liczenia z tym, co `reconcile_vesting` już wrzucił
  do `lots` — ta sama reguła co `unvested_summary`/`dashboard_buckets`).
- `qty_on` jest CZYSTA, żeby `entitled_base` liczyć **raz** i reużyć dla ~20 zdarzeń — bez tego
  każde `/dividends` GET i każdy 10-minutowy tick MQTT robiłby 20 skanów lotów.
- Zdarzenia: raty z `dividend_schedule` (niedopasowane, w horyzoncie) + zdarzenia szacowane
  (tylko gdy `sufficient`), z **deduplikacją po slocie (rok, kwartał)** — rata ogłoszona wypiera
  szacowaną. Daty szacowane poza rokiem 1 renderowane kwartalnie (`2028-Q1 (≈2028-01-28)`), bez
  udawania precyzji dziennej.
- Podatki: **reużycie łańcucha sekcji G bez nowej matematyki** — `taxdiv.compute_dividend_tax`
  (EUR) + `taxdiv.compute_dividend_tax_pln` (PLN). **Uwaga: ta druga zwraca tylko 2 klucze gdy
  `gross_pln is None`** (`tax/dividends.py:166`) → wszędzie `.get()`, nigdy indeksowania.
  `reclaimable_from_finland_*` zostaje osobną linią, **nigdy nie wliczaną w „na rękę"** (odzyskanie
  wymaga złożenia wniosku do Vero).

### 3. Web + szablon

Zostajemy na istniejącej `/dividends` (zgodnie z roadmapą i decyzją użytkownika) — historia u góry,
projekcja **pod nią**, każda karta projekcji z własnym disclaimerem.

- `GET /dividends` — dokłada `schedule` + `outlook`, whitelistowany `?lata=1|3|5` (domyślnie 3).
- `POST /dividends/harmonogram` — jedno ogłoszenie = jeden formularz: `fiscal_year`, `announced_on`,
  4 wiersze (`record_date_N`, `payment_date_N`, `per_share_N`, `confirmed_N`). Puste pomijane,
  UPSERT po `(fiscal_year, instalment)`, działa bez JS.
  **`_is_future_date` NIE wolno tu zastosować** — daty przyszłe są sensem tej tabeli, a ona nie
  dotyka NBP. Do zapisania w docstringu, żeby nikt „nie naprawił".
- `POST /dividends/harmonogram/<id>/delete` — wzorem `sales_delete` (`web.py:732`).
- Wszystko przez `url_for()` (middleware ingressu, `web.py:111`) i pod `dbm.WRITE_LOCK`.

Trzy nowe karty w `templates/dividends.html` po „Historii": **Kalendarz** (z badge'ami
potwierdzona/zapowiedziana/szacowana), **Ogłoszony harmonogram** (formularz + wiersze + delete +
znacznik dopasowania), **Założenia prognozy** (`stat()`: stawka z pasmem, wypłat/rok, ile realnych
wypłat użyto, ile szacunkowych wykluczono, użyty kurs EUR/PLN z jawną adnotacją „kurs bieżący, nie
NBP D-1"). Pusty stan → `empty_state(reason)`.

Filtry `money`/`qty` stosujemy na kartach projekcji (agregaty), **nie** na istniejącej tabeli
historii, która zostaje na `'%.2f'|format(...)` (zgodność co do grosza z wyciągiem).

Wykres: `NT.initDividendOutlookChart` w `static/app.js` wzorem `initDividendBarChart` (~366),
dopisany do eksportu `NT` (~527).

Brak zmian w `NAV_GROUPS` (bez nowej strony). Brak nowych ustawień → bez sześciomiejscowego rytuału.

### 4. Sensory MQTT — 3

Zerowa liczba byłaby broniona, gdyby nie to, że roadmapowa fala 0.17.0 (asystent proaktywny) jawnie
zależy od „zbliżającej się daty dywidendy z 0.14.0" — sensor to najtańszy nośnik.

| slug | typ | uwagi |
|---|---|---|
| `next_dividend_date` | `device_class="date"`, `has_attrs=True` | atrybuty: `gross_per_share_eur`, `entitled_qty`, `gross_eur`, `certainty`, `instalment` — wzorem `next_vest_date` |
| `dividend_next_12m_gross_eur` | monetary/total | |
| `dividend_next_12m_net_pln` | monetary/total | |

Nazwy świadomie omijają `forecast_*`. Rytuał 3-krokowy: klucz w `sensors.dividend_outlook_values`
== slug → `_Entity` w `publisher._ENTITIES` → wpięcie w pętlę `main.py` (~220). **Nie** wpinamy w
`daily_digest_job` (~525) — `notifier` tego nie konsumuje, ten łańcuch jest celowo krótszy (ta sama
decyzja co w kroku 26).

---

## Kolejność commitów (TDD, każdy czerwony → zielony)

| # | Commit | Treść |
|---|---|---|
| 0 | plan | `docs/PLAN_KROK_30_dywidendy.md` + **poprawka `docs/ROADMAP.md`** (kwartalnie nie rocznie, z realnymi datami jako dowodem; nowa tabela zamiast „zero tabel"; `dividend_outlook.py` zamiast `tax/dividends.py::forecast()`) |
| 1 | migracja | `db.py` v10, `tests/test_db.py` (bump `user_version` + `expected`), `backup.py` `_CSV_TABLES` + guard w `restore_preview`, `tests/test_backup.py` |
| 2 | silnik I | `taxdiv.is_estimated()` + delegacja z `pit38`, `per_share_history()`, `entitled_base()`, `qty_on()` |
| 3 | silnik II | `calendar()` — scalanie zdarzeń, dedupe slotu, łańcuch sekcji G, `by_year`, `ntm`/`ttm` |
| 4 | harmonogram | CRUD + `reconcile_schedule()` |
| 5 | web | trasy + 3 karty w `dividends.html` |
| 6 | wykres | `app.js` + canvas |
| 7 | sensory | `sensors.py`, `publisher.py`, `main.py` |
| 8 | wydanie | `config.yaml` + `__init__.py` bump na 0.14.0, `README.md`, `CHANGELOG.md` |

Commit 1 musi być atomowy (wersja schematu + jej test + backup).

---

## Pliki krytyczne

- `nokia_tracker/nokia_tracker/dividend_outlook.py` — **nowy**
- `nokia_tracker/nokia_tracker/db.py` — migracja v10
- `nokia_tracker/nokia_tracker/backup.py` — `_CSV_TABLES` + guard
- `nokia_tracker/nokia_tracker/tax/dividends.py` — `is_estimated()`
- `nokia_tracker/nokia_tracker/web.py` — 3 trasy
- `nokia_tracker/nokia_tracker/templates/dividends.html` — 3 karty
- `nokia_tracker/nokia_tracker/{sensors,publisher,main}.py` — 3 sensory
- `docs/ROADMAP.md` — poprawka dwóch błędnych przesłanek

Do reużycia, nie pisania od nowa: `tax/grants.py::vesting_timeline`/`reconcile_vesting` (wzorzec),
`tax/lots.py::open_lots`, `taxdiv.compute_dividend_tax`/`compute_dividend_tax_pln`, `format.py`,
`templates/_macros.html` (`stat`, `tax_disclaimer`, `empty_state`), `db.WRITE_LOCK`,
`web.py::_IngressPrefixMiddleware`.

---

## Weryfikacja

**Testy (~55 nowych, do 904 istniejących).** Nowy `tests/test_dividend_outlook.py` (~32) +
`test_web.py` (~12) + `test_sensors.py`/`test_publisher.py`/`test_db.py`/`test_backup.py`.
Najważniejsze przypadki:
- `test_per_share_excludes_estimated_rows` — wiersz `dividend_estimated:` obok 4 realnych →
  `per_share_eur ≈ 0,035`, nie ~5. **Najważniejszy test fali.**
- `test_entitled_base_includes_restricted_own_lots_excludes_pending_vests` — przypina ustalenie 2.
- `test_projection_reflects_vesting_jump` — 120 posiadanych + transza 2 800 w horyzoncie →
  zdarzenie po niej ~24× większe. **Regresja na to, po co ta fala w ogóle istnieje.**
  Liczby syntetyczne (120 / 2 800 / 0,04), nigdy realne — repo jest publiczne.
- `test_insufficient_history_emits_no_estimated_events_and_a_reason`
- `test_pln_keys_are_none_without_fx_rate_and_no_keyerror` — przypina wymóg `.get()`
- `test_tax_chain_matches_section_g_for_identical_inputs` — te same wejścia przez `pit38._section_g`
  i przez `calendar()` → równe co do grosza
- `test_schedule_row_suppresses_estimated_event_for_same_quarter` (brak podwójnego liczenia)
- `test_net_in_hand_excludes_reclaimable_from_finland`

**Twarde kryteria akceptacji:**
- A1: 904 istniejące testy zielone; edytowane tylko asercje w `test_db.py` i `test_backup.py`.
- A2: pusta tabela `dividends` → zero zdarzeń szacowanych + `reason`. Nigdy zmyślonej daty.
- A3: na danych produkcyjnych wyliczone `per_share_eur` ∈ **[0,02; 0,05] EUR** — dowód, że
  wykluczenie wierszy szacunkowych zadziałało.
- A4: `totals` na `/dividends` **bajtowo identyczne** przed i po (projekcja nigdy nie wchodzi do
  sum historycznych — to dokładnie regresja, którą krok 18 naprawiał, `web.py:296-303`).
- A5: `PRAGMA user_version == 10`, `dividend_schedule.csv` w eksporcie, backup z v9 nadal się
  podglądа bez wyjątku.
- A6: `grep -rniE '\bforecast' dividend_outlook.py templates/dividends.html` → zero trafień.

**Przed pushem:** pełny pytest zielony + **sweep PII na diffie** (repo publiczne; fixture'y
syntetyczne, nigdy realne 119,66 / 2888,66 / 61,491555 ani realny zestaw record date).

**Wdrożenie — wyłącznie bezpieczną ścieżką** (add-on trzyma realne dane podatkowe):
push → `gh release create v0.14.0` (potwierdzić `isDraft:false`) → `homeassistant.update_entity` na
`update.nokia_tracker_update` → poll `ha_get_addon` aż `version_latest` == 0.14.0 →
`ha_manage_addon(action="update")`, slug `5f59858c_nokia_tracker`.
**Nigdy** cyklu uninstall/reinstall — kasuje SQLite z realnymi danymi.
Przed wdrożeniem: pobrać backup z `/dane` (rollback) i zapisać obecne `totals` do porównania A4.

**Po wdrożeniu (proxy GET, read-only):**
1. `totals` identyczne jak przed (A4).
2. Karta „Założenia": `per_share_eur` ∈ [0,02; 0,05] (A3), `payments_per_year == 4`,
   `excluded_estimated_count` == liczba wierszy z niepustym `notes`.
3. `entitled_qty` najbliższego zdarzenia == pulpitowe „wolne + z ograniczeniem" (~2 887),
   **nie** +zablokowane — żywa reweryfikacja ustalenia 2.
4. Jego `gross_eur` ≈ 115 EUR wobec ostatniej realnej wypłaty 4,78 EUR — skok, po który ta fala jest.
5. `/dane` → eksport → manifest `schema_version: 10`, `dividend_schedule.csv` obecny (A5).
6. `sensor.nokia_tracker_next_dividend_date` żyje z sensowną datą i atrybutami.
7. **Wpisać realny ogłoszony harmonogram przez nowy formularz** → badge zmienia się na
   potwierdzona/zapowiedziana, zdarzenie szacowane dla tego kwartału znika (brak dublowania).
   Jedyne kryterium, którego nie da się sprawdzić offline.
8. Playwright na realnym URL-u ingressu (1920 px + 390 px), screenshot **i**
   `browser_console_messages(error)` — obie rzeczy.

---

## Ryzyka

1. **Podwójna semantyka `dividends.quantity`** (ustalenie 3) — naiwna stawka jest ~150× za wysoka i
   każda projekcja to dziedziczy. Mitygacja: `is_estimated()`, test #1, produkcyjny check A3.
2. **`pay_date` wierszy szacunkowych to data zakupu DRIP**, nie record date — psuje też wyliczenie
   kadencji. To samo wykluczenie.
3. **`compute_dividend_tax_pln` zwraca 2 klucze bez kursu** — gołe indeksowanie rzuci `KeyError`
   dopiero gdy Yahoo i ECB padną naraz (rzadko → przeżyje testy, wybuchnie na produkcji).
4. **„Dwie matematyki 40 px od siebie"** — projekcja liczy po kursie *bieżącym*, historia po
   zamrożonym NBP. Nigdy nie sumować projekcji do `totals`, zawsze etykietować podstawę kursu.
5. **`backup.py` cicho pomija nową tabelę** + wywala się na backupie z v9 — oba w commicie 1.
6. **`test_migrate_sets_user_version`** przypina dokładny int — czerwony, jeśli migracja i bump
   testu trafią w różne commity.
7. **`_is_future_date` omyłkowo dołożone** do harmonogramu wyłączyłoby całą funkcję po cichu.
8. **Dryf dat szacowanych** — realny wzorzec już się przesunął (lut/maj/sie/lis w 2023-24 →
   sty/kwi/lip/paź w 2025-26). Stąd renderowanie kwartalne poza rokiem 1.
9. **`dividends_get` jest już ciężka** (backfill + podatek per wiersz + `position_values_auto`);
   `reconcile_schedule` dokłada zapis przy każdym GET — dotyka tylko niedopasowanych wierszy, ale
   warto zmierzyć przed wydaniem.
