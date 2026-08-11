# Krok 21 — całkowite zestawienie portfela na pulpicie (wolne / z ograniczeniem / zablokowane)

## Context

Karta **Portfel** na pulpicie (`/`) pokazuje dziś jedną liczbę: `2 887,05` szt. — sumę
otwartych lotów. Nie widać ani akcji **zablokowanych** (nienabyte dopasowania ESPP i transze
LTI), ani tego, że część posiadanych akcji jest **objęta ograniczeniem zbycia**. Realnie
pulpit pokazuje ~68% majątku w tym planie i sugeruje, że całe 2 887,05 można sprzedać
od ręki — co nieprawda.

### Struktura, którą Computershare naprawdę raportuje

Audyt najnowszego wyciągu (stan na 2026-07-26, kurs 8,222 EUR) — trzy kubełki, nie dwa:

| Kubełek w wyciągu | Ilość | PLN | Zawartość |
|---|---|---|---|
| `Available for trading` | 2 737,450985 | 97 055,80 | LTI już nabyte (2 734) + drobne ESPP (dywidendowe 2,97 + dopasowanie 0,48) |
| `Available with restrictions` | 151,21213 | 5 361,20 | Zakupy własne: 27 Oct 2025 (58,49), 2 Feb 2026 (57,97), 27 Apr 2026 (34,75) |
| `Locked / Restricted stock units` | 1 341,60606 | 47 566,38 | Dopasowania ESPP (75,60606) + transze LTI 2027/2028 (1 266) |
| **Razem** | **4 230,269175** | **149 983,38** | zgadza się z „Your portfolio" ze str. 1 ✓ |

Weryfikacja spójności: str. 1 podaje `Locked 47 566,38` + `Available 102 417` = `149 983,38`;
`Assets by type` 2 888,663115 + 1 341,60606 = 4 230,269175 ✓.

Strona 4 potwierdza, że nabyte akcje LTI (2 734) są w czystym `Available`, **bez** podsekcji
„with restrictions" — ograniczenie dotyczy wyłącznie zakupów własnych z planu ESPP.

### Reguła ograniczenia — wyprowadzalna z danych, bez nowego parsera

Lot `własne` jest ograniczony dokładnie wtedy, gdy istnieje transza `pending`, której
`Allocation Date` równa się dacie nabycia lotu. Sprawdzone: transze `pending` mają alokacje
27 Oct 2025 / 2 Feb 2026 / 27 Apr 2026 — dokładnie te trzy daty, które wyciąg wymienia
w `Available with restrictions`. Ekonomia planu się zgadza: sprzedaż akcji przed nabyciem
dopasowania oznacza jego utratę.

Ta reguła **sama się aktualizuje** — gdy dopasowanie zvestuje, transza przestaje być
`pending` i lot automatycznie staje się wolny. Parsowanie sekcji `Available with restrictions`
dałoby to samo, ale jako zdjęcie stanu na dzień wyciągu, które zestarzeje się w tydzień.
Dlatego: wyprowadzenie, nie parsowanie.

> Uwaga do zapisania w kodzie: `lots.acquired_date` to **Trade Date** (decyzja z kroku 13),
> a dopasowanie idzie po `grants.grant_date` = **Allocation Date**. W realnych danych te daty
> są identyczne we wszystkich sześciu przypadkach, ale to zbieżność tego wyciągu, nie gwarancja
> — lot bez pasującej transzy `pending` traktujemy jako wolny (bezpieczny kierunek błędu:
> nie zawyżamy ograniczeń).

### Przyczyna źródłowa fałszywych „zaległych": importer wyrzuca kolumnę `Available from`

Harmonogram ma trzy daty: `Allocation` / `Vesting` / **`Available from`**. Akcje wpadają
na konto w dacie *Available from*, nie *Vesting*:

| Transza | Vesting date | Available from | Realny lot w bazie |
|---|---|---|---|
| ESPP 24,42 (zakup 2024-10-21) | 2025-08-01 | **2025-08-28** | Withhold-to-Cover Typ A 101,3967 z **2025-08-28** |
| ESPP 29,24 (zakup 2025-10-27) | 2026-08-01 | **2026-08-27** | jeszcze nie |
| ESPP 28,99 (zakup 2026-02-02) | 2026-08-01 | **2026-08-27** | jeszcze nie |
| ESPP 17,37 (zakup 2026-04-27) | 2026-08-01 | **2026-08-01** | jeszcze nie |
| LTI 634 (RS AWARD 2025) | 2026-07-05 | **2026-07-09** | lot `lti` 634 z **2026-07-09** ✓ |
| LTI 2100 (RS AWARD 2023) | 2026-07-06 | **2026-07-09** | lot `lti` 2100 z **2026-07-09** ✓ |

`importers/computershare_pdf.py` **już parsuje** `available_from` (zwracają go zarówno
`parse_matching_shares`, jak i `parse_rs_award`) — `import_statement()` go nie zapisuje,
a tabela `vests` nie ma na niego kolumny. Skutkiem jest ~4-tygodniowe okno, w którym każda
sierpniowa transza ESPP wygląda na „zaległą", choć jest po prostu przed terminem księgowania.
Ta sama data jest potrzebna do pokazania, **do kiedy** trwa ograniczenie zakupów własnych.

### Duch 24,42 — audyt zamknięty, naprawa POZA tym krokiem (decyzja użytkownika)

Transza zniknęła z tabeli „Matching Shares" między wyciągiem 3. a 4., a dokładnie w jej dacie
`Available from` (2025-08-28) wpadł zbiorczy Withhold-to-Cover Typ A na 101,396662 szt.
`reconcile_vesting` nie mógł tego dopasować, bo wymaga dokładnej i jednoznacznej zgodności
ilości (Computershare zagregował kilka źródeł w jeden wiersz). Cały ten lot został sprzedany
2025-10-27 (`qty_remaining` = 0). Potwierdza to też najnowszy wyciąg: 24,42 **nie występuje**
w `Locked` (tam jest tylko 75,60606). Akcje były, już ich nie ma, wiersz w harmonogramie został
jako `pending`. Ten krok **tylko to sygnalizuje** — zero zmian w danych podatkowych.

### Decyzje użytkownika przyjęte do planu

- Zakres UI: **wyłącznie karta Portfel na pulpicie** (nie `/portfel`, bez nowych encji MQTT).
- P&L i „Całkowity zwrot" liczone **z całej posiadanej pozycji** — czyli bez żadnej zmiany
  matematyki: akcje z ograniczeniem są posiadane i mają koszt bazowy, ograniczenie dotyczy
  tylko możliwości zbycia. Zablokowane (nienabyte) do P&L **nie** wchodzą — mają zerowy koszt
  i wrzucenie ich napompowałoby zysk oraz rozjechało się z `/pit38` i symulacją „co jeśli sprzedam".
- „Razem" = posiadane + zablokowane nieprzeterminowane; przeterminowane poza kwotą, jako
  osobna linia ostrzeżenia.

## Etap 1 — `available_from` jako realna data dostępności

Bez tego i „zablokowane", i „do kiedy ograniczone" musiałyby zgadywać heurystyką, mając
w wyciągu dokładną datę. Zakres: ~40 linii + testy.

**`nokia_tracker/db.py`** — migracja `v5` na końcu `_MIGRATIONS` (wzorzec jak `v4`, `db.py:220-233`):
```sql
ALTER TABLE vests ADD COLUMN available_from TEXT;
```

**`nokia_tracker/tax/grants.py`**
- `add_vest(...)` — nowy opcjonalny parametr `available_from: str | None = None`. Idempotencja
  po `natural_key` bez zmian.
- Nowa `backfill_available_from(conn, vest_id, available_from)` — `UPDATE ... WHERE
  available_from IS NULL`. **Łatwe do przeoczenia:** `add_vest` przy istniejącym `natural_key`
  zwraca wcześnie i nigdy nic nie zaktualizuje, więc bez tej funkcji kolumna zostałaby pusta
  na zawsze dla wszystkich obecnych transz.
- `list_espp` / `list_lti_grouped` — `overdue` po `COALESCE(available_from, vest_date) < today`.
  Fallback konieczny: do ponownego wgrania wyciągu wszystkie istniejące wiersze mają NULL.

**`nokia_tracker/importers/computershare_pdf.py`** — w `import_statement()` przekazać
`row["available_from"]` do `add_vest` (obie gałęzie: ESPP ~linia 506, LTI ~linia 544) i wywołać
`backfill_available_from` w gałęzi „transza już istnieje i ilość się zgadza" (~linie 508 i 546).
Parsery zostają nietknięte — wartość jest tam od kroku 13.

## Etap 2 — dwie funkcje czyste jako jedno źródło prawdy

Obie w **`nokia_tracker/tax/grants.py`**, obok istniejącej `valuation()`.

```python
def unvested_summary(conn, price_eur=None, eurpln_rate=None, today=None) -> dict
```
Jedno zapytanie po `vests` ze `status='pending'`, podział po `COALESCE(available_from, vest_date)`
względem `today`. Zwraca `pending_qty`, `upcoming_qty`/`_value_eur`/`_value_pln`,
`overdue_qty`/`_value_eur`/`_value_pln`, `next_vest_date`, `next_vest_qty`, `overdue_items`.
Wyceny `None` gdy brak ceny/kursu — ta sama zasada „milcz uczciwie zamiast zmyślić", co
w `sensors.whatif_values`.

```python
def restricted_own_summary(conn, price_eur=None, eurpln_rate=None, today=None) -> dict
```
Otwarte loty `own` (przez istniejące `taxlots.open_lots`) złączone z grantami mającymi
transzę `pending` po `grants.grant_date == lots.acquired_date`. Zwraca `restricted_qty`,
`restricted_value_eur`/`_pln`, `free_until` (najpóźniejsze `COALESCE(available_from, vest_date)`
— data, od której wszystko jest wolne) oraz `items` (per lot: data nabycia, ilość, data uwolnienia).

**`nokia_tracker/sensors.py::grants_values`** — przepisać na delegację do `unvested_summary(conn)`,
mapując na dotychczasowe 3 klucze. Cel: jedna definicja „nienabytego" zamiast dwóch rozjeżdżających się.

> ⚠️ **Świadoma zmiana zachowania encji MQTT:** `sensor.nokia_tracker_next_vest_date` przestanie
> pokazywać `vest_date`, a zacznie `available_from` — realnie z `2027-07-05` na `2026-08-27`
> (po wgraniu świeżego wyciągu). To poprawka, nie regresja: data dostępności jest tym, na co się
> czeka. `unvested_qty` bez zmian. Do odnotowania w CHANGELOG.

## Etap 3 — karta Portfel na pulpicie

**`nokia_tracker/web.py::dashboard`** (~linia 148) — po wyliczeniu `position`:
```python
unvested   = grantsm.unvested_summary(conn, values.get("price_eur"), values.get("eurpln_rate"))
restricted = grantsm.restricted_own_summary(conn, values.get("price_eur"), values.get("eurpln_rate"))
```
`grantsm` jest już zaimportowane (używa go `grants_get`); `values["eurpln_rate"]` to ten sam kurs,
którym liczy się `position` — bez drugiego źródła.

**`nokia_tracker/templates/dashboard.html`** — karta „Portfel" (linie 54-95) dzielona na trzy bloki
`.subcard` (klasa istnieje, `app.css:103-110`), wszystkie kafelki przez istniejące makro `stat()`:

1. **„W posiadaniu"** — obecne 5 kafelków, **liczby bez zmian** (P&L nadal z całej pozycji).
   Pod nimi jedna linia podziału: `wolne <N>` · `z ograniczeniem <M> — do <data>`, z krótkim
   wyjaśnieniem „sprzedaż przed tą datą oznacza utratę dopasowania 50%" i linkiem do `/granty`.
   Gdy `restricted_qty == 0` linia znika w całości.
2. **„Zablokowane — jeszcze nienabyte"** — `Ilość` · `Wartość szacunkowa` (EUR, `sub` = ≈ zł) ·
   `Najbliższa dostępność` (data, `sub` = ilość). Bez kosztu i bez P&L.
   Gdy `overdue_qty > 0`, pod spodem `<p class="disclaimer">` z liczbą, wyjaśnieniem („data
   dostępności minęła, a akcji nie ma w żadnym locie — wgraj najnowszy wyciąg") i linkami do
   `/granty` i `/importy`. Jawnie: **nie są wliczone w „Razem"**.
3. **„Razem"** — `Ilość razem` · `Wartość szacunkowa razem` (EUR + `sub` PLN), kafelki
   `cls='highlight'` (`app.css:250`). Razem = posiadane + `upcoming`.

Istniejąca stopka (dywidendy, „Edytuj stan posiadania", linia o kursie EUR/PLN) zostaje;
do linii o kursie dopisać, że wycena zablokowanych też idzie po kursie bieżącym — czyli szacunek.

## Testy (TDD, każdy plik najpierw czerwony)

- `tests/test_tax_grants.py` — `unvested_summary`: podział po `available_from`; fallback na
  `vest_date` gdy NULL; `price_eur=None` → wyceny `None`; pusta baza → zera; `overdue_items`.
  `restricted_own_summary`: lot `own` z pasującą transzą `pending` → ograniczony; ta sama transza
  po zvestowaniu → lot wolny; lot `own` bez pasującego grantu → wolny; loty `lti`/`matched`/
  `dividend_drip` nigdy nie są ograniczone; `free_until` = najpóźniejsza data.
  Plus `add_vest` z `available_from` i `backfill_available_from` (w tym: nie nadpisuje wartości
  już ustawionej).
- `tests/test_computershare_pdf_import.py` — `available_from` trafia do bazy przy pierwszym
  imporcie i **jest uzupełniane przy ponownym imporcie** istniejącej transzy.
- `tests/test_sensors.py` — `grants_values` nadal zwraca 3 klucze; `next_vest_date` po `available_from`.
- `tests/test_web.py` — pulpit z realistycznymi danymi renderuje trzy bloki z poprawnymi liczbami;
  linia podziału wolne/z ograniczeniem pojawia się i znika; ostrzeżenie o zaległych pojawia się
  i znika; pusta baza (zero grantów, zero lotów) nie wywala szablonu.
- `tests/test_db.py` — migracja `v5` przechodzi na bazie utworzonej na `v4`.

Baza: 529 testów zielonych na `0.5.3`.

## Wydanie

`0.5.3` → **`0.6.0`** (migracja schematu + nowa sekcja UI + zmiana semantyki encji).
Bump w **obu** plikach: `nokia_tracker/config.yaml` i `nokia_tracker/nokia_tracker/__init__.py`.
Plan skopiować do repo jako `docs/PLAN_KROK_21_portfel_calkowity.md` **przed pisaniem kodu**.

**Deploy bezpieczną ścieżką — NIE cyklem uninstall/reinstall** (baza zawiera realne dane
podatkowe): push → `gh release create v0.6.0` (sprawdzić `isDraft: false`) →
`homeassistant.update_entity` na `update.nokia_tracker_update` → poll ~65 s →
`ha_manage_addon(action="update")` na slugu `5f59858c_nokia_tracker`.

## Weryfikacja

1. `pytest` — pełny pakiet zielony lokalnie przed pushem.
2. Po deployu: logi Supervisora bez błędów, `ha_get_addon` → `version: "0.6.0"`,
   `update_available: false`.
3. `ha_manage_addon` proxy `GET /` — karta zawiera trzy bloki. Oczekiwane liczby **przed**
   wgraniem świeżego wyciągu (wszystkie `available_from` NULL → fallback na `vest_date`),
   przy kursie 8,04 EUR / 4,2973:

   | Pozycja | Ilość | ≈ EUR | ≈ PLN |
   |---|---|---|---|
   | W posiadaniu | 2 887,05 | 23 212 | 99 750 |
   | — w tym wolne | 2 744,32 | | |
   | — w tym z ograniczeniem (do 2026-08-01) | 142,73 | | |
   | Zablokowane | 1 266,00 | 10 179 | 43 741 |
   | Zaległe (poza sumą) | 100,02 | 804 | 3 455 |
   | **Razem** | **4 153,05** | **33 391** | **143 493** |

4. Poprosić użytkownika o wgranie najnowszego wyciągu przez `/importy` (upload binarny nie
   przechodzi przez proxy `ha_manage_addon` — musi to zrobić sam). Po nim `available_from`
   zostaje uzupełnione i podział ma się przestawić na: zablokowane **1 324,23** (1 266 + 29,24
   + 28,99), zaległe **41,79** (24,42 duch + 17,37 dostępne od 2026-08-01), ograniczenie
   do **2026-08-27**. Ta zmiana jest dowodem, że backfill zadziałał.
5. `ha_get_state` na `sensor.nokia_tracker_unvested_qty` (bez zmiany, 1 366,02) i
   `sensor.nokia_tracker_next_vest_date` (zmiana na datę dostępności).
6. Playwright: screenshot 1920 px + 390 px pulpitu + `browser_console_messages(error)` pusty.
   Jeśli ścieżka ingress/Playwright jest nadal zablokowana — proxy GET jak w pkt 3 jako uzgodniony
   substytut, z jawnym zaznaczeniem, że brak weryfikacji wizualnej i konsoli.

## Poza zakresem (świadomie)

- Naprawa ducha 24,42 w danych — audyt zamknięty, decyzja co dalej osobno.
- Poprawa `reconcile_vesting`, żeby radził sobie ze zbiorczymi wierszami Withhold-to-Cover
  (101,3967 = kilka źródeł w jednym) — prawdziwa luka, ale własny krok.
- Parsowanie sekcji `Available for trading` / `Available with restrictions` jako kontrola
  krzyżowa ograniczenia (analogicznie do `reconcile_holdings` dla sumy) — wzmocniłoby regułę
  wyprowadzenia, ale nie jest do niej potrzebne.
- Strona `/portfel` i nowe encje MQTT z wyceną zablokowanych i ograniczonych.
