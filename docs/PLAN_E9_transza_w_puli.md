# Nokia Tracker 0.25.0 — transza wydana w zbiorczym locie + polityka pushy + śledztwo -1,6118

## Kontekst

Od 2026-08-24 06:35 nocny kontroler spójności (`main.py::integrity_check_job`) wysyła
push „Nokia Tracker: problem ze spójnością danych". Log dodatku:

```
2026-08-24 06:35:02 WARNING [nokia_tracker] Kontrola spójności: 2 rodzajów problemów
(stale_pending_vest, statement_mismatch:shares)
```

**Diagnoza (potwierdzona empirycznie na produkcji, wersja 0.24.2):**

`stale_pending_vest` (waga `error`, 1 szt.) dotyczy transzy ESPP **24,4200 szt.**
(grant `espp_grant:2024-10-21:24.42`, vest 2025-08-01, `natural_key`
`espp_vest:2024-10-21:2025-08-01:24.42`). Wydanie 0.24.2 **celowo** cofnęło ją do
`status='pending'`, `lot_id=NULL` — jej akcje są już policzone w zbiorczym locie
Withhold-to-Cover `vested_release:2025-08-28:3.71:101.396662` (potwierdzony na `/loty`:
2025-08-28, 101,3967 szt., NBP 4,2639/2025-08-27). Docstring
`data_fixes.py::revert_phantom_espp_match_lot_2025_08` mówi wprost: transza `pending`
bez lotu jest tu **stanem POPRAWNYM**.

Kontroler `integrity.py::_stale_pending_vest` nie umie tego stanu wyrazić i nazywa go
błędem („prawdopodobnie brakujący import"). **Sprzeczne są nie dane, tylko niezmiennik
z własnym, udowodnionym modelem danych.**

Dwa niezależne dowody, że transza jest wydana (nie zaginiona):
1. Arytmetyka z 0.24.2: 50% dopasowania sześciu zakupów ESPP odblokowanych 2025-08-28
   = 101,396666 ≈ lot 101,396662 (różnica 0,000004 = zaokrąglenie druku PDF); grant
   2024-10-21 jest ostatnim z tej szóstki.
2. Uzgodnienie z wyciągiem 2026-08-18 (`/imports`): pozycja „Transze oczekujące —
   wiersze" = **wyciąg 1 360,8900 vs baza 1 360,8900, różnica +0,0000**. Computershare
   nie liczy tej transzy jako oczekującej.

**Skutki uboczne dzisiaj (wszystkie na produkcji, sprawdzone):**

| Miejsce | Objaw |
|---|---|
| push + `/dane` | `stale_pending_vest`, waga `error`, codziennie |
| `/imports` | „Transze oczekujące (RSU)" i „Suma (Akcje + RSU)" → **brak danych** (24,42 > tolerancja ⇒ `reconcile.py:357` zeruje pozycję) |
| `/grants` | badge „zaległe — sprawdź wyciąg" przy transzy 2025-08-01 |
| `/` (konto) | ostrzeżenie „…nie ma ich w żadnym locie — wgraj najnowszy wyciąg" |
| MQTT | `grants_values.unvested_qty` = `unvested_summary.pending_qty` **zawyżone o 24,42** |

Drugi finding, `statement_mismatch:shares` (waga `warning`): pozycja „Akcje" wyciąg
2 935,5107 vs baza 2 933,8989 = **-1,6118**. To znany, udokumentowany rozjazd
(`docs/PLAN_KROK_20_reported_override.md`, `docs/ROADMAP_V3.md:405`) — dawniej maskowany
płaską tolerancją ±2,0, odsłonięty przez tolerancję liczoną z danych w E7. Nie jest
regresją 0.24.2.

## Cel

1. Zapisać raz, jawnie i dowodowo, że transza jest **wydana w zbiorczym locie** — i
   pozwolić wszystkim czytelnikom się z tym zgodzić (znika fałszywy błąd, wraca
   uzgodnienie RSU/Sumy, przestaje zawyżać sensor `unvested_qty`).
2. Przestać wysyłać codzienny push dla findingów o wadze `warning`.
3. Zbadać rozjazd -1,6118 akcji — z uczciwym dopuszczeniem wyniku „nie do wyjaśnienia
   z danych" i wtedy jawną decyzją o tolerancji.

## Krok 0 — plan do repo

Skopiować ten plik do `/config/addons/nokia_tracker/docs/PLAN_E9_transza_w_puli.md`
**przed pierwszą zmianą kodu** (konwencja z `CLAUDE.md`).

## Krok 1 — model danych: `vests.pooled_lot_id`

**`nokia_tracker/nokia_tracker/db.py`** — dopisać migrację na koniec `_MIGRATIONS`
(`SCHEMA_VERSION` = `len(_MIGRATIONS)`, podbija się sam):

```sql
ALTER TABLE vests ADD COLUMN pooled_lot_id INTEGER REFERENCES lots(id);
```

Semantyka (do udokumentowania w komentarzu przy migracji): *transza została wydana jako
część zbiorczego lotu, który pokrywa też inne transze — nie ma i nie będzie miała
własnego lotu*. `lot_id` = lot wyłącznie tej transzy; `pooled_lot_id` = lot dzielony.
Oba NULL i `status='vested'` pozostaje błędem.

Stan docelowy transzy: `status='vested'`, `lot_id=NULL`, `pooled_lot_id=<id lotu 101,3967>`.
Dzięki `status='vested'` **wszystkie** zapytania `WHERE status='pending'`
(`tax/grants.py:92,158,321,395,498`, `integrity.py:226`) automatycznie przestają ją
widzieć — bez dotykania sześciu modułów liczących `overdue` (`sensors.py`,
`advisor.py`, `dividend_outlook.py`, `portfolio.py`, `breakdown.py`, `templates/plan.html`).
Grep potwierdza, że `'vested'` czyta tylko **trzy** miejsca: `reconcile.py:140`,
`integrity.py:103`, `templates/grants.html:6`.

## Krok 2 — jednorazowa naprawa danych

**`nokia_tracker/nokia_tracker/data_fixes.py`** — nowa idempotentna funkcja
`link_pooled_espp_match_2025_08(conn)`, dopisana do `apply_all()` **po**
`revert_phantom_espp_match_lot_2025_08` (kolejność ma znaczenie: revert ustawia
`pending`, ta funkcja domyka stan). Konwencja modułu: stare naprawy zostają jako ślad.

- Guard 1: znaleźć lot po `natural_key='vested_release:2025-08-28:3.71:101.396662'` —
  brak ⇒ no-op (nie zgadujemy).
- Guard 2: transza po `natural_key='espp_vest:2024-10-21:2025-08-01:24.42'` z
  `pooled_lot_id IS NULL` — inaczej no-op.
- `UPDATE vests SET status='vested', pooled_lot_id=?` — **zero** zmian w `lots`,
  `sale_allocations`, `sales`, `reported_*_pln`. Żadna suma akcji ani PIT-38 się nie rusza.
- Docstring: oba dowody z sekcji „Kontekst" + jawne „dlaczego NIE dokładamy lotu"
  (dołożenie = powrót fantomu z 0.24.1; rozbicie zbiorczego lotu na 24,42 + 76,98
  rozjechałoby `natural_key` z ilością i wygenerowało konflikt przy re-imporcie tego
  samego wyciągu — droga świadomie odrzucona).
- `logger.warning` ze śladem, jak pozostałe naprawy.

## Krok 3 — czytelnicy

| Plik | Zmiana |
|---|---|
| `integrity.py::_vested_without_lot` | `WHERE status='vested' AND lot_id IS NULL AND pooled_lot_id IS NULL`; komunikat: „…bez lotu własnego ani zbiorczego" |
| `reconcile.py::unvested_as_of` | `LEFT JOIN lots l ON l.id = COALESCE(v.lot_id, v.pooled_lot_id)`; `unreconstructable` tylko gdy oba NULL. Gałąź `vested` bez zmian — data uwolnienia bierze się z lotu zbiorczego (2025-08-28 ≤ 2026-08-18 ⇒ transza nie wchodzi do `unvested`, `ambiguous_qty` spada do 0 ⇒ RSU i Suma znów liczone) |
| `reconcile.py` docstring modułu | dopisać trzeci przypadek do „Klucz rekonstrukcji transz: `vests.lot_id`" |
| `tax/grants.py::valuation` | gałąź `lot_id IS NULL AND pooled_lot_id IS NOT NULL` → `reconciled=False`, `pooled_lot_id` w wyniku, żeby szablon pisał „wydane w zbiorczym locie #X" zamiast „niedopasowane — prognoza" (wartość tej transzy jest już liczona w locie zbiorczym) |
| `tax/grants.py::list_espp`, `list_lti_grouped` | dołożyć `v.pooled_lot_id` do `SELECT` (badge) |
| `templates/grants.html` | badge „wydane w zbiorczym locie" przy `pooled_lot_id`; `status_labels` bez zmian |
| `backup.py::_CSV_TABLES["vests"]` | dopisać `pooled_lot_id` do listy kolumn CSV |

## Krok 4 — push tylko dla wagi `error`

**`nokia_tracker/nokia_tracker/main.py::integrity_check_job`** — `alerts.allow_fire` i
`alerts.log_fired` zostają dla **wszystkich** findingów (log alertów i karta „Spójność
danych" na `/dane` nadal pokazują komplet), ale `ha_client.notify(...)` odpala się
wyłącznie gdy `f.severity == "error"`. Uzasadnienie do docstringu: `statement_mismatch`
i `unresolved_import_conflict` to stany, które bywają poprawne i trwałe — codzienny push
za coś, czego nie da się „naprawić", uczy ignorować powiadomienia.

## Krok 5 — śledztwo: rozjazd „Akcje" -1,6118

Read-only, offline, **bez zapisu do produkcyjnej bazy**:

1. Pobrać `/dane/eksport.zip` (sesja ingress wg pamięci `reference_ingress_session_no_admin_api`),
   rozpakować do scratchpada — analiza na kopii, nie na żywej bazie.
2. **Eksperyment rozstrzygający:** puścić `reconcile.reconcile()` na **każdym** wierszu
   `statement_snapshots` (nie tylko najnowszym) i zestawić różnicę pozycji „Akcje"
   w czasie:
   - różnica **stała** ⇒ jedno historyczne zdarzenie (brakujący lot sprzed pierwszego
     wyciągu) — szukać go po dacie skoku;
   - różnica **rosnąca** ⇒ systematyczny ubytek per zdarzenie (najbardziej podejrzane:
     akcje z reinwestycji dywidendy, `parse_vested_dividend_shares` /
     `source='holdings_snapshot'`, ~0,01 na wiersz — to samo źródło, z którego liczona
     jest tolerancja `shares_tolerance`).
3. Rozbicie różnicy per `lot_type` (`shares_as_of(...)["by_lot_type"]`) vs sekcje
   wyciągu — zawęzić do kubełka (own / matched / lti / dividend_drip).
4. Kontrola krzyżowa dywidend DRIP: `Σ lots(source='holdings_snapshot', lot_type='dividend_drip')`
   vs wiersze dywidendowe w snapshotach.

**Wyjście ze śledztwa — jedno z dwóch, obowiązkowo jawne:**
- znaleziona przyczyna ⇒ osobna, idempotentna naprawa w `data_fixes.py` + test;
- brak przyczyny ⇒ **żadnego podnoszenia tolerancji „żeby zgasło"**; zapis wniosku w
  `docs/PLAN_E9_transza_w_puli.md` i decyzja użytkownika (zostawić `warning` na karcie
  `/dane` — po kroku 4 i tak nie pushuje).

Śledztwo nie blokuje wydania kroków 1–4.

## Krok 6 — testy (TDD: czerwony przed zielonym)

- `tests/test_db.py` — migracja: `pooled_lot_id` istnieje, `SCHEMA_VERSION` = liczba migracji.
- `tests/test_integrity.py` — `vested` + `pooled_lot_id` ⇒ zero findingów; `vested` +
  oba NULL ⇒ nadal `vested_without_lot` (strażnik nie-regresji); transza po naprawie ⇒
  brak `stale_pending_vest`.
- `tests/test_reconcile.py` — `unvested_as_of`: pooled z `acquired_date ≤ as_of` ⇒ nie
  liczona ani do `unvested`, ani do `ambiguous`, `total is not None`; pooled z
  `acquired_date > as_of` ⇒ liczona jako oczekująca; oba `lot_id` NULL ⇒
  `unreconstructable`.
- `tests/test_data_fixes.py` — idempotencja (dwa przebiegi = ten sam stan), brak zmian
  w `lots`/`sale_allocations`/sumie akcji, no-op gdy brak lotu zbiorczego, no-op gdy
  transza już oznaczona.
- `tests/test_grants.py` — `valuation` gałąź pooled; `list_espp` zwraca `pooled_lot_id`.
- `tests/test_main.py` (lub `test_notifier.py`) — `integrity_check_job` nie woła
  `notify` dla wagi `warning`, woła dla `error`, a `log_fired` dostaje oba.

Uruchomienie: `cd /config/addons/nokia_tracker/nokia_tracker && python -m pytest` —
baza dziś ~1110 testów, żaden istniejący nie powinien zmienić asercji (jeśli zmienia,
to sygnał, że zmiana wycieka poza zamierzony zakres).

## Krok 7 — wydanie 0.25.0 i weryfikacja na produkcji

Wersja **0.25.0** (migracja schematu + zmiana zachowania powiadomień), nie 0.24.3.

1. Bump `nokia_tracker/config.yaml` **i** `nokia_tracker/nokia_tracker/__init__.py`.
2. `CHANGELOG.md` + `README.md` (sekcja o karcie „Spójność danych" i polityce pushy).
3. Push do repo `miczu71/nokia_tracker`, **opublikowany** (nie draft) GitHub release,
   tag == wersja w `config.yaml`.
4. Aktualizacja przez Supervisor (`ha_manage_app`), weryfikacja wersji w `/info`.
   **Nigdy** cyklu uninstall/remove_repository/... — kasuje SQLite z realnymi danymi.
5. Weryfikacja po restarcie (Playwright + konsola, zrzuty do `/config/playwright/`):
   - `/dane` → karta „Spójność danych": zostaje **wyłącznie** `statement_mismatch:shares`
     (waga `warning`), `stale_pending_vest` znika;
   - `/imports` → „Transze oczekujące (RSU)" 1 360,8991 vs baza z liczbą (nie „brak
     danych"), „Suma (Akcje + RSU)" policzona, „Akcje" nadal -1,6118;
   - `/grants` → transza 2025-08-01 (24,4200): status „nabyte" + badge „wydane w
     zbiorczym locie", bez „zaległe — sprawdź wyciąg";
   - `/` → ostrzeżenie o transzach spoza lotów znika; „Wartość całkowita" **bez zmian**
     co do grosza (transza nigdy do niej nie wchodziła);
   - MQTT `unvested_qty` niższe dokładnie o 24,4200;
   - log dodatku po 06:35 następnego dnia albo ręcznym wywołaniu joba.

## Ryzyka

| Ryzyko | Reakcja |
|---|---|
| `status='vested'` bez własnego lotu łamie założenie E7 („przejście pending→vested zawsze z lotem") | To jest właśnie zmiana modelu — docstring `reconcile.py` i `unvested_as_of` aktualizowane w tym samym kroku; test na `unreconstructable` pilnuje granicy |
| Zmiana zachowania sensora MQTT `unvested_qty` (spadek o 24,42) | Świadoma i pożądana — dotąd zawyżony; odnotować w CHANGELOG jak przy kroku 21 |
| Problem się powtórzy przy czterech transzach 2026-08-01 (uwolnienie 2026-08-27) | Znany, poza zakresem tego wydania: mechanizm istnieje (`pooled_lot_id`), brakuje tylko akcji w UI. Odnotować w `docs/ROADMAP_V3.md` jako kandydata na następny krok |
| Re-import wyciągu nadpisze `pooled_lot_id` | Importer nie dotyka tej kolumny; test idempotencji `data_fixes` + `apply_all` przy każdym starcie przywraca stan |
| Śledztwo -1,6118 nie znajdzie przyczyny | Dopuszczone z góry: wniosek zapisany, tolerancja **nie** podnoszona bez decyzji użytkownika |

## Wynik implementacji (2026-08-24)

Kroki 1–4 i 6 wykonane, wydanie 0.25.0. Pełny zestaw testów: **1340 przeszło, zero
regresji** (baza przed zmianą: 1110 z E8; przyrost obejmuje też testy niezwiązane z tym
wydaniem, dodane między E8 a E9).

## Wynik śledztwa: rozjazd „Akcje" -1,6118 (Krok 5)

**Metoda.** Ponieważ pobranie pełnego `/dane/eksport.zip` przez dostępne narzędzie
(proxy HTTP zwraca tekst, nie surowe bajty ZIP) okazało się niepraktyczne, śledztwo
wykorzystało istniejącą lokalną kopię bazy produkcyjnej sprzed rewersji 0.24.2 (z
wcześniejszej sesji, `/tmp/.../scratchpad/live_db/nokia.db`, po re-imporcie 6 wyciągów
z 0.24.1, ale przed cofnięciem fantomowego lotu). Zweryfikowano metodologię: migracja do
schematu v13 + `data_fixes.apply_all()` na tej kopii odtworzyła **dokładnie** bieżący
stan produkcyjny — pozycja „Akcje" na 2026-08-18 wyszła -1,611775 szt., co zgadza się co
do czwartej cyfry z tym, co pokazuje żywy `/imports` (-1,6118). Cała dalsza analiza na tej
zweryfikowanej, w pełni naprawionej (post-apply_all) kopii — offline, zero zapisu do
produkcji.

**Ustalone fakty:**
1. `reconcile.reconcile()` puszczony na **wszystkich 6** zapisanych snapshotach wyciągu
   pokazuje diff pozycji „Akcje" (baza − wyciąg): `2023-01-01: 0,0000`,
   `2024-01-01: +0,0102`, `2025-01-01: +0,0251`, `2026-01-01: -1,611775`,
   `2026-07-26: -1,611775`, `2026-08-18: -1,611775`. Różnica jest **stała** od
   2026-01-01 (identyczna w 4. miejscu po przecinku), nie rośnie dalej mimo kolejnych
   pół roku aktywności — wyklucza to hipotezę „ciągły drift" (np. kumulacja zaokrągleń
   DRIP, ~0,01/wiersz) jako główną przyczynę i wskazuje na **jedno dyskretne zdarzenie**
   w oknie 2025-01-01 → 2026-01-01 (jedynym roku ze sprzedażą — sale #1, 784 szt.,
   2025-10-27).
2. W TYM oknie **wszystkie cztery** kategorie uzgodnienia, które `reconcile.py` śledzi
   po `natural_key`, zgadzają się **co do 0,0000**: `espp_purchases_in_period` (7 zakupów,
   212,434465 szt.), `dividends_in_period` (3 wypłaty, 73,03 EUR), Withhold-to-Cover Typ A
   (101,396662 szt., dokładnie lot `vested_release:2025-08-28:3.71:101.396662`) i Typ B
   (sprzedaż 784 szt., dokładnie `sales` #1). Żadna z nich nie jest źródłem rozjazdu.
3. Poboczne, ale realne znalezisko przy okazji: `parse_vested_matching_shares()` /
   `parse_vested_dividend_shares()` (importer, sekcje „powtarzający się snapshot
   aktualnie posiadanego salda") tworzą **osobny lot za każdym razem, gdy zmieni się
   raportowana ilość** między kolejnymi wyciągami (natural_key zawiera ilość) — w bazie
   są tego dowody: cztery loty `vested_matching:2023-08-30:3.65:*` (7,20 / 7,33 / 8,21 /
   9,09 szt.) i cztery `vested_matching:2024-08-29:3.79:*` (28,48 / 30,00 / 30,62 / 33,36
   szt.), oraz pary `vested_dividend:*` z 2023–2024. **To nie jest przyczyna -1,6118**:
   te loty leżą PRZED oknem 2025 (już wliczone w małe, ustabilizowane diffy z 2024/2025),
   a próbna eliminacja „nadmiarowych" kopii (zostawiając tylko największą z każdej grupy)
   przesuwa diff o ~+112 szt. w PRZECIWNYM kierunku — więc to osobny defekt danych, nie
   ten. Wart dedykowanego audytu w przyszłości (potencjalne zawyżenie „Akcje" gdzie
   indziej w historii, zamaskowane przez FIFO konsumujące te stare loty w całości przy
   sprzedaży #1), ale POZA zakresem E9.

**Wniosek: przyczyna NIE znaleziona.** Rozjazd -1,6118 szt. powstał w oknie
2025-01-01→2026-01-01, ale nie w żadnej z czterech nazwanych, śledzonych kategorii —
oznacza to zdarzenie, którego obecny model danych **w ogóle nie rejestruje jako
osobnej pozycji** (żaden `natural_key` do porównania), więc jest niewidoczne dla
uzgodnienia per-kategoria mimo że psuje sumę. Zgodnie z planem: **tolerancja NIE zostaje
podniesiona**. Pozycja zostaje na karcie „Spójność danych" (`/dane`) jako `warning`
(po Kroku 4 bez codziennego pushu). Decyzja, czy warto zainwestować dalszy czas w
pogłębione śledztwo (np. ręczne porównanie PDF strona po stronie z sekcją „Vesting
Schedule"), należy do użytkownika.
