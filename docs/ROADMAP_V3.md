# nokia_tracker — refaktor + roadmapa v3 (0.18.0 → 0.24.0)

## Context

`nokia_tracker` jest na 0.17.3: ~16 300 linii kodu, ~1065 testów, 47 tras, ~60 encji MQTT.
Roadmapa v1 (0.8.1–0.13.0) i v2 (0.14.0–0.17.x) są **w całości wydane**. Zostało tylko
warunkowe e-Deklaracje (research) i zarezerwowane 1.0.0. Nie ma listy „co dalej".

Wywiad przeprowadzony 2026-08-22 zmienił obraz projektu. Zbudowana jest gruba warstwa
analityczna (XIRR, TWR, Sharpe, atrybucja, benchmark, co-pilot AI), ale **realne użycie jest inne**:

> „chcę mieć wszystkie dane dotyczące akcji i ich wartości w jednym miejscu, aby łatwo zobaczyć
> aktualny stan konta i zrobić szybkie wyliczenia ile wypłacę w danym miesiącu, uwzględniając
> podatek i symulacje lotów"

**Cel projektu (nazwany):** jedno wiarygodne miejsce z prawdą o stanie konta — akcje **i gotówka**
— plus szybki, dwukierunkowy kalkulator wypłaty.

**Kluczowa decyzja, którą narzędzie ma napędzać:** *ile sprzedać w tym miesiącu i co z tego
realnie zostanie na rękę.* Liczona w obie strony:
- „potrzebuję 20 000 zł netto w listopadzie" → ile akcji, z których lotów, jaki podatek, co przepada z dopłaty ESPP
- „mam 300 akcji do sprzedania" → brutto, koszt FIFO, podatek, na rękę, przepadająca dopłata

**Dwie przeszkody nazwane przez użytkownika:**
1. **Brak zaufania do liczb** — podejrzenie niespójności (osierocone vesty, stare `sale_allocations`, nierozstrzygnięte konflikty importu). Bez tego „jedno miejsce" tylko skonsoliduje kłamstwo.
2. **Brak kontekstu gotówki** — widać akcje, nie widać kasy: wpływy ze sprzedaży, podatek zapłacony/do zapłaty, dywidendy odebrane vs oczekiwane, saldo u brokera.

**Dobra wiadomość dla zakresu:** 3 z 4 pozycji gotówki są wyprowadzalne z istniejących tabel —
`sales(price_eur, fee_eur)`, `dividends(net_received_eur)`, `tax/pit38.py::annual_report`.
Importer już parsuje `net_proceeds_eur` i `fees_eur` (`importers/computershare_pdf.py:346-349`).
Nowych źródeł trzeba tylko dwóch: **realne saldo u brokera** i **faktycznie zapłacony podatek**.

---

## Decyzje podjęte w wywiadzie

| Decyzja | Wybór |
|---|---|
| Kierunek kalkulatora | Obie strony, jeden ekran |
| Zakres refaktoru | Blueprinty + warstwa widoków (trasy stają się cienkie) |
| Kontekst gotówki | Wszystkie 4: wpływy, podatek, dywidendy, saldo brokera |
| Budowanie zaufania | Wszystkie 4: audyt, niezmienniki, ślad „skąd ta liczba", uzgodnienie z wyciągiem |
| Kolejność | Fundament → wartość |
| Ekran „Stan konta" | **Zastępuje pulpit** (`/`); dzisiejszy pulpit → `/rynek` |
| Cięcie funkcji analitycznych | **Nie** — warstwa analityczna zostaje nietknięta |
| Wersjonowanie | 0.18.0 → 0.24.0; e-Deklaracje przesunięte na później; 1.0.0 nadal zarezerwowane |

---

## Zasady pracy (obowiązują w każdym etapie)

- **Jeden etap na raz, checkpoint po każdym.** Nie zaczynam kolejnego bez oceny poprzedniego.
- **TDD.** Testy przed implementacją. Punkt odniesienia: 1065 testów na 0.17.3.
- **`tax/` to beton.** Każda zmiana dotykająca `tax/` wymaga `test_tax_*.py` zielono przed i po.
- **Zero nowej matematyki FIFO.** Wszystko przez `tax/whatif.py::_plan_fifo` — będzie miał czwartego konsumenta, nie czwartą implementację.
- **Weryfikacja na produkcji, nie tylko w testach.** Wzorzec z 0.16.0/0.16.1 i 0.35.3: realne dane łapią błędy, których testy nie łapią. Po każdym wydaniu: Playwright + sprawdzenie liczb.
- **Podstawa prawna potwierdzana, nie zakładana** (BLUEPRINT §3a).
- **Cache-busting** wg reguły z `CLAUDE.md`: `?v={{ version }}` + `no-store` na HTML/API + badge wersji.
- **Krok 0 każdego etapu:** przeniesienie tego planu do repo jako `docs/ROADMAP_V3.md` (reguła „plan jako plik .md").

---

## Etapy

### E1 — Audyt bazy produkcyjnej (0.18.0-dev, bez wydania) · ~0.5 dnia

**Tylko diagnoza. Zero zmian w danych.** Wzorzec: audyt `pv_roi_tracker` 0.35.4.

Baza żyje w `/data` add-onu — z tej powłoki niedostępna. Wejście: **eksport ZIP z UI**
(`GET /dane/eksport.zip`, `backup.py::export_zip`) → kopia read-only do scratchpada.

Zestaw niezmienników do sprawdzenia zapytaniami SQL (każdy = osobne znalezisko z liczbą wierszy):

| # | Niezmiennik |
|---|---|
| 1 | `lots.qty_remaining == lots.quantity - Σ sale_allocations.quantity` dla każdego lotu |
| 2 | `Σ sale_allocations.quantity == sales.quantity` dla każdej sprzedaży |
| 3 | każdy `vests.lot_id` wskazuje na istniejący lot; każdy `vests.grant_id` na istniejący grant |
| 4 | brak `vests` ze statusem `vested` bez `lot_id` (osierocone vesty — zgłoszone przy 0.17.1) |
| 5 | `sale_allocations` bez odpowiadającego `sales` (stare rekordy — zgłoszone przy 0.17.1) |
| 6 | `import_conflicts WHERE resolved = 0` — ile i jakiego typu |
| 7 | `dividends`: `gross_eur - withholding_paid_eur == net_received_eur` (± grosz) |
| 8 | każde zdarzenie podatkowe ma `nbp_rate` i `nbp_rate_date`; data ≤ data zdarzenia |
| 9 | `tax_loss_carryforward`: Σ odliczeń ≤ kwota straty; żadna strata starsza niż 5 lat nieodliczona |
| 10 | suma akcji per plan vs ostatni wyciąg PDF (`/config/nokia_import/`) |

**Wynik:** tabela znalezisk (niezmiennik → liczba wierszy → czy realna korupcja czy szum) +
rekomendacja naprawy per pozycja. **Bez naprawiania.**

**Checkpoint:** oceniasz listę i decydujesz, co naprawiamy w E2, a co zostawiamy.

---

### E2 — Naprawa znalezisk + nocny kontroler spójności (0.18.0) · ~1 dzień

- Naprawa pozycji zatwierdzonych w E1 — każda jako osobny, odwracalny krok migracyjny, z kopią przed.
- **Nowy `integrity.py`** — te same niezmienniki co w E1, ale jako kod: `check_all(conn) -> list[Finding]`. Jedno źródło prawdy, nie skrypt audytowy plus osobna implementacja.
- Nocny job w `main.py` → alert przez `alerts.py` (`allow_fire`/`log_fired`, wzorzec `alert_min_interval`) gdy niezmiennik pęknie.
- Karta „Spójność danych" na `/dane` obok istniejącej karty „Stan systemu".

**Pliki:** nowy `nokia_tracker/integrity.py`, `main.py` (job), `templates/data.html`, `alerts.py` (bez zmian, tylko konsument).
**Migracje:** tylko jeśli naprawa tego wymaga.
**Ryzyko:** naprawa danych podatkowych. Mitygacja: eksport ZIP przed, `test_tax_*.py` zielono przed i po, weryfikacja przez restart add-onu (lekcja z `pv_roi_tracker` 0.30.2 — fix danych sprawdza się restartem, nie odczytem po zapisie).

**Checkpoint:** liczby na `/pit38` i `/wyniki` przed vs po naprawie — czy któraś się zmieniła i czy zmiana jest uzasadniona.

---

### E3 — Refaktor `web.py` (0.19.0) · WYDANE 2026-08-22

**Wynik:** `web.py` (1814 linii, 47 tras w jednej funkcji) → pakiet `web/`
(9 modułów tras + fabryka aplikacji) + `nokia_tracker/views/` (warstwa
składania danych, 7 modułów) + `nokia_tracker/exports/pit38.py`
(serializacja CSV/XLSX). `test_web.py` (2453 linie, 182 testy) → 12 plików
`tests/test_web_*.py` + wspólna fixture `client` w `conftest.py`.

Pełna deduplikacja wykonana: preambuła kursu (7 miejsc) →
`views/market_context.py`; silnik `/plan` ↔ trzy trasy
`/api/preview/{espp,sale-timing,exit-plan}` → `views/plan.py` (dzielone
wyłącznie wywołanie silnika + jego obsługa błędu, walidacja/parsowanie
zostały w trasach — realna różnica zachowania między HTML a JSON, patrz
`views/plan.py` docstring). Po drodze znaleziony i **naprawiony przy okazji**
hazard: `Flask(__name__)` w pakiecie liczy `root_path` inaczej niż w module —
statyki cichoby przestały się serwować (`web/__init__.py` dostał jawny
`template_folder`/`static_folder`).

**Kryterium twarde spełnione:** `git diff --stat` nie dotyka `templates/`
ani `static/`; 1103 testy zielono bez zmiany ani jednej asercji (1110 po
doliczeniu 6 nowych testów rozwiązywalności `url_for` — wymóg roadmapy,
łapią też gołe nazwy endpointów w `NAV_GROUPS` szablonu nawigacji).

**Znalezisko poboczne, świadomie NIE naprawione:** `/plan?timing_qty=…` przy
niewystarczających lotach rzuca `InsufficientLotsError` niezłapany → gołe
500 (dotyczy dziś OBU tras, HTML i JSON — żadna nie łapie wyjątku wokół
`optimize_sale_timing`). Naprawa zmienia zachowanie, więc łamałaby kryterium
E3. Naturalne miejsce: E6 (kalkulator dotyka tej samej logiki).

<details><summary>Plan sprzed implementacji (E3)</summary>

### E3 — Refaktor `web.py` (0.19.0) · ~2 dni

`web.py` to 1810 linii, z czego 47 tras zagnieżdżonych w jednej funkcji `create_app()`
(`web.py:133`), zamkniętych na `_conn()` i `db_path`. `test_web.py` ma 2453 linie.

**Decyzja techniczna do potwierdzenia przy implementacji — prawdziwe Blueprinty vs `register_*(app, ctx)`.**
Nawigacja rozwiązuje trasy po nazwie endpointu (`url_for('dashboard')` w `templates/base.html:51`),
a Flask automatycznie prefiksuje endpointy nazwą blueprintu. Prawdziwe Blueprinty zmieniają więc
`dashboard` → `portfel.dashboard` we **wszystkich** szablonach i w `_IngressPrefixMiddleware`.
**Rekomendacja: funkcje rejestrujące `register_portfel_routes(app, ctx)`** — ten sam podział na pliki,
identyczne nazwy endpointów, zero zmian w szablonach, zero ryzyka na ingressie. Blueprinty tylko jeśli
przy implementacji okaże się, że dają coś, czego funkcje nie dają.

Podział wg domen (~8 modułów `web/`): `portfel`, `podatki`, `plan`, `dane`, `ai`, `rynek`, `ustawienia`, `api`.

**Warstwa widoków (`views/`):** obliczenia wychodzą z tras do modułów składających dane.
Trasa staje się cienka: *pobierz → złóż → wyrenderuj*. To warunek wstępny E5 — „Stan konta"
składa się z gotowych klocków zamiast duplikować logikę pulpitu.

**Kryterium ukończenia (twarde):** `git diff` nie zmienia **żadnej** liczby ani żadnego HTML-a.
1065 testów zielono bez modyfikacji asercji. Nowy test: każdy `url_for()` w szablonach rozwiązuje się.

**Pliki:** `web.py` → `web/__init__.py` + `web/routes_*.py` + `views/*.py`; `test_web.py` dzielony analogicznie.
**Ryzyko:** cichy rozjazd nazw endpointów. Mitygacja: test rozwiązywalności `url_for` + Playwright po każdej stronie.

**Checkpoint:** przeklikanie wszystkich 12 stron w Playwright, screenshot + konsola, porównanie z poprzednią wersją.

</details>

---

### E4 — Księga gotówki (0.20.0) · WYDANE 2026-08-22

**Wynik:** nowy `cash.py` (model odczytu, zero zapisu do `tax/`) + `views/cash.py`
+ strona `/gotowka` (`web/routes_podatki.py`, `templates/cash.html`) w grupie
nawigacji „Podatki". Migracja v11 (`tax_payments`, `broker_cash`), nowy
niezmiennik w `integrity.py`, obie tabele dopisane do `backup.py::_CSV_TABLES`.
1146 testów zielono (+36, w tym 19 dla `cash.py` i 9 dla tras), `test_tax_*.py`
(beton) bez zmian.

**Zweryfikowane na produkcji (Playwright, 2026-08-22, po `ha_manage_updates`
z backupem):** `/gotowka?year=2025` pokazuje wpływy ze sprzedaży
**17 596,49 PLN / 4 154,72 EUR** — zgadza się co do grosza z jedyną sprzedażą
w bazie (`sales.revenue_pln`). Karta dywidend pokazuje **0,00 EUR** wkładu do
gotówki przy 3 realnych wypłatach 2025 (wszystkie DRIP). Zero błędów konsoli,
zero regresji layoutu na 390 px i 1920 px. Saldo u brokera renderuje się jako
„Brak danych" (nie zero) — poprawnie, bo nikt jeszcze nie wpisał odczytu; sam
wpis wymaga realnej liczby od użytkownika, więc pozostawiony jako otwarty krok
po stronie użytkownika, nie zasymulowany danymi testowymi na produkcji.

<details><summary>Plan sprzed implementacji (E4)</summary>

Nowy moduł `cash.py` — **model odczytu nad istniejącymi tabelami**, plus dwa nowe źródła.

**Dwa ustalenia empiryczne z 2026-08-22 (roadmapa kazała sprawdzić, nie zakładać —
oba zmieniają literę poniższych podpunktów):**

1. **Wyciąg Computershare NIE zawiera salda gotówkowego.** Sprawdzone na realnym PDF
   (`/config/nokia_import/Plan holdings statement 13219230 2026-08-19 14_00_16.pdf`,
   9 stron): sekcja „Assets by type" ma tylko `Shares`/`Restricted stock units`; ciągi
   `Cash`/`Balance`/`Currency` — zero wystąpień. **„Krok 2 — auto z importu" poniżej
   odpada.** `broker_cash` zostaje wyłącznie ręczne, jako szereg odczytów z wiekiem
   (nie jedna nadpisywana wartość — inaczej nie widać, kiedy przestało być aktualne).
2. **Wszystkie 20 dywidend w produkcji to DRIP** (`reinvested_lot_id` wypełniony na
   każdym wierszu). `dividends.net_received_eur` nigdy nie trafia na konto jako
   gotówka — to przychód podatkowy z zerowym przepływem gotówki. Księga pokazuje
   dywidendy **osobno, jako bezgotówkowe**, nie wlicza ich do salda.

Wyprowadzane z tego, co już jest:
- **Wpływy ze sprzedaży**: **`sales.revenue_pln` / `sales.nbp_rate`** dla EUR, nie
  `quantity × price_eur - fee_eur` — ten wzór gubi `proceeds_eur` (override „Sale
  Proceeds" z Withhold-to-Cover Typ B, patrz `tax/lots.py::record_sale`). Narastająco
  w roku i łącznie. `reported_revenue_pln` (v4) świadomie ignorowane — to liczba do
  deklaracji, nie przepływ gotówki.
- **Dywidendy**: pokazywane bezgotówkowo (patrz wyżej), przez
  `tax/dividends.py::payouts()` (nie surowe wiersze — pay_date nie jest unikalny,
  lekcja 0.17.3).
- **Podatek do zapłaty**: `tax/pit38.py::annual_report` za rok bieżący, z terminem 30.04

Nowe (minimalna migracja, v11):
- **`tax_payments`** — faktycznie zapłacone: rok, data, kwota, notatka. Zamienia PIT-38 z raportu rocznego w żywe saldo zobowiązania. Tylko PIT-38 w PL (podatek u źródła w Finlandii jest już wyprowadzany z `dividends` — nie dublować).
- **`broker_cash`** — saldo u brokera: data, kwota, waluta, źródło (`manual` | `pdf`, dziś zawsze `manual` — patrz ustalenie 1 wyżej). Append-only, `UNIQUE(as_of_date, currency)` jako UPSERT.

**Znane ograniczenie, świadomie NIE naprawiane tutaj:** `parse_withhold_to_cover`
paruje `taxes_eur` (podatek potrącony u źródła przy Withhold-to-Cover), ale
`imports_confirm_sale` go nie przekazuje do `record_sale` — nie ma gdzie wylądować w
schemacie. Pierwsza zaimportowana sprzedaż typu B zawyży wyliczony wpływ gotówkowy o
kwotę potrącenia. Naprawa dotyka `tax/`, więc odłożona jako osobna pozycja backlogu.

**Pliki:** nowy `cash.py`, nowy `views/cash.py`, `db.py` (migracja v11), nowa strona
`/gotowka` w `web/routes_podatki.py` + `templates/cash.html`, `backup.py`
(`_CSV_TABLES`), `integrity.py` (niezmiennik: zapłacony podatek > należny).
**Ryzyko:** mylenie „gotówki" z „przychodem podatkowym" — to dwie różne liczby (opłaty, kurs NBP, moment). Test musi to jawnie rozróżniać.

**Checkpoint:** ręczne uzgodnienie wpływów ze sprzedaży z realnym wyciągiem za jeden rok.

</details>

---

### E5 — Ekran „Stan konta" zastępuje pulpit (0.21.0) · ~1.5 dnia

`/` staje się odpowiedzią na „jaki jest mój stan": akcje + gotówka + podatek + najbliższe zdarzenia
+ wejście do kalkulatora. Dzisiejszy pulpit (cena, wykres, newsy, prognozy AI, co-pilot) → **`/rynek`**,
bez zmian w treści. Zero nowych pozycji w nawigacji.

**Decyzje domknięte przed implementacją (2026-08-22):**

| Pytanie | Wybór |
|---|---|
| Podział treści `/` ↔ `/rynek` | **Czysty podział** — karty Portfel / „Dziś warto wiedzieć" / „Zapytaj asystenta" przenoszą się na Stan konta i **znikają** z `/rynek` (odstępstwo od litery „bez zmian w treści" powyżej, świadome — inaczej karta Portfel byłaby w dwóch miejscach naraz) |
| Karta „Najbliższe zdarzenia" | **Pełne cztery** — vesting, dywidenda, koniec restrykcji ESPP, termin PIT-38 |
| Wejście do kalkulatora (E6) | **Link do `/plan`** na razie; w E6 podmiana `href` na `/wyplata` |
| Nawigacja | **Dwa płaskie linki** — „Stan konta" (`/`) i „Rynek" (`/rynek`); jeśli pasek zawinie na 390 px, etykieta skraca się do „Konto" |

Składane z klocków `views/` z E3 i z `cash.py` z E4 — **zero nowych obliczeń w tym etapie**.
Mobile-first od razu (warunek ukończenia, nie osobna praca).

**Pliki:** nowy `templates/account.html`, `templates/dashboard.html` → `templates/market.html`, `views/account.py`, `base.html` (nawigacja).
**Ryzyko:** utrata przyzwyczajeń — `/rynek` musi być jednym kliknięciem, nie schowane w grupie.

**Checkpoint:** Playwright na 390 px i 1920 px, screenshot + konsola. Ocena, czy pierwsze spojrzenie odpowiada na Twoje pytanie.

---

### E6 — Kalkulator wypłaty, dwukierunkowy (0.22.0) · ~2 dni

Jeden ekran `/wyplata`, przełącznik kierunku.

- **Kierunek A („ile akcji na X zł netto")** — **jedyna nowa matematyka w całej roadmapie**: odwrócenie `simulate_sale`. Bisekcja po ilości akcji nad istniejącym `_plan_fifo` (funkcja jest monotoniczna względem ilości, więc bisekcja jest poprawna i zbieżna). Zero duplikatu logiki FIFO.
- **Kierunek B („ile z N akcji")** — `tax/whatif.py::simulate_sale`, już istnieje. Praca prezentacyjna.
- Oba kierunki pokazują: brutto, opłaty, koszt FIFO, podatek wg aktywnej polityki, **na rękę**, przepadająca dopłata ESPP (`advisor.py::forfeit_for_quantity`), wykorzystanie straty z lat ubiegłych, wynikowa koncentracja.
- Wybór miesiąca sprzedaży (wpływ na rok podatkowy i na uwolnienie ograniczonych lotów) — spina się z `advisor.py::optimize_sale_timing`.

**Pliki:** `tax/whatif.py` (nowa `solve_for_net()`), `templates/withdrawal.html`, `views/withdrawal.py`, `web/routes_plan.py`.
**Kryteria twarde:** `solve_for_net(X)` → `simulate_sale(wynik)` daje netto w granicach ±1 zł od X; wynik nigdy nie przekracza dostępnej ilości akcji; brak zbieżności zwraca jawny błąd, nie przybliżenie po cichu.
**Ryzyko:** progi nieciągłe (przejście przez rok podatkowy, wyczerpanie straty) mogą łamać monotoniczność. Test na skonstruowanym przypadku z progiem, jawnie.

**Checkpoint:** trzy realne scenariusze policzone ręcznie w arkuszu vs wynik narzędzia.

---

### E7 — Uzgodnienie z wyciągiem (0.23.0) · ~1 dzień

Rozszerzenie dzisiejszego konfliktu `entity_type='balance'` (`computershare_pdf.py:399-454`)
z samego salda akcji na **pełną tabelę różnic**: akcje per plan, gotówka, dywidendy w okresie,
transze pending. Zielono/czerwono, z kwotą różnicy i linkiem do wiersza źródłowego.

Widok na `/imports`, liczony przy imporcie i na żądanie. Karmi też `integrity.py` z E2 —
niezgodność z wyciągiem to kolejny niezmiennik.

**Pliki:** `importers/computershare_pdf.py` (rozszerzenie funkcji uzgadniającej), `integrity.py`, `templates/imports.html`.
**Ryzyko:** dopóki `broker_cash` z E4 jest ręczne, różnica gotówki będzie fałszywie czerwona. Pozycja bez źródła musi się pokazywać jako „brak danych", nie jako „niezgodność".

---

### E8 — Ślad „skąd ta liczba" (0.24.0) · ~1.5 dnia

Rozciągnięcie istniejącego `tax/trace.py` (`fx_derivation`, `enrich_allocations`) ze stron
podatkowych na **stan konta i kalkulator**. Każda kwota klikalna → rozwinięcie ze składnikami
i źródłem: który lot, który wiersz PDF, która tabela NBP z jakiego dnia.

**Kryterium twarde:** suma składników w rozwinięciu == wyświetlana kwota, co do grosza.
Ta sama zasada, którą roadmapa v1 nałożyła na atrybucję — inaczej rozwinięcie jest ozdobnikiem.

**Pliki:** `tax/trace.py` (uogólnienie), `templates/_macros.html` (makro `traced()`), `views/*`, `static/app.js`.

---

## Wersjonowanie

| Wersja | Etap |
|---|---|
| 0.18.0 | E1 audyt (bez wydania) + E2 naprawa i niezmienniki |
| 0.19.0 | E3 refaktor |
| 0.20.0 | E4 księga gotówki |
| 0.21.0 | E5 stan konta |
| 0.22.0 | E6 kalkulator |
| 0.23.0 | E7 uzgodnienie |
| 0.24.0 | E8 ślad |

E2 i E4 mają migracje — **przed każdą pełny eksport ZIP**.
E1 nie jest wydawany (sama diagnoza).
**e-Deklaracje** (dawne 0.18.0 z roadmapy v2) przesunięte na po E8, nadal warunkowe, nadal zaczyna się od researchu XSD, nie od kodu.
**1.0.0** zarezerwowane zgodnie z pierwotną zasadą — po pełnym sezonie rozliczeniowym na tym silniku.

Release wg `feedback_ha_addon_release`: bump `nokia_tracker/config.yaml` + `__init__.py`, **published** GitHub release, weryfikacja wersja == tag, potem update przez Supervisor.

---

## Świadomie poza zakresem

- **Cięcie warstwy analitycznej** (benchmark, atrybucja, ryzyko, co-pilot) — odrzucone w wywiadzie. Zostaje nietknięte, ale też nie rozwijane.
- **Obsługa splitu akcji** — nadal znana bomba zegarowa. Wraca do backlogu, nie do tej roadmapy.
- **Klastrowanie newsów** — nadal zablokowane przez 503 na `/v1/embeddings`.
- **Inny broker** — nadal bez powodu.
- **Automatyczne złożenie PIT** — potwierdzone: brak publicznego API.

---

## Weryfikacja końcowa (po E8)

1. `pytest` — wszystkie testy zielono; oczekiwany wzrost 1065 → ~1250.
2. `test_tax_*.py` zielono (beton nietknięty).
3. `integrity.check_all()` na produkcji — zero pęknięć.
4. Playwright na 390 px i 1920 px: `/`, `/rynek`, `/wyplata`, `/imports`, `/pit38` — screenshot **i** konsola bez błędów, do `/config/playwright/`.
5. Test empiryczny celu: „potrzebuję X zł netto w listopadzie" → odpowiedź w ≤ 3 kliknięciach od `/`, liczba zgodna z ręcznym wyliczeniem.
6. Uzgodnienie stanu konta z najnowszym wyciągiem Computershare — zielono we wszystkich pozycjach albo jawnie wyjaśniona różnica.
