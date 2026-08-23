# nokia_tracker E7 — uzgodnienie z wyciągiem (0.23.0)

## Context

`nokia_tracker` jest na **0.22.1** (live, `update_available: false`, working tree czysty i zgodny
z `origin/main`). Etapy E1–E6 roadmapy v3 (`docs/ROADMAP_V3.md`) są wydane i zweryfikowane na
produkcji. Następny etap to **E7 — Uzgodnienie z wyciągiem** (`ROADMAP_V3.md:359-373`).

E7 realizuje czwartą i ostatnią nienapisaną pozycję z listy „budowanie zaufania" z wywiadu, który
napędza całą roadmapę v3. Pierwsza przeszkoda nazwana przez użytkownika brzmiała: *brak zaufania do
liczb*. E1/E2 dały audyt i niezmienniki (spójność **wewnętrzna**), E7 dokłada spójność **zewnętrzną** —
odpowiedź na pytanie „czy to, co pokazuje narzędzie, zgadza się z tym, co pokazuje Computershare".

**Stan wyjściowy:** uzgodnienie istnieje, ale jest jedną liczbą. `computershare_pdf.py::reconcile_holdings`
(375-456) porównuje `parse_shares_total()` („Assets by type → Shares") z `SUM(lots.qty_remaining)`,
z tolerancją 2,0 akcji, i zapisuje rozjazd jako konflikt `entity_type='balance'`. Na `/imports` ten
konflikt renderuje się **generycznym fallbackiem** (`imports.html:70-84`) — dwa surowe napisy
`klucz: wartość`, bez policzonej różnicy i bez kolorowania.

**Cel E7:** pełna tabela pozycji — każda z wartością z wyciągu, wartością z bazy, różnicą i statusem
zielono/czerwono/brak-danych — liczona przy imporcie **i na żądanie**, widoczna na `/imports`
i zwięźle na `/` (Stan konta), karmiąca też `integrity.py` z E2.

---

## Ustalenia empiryczne przed planem

Wszystko poniżej **sprawdzone**, nie założone — na 6 realnych wyciągach użytkownika
(`/config/akcje_temp/*.pdf` × 5 + `/config/nokia_import/…2026-08-19…pdf`), przez wywołanie
prawdziwych funkcji parsera, nie przez czytanie kodu.

### 1. Co wyciąg naprawdę zawiera

Najnowszy wyciąg (`as_of_date = 2026-08-18`, okres `2026-01-01 … 2026-08-18`):

| Sekcja PDF | Wartość | Odpowiednik w bazie |
|---|---|---|
| Assets by type → **Shares** | 2 935,510655 | loty (dziś: `SUM(qty_remaining)`) |
| Assets by type → **Restricted stock units** | 1 360,89909 | `vests` ze statusem `pending` |
| suma portfela | 4 296,409745 | suma obu |
| Matching Shares (4 wiersze) + RS AWARD (2 wiersze) | 94,89 + 1266 | `vests` pending per `natural_key` |
| Dividend (Reinvested), 4 wiersze | Σ gross 122,16 EUR | `dividends` w okresie wyciągu |
| Purchases, 9 wierszy | Σ 131,30996 szt. | `lots` z importu w okresie |
| Withhold-to-Cover Typ A, 2 wiersze | 634 + 2100 (2026-07-09) | loty `lti` |
| **Cash / Balance** | **nie istnieje** | — |

### 2. Suma „Restricted stock units" jest parsowalna symetrycznie do dzisiejszej

Etykieta ` Restricted stock units` ze strony 1 ma **dokładnie ten sam kształt** co ` Shares`, którą
czyta dzisiejsze `parse_shares_total` (`^\s{0,3}Shares\b` + liczba z linii poprzedniej). Sprawdzone
na wszystkich 6 plikach: **dokładnie jedno trafienie** w każdym (żadnych fałszywych z tabel
„Available for trading" ze stron dalszych, które mają duże wcięcie).

### 3. …ale na najstarszym wyciągu jej NIE MA

| wyciąg (`as_of`) | Shares | Restricted stock units |
|---|---|---|
| 2023-01-01 | 14,657496 | **brak sekcji → `None`** |
| 2024-01-01 | 163,187488 | 2 133,35851 |
| 2025-01-01 | 519,020364 | 2 124,423544 |
| 2026-01-01 | 61,491555 | 4 029,24411 |
| 2026-07-26 | 2 888,663115 | 1 341,60606 |
| 2026-08-18 | 2 935,510655 | 1 360,89909 |

Brak sekcji musi dawać **`no_data`**, nigdy zera i nigdy niezgodności — dokładnie ta sama reguła,
którą E4 nałożył na `broker_balance` (`cash.py:195-197`).

### 4. Wiersze transz są zaokrąglone, sumy nie

Σ wierszy Matching + RS AWARD vs suma „Restricted stock units" z tej samej strony:

| `as_of` | suma z sekcji | Σ wierszy | różnica |
|---|---|---|---|
| 2024-01-01 | 2 133,35851 | 2 133,36 | −0,00149 |
| 2025-01-01 | 2 124,423544 | 2 124,42 | +0,003544 |
| 2026-01-01 | 4 029,24411 | 4 029,24 | +0,00411 |
| 2026-07-26 | 1 341,60606 | 1 341,60 | +0,00606 |
| 2026-08-18 | 1 360,89909 | 1 360,89 | +0,00909 |

Tabela „Matching Shares" drukuje ilości do 2 miejsc, sekcja podsumowania do 6. **Sam wyciąg nie
zgadza się ze sobą co do grosza** — tolerancja jest wymuszona przez dane, nie przez wygodę.
Błąd rośnie z liczbą wierszy → tolerancja skalowana liczbą wierszy, nie stała.

### 5. Gotówki nie ma i nie będzie

`grep -i cash` daje **zero** trafień w treści PDF i zero w parserze; potwierdzenie od drugiej
strony w `db.py:342-345` („`broker_cash.source='pdf'` … jest dziś nieosiągalne"). To **koryguje
literę roadmapy**: E7 wymienia „gotówkę" jako wiersz różnicy, ale różnica gotówki nie może
istnieć, bo wyciąg nie ma z czym się rozjechać. Pozycja zostaje w tabeli jako trwałe
`no_data` z jawnym wyjaśnieniem — to odpowiedź na ryzyko nazwane wprost w `ROADMAP_V3.md:369`.

### 6. „Akcje per plan" świadomie poza zakresem

Wyciąg rozkłada te same 4 296,409745 akcji na dwa **ortogonalne** sposoby: po typie
(Shares / Restricted stock units) i po planie (Share in Success 288,59 / Vested Shares 2 741,82 /
Restricted Shares 1266). Rozkład po **typie** mapuje się 1:1 na `lots` ↔ `vests`. Rozkład po
**planie** nie ma czystego mapowania: kubełek planu w PDF to konto custody, a `lots.lot_type` to
pochodzenie — DRIP z jednej dywidendy 2026-07-24 rozszedł się na dwa różne plany (7,81916 do
„Vested Shares", 0,44233 do ESPP). Decyzja użytkownika: **per typ + wiersze**, bez per plan.

---

## Decyzje z wywiadu (2026-08-23)

| Pytanie | Wybór |
|---|---|
| Zakres tabeli | **Per typ + wiersze** — Shares vs Restricted stock units + kontrola sumy + wiersze transz, dywidend i zakupów. Bez „per plan" (patrz ustalenie 6) |
| Miejsce | **Karta na `/imports` + jednolinijkowy status na `/`**. Zero nowych pozycji w nawigacji (pasek zawija już przy 6 — 0.21.1) |
| Snapshot w bazie | **Tak, migracja v12** (`statement_snapshots`) — uzgodnienie przeliczalne na żądanie bez ponownego wgrywania PDF. Świadome odstępstwo — `ROADMAP_V3.md:398` wymienia migracje tylko dla E2 i E4, więc E7 dokłada trzecią; obowiązuje ta sama reguła „przed każdą pełny eksport ZIP" |
| Zgłaszanie niezgodności | **Jeden konflikt `balance` z bogatszym JSON-em + osobne findingi `integrity` per pozycja**. Kolejka konfliktów nie puchnie, nocny alarm rozróżnia pozycje |

---

## Ryzyko centralne: historyczny wyciąg vs bieżąca baza

Dzisiejsze porównanie działa **tylko dlatego**, że odpala się w momencie importu i wyłącznie dla
najświeższego `as_of_date` (bramka `computershare_pdf.py:406-409`). Porównuje stan wyciągu na
dzień `as_of` z **bieżącym** `SUM(qty_remaining)`.

Przeliczanie „na żądanie" (decyzja o snapshocie) łamie to założenie: każda sprzedaż lub import po
dacie wyciągu zacząłby dawać **fałszywą czerwoną**. E7 musi więc odtwarzać stan bazy **na dzień
`as_of`**, a nie czytać stanu bieżącego. To jedyna realnie trudna część tego etapu i główny powód,
dla którego jest osobnym krokiem TDD przed czymkolwiek innym.

**Zakres uzgodnienia: wyłącznie NAJNOWSZY snapshot.** Starsze wyciągi zostają w tabeli jako
historia, ale nie są uzgadniane — rekonstrukcja dla dat sprzed pierwszego importu jest z definicji
niepełna (sekcja „Purchases" jest okresowa, nie kumulatywna), a porównywanie ich niczego nie wnosi.
To zachowuje dzisiejszą bramkę świeżości (`computershare_pdf.py:406-409`) zamiast ją znosić.

### Rekonstrukcja A — akcje na dzień D

Trzy fakty **zweryfikowane grepem**, nie założone:
1. `lots.quantity` **nigdy nie jest mutowane ani kasowane** — jedyne `UPDATE lots` dotyczą
   `qty_remaining` (`tax/lots.py:205,277`, `data_fixes.py:79,94`) albo kursu NBP
   (`tax/lots.py:87`); zero `DELETE FROM lots` w całym pakiecie. `lots` to append-only log nabyć.
2. Alokacja FIFO nigdy nie sięga lotu z przyszłości względem sprzedaży — `record_sale`
   (`tax/lots.py:153,181`) woła `open_lots(as_of=sale_date)`, a ten filtruje `acquired_date <= ?`
   (`tax/lots.py:111`).
3. `reverse_sale` usuwa wiersz z `sales`, a `sale_allocations` znika kaskadą (`db.py:126`) —
   obie strony rekonstrukcji znoszą się razem.

Stąd: `Σ lots.quantity WHERE acquired_date <= D` − `Σ sale_allocations.quantity` po sprzedażach
`sale_date <= D`, liczone **per lot** (`LEFT JOIN` na pod-zapytaniu grupującym alokacje), nie
dwoma płaskimi sumami — dzięki temu alokacja przypięta do lotu spoza okna zostaje odrzucona przez
`WHERE`, zamiast odjąć ilość, której nigdy nie dodano.

**Zapytanie-strażnik (obowiązkowe):** liczba alokacji, gdzie `lots.acquired_date > sales.sale_date`,
musi wynosić 0. Filtr `as_of` w `open_lots` dopisano dopiero w kroku 19 (docstring
`tax/lots.py:104-114` opisuje realny przypadek sprzed naprawy) — dane sprzed niej mogą łamać
niezmiennik i **zaniżać** rekonstrukcję. Naruszenie ⇒ pozycja „Akcje" na `no_data` (nie
`mismatch`) **plus nowy `Finding` w `integrity.py`**. Ostre `>`, bo `acquired_date == sale_date`
jest legalne i nieszkodliwe.

### Rekonstrukcja B — transze oczekujące na dzień D

Klucz: **`vests.lot_id`, nie `vests.status`.** `status` to stan bieżący i nie ma znacznika czasu
zmiany (`vests` ma tylko `reminder_sent_at`, `db.py:217`). Ale przejście `pending → vested`
zachodzi **wyłącznie razem z przypięciem lotu** (`tax/grants.py:169`, `data_fixes.py:57`), a ten
lot ma `acquired_date` = realna data uwolnienia z wyciągu. Zatem: transza `vested` była
nieuwolniona na dzień D wtedy i tylko wtedy, gdy `lots.acquired_date > D`. To fakt z danych,
nie rekonstrukcja ze statusu.

Dla transz wciąż `pending` osią jest **`COALESCE(available_from, vest_date)`**, nie samo
`vest_date` — krok 21 pokazał, że dla ESPP `available_from` jest ~4 tygodnie później i użycie
samego `vest_date` fałszywie oznaczało transze jako zaległe, zanim Computershare je zaksięgował.

Cztery kubełki: `unvested` (wchodzi do sumy), `ambiguous_overdue`
(`COALESCE(available_from, vest_date) <= D`, wciąż `pending` — nie wiadomo, po której stronie
była na D; `unvested_summary` nazywa to `overdue` i jego docstring wprost zabrania cichego
sumowania), `already_vested`, `unreconstructable` (`vested` bez `lot_id` — istnieje jako osobny
niezmiennik `integrity.py:97`; `cancelled` — **zweryfikowane: nie jest zapisywany nigdzie
w kodzie produkcyjnym**, tylko w testach, ale trzeba go domknąć).

**Odrzucona rekomendacja analizy:** „jakikolwiek `ambiguous_overdue` ⇒ cała pozycja `no_data`".
Sprawdzone na produkcji — dziś `overdue_qty` wynosi 0 (`GET /` nie renderuje disclaimera
z `account.html:87-93`), ale cztery transze ESPP mają `available_from = 2026-08-27`, czyli za
kilka dni. Blankietowa reguła wyciszałaby pozycję rutynowo, za każdym razem gdy vesting wyprzedzi
najbliższy import — czyli dokładnie wtedy, kiedy uzgodnienie jest najbardziej potrzebne.
**Reguła przyjęta:** `no_data` tylko gdy `ambiguous_qty > tolerancja` (czyli gdy niepewność
realnie może odwrócić werdykt); poniżej — pozycja liczona normalnie, a ilość niepewna wypisana
w `note`. Ambiguity liczona względem **D**, nie względem dzisiaj.

### Tolerancja — przestaje być magiczną stałą

Dzisiejsze 2,0 akcji jest uzasadnione kumulacją błędu `parse_vested_dividend_shares`
(~0,01/wiersz) — a to są konkretne, policzalne loty: `source='holdings_snapshot'`
(jedyne wystąpienie: `computershare_pdf.py:651`) i `lot_type='dividend_drip'`. Zamiast stałej:

```
tolerance_shares(D) = min(2.0, 0.02 + 0.01 × liczba lotów holdings_snapshot z acquired_date <= D)
```

Rośnie monotonicznie z D, dokładnie jak kumulacja; spada do ~0,02 dla historii idącej wyłącznie
ścieżką transakcyjną `parse_dividends`; **2,0 zostaje jako sufit** — jeśli wzór wyjdzie wyżej,
lepiej nie rozmywać alarmu. W UI pokazywana obok delty („±0,14 przy 12 lotach ze snapshotu"),
żeby czerwony/zielony był wyjaśnialny, a nie arbitralny. Dla transz analogiczny błąd nie istnieje
(ilości z harmonogramu są dokładne) → `_QTY_EPSILON` (0,001) na wiersz.

### Korekta o niepotwierdzone Withhold-to-Cover — zostaje, ale wymaga filtra daty

Powód korekty (`computershare_pdf.py:413-430`) nie znika: Computershare pokazuje te akcje jako
sprzedane, a baza świadomie ich nie księguje do ręcznego potwierdzenia. To rozjazd *definicyjny*,
nie *czasowy*. **Ale dziś korekta nie patrzy na `execution_date`** — bo funkcja i tak odpalała się
tylko dla najnowszego wyciągu. Przy rekonstrukcji as-of brak filtra `execution_date <= D` byłby
**nową regresją wprowadzoną przez E7**. Podkontrola „already_booked" (424-430) zostaje jako siatka
bezpieczeństwa: auto-rozstrzyganie z kroku 20 odpala się dopiero przy kolejnym imporcie, więc
scenariusz „konflikt wisi, sprzedaż zaksięgowana ręcznie przez `/loty`, brak kolejnego importu"
bez niej odjąłby tę samą sprzedaż dwa razy (dokładnie fałszywy alarm naprawiany w kroku 20).
Niepotwierdzone sprzedaże pokazywane **dodatkowo jako osobny wiersz tabeli** z linkiem do
konfliktu — korekta przestaje być cicha.

---

## Kroki

### Krok 0 — plan do repo
Skopiować ten plik jako `docs/PLAN_E7_uzgodnienie.md` przed pierwszą linią kodu
(`feedback_plans_as_md`). Osobny commit.

### Krok 1 — parser: suma RSU + snapshot wyciągu
`importers/computershare_pdf.py`:
- `parse_restricted_units_total(text) -> float | None` — symetryczne do istniejącego
  `parse_shares_total` (365-372), ta sama para regexów, etykieta `Restricted stock units`.
  **`None` gdy sekcji nie ma** (ustalenie 3), nie 0.
- `statement_snapshot(text) -> dict` — jeden słownik zbierający **wyłącznie stronę wyciągu**,
  z już istniejących parserów (`parse_document_meta`, `parse_shares_total`,
  `parse_restricted_units_total`, `parse_matching_shares`, `parse_rs_award`, `parse_dividends`,
  `parse_purchases`, `parse_withhold_to_cover`). Zero odwołań do bazy — to ma być czysta funkcja
  tekstu, żeby dało się ją testować bez `conn` i zapisać jako JSON.

**Testy przed implementacją:** suma RSU parsowana z syntetycznego bloku strony 1; brak sekcji →
`None`; blok „Available for trading" ze strony dalszej **nie** daje fałszywego trafienia;
`statement_snapshot` zwraca komplet kluczy przy pustym tekście (same `None`/puste listy).
Wzorzec fikstur: stałe modułowe z syntetycznym tekstem layout-mode w
`tests/test_computershare_pdf_import.py:12-70` (z `_THIN = " "`), **nie** pliki PDF.

### Krok 2 — rekonstrukcja stanu bazy na dzień `as_of`
Nowy moduł **`nokia_tracker/reconcile.py`** — model **odczytu**, zero zapisu, kontrakt jak
`integrity.py` („świadomie READ-ONLY") i jak `cash.py` z E4.

```python
def shares_as_of(conn, as_of) -> dict | None          # None gdy strażnik wykryje naruszenie
def unvested_as_of(conn, as_of) -> dict               # kubełki + ambiguous_qty + unreconstructable
def pending_wtc_sales_as_of(conn, as_of) -> list[dict]
def _allocation_date_violations(conn) -> int          # strażnik
def shares_tolerance(conn, as_of) -> float            # wzór z sekcji „Ryzyko centralne"
```

Szczegóły algorytmiczne, SQL i uzasadnienie każdej reguły — w sekcji „Ryzyko centralne" wyżej.
**Przypadki nieodtwarzalne kończą jako `no_data`, nie jako niezgodność** — ta sama reguła co
przy gotówce i przy braku sekcji RSU.

**Testy przed implementacją** (`tests/test_reconcile.py`):

*Akcje:* lot z `acquired_date = D+1` nie wchodzi do sumy na D; sprzedaż z `sale_date = D+1` **nie**
pomniejsza stanu na D (to jest test na dzisiejszy fałszywy czerwony); nabycie i sprzedaż tego
samego dnia D → netto 0 (granica `<=` po obu stronach); dla `D = dziś` wynik równy
`SUM(qty_remaining)` (most do dzisiejszego zachowania); lot sprzedany częściowo przed i po D;
rozbicie po `lot_type` sumuje się do totalu; po `reverse_sale` stan wraca do sprzed sprzedaży.

*Strażnik:* ręcznie wstawiona alokacja łamiąca `acquired_date <= sale_date` (symulacja danych
sprzed kroku 19) → `None`/`no_data`, **nigdy liczba**; dane zapisane przez `record_sale` nigdy
nie łamią niezmiennika.

*Transze:* `status='vested'` z lotem o `acquired_date = D+1` liczy się na D jako nieuwolniona
(**główny test odtwarzalności historycznej**); ten sam vest z lotem sprzed D — nie; `pending`
z terminem po D — tak; ESPP z `vest_date <= D < available_from` — tak (krok 21); `ambiguous_overdue`
nie wchodzi do sumy; `ambiguous_qty > tolerancja` → `no_data`, poniżej → pozycja liczona z notatką;
`vested` bez `lot_id` → `no_data`; `cancelled` → `no_data`; dla `D = dziś` i zera `overdue` wynik
zgadza się z `grants.unvested_summary()['upcoming_qty']` — **blokuje powstanie drugiego,
rozjeżdżającego się źródła prawdy**.

*Withhold-to-Cover:* konflikt z `execution_date = D+1` nie rusza stanu na D (regresja na dziurę,
którą E7 by wprowadził); z `execution_date <= D` — odejmowany; obecny już w `sales` — nie odejmowany
drugi raz (port `test_reconcile_holdings_does_not_double_subtract_an_already_booked_sale`,
`tests/test_computershare_pdf_import.py:507`).

*Tolerancja:* brak lotów `holdings_snapshot` → wartość epsilonowa; 12 lotów → ~0,14, a lot
z `acquired_date = D+1` jej nie podnosi; sufit 2,0 nieprzekraczalny.

### Krok 3 — silnik uzgodnienia
W `reconcile.py`:

```python
@dataclass
class Position:
    key: str            # 'shares' | 'restricted_units' | 'total' | 'pending_tranches'
                        # | 'dividends_in_period' | 'espp_purchases_in_period' | 'broker_cash'
    label: str          # etykieta PL do UI
    statement: float | None
    database: float | None
    diff: float | None
    tolerance: float
    status: str         # 'ok' | 'mismatch' | 'no_data'
    note: str           # dlaczego no_data / co składa się na różnicę
    details: list[dict] # wiersze rozbieżne (brakujące/nadmiarowe), pusto gdy ok

def reconcile(conn, snapshot: dict) -> list[Position]
```

Pozycje i ich strony bazy:

| `key` | strona wyciągu | strona bazy | tolerancja |
|---|---|---|---|
| `shares` | Assets by type → Shares | `shares_as_of(as_of)` − niepotwierdzone WtC Typ B | `shares_tolerance(as_of)`, sufit 2,0 |
| `restricted_units` | Assets by type → RSU | `unvested_as_of(as_of)['total']` | `max(0.02, 0.01 × liczba wierszy transz)` — ustalenie 4 |
| `total` | suma obu z wyciągu | suma obu z bazy | suma tolerancji |
| `pending_tranches` | wiersze Matching + RS AWARD | `vests` nieuwolnione na `as_of`, dopasowanie po `natural_key` | `_QTY_EPSILON` (0,001) na wiersz |
| `dividends_in_period` | wiersze `parse_dividends` w `period_start…period_end` | `tax/dividends.py::payouts()` w tym zakresie | 0,02 EUR (jak `_check_dividend_arithmetic`) |
| `espp_purchases_in_period` | wiersze `parse_purchases` | `lots` z importu w zakresie, po `natural_key` | `_QTY_EPSILON` |
| `pending_wtc_sales` | wiersze WtC Typ B | nierozstrzygnięte konflikty `withhold_to_cover_sale` | — → informacyjny, z linkiem do konfliktu |
| `broker_cash` | **brak w wyciągu** | `cash.broker_balance()` | — → zawsze `no_data` |

Trzy zasady twarde:
- **`None` po którejkolwiek stronie ⇒ `no_data`**, nigdy `mismatch`. Reguła, o którą prosi wprost
  `ROADMAP_V3.md:369`.
- **Odtworzenie nie do udowodnienia też jest `no_data`** — naruszenie strażnika dat alokacji,
  `vested` bez `lot_id`, `cancelled`, `ambiguous_qty > tolerancja`.
- **Sprzedaż wykonana poza systemem i nigdy niewprowadzona jest nierozróżnialna od prawdziwego
  rozjazdu** → zostaje `mismatch`. To nie jest luka — to dokładnie sygnał, po który E7 istnieje.

`dividends_in_period` **musi** grupować po `pay_date` przez `tax/dividends.py::payouts()`, nie po
surowych wierszach — `pay_date` nie jest unikalny, Computershare drukuje osobny wiersz na koszyk
planu (lekcja 0.17.2, potwierdzona ponownie w najnowszym wyciągu: dwa wiersze `2026-07-24`).

### Krok 4 — migracja v12 i zapis snapshotu
`db.py`, nowa migracja (v11 jest dziś ostatnia, `SCHEMA_VERSION = len(_MIGRATIONS)`):

```sql
CREATE TABLE statement_snapshots (
    id INTEGER PRIMARY KEY,
    import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    as_of_date TEXT NOT NULL,
    period_start TEXT, period_end TEXT,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(as_of_date)
);
```

UPSERT po `as_of_date` (ponowny import tego samego wyciągu nadpisuje, nie duplikuje — ta sama
filozofia idempotencji co `_record_conflict`). Dopisać tabelę do `backup.py::_CSV_TABLES`
(wymóg z E4 — inaczej eksport ZIP przestaje być pełną kopią).

### Krok 5 — przepięcie `reconcile_holdings`
`computershare_pdf.py::reconcile_holdings` przestaje liczyć samo i staje się orkiestracją:
zbuduj snapshot (krok 1) → zapisz (krok 4) → `reconcile.reconcile(conn, snapshot)` (krok 3) →
jeśli którakolwiek pozycja ma `mismatch`, zapisz **jeden** konflikt `balance` z pełną tabelą
różnic w `incoming_json`/`existing_json`; jeśli żadna, auto-rozstrzygnij stare `balance` jak dziś
(440-450).

Zachować bez zmian: bramkę świeżości `as_of_date` (406-409), `natural_key = f"balance:{as_of_date}"`
(kreator strat `tax/losses.py:318-328` liczy konflikty właśnie po `entity_type='balance'` — nie
ruszamy go), korektę o niepotwierdzone `withhold_to_cover_sale` (chyba że analiza z kroku 2
wykaże, że przy rekonstrukcji as-of staje się podwójnym odjęciem — wtedy osobny commit
i jawny wpis w CHANGELOG).

**Zmiana zachowania:** dziś konflikt `balance` powstaje wyłącznie przy rozjeździe liczby akcji;
po E7 także przy rozjeździe transz, dywidend lub zakupów. To rozszerzenie, nie regresja, ale
wymaga jawnego CHANGELOG-u i checkpointu na realnych danych.

### Krok 6 — `views/imports.py` + karta na `/imports`
`/imports` to dziś jedyna trasa z logiką **inline** (`web/routes_dane.py:49-70`) — E3 zrefaktorował
wszystkie pozostałe do `views/`. E7 domyka tę lukę przy okazji:
- nowy `views/imports.py::imports_view(conn) -> dict` — historia, konflikty (dzisiejsze
  `json.loads` na obu kolumnach), plus uzgodnienie z najnowszego snapshotu;
- `templates/imports.html`: nowa karta **„Uzgodnienie z wyciągiem"** nad kolejką konfliktów —
  tabela `Pozycja | Wyciąg | Baza | Różnica | Status`, kolorowanie zielono/czerwono/szaro,
  rozwinięcie `<details>` z wierszami rozbieżnymi; `data-label` na tabeli od razu (lekcja z E5,
  której `cash.html` nie miał);
- **dedykowany renderer konfliktu `balance`** zamiast generycznego fallbacku (70-84) — analogicznie
  do tego, co `withhold_to_cover_sale` ma dziś (55-69);
- link do wiersza źródłowego: pozycja rozbieżna wskazuje na `/loty`, `/granty` albo `/dywidendy`
  wg typu (wymóg „linkiem do wiersza źródłowego", `ROADMAP_V3.md:363`).

### Krok 7 — status na Stanie konta
`views/account.py::account_view` dokłada jedno pole (skrót uzgodnienia: data wyciągu + liczba
pozycji `mismatch`), `templates/account.html` — jedna linia w karcie Portfel, obok istniejącego
`disclaimer` o `unvested.overdue_qty` (87-93), który już dziś kieruje na `/imports`.
Bez nowej karty i bez nowej pozycji w nawigacji.

**Gdy nie było jeszcze żadnego importu** — „brak wyciągu do porównania", nie „niezgodność".

### Krok 8 — niezmienniki w `integrity.py`

**Dwa** nowe niezmienniki:

1. **`allocation_predates_its_lot`** (`severity="error"`) — alokacja FIFO przypięta do lotu
   nabytego PO dacie sprzedaży. To realna korupcja danych sprzed kroku 19, dziś niesprawdzana
   przez żaden niezmiennik, a od E7 blokująca rekonstrukcję. Sygnatura `(conn) -> Finding | None`,
   więc idzie do jawnej krotki w `check_all` (278-286).
2. **`statement_mismatch:{key}`** (`severity="warning"`) — jeden `Finding` na pozycję
   z `mismatch`. Warning, nie error, bo rozjazd bywa uzasadniony (np. niepotwierdzona sprzedaż)
   i nie jest korupcją danych — ta sama waga co `unresolved_import_conflict` (`integrity.py:123-133`).
   Sygnatura `(conn) -> list[Finding]`, więc dołącza do czterech ręcznych wywołań w `check_all`
   (290-297).

Brak snapshotu ⇒ **zero findingów** (nie finding „brak danych") — dokładnie wzorzec
`_tax_payments_exceed_due` („zero wpłat nigdy nie jest błędem", `integrity.py:247-249`).

Nocny job (`main.py:523-556`, 6:35) i karta „Spójność danych" (`templates/data.html:88-115`)
konsumują to bez żadnej zmiany — per-pozycyjne `check` daje osobny anty-spam
`alerts.allow_fire(c, f"integrity:{f.check}", 24*60)` dla każdej pozycji.

### Krok 9 — wydanie 0.23.0
Wg `feedback_ha_addon_release` + `feedback_release_notes`: bump `nokia_tracker/config.yaml`
**i** `nokia_tracker/__init__.py`, CHANGELOG z tabelą pozycji uzgodnienia, README (Features +
Entities), **published** GitHub release, weryfikacja `wersja == tag`, potem update przez Supervisor
(`ha_manage_updates` z backupem — **nigdy** cykl rebuild z
`reference_supervisor_git_addon_rebuild`; ta baza ma realne dane z importu PDF).
Na koniec `docs/ROADMAP_V3.md`: E7 → WYDANE + wynik weryfikacji na produkcji.

**Przed migracją v12: pełny eksport ZIP** (`GET /dane/eksport.zip`) — reguła `ROADMAP_V3.md:398`.

---

## Pliki

| Plik | Zmiana |
|---|---|
| `nokia_tracker/reconcile.py` | **nowy** — rekonstrukcja as-of (`shares_as_of`, `unvested_as_of`, `pending_wtc_sales_as_of`, strażnik, tolerancja) + silnik (`Position`, `reconcile`) |
| `importers/computershare_pdf.py` | +`parse_restricted_units_total`, +`statement_snapshot`; `reconcile_holdings` → orkiestracja; korekta WtC dostaje filtr `execution_date <= D` |
| `db.py` | migracja **v12** — `statement_snapshots` |
| `backup.py` | `_CSV_TABLES` += `statement_snapshots` |
| `integrity.py` | dwa niezmienniki: `allocation_predates_its_lot`, `statement_mismatch:*` |
| `views/imports.py` | **nowy** — domyka lukę po E3 (jedyna trasa z logiką inline) |
| `views/account.py` | skrót uzgodnienia do karty Portfel |
| `web/routes_dane.py` | `imports_get` cienkie, przez `views/imports.py` |
| `templates/imports.html` | karta „Uzgodnienie z wyciągiem" + dedykowany renderer konfliktu `balance` |
| `templates/account.html` | jedna linia statusu |
| `tests/test_reconcile.py`, `tests/test_views_imports.py` | **nowe** |
| `tests/test_computershare_pdf_import.py`, `test_integrity.py`, `test_web_imports.py`, `test_backup.py` | rozszerzenia |

## Ryzyka

| Ryzyko | Mitygacja |
|---|---|
| Fałszywa czerwona po sprzedaży/imporcie późniejszym niż wyciąg | Rekonstrukcja stanu **na dzień `as_of`** (krok 2), nie odczyt bieżący — osobny krok TDD przed resztą |
| Pozycja bez źródła pokazana jako niezgodność | Twarda reguła: `None` po którejkolwiek stronie ⇒ `no_data`; testy per pozycja (`broker_cash`, brak sekcji RSU, brak importu) |
| Migracja v12 na produkcyjnej bazie z realnymi danymi | Eksport ZIP przed; tabela **wyłącznie dopisywana**, zero ALTER na istniejących; weryfikacja restartem add-onu (lekcja `pv_roi_tracker` 0.30.2) |
| Rozszerzony konflikt `balance` zaśmieca kolejkę | Jeden konflikt na `as_of_date` (`natural_key` bez zmian), auto-rozstrzyganie przy zgodności jak dziś; per-pozycyjne findingi idą do `integrity`, nie do kolejki |
| Zerwanie kreatora strat | `tax/losses.py:318-328` liczy po `entity_type='balance'` — `natural_key` i `entity_type` **niezmienione**; test regresyjny na kroku kreatora |
| `dividends_in_period` liczone z surowych wierszy | Przez `tax/dividends.py::payouts()`; `pay_date` nie jest unikalny (0.17.2), w najnowszym wyciągu dwa wiersze `2026-07-24` |
| Zbyt ciasna tolerancja przy zaokrąglonych wierszach | Tolerancja skalowana liczbą wierszy, wyprowadzona z pomiaru na 5 wyciągach (ustalenie 4), nie zgadnięta; dla akcji liczona z lotów `holdings_snapshot`, z sufitem 2,0 |
| **Nowa regresja wprowadzona przez samo E7:** korekta WtC bez filtra daty | `execution_date <= D`; dedykowany test na konflikt z datą po `as_of` |
| Rekonstrukcja oparta o nieudowodniony niezmiennik dat alokacji | Zapytanie-strażnik przed każdym uzgodnieniem; naruszenie ⇒ `no_data` + `Finding` w `integrity`, nigdy liczba |
| Drugie, rozjeżdżające się źródło prawdy o transzach | Test wiążący `unvested_as_of(dziś)` z `grants.unvested_summary()['upcoming_qty']` |

## Weryfikacja

1. `cd /config/addons/nokia_tracker/nokia_tracker && python3 -m pytest` — wszystko zielono.
   **Punkt odniesienia zmierzony, nie przepisany z CHANGELOG-u: 1209 passed w 374 s** (uruchomione
   przed napisaniem tego planu, na czystym drzewie 0.22.1). Oczekiwane po E7: ~1250.
2. `test_tax_*.py` zielono (beton nietknięty — E7 nie dotyka `tax/`, czyta tylko `dividends.payouts`).
3. **Regresja na dzisiejszym uzgodnieniu:** cztery istniejące testy zachowania
   `reconcile_holdings` (`tests/test_computershare_pdf_import.py:475-647` — brak konfliktu przy
   zgodnym saldzie, flagowanie rozjazdu, auto-rozstrzyganie starego konfliktu, pominięcie przy
   imporcie starszego backfillu) muszą przejść **bez zmiany asercji** po przepięciu na
   `reconcile.py`. To samo kryterium co w E3.
3. **Bramkowane testy na realnych plikach** — `tests/test_computershare_pdf_real_files.py` odpala się,
   bo `/config/akcje_temp` istnieje (5 wyciągów 2023-01-01 … 2026-07-26). Dopisać asercje sumy RSU
   i `statement_snapshot` dla każdego pliku, z jawną obsługą braku sekcji na 2023-01-01.
4. **Uzgodnienie na najnowszym wyciągu** (`/config/nokia_import/…2026-08-19…`, `as_of=2026-08-18`) —
   ręczne porównanie z liczbami z ustalenia 1: Shares 2 935,510655, RSU 1 360,89909,
   suma 4 296,409745, 6 transz, 4 dywidendy, 9 zakupów. Każda pozycja albo zielona, albo z
   wyjaśnioną przyczyną różnicy.
5. `integrity.check_all()` na produkcji — zero **nowych** pęknięć poza tymi, które uzgodnienie
   ma realnie wykryć.
6. Po instalacji przez Supervisor: **Playwright na 390 px i 1920 px** — `/imports` (karta
   uzgodnienia + kolejka konfliktów), `/` (linia statusu), `/dane` (karta Spójność danych),
   screenshot **i** konsola bez błędów, pliki do `/config/playwright/`.
7. **Test empiryczny celu E7:** wgranie najnowszego wyciągu daje odpowiedź „czy moje liczby
   zgadzają się z Computershare" w jednym spojrzeniu, a różnica — jeśli jest — wskazuje pozycję
   i prowadzi linkiem do wiersza źródłowego.

---

## Poza zakresem (świadomie)

- **Akcje per plan** — brak czystego mapowania plan↔`lot_type` (ustalenie 6). Decyzja użytkownika.
- **Parsowanie gotówki z PDF** — niemożliwe, wyciąg jej nie zawiera (ustalenie 5).
- **Zawężenie tolerancji 2,0 dla `shares`** — wymaga osobnego pomiaru, nie refaktoru przy okazji.
- **`taxes_eur` z Withhold-to-Cover Typ B** — znany dług z E4 (`imports_confirm_sale` nie przekazuje
  potrącenia do `record_sale`, bo nie ma go gdzie zapisać). Dotyka `tax/`, zostaje w backlogu.
- **Token GitHub w `.git/config`** repo add-onu — zgłoszone przy 0.17.1, nadal aktualne; poza
  zakresem E7, ale warte osobnej decyzji.
