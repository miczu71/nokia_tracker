# Krok 23 — karta „Portfel" na pulpicie jako kafelki (0.8.0)

## Kontekst

Użytkownik: *„przerób sekcję »portfel« w pulpicie. ma to być lepsze wizualnie, np. w formie
kafelków. obecnie nie jest to dla mnie czytelne"*.

Karta „Portfel" (`templates/dashboard.html:54-143`, dodana w kroku 21) ma dziś realne problemy
czytelności, potwierdzone na zrzutach `/config/playwright/nokia_tracker_dashboard_0.6.0_*.jpg`:

1. **Najważniejsza liczba jest na dole, poza ekranem.** Blok „Razem" (wartość całkowita portfela)
   ląduje pod dwoma innymi blokami — na 1920×1080 jest ucięty, na telefonie trzeba przewijać.
2. **Brak separatorów tysięcy.** „23241 EUR", „99838 zł", „43780 zł" — oko musi liczyć cyfry.
3. **Ilości z 4-5 miejscami po przecinku.** „2887.05134", „2744.3219", „142.7294" — szum,
   który dominuje kafelek.
4. **Trzy realne kubełki wyciągu Computershare są opisane niespójnie**: „wolne / z ograniczeniem"
   to zdanie tekstowe wciśnięte pod kafelkami, a „zablokowane" ma własny blok — czyli dwa z trzech
   kubełków wyglądają zupełnie inaczej niż trzeci.
5. **Kafelki rozjeżdżają się na całą szerokość** (`.main { max-width: none }` z kroku 17):
   5 statystyk rozciągniętych na 1920 px, z ogromnymi dziurami między etykietą a wartością.
6. Wszystko ma tę samą wagę wizualną — brak hierarchii i koloru; `.stat` to goły `label + liczba`
   bez ramki, więc „kafelek" jest kafelkiem tylko z nazwy.

Efekt docelowy: karta „Portfel" odpowiada od pierwszego spojrzenia na trzy pytania — *ile to jest
warte*, *co mogę sprzedać dziś*, *jak mi idzie* — z liczbami w PLN, sformatowanymi po polsku.

**Decyzje użytkownika (AskUserQuestion, ta sesja):**
- układ: **suma na górze + trzy kubełki + pasek wyniku**,
- waluta: **PLN duże, EUR jako druga linia** (tylko w karcie Portfel; reszta stron bez zmian),
- liczby: **separator tysięcy + 2 miejsca po przecinku** dla ilości (pełne 4 miejsca zostają tam,
  gdzie służą do uzgodnienia z wyciągiem).

Zakres: **wyłącznie karta „Portfel" na pulpicie**. Karta kursu/wykresu, sentyment, rekomendacja,
prognozy, alerty i pozostałe strony (`/portfolio`, `/lots`, `/grants`, `/pit38`) — bez zmian.
Zero zmian w silniku podatkowym (`tax/*.py`) i w liczbach — to zmiana prezentacji plus jedna
czysta funkcja składająca dane, które już są liczone.

## Docelowy układ

```
┌ Portfel ─────────────────────────────────────────────────────┐
│  WARTOŚĆ CAŁKOWITA                                           │
│  143 618 zł            ≈ 33 423 EUR · 4 153,05 akcji         │
├──────────────┬───────────────────┬───────────────────────────┤
│ 🟢 Wolne     │ 🟡 Z ograniczeniem│ ⚪ Zablokowane            │
│ 2 744,32     │ 142,73            │ 1 266,00       akcji      │
│ 94 936 zł    │ 4 902 zł          │ 43 780 zł                 │
│ ≈ 22 078 EUR │ ≈ 1 140 EUR       │ ≈ 10 191 EUR              │
│ można        │ do 2026-08-01      │ najbliższe 633,00        │
│ sprzedać     │ (utrata dopł. 50%) │ od 2027-07-05            │
├──────────────┴───────────────────┴───────────────────────────┤
│ Koszt bazowy · Wartość rynkowa · Niezrealizowany P&L ·        │
│ Całkowity zwrot · Dywidendy netto           (pasek „Wynik")   │
│ [Edytuj stan posiadania]  Loty · Granty · Dywidendy           │
│ ostrzeżenie o zaległych transzach (jeśli jest) + stopka kursu │
└───────────────────────────────────────────────────────────────┘
```

Reguły widoczności:
- kubełek **Wolne** i **Zablokowane** — zawsze (zero to też informacja),
- kubełek **Z ograniczeniem** — tylko gdy `restricted.restricted_qty > 0` (zachowuje dzisiejsze
  zachowanie linii tekstowej),
- brak ceny/kursu → w kafelku `—`, nigdy `None` ani zgadywana wartość
  (wzorzec z `grants.py::_value`).

## Zmiany w kodzie

### 1. `nokia_tracker/format.py` (nowy) — formatowanie liczb po polsku

Czyste funkcje, zero zależności, testowalne bez Flaska:

- `money(v, decimals=0)` → `"143 618"`, `"4 902,50"` — separator tysięcy to **znak U+00A0**
  (nie encja `&nbsp;`: filtr Jinja z autoescape zamieniłby `&` na `&amp;`), separator dziesiętny
  to przecinek.
- `qty(v, decimals=2)` → `"2 887,05"`.
- `pct(v, decimals=1, signed=True)` → `"+2 467,5"`.
- Każda zwraca `"—"` dla `None` — to jest warunek, od którego zależy istniejący
  `test_dashboard_omits_pln_when_no_fx_rate` (`assert "None" not in html`).

Rejestracja w `web.py::create_app`: `app.jinja_env.filters.update({"money": …, "qty": …, "pct": …})`.
Filtry są ogólne — używam ich w tym kroku **tylko** w karcie Portfel, żeby nie ruszać liczb na
stronach podatkowych, ale są gotowe na późniejsze kroki.

### 2. `nokia_tracker/portfolio.py::dashboard_buckets()` (nowa czysta funkcja)

Sygnatura: `dashboard_buckets(position: dict, restricted: dict, unvested: dict) -> dict`.

Składa trzy słowniki, które `web.py::dashboard` już pobiera, w strukturę kubełków:

```python
{"free": {"qty", "value_eur", "value_pln"},
 "restricted": {"qty", "value_eur", "value_pln", "free_until"},
 "locked": {"qty", "value_eur", "value_pln", "next_date", "next_qty"},
 "total": {"qty", "value_eur", "value_pln"}}
```

- `free = position − restricted` (ilość i wartości; oba pochodzą z tej samej ceny, więc odejmowanie
  jest spójne). `restricted_own_summary()` **już zwraca** `restricted_value_eur`/`restricted_value_pln`
  (`tax/grants.py:400-407`) — dziś szablon je wyrzuca, tu wreszcie się przydają.
- `locked` = `unvested.upcoming_*` (zaległe `overdue` nadal poza sumą — świadoma decyzja kroku 21,
  udokumentowana w `unvested_summary()`).
- `total` zastępuje dzisiejsze ręczne składanie w `web.py:160-173` (ta arytmetyka przenosi się
  1:1 do funkcji, razem z obsługą `None`) — mniej logiki w widoku, plus test jednostkowy bez DB.

### 3. `templates/dashboard.html` — przebudowa bloku `54-143`

Trzy `.subcard` → hero + siatka kubełków + pasek wyniku. Reszta pliku (kurs, wykres, sentyment,
rekomendacja, prognozy, alerty) bez zmian.

- Hero: `.pf-hero` z `total.value_pln` jako wartością główną, `≈ EUR · X akcji` jako podlinią.
- `.pf-buckets` → trzy `.pf-bucket` (`.free` / `.restricted` / `.locked`), każdy: kropka statusu +
  nazwa, ilość, wartość PLN (duża), wartość EUR (mała), linia kontekstowa (co to znaczy / do kiedy).
- Pasek „Wynik": istniejąca siatka `.grid.stats` z makrem `stat()` z `_macros.html` — bez zmian
  w makrze (jest pinowane bajtowo przez `test_sales_page_shows_year_totals`), tylko z wartościami
  przepuszczonymi przez nowe filtry i z PLN jako wartością główną, EUR w `sub=`.
- Zostają bez zmian merytorycznych: ostrzeżenie o zaległych transzach (**pełne 4 miejsca** — służy
  do uzgodnienia z wyciągiem), stopka „kurs bieżący, nie tabela NBP" (pinowana testem),
  przycisk „Edytuj stan posiadania", linki do Lotów/Grantów/Dywidend.

### 4. `static/app.css` — nowe klasy

`.pf-hero`, `.pf-buckets`, `.pf-bucket`, `.pf-bucket .dot`, `.pf-note`, `.pf-perf`.

- `.pf-buckets { display:grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); max-width: 1120px }`
  — kafelki przestają się rozciągać na 1920 px, na 390 px schodzą do jednej kolumny bez media query.
- `.pf-bucket` dostaje realną ramkę, `background: var(--page)`, `border-radius` i **lewy akcent**
  (wzorzec z istniejącego `.stat.highlight`, `app.css:250-253`): `--good` dla wolnych, `--series-3`
  dla ograniczonych, `--baseline` dla zablokowanych.
- Wyłącznie istniejące tokeny — light/dark działa bez nowych deklaracji kolorów.
- `@media print`: hero i kubełki nie mogą się łamać w poprzek strony (`break-inside: avoid`).

## Testy

Nowe:
- `tests/test_format.py` — separator tysięcy jest U+00A0, przecinek dziesiętny, `None → "—"`,
  ujemne, zero, zaokrąglanie.
- `tests/test_portfolio.py` — `dashboard_buckets()`: podział wolne/ograniczone, brak ograniczeń,
  `None` w cenie propaguje się do `None` w wartościach (nie do zera), suma = posiadane + nadchodzące
  bez zaległych.
- `tests/test_web.py` — pulpit renderuje trzy kubełki z poprawnym podziałem na danych z istniejącego
  `_make_full_portfolio_dashboard_app()`.

Do aktualizacji (pinują dzisiejsze surowe napisy — zmiana formatu jest celem tego kroku, nie
skutkiem ubocznym; ~7 asercji w `tests/test_web.py`):
- `test_dashboard_shows_three_portfolio_blocks_with_correct_totals` — etykiety
  (`"W posiadaniu"`/`"Razem"` → `"Wolne"`/`"Wartość całkowita"`) i liczby (`"2800"` → `"2 800"`,
  `"170.0000"` → `"170,00"`).
- `test_dashboard_shows_restriction_line_when_own_lot_restricted` (`"100.0000"` → `"100,00"`),
- `test_dashboard_hides_restriction_line_when_nothing_restricted` — przestawić na nową etykietę
  kubełka, żeby dalej realnie czegoś pilnował,
- `test_dashboard_empty_portfolio_blocks_render_without_error` (`"Razem"` → nowa etykieta),
- `test_dashboard_shows_pln_alongside_eur` (`"1720"` → `"1 720"`),
- `test_dashboard_reflects_saved_portfolio` (`"100.0"` → `"100,00"`),
- `test_dashboard_shows_lots_based_qty_not_manual_settings` (`"12.5"` → `"12,50"`).

Bez zmian: `test_dashboard_shows_overdue_warning_when_present` (`"5.0000"`) — ostrzeżenie celowo
zachowuje 4 miejsca; `test_dashboard_labels_current_rate_not_nbp`; wszystkie testy `tax/*`.

TDD: najpierw `test_format.py` i test `dashboard_buckets()` (czerwone z właściwego powodu), potem
implementacja, na końcu szablon/CSS i aktualizacja pinów w `test_web.py`.

## Kolejność wykonania

1. `docs/PLAN_KROK_23_portfel_kafelki.md` — kopia tego planu do repo **przed kodem** (konwencja
   CLAUDE.md / `feedback_plans_as_md`).
2. `format.py` + testy → `portfolio.py::dashboard_buckets()` + testy.
3. `web.py`: rejestracja filtrów, użycie `dashboard_buckets()` zamiast arytmetyki inline.
4. `dashboard.html` + `app.css`.
5. Aktualizacja pinowanych asercji, pełny `pytest` (baza: 602 testy).
6. README (sekcja opisu pulpitu) + CHANGELOG `## [0.8.0]`, bump `config.yaml` **i**
   `nokia_tracker/__init__.py` (dziś oba 0.7.0).

## Weryfikacja

1. **Testy:** `python -m pytest nokia_tracker/tests -q` — wszystko zielone, licznik ≥ 602 + nowe.
   (Znany flake: 5 × `test_reconcile_vesting_*` przy braku DNS do `api.nbp.pl` — pre-existing,
   nie tego kroku.)
2. **Lokalnie przed deployem:** dev-serwer Flask + `curl /` — sprawdzenie, że kubełki renderują
   się na realnych danych i że w HTML nie ma `None`.
3. **Deploy bezpieczną ścieżką** (add-on trzyma realne dane podatkowe — `reference_supervisor_git_addon_rebuild`):
   push → `gh release create v0.8.0` (published, nie draft) → `homeassistant.update_entity` na
   `update.nokia_tracker_update` → poll → `ha_manage_addon(action="update")`. **Nigdy** cyklu
   uninstall/reinstall.
4. **Playwright po deployu** (recepta z `reference_playwright_ha_token`; ingress działał w ostatniej
   sesji): zrzuty 1920 px i 390 px do `/config/playwright/nokia_tracker_portfel_0.8.0_*.jpg`
   **plus** `browser_console_messages(error)` — zero błędów konsoli jest częścią kryterium, sam
   zrzut nie wystarcza. Fallback, gdyby ingress znów blokował: `ha_manage_addon` proxy GET `/`.
5. **Kontrola liczb na żywo:** suma z hero musi się zgadzać z `sensor.nokia_tracker_*`
   (wartość rynkowa + wartość zablokowanych) i z ostatnim wyciągiem Computershare
   (`/config/akcje_temp/`) — ta sama kontrola, która w kroku 21 potwierdziła 149 983,38 PLN.
6. **Samoocena zrzutów przed pokazaniem** (`feedback_playwright_self_review`) — dopiero wersja
   finalna idzie do akceptacji użytkownika.
