# Nokia Tracker — krok 15: PIT-38, „co jeśli sprzedam teraz", eksporty + wydanie 0.2.0

## Context

Add-on `nokia_tracker` (repo `miczu71/nokia_tracker`, live slug `5f59858c_nokia_tracker`) jest
na wersji **0.1.4** i działa produkcyjnie z **realnymi danymi podatkowymi użytkownika**: loty
z importu PDF Computershare (własne/matched/LTI/DRIP), zaksięgowana sprzedaż (przychód
17 596,49 PLN), transze vestingu i rejestr dywidend. Kroki 0–14 z `docs/BLUEPRINT.md` są
zamknięte, 416 testów zielonych.

Zostaje **krok 15** — ostatni przed publicznym wydaniem 0.2.0. To on zamienia zebrane dane
w produkt końcowy: roczny raport PIT-38 z pełnym śladem obliczeń, symulację „co jeśli sprzedam
teraz" (spinającą podatek z rekomendacją AI) oraz eksporty. Bez niego użytkownik ma poprawnie
zaksięgowane loty, ale nadal musi ręcznie złożyć z nich deklarację.

Po drodze domykana jest jedna świadomie odłożona luka z kroku 13: `dividends.pl_tax_due_pln`
jest `NULL`, bo orkiestracja „u źródła → zaliczenie traktatowe → Belka" nigdy nie została
policzona **na zamrożonym kursie NBP**. Sekcja G PIT-38 tego wymaga — liczenie jej na kursie
bieżącym byłoby niezgodne z art. 11a ustawy o PIT.

## Zakres (5 części + wydanie)

### 1. Domknięcie sekcji G — podatek od dywidend w PLN na kursie zamrożonym
**Plik:** `nokia_tracker/nokia_tracker/tax/dividends.py`

- `compute_dividend_tax_pln(row, cfg) -> dict` — ten sam łańcuch co istniejące
  `compute_dividend_tax()` (u źródła → `min(pobrane, cap traktatowy)` → Belka → dopłata PL +
  kwota do odzysku z Vero), ale w PLN, przeliczone `row["nbp_rate"]` zamrożonym na Record Date.
- `backfill_pl_tax_due(conn, cfg) -> int` — uzupełnia `pl_tax_due_pln` dla wierszy, gdzie jest
  `NULL` **lub** gdzie zmieniły się stawki; wzorzec 1:1 z `tax/lots.py::backfill_missing_rates`
  (wołane z web route ORAZ z dziennego joba `main.py::backfill_nbp_rates`).
- **Semantyka do zapisania w docstringu:** zamrożony jest **kurs NBP**, nie stawki procentowe —
  `treaty_withholding_pct`/`pl_capital_gains_tax_pct` stosowane są w momencie przeliczenia, więc
  zmiana ustawień przelicza kwoty PLN po kursach z dnia zdarzenia. To celowe, nie niedopatrzenie.

### 2. `tax/pit38.py` — roczny raport ze śladem obliczeń
**Nowy plik.** Jedna funkcja wejściowa `annual_report(conn, cfg, year) -> dict`:

| Sekcja | Zawartość | Skąd |
|---|---|---|
| poz. C (kapitały) | przychód / koszt / dochód-strata / podatek 19% — **wszystkie trzy polityki obok siebie** | reuse `tax/policy.py::compute_all_policies(conn, cfg, year=...)` — bez duplikowania logiki |
| sekcja G | dywidendy: brutto PLN, pobrane u źródła, zaliczenie ograniczone traktatem, Belka, dopłata w PL | część 1 wyżej |
| PIT/ZG | dochód zagraniczny + podatek zapłacony za granicą, kraj = FI | ta sama pula dywidend |
| ślad per lot | per sprzedaż → per alokacja: lot_id, data nabycia, `lot_type`, ilość, kurs NBP + jego data, koszt PLN, przychód PLN | `sale_allocations` JOIN `lots`/`sales` |
| do odzysku z Vero | nadwyżka ponad stawkę traktatową, PLN | część 1 wyżej |

Strata z akcji **nie** obniża podatku od dywidend — dwa rozdzielne strumienie, tak jak już
robi `compute_all_policies` (`max(0.0, ...)`).

### 3. `tax/whatif.py` — „co jeśli sprzedam teraz"
**Nowy plik + refaktor bez zmiany zachowania w `tax/lots.py`.**

`lots.py::_allocate_fifo()` dziś liczy alokację **i** zapisuje ją do bazy w jednej pętli.
Symulacja musi dostać dokładnie tę samą kolejność FIFO i tę samą matematykę kosztu na akcję,
ale nic nie zapisać. Refaktor: wydzielić czystą funkcję
`_plan_fifo(open_lots, quantity, price_eur, fee_eur, nbp_rate) -> list[dict]` (żadnego `conn`,
żadnego `INSERT`), którą wołają obydwaj: `_allocate_fifo` (potem zapisuje) i `whatif`
(tylko czyta). Testy `test_tax_lots.py` muszą zostać zielone bez zmian — to dowód, że refaktor
niczego nie zmienił.

`simulate_sale(conn, cfg, quantity, price_eur, fee_eur=0)` zwraca: które loty zjada sprzedaż
(z datami i typami), przychód/koszt/dochód, podatek we wszystkich trzech politykach, kwotę
netto na rękę. Kurs: NBP D-1 od dzisiaj (`fx_nbp.rate_for_event`) — **bez zapisu do bazy**.

### 4. Sensory MQTT (5 nowych)
**Pliki:** `nokia_tracker/publisher.py` (`_ENTITIES` + `object_id` jak dotąd), `sensors.py`
(nowa `pit38_values(conn, cfg)`), `main.py::publish_sensors()`.

`pit38_income_pln`, `pit38_tax_pln`, `pit38_dividend_due_pln`, `pit38_reclaimable_pln`,
`whatif_sell_all_tax_pln` (podatek, gdyby sprzedać dziś całą otwartą pozycję po cenie bieżącej).

### 5. Web UI — strona `/pit38` + eksporty
**Pliki:** `nokia_tracker/web.py`, `templates/pit38.html`, `templates/base.html` (nav),
`static/app.css` (`@media print`).

- `GET /pit38` — selektor roku (domyślnie `cfg["tax_year"]` lub bieżący), tabela trzech polityk,
  sekcja G, PIT/ZG, rozwijany `<details>` ze śladem per lot, klauzula (ten sam tekst
  `disclaimer` co `/lots`), formularz „co jeśli sprzedam teraz" (ilość + cena prefill z ceny
  bieżącej) — **GET/read-only, więc bezpieczny do przeklikania na żywych danych**.
- `GET /pit38?print=1` — układ do druku (`@media print`: ukryty nav/formularze, rozwinięty ślad);
  PDF robi przeglądarka. Świadomie **bez `reportlab`** — brak kół musl, wymagałby `gcc` w obrazie.
- `GET /pit38/export.csv` — `csv.writer` + `io.StringIO`, wzorzec z
  `pv_roi_tracker/pv_roi_tracker/web.py::export_csv()` (mimetype, `Content-Disposition`).
- `GET /pit38/export.xlsx` — `openpyxl` (czysty Python, działa na musl bez budowania), arkusze:
  Podsumowanie / Ślad per lot / Dywidendy.
- `requirements.txt`: dopisać `openpyxl==3.1.5` (przypięte, jak reszta).

Wszystkie linki/formularze przez `url_for()` — wymóg middleware ingress z kroku 9.

### 6. Wydanie 0.2.0
- `README.md`: nowa sekcja o silniku podatkowym (PIT-38, trzy polityki, sekcja G, what-if,
  eksporty) + tabela 5 nowych encji + klauzula „kalkulator pomocniczy, nie doradztwo podatkowe".
- `CHANGELOG.md`: wpis 0.2.0 obejmujący kroki 11–15 (dotąd wydane były tylko 0.1.x).
- Bump **obu** plików: `nokia_tracker/config.yaml` i `nokia_tracker/nokia_tracker/__init__.py`
  → `0.2.0`.
- **Opublikowany** (nie draft) release `v0.2.0` przez `gh release create` z tabelami encji
  w treści (per `feedback_release_notes`).

## Branch i commity

`main` (per `feedback_use_main_branch`). Commity per część, w kolejności TDD:
`Krok 15: plan implementacji` → `(1/5) sekcja G` → `(2/5) pit38.py` → `(3/5) whatif + refaktor
_plan_fifo` → `(4/5) sensory` → `(5/5) web UI + eksporty` → `README/CHANGELOG` →
`Bump version to 0.2.0`.

Pierwszy commit kopiuje ten plan do `docs/PLAN_KROK_15_pit38.md` (per `feedback_plans_as_md`).

## Komendy

```bash
# testy po każdej części (TDD: test najpierw, ma failować z właściwego powodu)
cd /config/addons/nokia_tracker/nokia_tracker && python3 -m pytest -q

# OBOWIĄZKOWY sweep PII przed KAŻDYM pushem (repo jest publiczne, PDF-y zawierają
# nazwisko/adres/ID pracownika) — per lekcja z kroku 13
cd /config/addons/nokia_tracker && git diff --cached | grep -inE '<imię>|<nazwisko>|13219230|<adres>'

git push && gh release create v0.2.0 --repo miczu71/nokia_tracker --title ... --notes-file ...
```

## Deploy — ścieżka wydania, NIE cykl przeinstalowania

> ⚠️ **Add-on trzyma realne dane podatkowe użytkownika.** Cykl
> `uninstall → remove_repository → add_repository → install → start` **kasuje SQLite**
> (potwierdzony incydent 2026-07-28, patrz `reference_supervisor_git_addon_rebuild`).
> **Nie wolno go użyć w tym kroku.**

Poprawna droga (ta sama, którą wjechały 0.1.3/0.1.4 na żywo):
1. `gh release create` — opublikowany release.
2. Odświeżenie sklepu: `homeassistant.update_entity` na `update.nokia_tracker_update` + poll
   (~1 min) — per `reference_supervisor_store_reload`.
3. `ha_manage_addon(action="update", slug="5f59858c_nokia_tracker")` — zachowuje `/data`.
4. `ha_get_addon(slug=...)` → `version == "0.2.0"`, `update_available: false`, `state: started`.

## Weryfikacja

1. **Testy:** `python3 -m pytest -q` zielone; ~40 nowych testów (pit38 / whatif / sekcja G /
   eksporty / routes), 416 → ~455. Kluczowe przypadki:
   - dywidendy: przykład 100 € / 35% / 15% / 19% → 4 € dopłaty i 20 € do odzysku, ale **w PLN
     po kursie zamrożonym**, nie bieżącym;
   - `_plan_fifo` daje tę samą alokację co zapisany `sale_allocations` na tych samych danych
     (dowód, że symulacja nie kłamie);
   - `whatif` **nie zmienia** `lots.qty_remaining` ani liczby wierszy w `sales` (asercja przed/po);
   - raport za rok zamknięty jest stabilny — dwa wywołania z rzędu dają identyczny wynik;
   - trzy polityki w raporcie == trzy polityki na `/lots` (ten sam `compute_all_policies`).
2. **Na żywo (read-only, bezpieczne na realnych danych):** `ha_manage_addon` proxy `GET /pit38`,
   `GET /pit38?print=1`, `GET /pit38/export.csv` — sprawdzenie, że raport renderuje się na
   **realnych** lotach użytkownika i że kwoty zgadzają się z tabelą trzech polityk na `/lots`
   (17 596,49 PLN przychodu). Playwright/ingress pozostaje zablokowany — proxy GET to
   zaakceptowany substytut dla tego add-onu.
3. **Encje:** `ha_search` na `sensor.nokia_tracker_pit38_*` i `..._whatif_*` po deployu —
   potwierdzenie `entity_id` (mechanizm `object_id` z kroku 7 powinien je nadać poprawnie od razu).
4. **Zależność:** po deployu w logach Supervisora brak `ModuleNotFoundError: openpyxl`;
   `GET /pit38/export.xlsx` zwraca 200 i niepusty plik.
5. **Wersja:** `ha_get_addon` → `version == version_latest == 0.2.0`, `gh release view v0.2.0
   --json isDraft` → `false`.

## Ryzyka

| Ryzyko | Mitygacja |
|---|---|
| **Utrata realnych danych podatkowych przy deployu** | Tylko ścieżka release + `update`; cykl uninstall/reinstall zakazany w tym kroku (sekcja wyżej) |
| **PII w publicznym repo** (PDF-y zawierają nazwisko/adres/ID) | Sweep `git diff --cached` przed każdym pushem; testy na realnych plikach zostają za `skipif` na `/config/akcje_temp` |
| Refaktor `_allocate_fifo` psuje zaksięgowaną sprzedaż | Czysta funkcja + istniejące `test_tax_lots.py` bez zmian jako test regresyjny; refaktor w osobnym commicie |
| `openpyxl` nie zainstaluje się na musl | Czysty Python, bez rozszerzeń C — ale weryfikacja w punkcie 4 jest twarda, nie założeniowa |
| Raport nie zgadza się z ręcznym rozliczeniem użytkownika | Ślad per lot jest widoczny w UI i eksportach — rozbieżność da się wskazać co do lotu, zamiast „silnik mówi X" |
| Symulacja what-if zapisuje coś do bazy | Asercja przed/po w teście + brak `conn.commit()` w ścieżce `whatif` |
