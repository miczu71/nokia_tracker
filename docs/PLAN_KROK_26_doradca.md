# Krok 26 — Doradca planu pracowniczego (`nokia_tracker` 0.10.0)

## Context

`nokia_tracker` jest na **0.9.0** (wydane 2026-08-14, live na Supervisorze, `update_available:false`,
699 testów). Fala 0.8.1 (kopia zapasowa) i 0.9.0 (Wyniki: XIRR/TWR/atrybucja/benchmark) zamknięte.
Następna pozycja roadmapy (`docs/ROADMAP.md:120-143`) to **0.10.0 / krok 26 — Doradca planu
pracowniczego**: jedyna część roadmapy, której nie da się kupić w narzędziu premium.

Problem: dodatek zna już wszystkie fakty (loty, granty, transze, polityki kosztu, kurs), ale nie
odpowiada na cztery pytania, które użytkownik realnie sobie zadaje:
1. **Ile tracę, sprzedając dziś?** — dziś na pulpicie jest tylko zdanie *„sprzedaż wcześniej oznacza
   utratę dopasowania 50%"* (`templates/dashboard.html:104-105`) — **bez kwoty**.
2. **Kiedy co wpada?** — `unvested_summary()` daje tylko agregaty, żadnej listy transz.
3. **Ile mi da wpłacanie X przez N miesięcy?** — brak jakiegokolwiek planera ESPP.
4. **Czy nie mam za dużo w jednym koszyku, który jest jednocześnie moim pracodawcą?** — brak.

**Decyzje użytkownika podjęte przed planowaniem (nie relitygować):**
- Pełny zakres kroku 26 (wszystkie cztery funkcje).
- Reszta majątku do koncentracji = **zwykłe pole liczbowe w ustawieniach dodatku**, nie encja HA
  (sprawdzone: w HA użytkownika nie ma dziś żadnej encji z majątkiem netto). `net_worth_entity`
  **nie powstaje**; `ha_client.get_numeric_state` zostaje bez wołających.
- Przepadek przy częściowej sprzedaży liczony **proporcjonalnie do sprzedanych sztuk**, mianownik =
  **oryginalna `lots.quantity`**.

Efekt końcowy: nowa strona `/plan`, 3 nowe sensory MQTT, kwota przepadku dopisana do istniejącego
ostrzeżenia na pulpicie, wydanie 0.10.0.

---

## 0. Migracja bazy — NIE MA JEJ, świadomie

`SCHEMA_VERSION = len(_MIGRATIONS)` (`db.py:269`) zostaje **7**. Wszystko jest wyprowadzalne:
przepadek z `lots`×`grants`×`vests`, oś czasu z `vests`, planer to czysta arytmetyka + `_plan_fifo`,
koncentracja to jeden wiersz w istniejącej tabeli KV `settings`. Migracja „na wszelki wypadek"
byłaby czystym kosztem (nieodwracalny bump, nowa powierzchnia w `backup.py`, zero zysku).
**Krok 26 nie dotyka `db.py`.**

## 1. Silnik utraconego dopasowania

### 1.1 Podział: `tax/grants.py` = fakt, `advisor.py` = pieniądz

Reguła ograniczenia („lot `own` jest ograniczony ⟺ istnieje transza `pending` z tą samą datą
alokacji", `tax/grants.py:352-408`) została w kroku 21 świadomie skonsolidowana do jednego miejsca —
skopiowanie tego SQL-a do `advisor.py` odtworzyłoby dokładnie ten problem, który krok 21 usuwał.
Dlatego:

- **`tax/grants.py`** dostaje `restricted_own_lots()` — surowe fakty per lot, zero wyceny.
  `restricted_own_summary()` zostaje przepisana na delegację do niej; **jej liczby nie zmieniają się
  o grosz** (zasila pulpit `web.py:172` i `portfolio.py::dashboard_buckets`).
- **`nokia_tracker/advisor.py`** (nowy, poziom pakietu — nie `tax/`, bo składa podatki + portfel +
  ustawienia, a nie jest księgą podatkową) robi całą matematykę pieniądza.

### 1.2 `grants.restricted_own_lots(conn, today=None) -> list[dict]`

```python
[{"lot_id", "acquired_date", "original_quantity",  # lots.quantity — MIANOWNIK proporcji
  "qty_remaining", "matched_qty", "match_rate",    # matched_qty / original_quantity
  "free_until", "pending_vest_ids"}]
```

Trzy kroki i trzy pułapki:

1. **Mapa daty → oczekujące dopasowanie**, jak dziś (`grants.py:377-380`), z jedną różnicą: do
   `matched_qty` liczą się **tylko** transze `pending` grantów `program='espp'`. Kilka transz na
   jednej `grant_date` → suma ilości, `free_until = max(effective_date)`.
2. **Wykrycie ograniczenia zostaje niezmienione** — lot jest ograniczony, gdy istnieje JAKAKOLWIEK
   transza `pending` z tą datą (także LTI). Zawężenie do ESPP zmieniłoby `restricted_qty` na
   pulpicie. Skutek: lot ograniczony wyłącznie grantem LTI ma `matched_qty == 0.0` → przepada zero.
   To poprawne — sprzedaż akcji własnych nie unieważnia transzy RSU.
3. **PUŁAPKA: dwa loty `own` w tej samej dacie.** `lots.grant_id` jest martwe (nigdy niezapisywane),
   jedynym powiązaniem jest wspólna data — naiwna pętla przypisałaby każdemu lotowi CAŁE
   `matched_qty` i podwoiła przepadek. Rozdział pro rata po oryginalnej ilości:
   `matched_qty_i = matched_total(date) * quantity_i / Σ_j quantity_j`, gdzie `j` biegnie po
   **wszystkich** lotach `own` z tą datą — także sprzedanych do zera
   (`SELECT * FROM lots WHERE lot_type='own' AND acquired_date=?`, **nie** `open_lots`).
   Na danych produkcyjnych jest jeden lot na datę, więc test tego sam nie znajdzie.

Iteracja po lotach nadal przez `taxlots.open_lots(conn, as_of=today)` — pozycja zamknięta nie ma
czego przepaść.

### 1.3 API `advisor.py`

```python
forfeit_summary(conn, price_eur=None, eurpln_rate=None, today=None) -> dict
# {"forfeit_qty", "forfeit_value_eur"|None, "forfeit_value_pln"|None,
#  "restricted_qty", "restricted_value_pln", "free_until", "days_until_free" (clamp>=0),
#  "items": [restricted_own_lots + forfeit_qty/_value_eur/_value_pln/days_until_free]}

forfeit_for_allocations(allocations, rates_by_lot_id) -> dict   # CZYSTA
forfeit_for_quantity(conn, quantity, price_eur=None, eurpln_rate=None, today=None) -> dict
```

- Kubełek („co jeszcze mam do stracenia") = `Σ match_rate_i * qty_remaining_i`.
- Per ilość („co stracę, sprzedając q") = `Σ match_rate_i * take_i`, gdzie `take_i` to FIFO.
- `forfeit_for_allocations` przyjmuje listę w kształcie `_plan_fifo` (`tax/lots.py:241-248`, klucze
  `lot_id`/`quantity`) — czyli **dosłownie `simulate_sale(...)["lots_consumed"]`**. To hak dla
  przyszłego what-if na `/pit38`: wiersz „utracone dopasowanie" bez nowej matematyki.
- `forfeit_for_quantity` iteruje po **wszystkich** otwartych lotach, nie tylko ograniczonych —
  sprzedaż 15 akcji, gdy pierwsze 10 w FIFO to wolny lot z 2020, przepala 5 z lotu ograniczonego.
  Zwraca `{"sell_qty","forfeit_qty","forfeit_value_eur","forfeit_value_pln",
  "lots_touched":[{lot_id, acquired_date, taken_qty, match_rate, forfeit_qty}]}`.

Uczciwy przypis do docstringa: `qty_remaining` spada tylko wtedy, gdy FIFO faktycznie dotarło do
tego lotu — wcześniejsza sprzedaż mogła zjeść w całości starszy, nieograniczony lot i wtedy
`match_rate * qty_remaining` = pełne dopasowanie. To prawda księgi, nie przybliżenie.

## 2. Oś czasu vestingu

**Nowa funkcja siostrzana, nie rozszerzenie `unvested_summary`** — tamta ma trzech konsumentów
(`sensors.py:322` → encja `sensor.nokia_tracker_next_vest_date` z historią, `web.py:170`,
`portfolio.py:132-138`); zmiana kontraktu = ryzyko bez zysku.

`grants.vesting_timeline(conn, price_eur=None, eurpln_rate=None, today=None) -> dict` (w `grants.py`,
nie `advisor.py` — ta sama tabela, ten sam `_value` z `grants.py:284`; roadmapa sama nazywa to
„pracą prezentacyjną, nie silnikową"). Regułę `overdue` obie funkcje biorą z jednego prywatnego
`_effective_date(row)`, żeby nie dublować `COALESCE(available_from, vest_date)` po raz czwarty.

```python
{"tranches": [{vest_id, grant_id, program, grant_date, vest_date, available_from,
               effective_date, quantity, value_eur|None, value_pln|None,
               overdue, days_until|None, quarter "2026-Q3", year "2026", offset_pct}],
 "buckets": {"this_quarter", "this_year" (KUMULATYWNIE), "next_year", "later"},  # {qty,value_eur,value_pln}
 "overdue": {qty, value_eur, value_pln, count},
 "span": {"first", "last"}}
```

- Sortowanie `(effective_date, vest_id)` — pinowane testem, inaczej kropki na osi lądują losowo.
- Tylko `status == 'pending'`.
- **`overdue` listowane, ale NIE w `buckets`** — ta sama zasada co krok 21 (`grants.py:296-300`).
- `offset_pct` liczony w Pythonie (`(dni(eff)-dni(first))/(dni(last)-dni(first))*100`, przy
  `first==last` → `50.0`), żeby szablon był głupi (`style="left:{{ t.offset_pct }}%"`), a pozycja
  kropki była **testowalna liczbą**.

## 3. Planer ESPP

### 3.1 Uczciwe rozwiązanie „`simulate_sale` potrzebuje istniejących lotów"

`simulate_sale` (`tax/whatif.py:26`) czyta `open_lots(conn)` i rzuca `InsufficientLotsError` —
hipotetyczny lot z przyszłości nie ma prawa tam trafić, a wstawianie atrapy do bazy jest wykluczone.
Ale silnikiem jest wydzielona z niej **czysta** `taxlots._plan_fifo(candidates, sale_quantity,
price_eur, fee_eur, nbp_rate)` (`tax/lots.py:212`), która operuje na zwykłych słownikach.
Zweryfikowane w kodzie: wymaga wyłącznie kluczy `id`, `lot_type`, `acquired_date`, `quantity`,
`qty_remaining`, `cost_pln` (`lots.py:231-247`). Karmimy ją syntetycznymi kandydatami:

```python
candidates = [
  {"id": None, "lot_type": "own",     "acquired_date": horizon_date,
   "quantity": own_shares,     "qty_remaining": own_shares,
   "cost_pln": contributed_eur * eurpln_rate},
  {"id": None, "lot_type": "matched", "acquired_date": horizon_date,
   "quantity": matched_shares, "qty_remaining": matched_shares,
   "cost_pln": matched_shares * price_eur * eurpln_rate},
]
```

To nie podrabianie lotów — to dokładnie scenariusz, dla którego krok 15 wydzielił `_plan_fifo`.
Zero zapisu, zero `conn`.

### 3.2 Wydzielenie pętli polityk (refaktor bez zmiany zachowania)

`whatif.py:57-72` → `_apply_policies(plan, revenue_pln, cfg) -> (policies, active_policy)`, wołane
z `simulate_sale` i z planera, żeby nigdy się nie rozjechały. Istniejące `test_tax_whatif.py`
pinują wynik — refaktor mechaniczny, musi przejść bez zmian w asercjach.

### 3.3 `advisor.espp_plan(...)` — czysta, bez `conn`

```python
espp_plan(monthly_eur, months, price_eur, eurpln_rate=None, match_pct=50.0,
          cost_basis_policy="own_only", tax_pct=19.0, horizon_date=None) -> dict
```

`contributed_eur = monthly_eur*months`; `own_shares = contributed_eur/price_eur`;
`matched_shares = own_shares*match_pct/100`; `end_value_eur = total_shares*price_eur`;
`revenue_pln`/`policies`/`tax_pln`/`net_proceeds_pln` z `_plan_fifo` + `_apply_policies`.
`match_pct` z `cfg["espp_match_pct"]` (istnieje w `settings.py` DEFAULTS = 50.0), `tax_pct` z
`cfg["pl_capital_gains_tax_pct"]` — **nie hardkodować**. Guardy: `months<=0`, `monthly_eur<=0`,
`price_eur<=0` → `ValueError`; `eurpln_rate is None` → sekcja PLN i podatek `None`, część akcyjna
liczy się dalej („milcz uczciwie", wzorzec `unvested_summary`).

Cztery zastrzeżenia **i w docstringu, i w akapicie `.muted` na stronie**:
1. Cena płaska (ta sama przy zakupie i sprzedaży) → przy `all_at_acquisition` podatek wychodzi 0.
2. Podatek liczony na samych nowych akcjach, w izolacji od realnego stosu FIFO.
3. Jeden dzisiejszy kurs EUR/PLN; realne rozliczenie zamraża kurs NBP D-1 na każde zdarzenie.
4. Dopasowanie zakłada dotrwanie do vestingu — sprzedaż wcześniej je kasuje (odsyłacz do karty
   „Ile tracę, sprzedając dziś" wyżej na tej samej stronie).

### 3.4 GET + podgląd JSON (symetria z „Co jeśli sprzedam teraz" na `/pit38`)

- **GET `/plan?espp_monthly=&espp_months=&espp_price=`** — serwer renderuje wynik. Działa bez JS,
  da się zabookmarkować, da się przetestować twardymi liczbami w HTML.
- **`GET /api/preview/espp`** — `NT.initFormPreview` (`static/app.js`), kontrakt jak trzy istniejące
  podglądy: **zawsze HTTP 200**, sukces `{"ok":true,"lines":[{label,value,unit,emphasis}]}`,
  błąd `{"ok":false,"error":"..."}` (`web.py:465-517`).
- **Suwak cenowy = trzy serwerowe chipy-linki** (bieżąca / −20% / +20%) + wolne pole liczbowe.
  Zero nowego JS; na telefonie trzy kciukowe chipy biją `<input type=range>`.

## 4. Ryzyko koncentracji

Dwa nowe klucze ustawień: `other_net_worth_pln` (float, default `0.0`, „Reszta majątku poza akcjami
pracodawcy (zł)") i `concentration_alert_pct` (float, default `25.0`).

**Sześć miejsc na jedno ustawienie** (pominięcie któregokolwiek = opcja cicho się nie zaseeduje):
`config.yaml` `options:` + `schema:`, `run.sh` export, `main.py:69-105` seed, `settings.py`
`SETTINGS_TYPES` + `DEFAULTS`, `templates/settings.html`, `web.py::settings_post` (`web.py:1068-1097`).
Kontrola: `grep -rn other_net_worth_pln` ≥ 6 trafień w kodzie produkcyjnym.

```python
concentration(employer_value_pln, other_net_worth_pln, threshold_pct=25.0) -> dict
# {"employer_value_pln","other_net_worth_pln","total_net_worth_pln",
#  "pct"|None,"threshold_pct","over_threshold","configured"}
```

- `employer_value_pln` = pełna ekspozycja = `dashboard_buckets(...)["total"]["value_pln"]`
  (`portfolio.py:139-143`, czyli `position.market_value_pln + unvested.upcoming_value_pln`;
  `overdue` poza sumą, jak w kubełkach). Nienabyte transze to też ekspozycja i to skorelowana
  z dochodem — wykluczenie ich zaniżałoby dokładnie tę liczbę, o którą chodzi.
- `over_threshold = pct > threshold_pct` (ostro większe — 25.0 przy progu 25.0 nie zapala).
- **`other_net_worth_pln == 0` → `configured=False`, `pct=None`, `over_threshold=False`.**
  Inaczej wyszłoby 100% i ostrzeżenie wrzeszczałoby u każdego, kto nic nie wpisał. Twardy test.
- Ostrzeżenie na karcie `/plan` + sensor `concentration_pct`. **Świadomie NIE w `alerts.py`** —
  tamta maszyneria (dedup, `alert_min_interval_minutes`) jest pod zdarzenia rynkowe; wolno pełzający
  wskaźnik majątkowy nie jest zdarzeniem. Tekst musi zawierać zdanie o korelacji: „To jednocześnie
  Twój pracodawca — spadek kursu i utrata dochodu przychodzą tym samym kanałem."

## 5. Sensory MQTT

Nowa sekcja w `publisher._ENTITIES` (`publisher.py:44-182`):

```python
_Entity("sensor", "forfeit_value_pln",  "Forfeit Value PLN",  "PLN", "monetary", "total", "mdi:cash-remove"),
_Entity("sensor", "concentration_pct",  "Concentration Pct",  "%",   None,       "measurement", "mdi:chart-pie"),
_Entity("sensor", "vest_this_year_qty", "Vest This Year Qty", None,  None,       "measurement", "mdi:calendar-check"),
```

`sensors.advisor_values(conn, cfg, price_eur, eurpln_rate) -> dict`. **Świadome odstępstwo od
`results_values` (`sensors.py:375-388`)**: tam wszystkie klucze wracają `None` bez ceny, bo wszystkie
są od niej pochodne; tutaj `vest_this_year_qty` jest znane bez ceny — zwracanie `None` byłoby
kłamstwem przez przemilczenie. Odstępstwo z uzasadnieniem do docstringa. Zaokrąglenia na granicy:
kwota/procent `round(...,2)`, ilość `round(...,4)`.

Wpięcie: jedna linia w `main.py` po `results_values` (`main.py:217-219`; `cfg` jest już w zasięgu).
**Do łańcucha digestu (`main.py:489-499`) nic nie dopisujemy** — `notifier` tych kluczy nie
konsumuje; komentarz, żeby nikt tego nie „naprawił".

Żeby strona i sensor liczyły dokładnie to samo, obie idą przez jeden kompozytor:
`advisor.overview(conn, cfg, price_eur, eurpln_rate, today=None) -> {"forfeit","timeline",
"concentration","sale_today"|None}`, wołający `portfolio.position_values_auto`,
`grants.unvested_summary`, `grants.vesting_timeline`, własne `forfeit_summary`/`concentration`.
`sale_today` = `taxwhatif.simulate_sale(conn, cfg, restricted_qty, price_eur)` — tu loty **istnieją**,
więc użycie legalne; owinięte w `except (InsufficientLotsError, CostBasisMissingError): None`.

## 6. Strona `/plan`

Trasa `@app.get("/plan")` → `plan_get()` → `plan.html`, wzorzec 1:1 z `/wyniki` (`web.py:761-830`):
`conn=_conn()` / `try` / `finally: conn.close()`, zawsze `active="plan"` i `version=__version__`,
jawne boole stanów pustych. Nawigacja — jedna krotka w `templates/base.html:22-33`, grupa `portfel`,
po `Wyniki`: `('plan_get', 'plan', 'Plan')`.

Cztery karty:
1. **„Ile tracę, sprzedając dziś"** — kafelki: akcje z ograniczeniem, przepadające dopasowanie,
   wartość przepadku (`highlight`), uwolnienie za N dni (`sub` = data). Gdy jest `sale_today`:
   podatek przy sprzedaży dziś + łączny koszt (podatek + przepadek). Tabela per lot: data nabycia ·
   pozostało/z ilu · dopasowanie · wartość · wolne od. Stan pusty: „Żaden lot własny nie jest dziś
   objęty ograniczeniem".
2. **„Harmonogram vestingu"** — kafelki w tym kwartale / w tym roku / w przyszłym roku (szt. + zł),
   pod nimi pozioma szyna `.tl-rail` z kropkami `left:{{ t.offset_pct }}%`. Zaległe jako osobny
   pasek ostrzegawczy **nad** szyną, nigdy w kafelkach.
3. **„Planer ESPP"** — formularz GET + trzy chipy scenariusza + `.preview-box` + kafelki wyniku +
   akapit z czterema zastrzeżeniami + `{{ tax_disclaimer() }}`.
4. **„Ryzyko koncentracji"** — pasek `.conc-bar` (szerokość = `pct`) + kafelki + zdanie o korelacji.
   Stan pusty (`configured==False`): link do `url_for('settings_get')`.

**Mobile-first (kryterium ukończenia z roadmapy):** nowe komponenty definiowane odwrotnie niż reszta
`app.css` — domyślnie oś czasu to pionowa lista, a `@media (min-width:560px)` **dokłada** wariant
poziomy. Na 360 px poziome kropki i tak by na siebie nachodziły. Chipy `min-height:40px` + `flex-wrap`.

## 7. Pliki

**Nowe:** `nokia_tracker/advisor.py`, `nokia_tracker/templates/plan.html`, `tests/test_advisor.py`,
`docs/PLAN_KROK_26_doradca.md`.

**Zmienione:** `tax/grants.py` (`restricted_own_lots`, `vesting_timeline`, `_effective_date`,
`restricted_own_summary` na delegację) · `tax/whatif.py` (`_apply_policies`) · `sensors.py` ·
`publisher.py` · `main.py` (seed + 1 linia łańcucha) · `settings.py` · `web.py` (`/plan`,
`/api/preview/espp`, 2 pola w `settings_post`) · `templates/base.html` · `templates/settings.html` ·
`templates/dashboard.html:104-105` (**dopisanie kwoty** do istniejącego zdania — to dokładnie ta
luka, którą roadmapa nazywa „zdanie ostrzegawcze bez kwoty") · `static/app.css` · `config.yaml` ·
`run.sh` · `__init__.py` → `0.10.0` · `CHANGELOG.md` · `README.md`.

## 8. Plan testów (TDD — czerwony przed zielonym, w tej kolejności)

`tests/test_advisor.py` (nowy) — **przepadek:** pełny kubełek == suma transz `pending`; proporcja po
częściowej sprzedaży (lot 58.49 → `qty_remaining` 29.245, dopasowanie 29.24 ⇒ **14.62**); rozdział
jednego grantu na dwa loty tej samej daty (30/10 przy grancie 20 ⇒ **15.0 i 5.0**, nie 20/20);
mianownik pro rata obejmuje lot sprzedany do zera; suma dwóch transz na jednej dacie + `free_until`
= max; **grant LTI na tej samej dacie: `restricted_qty>0` ale `forfeit_qty==0.0`**; zero gdy nic nie
ograniczone; `days_until_free` w dniach kalendarzowych + clamp do 0; `forfeit_for_quantity` pomija
starszy wolny lot w FIFO (wolny 10 + ograniczony 20 z `match_rate` 0.5, sprzedaż 15 ⇒ **2.5**);
zgodność `forfeit_for_allocations(simulate_sale(...)["lots_consumed"])` z `forfeit_for_quantity`;
wyceny `None` bez ceny.

**Planer:** 200 EUR/mc × 12 @ 8.0 ⇒ `own 300.0`, `matched 150.0`, `end_value_eur 3600.0`; przy
`eurpln_rate=4.0` ⇒ `revenue_pln 14400.0`, `cost_pln 9600.0`, `tax_pln 912.0`,
`net_proceeds_pln 13488.0`; `all_at_acquisition` ⇒ `tax_pln == 0.0` (test dokumentuje płaskość
scenariusza); `match_pct=30` ⇒ 90.0; funkcja czysta (brak `conn`); `ValueError` na zerowej cenie
i zerowych miesiącach; bez FX akcje policzone, `tax_pln is None`.

**Koncentracja/kompozytor:** 100000/300000 ⇒ `pct 25.0`, `over_threshold False`; 100001/300000 ⇒
`True`; `other==0` ⇒ `pct is None`, `configured False` (**nie 100%**); `upcoming_unvested` wliczone
do ekspozycji; `overview` składa trzy części; `overview` przeżywa wyjątek `simulate_sale`
(`sale_today is None`, reszta policzona).

`tests/test_tax_grants.py`: **regresja — `restricted_own_summary` bit-w-bit po refaktorze** (5
istniejących testów `test_restricted_own_summary_*` też bez zmian); `restricted_own_lots` raportuje
`matched_qty` i `original_quantity`; oś czasu posortowana; kubełki kwartał/rok(kumulatywnie)/
przyszły rok; granica kwartału 30.09 vs 01.10; `overdue` listowane ale poza kubełkami;
`offset_pct` 0/100 na krańcach i 50.0 przy jednej transzy; ignoruje `vested`/`cancelled`;
wyceny `None` bez ceny.

`tests/test_tax_whatif.py`: `_apply_policies` wołane bezpośrednio daje to samo co przez
`simulate_sale`.

`tests/test_sensors.py`: trzy klucze obecne; **bez ceny `forfeit_value_pln`/`concentration_pct` są
`None`, ale `vest_this_year_qty` jest liczbą** (pinuje świadome odstępstwo); zaokrąglenia;
koncentracja czyta ustawienie.

`tests/test_publisher.py`: trzy encje z `object_id`; `forfeit_value_pln` = monetary+total;
`concentration_pct` = `%` + brak `device_class` + `measurement`.

`tests/test_settings.py`: default 0.0; **strażnik `set(DEFAULTS) == set(SETTINGS_TYPES)`** (dziś go
brak, a `get_settings` wywali `KeyError` przy zapomnianym DEFAULT).

`tests/test_web.py`: **dopisać `"/plan"` do parametryzacji smoke (`tests/test_web.py:30-32`)** —
inaczej strona nie dostanie sprawdzenia `Cache-Control: no-store`; trio pusty/zapełniony/pochodny jak
dla `/wyniki`; kwota przepadku w HTML (29.24 × 8.0 × 4.0 ⇒ `"936"`); dni do uwolnienia; daty osi
czasu + kubełek kwartału; planer z parametrów GET (`"450"` i `"912"`); chip −20% ma dokładną cenę
w linku; ostrzeżenie koncentracji powyżej progu; stan pusty z linkiem do ustawień; `/api/preview/espp`
zwraca `lines` z HTTP 200; zły input ⇒ HTTP 200 + `ok:false`; **podgląd nic nie zapisuje**
(`COUNT(*) FROM lots` przed i po); `settings_post` zapisuje oba pola; kwota w ostrzeżeniu na pulpicie;
link `/plan` w nawigacji.

Bilans: **699 → ~745 testów.**

## 9. Weryfikacja

1. **Cała sekcja `tax/` jak beton** — `test_tax_*.py` zielone przed i po refaktorze
   `restricted_own_summary` i `_apply_policies` (wzorzec kroku 15).
2. **Przewidzieć liczby przed wdrożeniem, porównać po.** Dane produkcyjne: 142,7294 ograniczonych
   akcji `own`; trzy transze dopasowania 29,24 (alok. 27.10.2025) + 28,99 (02.02.2026) + 17,37
   (27.04.2026) = **75,60606**; cena ~8,222 EUR; wszystkie `available_from` w sierpniu 2026.

   | Wielkość | Przewidywanie |
   |---|---|
   | `vest_this_year_qty` | **75,60606** |
   | `buckets.this_quarter.qty` | **75,60606** (musi się równać powyższemu) |
   | `Σ items.matched_qty` | **75,60606** |
   | `forfeit_qty` | **≈ 71,4** |
   | `forfeit_value_eur` | ≈ **587 EUR** (górna granica 621,63) |

   Dlaczego mniej niż 75,60606: przy 50% trzy transze implikują 58,48+57,98+34,74 = **151,20** akcji
   własnych na tych datach, a ograniczonych jest **142,7294** — brakuje 8,47, czyli ślad znanej
   sprzedaży z 2025-10-27 (`tax/lots.py:100-103`). Jeśli ubytek siedzi w locie z 27.10.2025
   (58,49 → 50,02), przepadek tego lotu = 29,24 × 50,02/58,49 = 25,01, suma **25,01+28,99+17,37 =
   71,37**. **Jeśli `Σ matched_qty == Σ forfeit_qty`, reguła proporcjonalna się nie uruchomiła** —
   albo mianownik wziął `qty_remaining` zamiast `quantity`, albo lot z częściową sprzedażą nie
   został rozpoznany. To najostrzejszy sygnał diagnostyczny w całym kroku.
3. **Playwright na realnym URL-u ingressu** (1920 px + 390 px + tryb ciemny), screenshot **i**
   `browser_console_messages(error)`. Ścieżka `ws_command:"supervisor/api"` → `ingress_session` →
   `document.cookie` działa (kroki 21/23) — próbować jej **przed** proxy GET przez `ha_manage_addon`.
4. **Sweep PII na diffie przed każdym pushem** — repo publiczne, realne wyciągi zawierają imię,
   adres i ID pracownika. Fikstury syntetyczne, nigdy kopiuj-wklej z realnego wyciągu.
5. **Wdrożenie bezpieczną ścieżką** (add-on trzyma realne dane podatkowe): push →
   `gh release create v0.10.0` z potwierdzeniem `isDraft:false` → `homeassistant.update_entity` na
   `update.nokia_tracker_update` → poll `ha_get_addon` aż `version_latest=="0.10.0"` →
   `ha_manage_addon(action="update")` na slugu `5f59858c_nokia_tracker`.
   **Nigdy** cyklu uninstall/remove_repository/add_repository/install — on kasuje SQLite.
6. `README.md` (tabele encji + opis stron) i `CHANGELOG.md` w tym samym wydaniu.

## 10. Świadomie poza krokiem

Brak `net_worth_entity` (decyzja użytkownika) · brak alertu koncentracji w `alerts.py` (§4) · brak
„ile sprzedać, żeby zapłacić dokładnie X podatku" (wymaga solvera — 0.11.0/0.12.0) · brak kolumny
przepadku w symulacji na `/pit38` (hak `forfeit_for_allocations` powstaje teraz, użycie później) ·
planer nie modeluje dryfu FX ani ścieżki ceny · **`lots.grant_id` zostaje martwe — nie „naprawiać"
przy okazji**, bo jedynym powiązaniem matched↔own jest wspólna data i cały krok 26 na tym stoi.

## 11. Pułapki

1. Refaktor `restricted_own_summary` nie może zmienić ani jednej liczby (test regresji **przed**).
2. Podwójne liczenie przy dwóch lotach `own` na jednej dacie — najłatwiejsze do przeoczenia,
   bo produkcja ma jeden lot na datę.
3. Mianownik pro rata musi obejmować loty sprzedane do zera ⇒ `SELECT ... FROM lots`, nie `open_lots`.
4. `simulate_sale` na kubełku ograniczonym może rzucić — `/plan` i `advisor_values` łapią, nigdy 500.
5. Dzielenia przez zero: `original_quantity`, `total_net_worth_pln`, `price_eur`, `span` przy jednej
   transzy.
6. Granica kwartału `(month-1)//3+1`, test po obu stronach 30.09/01.10.
7. `_value` zwraca `(None, None)` bez ceny (`grants.py:284-288`) — każdy `round()` w dół łańcucha
   musi to przewidzieć, inaczej `TypeError` przy publikacji MQTT.
8. `stat()` przyjmuje **sformatowany string**, białe znaki w `.stat-value` pinowane testem — nigdy
   surowy float.
9. Zakres filtrów `money/qty/pct` (`format.py:4-8`): `/plan` to strona agregatów, więc filtry są
   właściwe — z wyjątkiem tabeli przepadku per lot, gdzie użytkownik zestawia ilości z wyciągiem
   Computershare ⇒ tam `qty(x, decimals=4)`.
10. Sześć miejsc na jedno ustawienie (§4).
11. **`espp_match_pct` używa planer, ale NIE silnik przepadku** — przepadek liczy się z rzeczywistych
    ilości transz z wyciągu (fakt), nie z procentu z ustawień (założenie). Pomylenie tych źródeł daje
    liczbę, która wygląda dobrze i jest fałszywa.
12. Nawigacja renderuje się na każdej stronie — zły `endpoint` w krotce `base.html` wywala całą
    aplikację, nie tylko `/plan`.
13. Nowy CSS pisany mobile-first (`min-width`), odwrotnie niż reszta `app.css` (`max-width`) —
    opisać w komentarzu, żeby nie wyglądało na niespójność.

## Pierwszy krok implementacji

Skopiować ten dokument do repo jako `docs/PLAN_KROK_26_doradca.md` **zanim powstanie pierwsza linia
kodu** (zasada z `feedback_plans_as_md`), commit osobno.
