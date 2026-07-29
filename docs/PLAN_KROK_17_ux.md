# Nokia Tracker 0.4.0 — pełna szerokość + skondensowane Sprzedaże i PIT-38

## Context

`nokia_tracker` 0.3.0 pokazuje komplet danych podatkowych, ale **układ marnuje ekran i topi
liczby w prozie**:

1. **Poziome scrollbary przy pustych bokach.** `.main { max-width: 900px }`
   (`static/app.css:59`) zamyka całą treść w 900-pikselowej kolumnie niezależnie od
   rozdzielczości, a `.table th, .table td { white-space: nowrap }` (`app.css:104`)
   zabrania łamania czegokolwiek. Efekt: tabela z 9 kolumnami (`_alloc_detail.html`)
   nie mieści się w 900px → `.table-wrap { overflow-x: auto }` włącza scrollbar, mimo że
   na monitorze jest ~1000px wolnego miejsca po bokach. Dodatkowo `.grid.stats` używa
   `auto-fill` (`app.css:81`) — na szerokim ekranie tworzy puste ścieżki siatki zamiast
   rozciągnąć kafelki.

2. **`/sales` i `/pit38` są rozwlekłe, nie skondensowane.** Każda sprzedaż to osobny
   akordeon `<details>` z dwiema pełnymi tabelami; **nie da się porównać sprzedaży między
   sobą**, bo nigdy nie widać dwóch naraz. W `_alloc_detail.html:32-51` każdy lot dostaje
   dodatkowy wiersz `colspan="9"` z dwiema linijkami prozy, przy czym **linia „cena
   sprzedaży + kurs" jest identyczna dla wszystkich lotów tej samej sprzedaży i mimo to
   powtarza się przy każdym** — przy 5 lotach to 10 wierszy prozy zamiast 5 wierszy danych
   i 1 nagłówka. `/pit38` rozbija na 5 osobnych kart to, co jest jedną odpowiedzią
   („ile wpisać w deklarację"), a kolumna „Podstawa prawna" wypycha tabelę polityk w scroll.

**Cel 0.4.0:** treść wypełnia całą dostępną szerokość, tabele nie scrollują poziomo na
desktopie, a Sprzedaże i PIT-38 dają odpowiedź w pierwszym ekranie — szczegóły na żądanie.

**Decyzje użytkownika** (potwierdzone przed planem):
- szerokość: **całkowicie płynna, 100%** — bez limitu, także formularze;
- `/sales`: **rejestr transakcji** (1 wiersz = 1 sprzedaż) z rozwijanym detalem, wzorzec
  z Sharesight / Koinly / raportów IBKR;
- `/pit38`: **nagłówek „ile wpisać w PIT"** + reszta jako skondensowany detal.

---

## Krok 0 — utrwalić plan w repo

Skopiować ten plik do `docs/PLAN_KROK_17_ux.md` (konwencja repo: `PLAN_KROK_12..16`)
**przed** pierwszą zmianą w kodzie.

---

## Krok 1 — pełna szerokość (`static/app.css`)

Jeden plik, cztery zmiany. Dotyczy WSZYSTKICH stron, nie tylko dwóch przebudowywanych.

| linia | teraz | po zmianie |
|---|---|---|
| `app.css:59` | `.main { max-width: 900px; margin: 0 auto; padding: 14px 14px 60px }` | `max-width: none; margin: 0; padding: 14px clamp(12px, 1.5vw, 28px) 60px` |
| `app.css:101-105` | `.table th, .table td { … white-space: nowrap }` | `white-space: normal` + nowa reguła `.table .num, .table .nowrap { white-space: nowrap }` |
| `app.css:81` | `grid-template-columns: repeat(auto-fill, minmax(140px, 1fr))` | `repeat(auto-fit, minmax(160px, 1fr))` — `auto-fit` zwija puste ścieżki, kafelki realnie się rozciągają |
| — | — | dodać `.table { table-layout: auto }` (jawnie) i zostawić `.table-wrap { overflow-x: auto }` jako zabezpieczenie na telefonie |

Kolumny dat/kwot zachowują `nowrap` przez istniejącą klasę `.num` oraz nową `.nowrap`
(daty w `lots.html`, `dividends.html`, `sales.html`). Kolumny tekstowe (`Źródło`,
`Podstawa prawna`, `Reinwestycja`, `Uznany w`) łamią się i przestają rozpychać tabelę.

Sekcja `@media print` (`app.css:185-190`) zostaje — `body.print-mode .main` już zeruje
`max-width`, po zmianie jest to no-op, ale nie szkodzi.

---

## Krok 2 — `templates/_alloc_detail.html` (współdzielony, przepisany)

To jest główne źródło rozwlekłości i **jedno miejsce naprawia oba widoki** — plik jest
świadomie współdzielony przez `/sales` i kartę „co jeśli sprzedam teraz" na `/pit38`
(patrz komentarz w nagłówku pliku i `tax/trace.py::enrich_allocations`). Zachować tę
współdzieloność.

Nowy układ:

1. **Kurs sprzedaży raz, nad tabelą** (nie przy każdym locie):
   `detail.sale_fx.explanation_pl` + linki `detail.sale_fx.urls.nbp` / `.api` w jednej
   linii `.fx-line`.
2. **Jedna tabela alokacji, bez wierszy `alloc-fx-row`.** Kolumny:
   `Lot` (`#id · data`) | `Typ` | `Ilość` (`qty_taken / lot_quantity`) |
   `Kurs lotu` (`'%.4f'|format(a.lot_fx.rate)` + `<abbr title="{{ a.lot_fx.explanation_pl }}">ⓘ</abbr>`
   + link do tabeli NBP, jeśli `a.lot_fx.urls`) | `Koszt EUR` | `Koszt PLN` |
   `Przychód PLN` | `Dochód PLN` | `Uznany w`.
   Cena nabycia/prowizja (dziś w prozie) trafiają do `title` na komórce `Lot`.
3. **Polityki jako jedna linia** zamiast drugiej tabeli:
   `Tylko własne 1 415 zł ✓ · Własne + dywidenda 1 267 zł (−148) · Wszystkie 980 zł (−435)`
   — aktywna z `badge saved`, delty kolorowane `--good`/`--bad`.
4. **Podsumowanie jednym zdaniem** (zamiast obecnego akapitu na 4 linie):
   `Przychód {{revenue_pln}} PLN · na rękę {{net_pln}} PLN (≈ {{net_eur}} EUR)`.

**Twardy wymóg regresyjny:** `detail.sale_fx.explanation_pl` musi nadal trafiać do HTML —
`tests/test_web.py:307` sprawdza obecność frazy `"dzień roboczy poprzedzający"`.
Wyprowadzenia kursów lotów schowane w `title` też muszą być renderowane serwerowo
(żadnego doładowywania AJAX-em).

Usuwane reguły CSS: `.alloc-fx-row`, `.alloc-fx` (`app.css:163-166`). Dodawane:
`.fx-line`, `.policy-line`, `.abbr-info`.

---

## Krok 3 — `templates/sales.html` → rejestr transakcji

**Backend (`web.py::sales_get`, linie 367-407):** pętla już liczy `detail` per sprzedaż;
dołożyć agregat dla paska KPI — **liczony w Pythonie, nie w Jinja** (styl repo):

```python
active = cfg.get("cost_basis_policy", "own_only")
totals = {"count": len(sales),
          "revenue_pln": sum(i["detail"]["revenue_pln"] for i in sales),
          "cost_pln":    sum(i["detail"]["policies"][active]["cost_pln"] for i in sales),
          "income_pln":  sum(i["detail"]["policies"][active]["income_pln"] for i in sales),
          "tax_pln":     sum(i["detail"]["policies"][active]["tax_pln"] for i in sales),
          "net_pln":     sum(i["detail"]["net_pln"] for i in sales)}
```
(zaokrąglić do 2 miejsc; `active_policy` już siedzi w `detail`). Przekazać `totals` do
`render_template`. Reszta trasy bez zmian.

**Szablon:**
- `page-head` + selektor roku (bez zmian).
- Karta **„Podsumowanie {{ year or 'wszystkie lata' }}"** — `grid stats` z 6 kafelkami
  z `totals`.
- Karta **„Rejestr sprzedaży"** — jedna tabela, per sprzedaż **dwa wiersze**:
  - `<tr class="row-toggle" data-target="sale-{{ item.sale.id }}">`: chevron, `sale_date`,
    ilość, cena EUR, kurs NBP sprzedaży, przychód PLN, koszt PLN, dochód PLN, podatek PLN,
    na rękę PLN (wszystkie `.num`);
  - `<tr class="row-detail" id="sale-{{ item.sale.id }}" hidden><td colspan="10">` →
    `{% include "_alloc_detail.html" %}` + formularz „Cofnij tę sprzedaż".
- Usunąć `.sale-summary` / `<details>` (i odpowiadające im reguły `app.css:168-175`).

**JS (`static/app.js`)** — dołożyć do modułu `window.NT` delegowany handler (bez nowych
zależności; `[hidden] { display: none !important }` już jest w `app.css:157`):
`initRowToggles()` na `document` — klik/Enter/Space na `.row-toggle` przełącza `hidden`
na wierszu z `data-target` i podmienia chevron `▸`/`▾`; `tabindex="0"` + `aria-expanded`
na wierszu-nagłówku. Wywołanie w `{% block scripts %}` na `sales.html`.

**Druk:** dodać w `@media print` regułę `.row-detail[hidden] { display: table-row !important }`,
żeby widok do druku pokazywał komplet.

---

## Krok 4 — `templates/pit38.html` → nagłówek „ile wpisać w PIT"

Bez zmian w `tax/pit38.py` — `annual_report()` już zwraca wszystko, czego potrzeba
(`policies`, `section_g`, `pit_zg`, `sale_trace`). Zmiana wyłącznie prezentacyjna.

1. **`page-head`**: tytuł + selektor roku + przyciski `Druk / CSV / XLSX` przeniesione
   z osobnego `.form-actions` (linie 31-35) do prawej strony nagłówka.
2. **Karta „Do wpisania w deklarację"** (nowa, pierwsza) — `grid stats`:
   Poz. C przychód / koszty / dochód / podatek (z `report.policies[cfg.cost_basis_policy]`),
   Sekcja G dopłata w PL, oraz wyróżniony kafelek **RAZEM DO ZAPŁATY** =
   `podatek poz. C + report.section_g.pl_tax_due_pln`. Suma liczona w Jinja jest tu
   akceptowalna (jedno dodawanie), ale czyściej dołożyć ją w `_pit38_report_for_request`
   jako `report["total_due_pln"]` — **preferowane**, bo eksporty CSV/XLSX mogą jej użyć.
3. **Karta „Polityka kosztu"** — 3 kafelki obok siebie (`grid stats`) zamiast tabeli
   7-kolumnowej: nazwa, podatek PLN (duże), delta vs aktywna, `badge saved` na aktywnej;
   przychód/koszt/dochód i `legal_basis_pl` w `<details>` „Podstawa prawna i rozbicie"
   pod kafelkami. **Zachować dosłowne etykiety** „Tylko własne", „Własne + dywidenda",
   „Wszystkie w wartości nabycia" (`tests/test_web.py:865-867`).
4. **Karta „Sekcja G — dywidendy zagraniczne (PIT/ZG)"** — scalić z obecną osobną kartą
   PIT/ZG (i tak jest jej pochodną, `tax/pit38.py` docstring). 4 kafelki (brutto,
   u źródła, dopłata w PL, do odzyskania) + linia `muted`:
   `Zaliczenie traktatowe X · Belka Y · N dywidend` + linia
   `PIT/ZG: {{ country }} · dochód zagraniczny X PLN · podatek zapłacony za granicą Y PLN`.
   **Ciąg „PIT/ZG" musi zostać** (`tests/test_web.py:870`).
5. **Karta „Co jeśli sprzedam teraz"** — formularz w jednej linii (`.inline-form`:
   ilość, cena, przycisk). Wynik jako pasek KPI (podatek PLN, na rękę PLN, efektywna
   stopa %) + `<details>` **domyślnie zwinięte** (dziś `open`, linia 137)
   z `_alloc_detail.html`.
6. **Karta „Ślad obliczeń — per lot"** — tabela pełnej szerokości, wiersze pogrupowane
   po `sale_date` (delikatny separator przy zmianie daty), kolumny kursów jako
   `4.2011 (2024-06-01)` bez `nowrap` na całej tabeli.

---

## Krok 5 — testy

`tests/test_web.py` (58 testów). Istniejące asercje przechodzą, o ile Krok 2/4 utrzymają
frazy wskazane wyżej. Dołożyć:

- `test_sales_page_shows_year_totals` — `/sales` zawiera pasek KPI z sumą podatku
  z dwóch sprzedaży (nie tylko per-sprzedaż);
- `test_sales_row_detail_rendered_server_side` — HTML zawiera `class="row-detail"`,
  `hidden` **oraz** treść rozbicia (dowód, że detal nie jest doładowywany);
- `test_pit38_shows_total_due` — `RAZEM DO ZAPŁATY` i suma `podatek poz. C + dopłata G`;
- `test_alloc_detail_renders_sale_fx_once` — `detail.sale_fx.explanation_pl` występuje
  w HTML **dokładnie raz** na sprzedaż z ≥2 lotami (regresja na powtarzaną prozę).

Uruchomienie: `cd /config/addons/nokia_tracker/nokia_tracker && python -m pytest -q`.

---

## Krok 6 — weryfikacja UI (Playwright, przed pokazaniem)

Zgodnie z konwencją repo (self-review przed prezentacją):
1. `persist-install chromium` jeśli nie ma binarki (`/usr/bin/chromium-browser`),
   `--disable-gpu`.
2. Zrzuty do `/config/playwright/` (`filename: "playwright/<nazwa>.jpg"`) dla
   `/sales` i `/pit38` w **1920×1080** i **390×844**.
3. Dowód na główny zarzut: w 1920px `document.querySelectorAll('.table-wrap')`
   ma mieć `scrollWidth <= clientWidth` (brak scrolla) — sprawdzić przez
   `browser_evaluate`, nie „na oko".
4. `browser_console_messages(error)` po każdym zrzucie.
5. Sprawdzić rozwijanie wiersza sprzedaży (klik + zrzut stanu rozwiniętego).

---

## Krok 7 — wydanie 0.4.0

1. Bump **obu** plików: `nokia_tracker/config.yaml` (`version: "0.4.0"`) i
   `nokia_tracker/nokia_tracker/__init__.py` (`__version__ = "0.4.0"`).
2. `CHANGELOG.md` — sekcja `[0.4.0]`; `README.md` — opis nowego układu Sprzedaży/PIT-38.
3. Commit + push na `main` do `github.com/miczu71/nokia_tracker`.
4. **Opublikowany** (nie draft) release `0.4.0` przez `gh release create` z rozpisanymi
   zmianami; zweryfikować, że `config.yaml` na `main` == tag.
5. Odświeżenie sklepu: `homeassistant.update_entity` na `update.nokia_tracker_update`,
   potem `ha_manage_addon` → `update` na slugu z hash-prefiksem; weryfikacja przez `/info`.

### ⚠️ Czego NIE robić przy deployu

**Nie używać cyklu `uninstall → remove_repository → add_repository → install`.**
Ten add-on trzyma realne dane z importu PDF w SQLite, a ten cykl je kasuje —
zdarzyło się to już 2026-07-28. Deploy wyłącznie przez opublikowany release + update.

Cache frontu jest już obsłużony (`no-store` na HTML w `web.py:117-120`, statyki z
`?v={{ version }}`, badge wersji w nawigacji) — bump wersji sam w sobie unieważnia CSS/JS.

---

## Pliki

| Plik | Zakres |
|---|---|
| `docs/PLAN_KROK_17_ux.md` | nowy — kopia tego planu (krok 0) |
| `nokia_tracker/nokia_tracker/static/app.css` | pełna szerokość, `nowrap`, `auto-fit`, nowe klasy, druk |
| `nokia_tracker/nokia_tracker/static/app.js` | `initRowToggles()` |
| `nokia_tracker/nokia_tracker/templates/_alloc_detail.html` | przepisany (rdzeń kondensacji) |
| `nokia_tracker/nokia_tracker/templates/sales.html` | rejestr transakcji + KPI |
| `nokia_tracker/nokia_tracker/templates/pit38.html` | nagłówek deklaracji + kondensacja 5 kart → 4 |
| `nokia_tracker/nokia_tracker/web.py` | `totals` w `sales_get`, `total_due_pln` w raporcie |
| `nokia_tracker/tests/test_web.py` | 4 nowe testy |
| `nokia_tracker/config.yaml`, `.../__init__.py`, `CHANGELOG.md`, `README.md` | wydanie 0.4.0 |

**Bez zmian:** `tax/*.py` (logika podatkowa nietknięta — zmiana czysto prezentacyjna),
eksporty CSV/XLSX poza ewentualnym `total_due_pln`, schemat bazy.

---

## Uwaga poboczna (nie część zadania)

`git remote -v` w `/config/addons/nokia_tracker` ma **wklejony token GitHub w URL-u**
(`https://x-access-token:ghp_…@github.com/…`). Leży jawnym tekstem w `.git/config` i
wycieka do outputu każdej komendy gita. Warto go unieważnić i przestawić remote na czysty
URL + `GITHUB_PERSONAL_ACCESS_TOKEN` z env / `gh auth`. Mogę to zrobić w osobnym kroku.
