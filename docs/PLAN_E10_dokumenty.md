# nokia_tracker — dokumenty dowodowe (HTML + PDF) dla symulacji i sprzedaży

Repo: `/config/addons/nokia_tracker` · dodatek `nokia_tracker` 0.28.0 → **0.29.0**

## Context

Dzisiaj dodatek liczy wszystko, co trzeba do obrony PIT‑38 — dopasowanie FIFO per lot,
kurs NBP D‑1 z numerem tabeli i polską prozą wyprowadzenia (`tax/trace.py::fx_derivation`),
trzy polityki kosztu z podstawą prawną, ślad prowenniencji aż do pliku wyciągu PDF
(`breakdown.py::provenance`) — ale **nie da się tego wynieść z aplikacji**. Eksport to dziś
`pit38_<rok>.csv` / `.xlsx` (surowe wiersze, bez narracji) oraz „widok do druku" (`?print=1`),
który w dodatku ma `.trace-body { no-print }`, więc drukuje same kwoty **bez** śladu.

Cel: dwa poziomy dokumentów — **roczne dossier PIT‑38** i **pojedyncza pozycja** (jedna
zrealizowana sprzedaż albo jedna symulacja) — w HTML i PDF, samodzielne (bez nawigacji,
bez `url_for`, z wbudowanym CSS), po polsku, z pełnym śladem audytowym łącznie ze źródłem
danych. Odbiorca: urząd skarbowy **oraz** własne archiwum.

Decyzje podjęte w wywiadzie (nie renegocjujemy):
1. Odbiorca: dowód do PL US + archiwum osobiste.
2. Dwa poziomy dokumentu, wspólny kod renderujący.
3. Rok = **pełne dossier PIT‑38** (sprzedaże + ślad per lot + Sekcja G + PIT/ZG + strata + polityki).
4. Głębokość: pełny ślad audytowy **łącznie z prowenniencją źródła**.
5. Silnik PDF: **WeasyPrint po stronie serwera**, jeden szablon Jinja → HTML i PDF (zero rozjazdu).
6. Dochodzi `breakdown.sale_traces()` — symetryczne do istniejącego `withdrawal_traces()`.

---

## Weryfikacja wykonalności (zrobiona, nie założona)

- Wszystkie koła musl istnieją dla `weasyprint==69.0` i jego zależności
  (`pydyf, cffi, tinyhtml5, tinycss2, cssselect2, Pyphen, Pillow, fonttools[woff]`)
  na `musllinux_1_2_x86_64` **i** `musllinux_1_2_aarch64` — sprawdzone `pip download --only-binary`.
- **armv7**: koła istnieją, ale w starszych wersjach (`Pillow 11.1.0`, `cffi 1.17.1`).
  Nie da się przypiąć jednego zestawu wersji dla wszystkich trzech architektur.
- Alpine ma `pango 1.54.0` (WeasyPrint wymaga ≥1.44) i `fontconfig 2.15.0` — oba już
  zainstalowane w tym kontenerze; `font-dejavu 2.37-r6` jest w repo `main`.
  DejaVu Sans pokrywa komplet polskich znaków diakrytycznych.
- Dodatek **nie ma klucza `image:`** w `config.yaml` → Supervisor buduje obraz lokalnie
  na maszynie użytkownika (x86_64). Aktualizacja potrwa o kilka minut dłużej.

**Rekomendacja co do armv7:** usunąć `armv7` z listy `arch:` w `nokia_tracker/config.yaml`
i zostawić dokładne piny wszystkich wersji (zgodnie z etosem repo). Dodatek nigdy nie był
budowany na armv7, a utrzymywanie luźnych pinów tylko dla tej architektury psuje
powtarzalność buildu. Alternatywa (gdyby armv7 miał zostać): przypiąć wyłącznie
`weasyprint==69.0`, a zależności przechodnie zostawić bez pinów.

---

## Architektura

Trzymamy istniejący podział warstw: `views/` składa dane domenowe, `exports/` serializuje
już policzone dane, `web/routes_*.py` obsługuje HTTP. **Bez Blueprintów** — nawigacja w
`base.html` rozwiązuje gołe nazwy endpointów przez `url_for`.

### Nowe pliki

| Plik | Rola |
|---|---|
| `nokia_tracker/views/documents.py` | Składa kontekst dokumentu z istniejących silników. `sale_document_view(conn, cfg, sale_id)`, `simulation_document_view(conn, cfg, **params)`, `year_document_view(conn, cfg, year)`. Zero nowej matematyki finansowej. |
| `nokia_tracker/exports/pdf.py` | `available() -> bool`, `render_pdf(html: str) -> bytes`. **Leniwy import** `weasyprint` wewnątrz funkcji. |
| `nokia_tracker/exports/documents.py` | Samodzielny `jinja2.Environment` (własny `autoescape`, własne filtry `money/qty/pct`), `render_html(kind, doc) -> str`, `payload_digest(doc) -> str` (SHA‑256 kanonicznego JSON‑a danych dokumentu, bez `meta`). |
| `nokia_tracker/web/_documents.py` | `document_response(kind, doc, *, fmt, filename_stem, download) -> Response` — wspólne dostarczanie (mimetype, `Content-Disposition`, jawny `no-store`), wołane z `routes_portfel.py`/`routes_plan.py`/`routes_podatki.py`. Bez własnych tras — wzorzec `web/_helpers.py`. |
| `nokia_tracker/templates/doc_base.html` | Samodzielny szkielet — **nie dziedziczy z `base.html`**. Brak nawigacji, brak `url_for`, `<style>{{ doc_css }}</style>` inline. |
| `nokia_tracker/templates/doc_sale.html`, `doc_simulation.html`, `doc_dossier.html`, `_doc_macros.html` | Treść trzech typów dokumentu + współdzielone makra drukowe (tabele śladu/FIFO/prowenniencji). |
| `nokia_tracker/static/doc.css` | Nadpisania druku: `@page { size: A4; margin: 18mm 14mm; @bottom-right { content: counter(page) "/" counter(pages) } }`, `break-inside: avoid` na blokach śladu, `.trace-body` **widoczne** (odwrotnie niż w UI). |

### Dlaczego trasy zostają w modułach domenowych, nie w nowym `routes_dokumenty.py`

Reguła repo to „dziel po domenie nawigacyjnej, nie po tabeli" (dosłownie z docstringa
`routes_podatki.py`). Dokument sprzedaży należy do `/sales` → `routes_portfel.py`, dokument
symulacji do `/wyplata` → `routes_plan.py`, dossier roczny do `/pit38` → `routes_podatki.py`.
Nowy `routes_dokumenty.py` byłby dokładnie tym błędem, który reguła zakazuje — dzieleniem po
rodzaju artefaktu, nie po domenie. Powtarzalny kod dostarczania (mimetype, nagłówki, wybór
HTML/PDF) trafia do `web/_documents.py` — analogicznie do `web/_helpers.py`, który już dziś
istnieje jako „pomocnicy współdzieleni przez kilka modułów tras" bez własnych tras. Nie
dodajemy nowej grupy nawigacji — wejścia to przyciski na istniejących stronach.

### Dlaczego `doc_base.html`, a nie `base.html` + `print_mode`

`_IngressPrefixMiddleware` wstrzykuje prefiks ingressu do każdego `url_for()`. Plik HTML,
który opuszcza przeglądarkę, miałby w środku martwe linki do sesji ingressu i zewnętrzny
`<link rel=stylesheet>`, którego nie da się rozwiązać. Dokument musi być samowystarczalny:
CSS inline, żadnych linków do tras aplikacji (linki do `nbp.pl`/API NBP zostają — to
dowody zewnętrzne i mają wartość).

---

## Trasy

| Trasa | Moduł | Nazwa pliku (ASCII, `Content-Disposition`) |
|---|---|---|
| `GET /sales/<int:sale_id>/dokument.html` / `.pdf` | `routes_portfel.py` | `sprzedaz_<sale_date>_id<id>.{html,pdf}` |
| `GET /wyplata/dokument.html` / `.pdf` | `routes_plan.py` | `symulacja_<sale_date>_<hash8>.{html,pdf}` |
| `GET /pit38/dokumentacja.html` / `.pdf?year=` | `routes_podatki.py` | `pit38_dokumentacja_<year>.{html,pdf}` |

Dokument symulacji przyjmuje **te same parametry zapytania co `/wyplata`**
(`direction`, `wyplata_target`, `wyplata_qty`, `wyplata_price`, `wyplata_fee_pct`,
`wyplata_date` — `routes_plan.py:199-256`), wydzielone do wspólnego `_wyplata_params(request)`
(dziś zduplikowane między `wyplata_get` i `preview_wyplata` — to je scala, nie dokłada
warstwy) i **wypisuje je w bloku „Parametry symulacji"** razem z pełnym query stringiem jako
tekstem (nie linkiem — `url_for` jest bezużyteczny poza sesją ingressu i zabroniony przez
sam Environment). Symulacja jest bezstanowa; ten blok jest jedynym nośnikiem odtwarzalności.
`?pobierz=1` na trasach `.html` przełącza `Content-Disposition` z `inline` na `attachment`
(domyślnie `inline` — `attachment`+`text/html` w Android WebView aplikacji HA Companion
potrafi nic nie zrobić po kliknięciu). `.pdf` zawsze `attachment`.

Dostarczanie — istniejący idiom (3 miejsca wywołania, `routes_podatki.py:84`), opakowany w
`web/_documents.py::document_response`: `Response(body, mimetype=..., headers={
"Content-Disposition": ..., "Cache-Control": "no-store"})`. Bez `send_file`. PDF:
`application/pdf`. HTML: `text/html; charset=utf-8`. `Cache-Control` ustawiany **jawnie**,
bo istniejący `_no_cache` nie obejmuje `application/pdf`.

---

## `breakdown.sale_traces()`

Sygnatura symetryczna do `withdrawal_traces` (`breakdown.py:348`):

```python
def sale_traces(conn, ctx: BreakdownCtx, cfg: dict, sale: dict, detail: dict
                ) -> dict[str, Breakdown]
```

Budowniczy dostają **wyłącznie liczby policzone przez `views/sales.py` /
`tax/trace.py::enrich_allocations`** — jedyna algebra to przeliczenie w `close_formula`.
Ta sama pętla `try/except BreakdownNotClosedError: continue`, co w `withdrawal_traces`.

| Klucz | Domknięcie | Formuła |
|---|---|---|
| `sprzedaz.quantity` | `close_formula` | `Σ ilości pobranych z lotów` (krzyżowy test z `sales.quantity`) |
| `sprzedaz.revenue_pln` | `close_formula` | `(ilość × cena − prowizja) × kurs NBP D‑1`; składnik informacyjny z `detail.sale_fx.explanation_pl` + `Source("nbp", tabela)` |
| `sprzedaz.cost_fifo` | `close_sum` | `Σ koszt PLN per lot`; **każdy składnik z `sources=provenance(ctx, lot)`** i `detail` = `lot_fx.explanation_pl` |
| `sprzedaz.income` | `close_formula` | `przychód − koszt (polityka aktywna)` |
| `sprzedaz.tax` | `close_formula` | `dochód × 19%` |
| `sprzedaz.net` | `close_formula` | `przychód − podatek` |
| `sprzedaz.reported_override` | `close_formula` | tylko gdy `detail.is_reported_override`: `zgłoszone vs wyliczone przez silnik` |

Efekt uboczny o realnej wartości: te same ślady można podać do istniejącego makra
`_macros.html::traced(bd)` na `/sales`, więc rejestr sprzedaży w UI dostaje rozwijany
ślad, którego dziś nie ma — bez nowego kodu widoku.

---

## Nagłówek i stopka każdego dokumentu

Blok tożsamości (na pierwszej stronie, powtarzany w nagłówku PDF):
- tytuł + zakres (np. „Zrealizowana sprzedaż #12 z 2025‑10‑27")
- `Wygenerowano: <ISO 8601, strefa lokalna>`
- `Nokia Tracker <__version__>` · `schema bazy: v<PRAGMA user_version>` (jak
  `backup.py::_build_manifest`, linia 86)
- `Aktywna polityka kosztu: <POLICY_LABELS> — <LEGAL_BASIS_PL>` (`tax/policy.py:17,23`)
- `Stawka podatku: 19%` z `cfg`
- zastrzeżenie z istniejącego makra `_macros.html::tax_disclaimer` (wariant `sales`
  dla sprzedaży/roku; dla symulacji **mocniejsze**: to projekcja, nie zdarzenie zgłoszone)

Stopka: `Suma kontrolna danych: sha256:<12 znaków>` — skrót liczony z **kanonicznego
JSON‑a danych dokumentu** (nie z wyrenderowanego HTML, bo ten zawiera znacznik czasu i
sam skrót). Uczciwe i wykonalne: dwa dokumenty z tym samym skrótem stoją na tych samych
liczbach. Numeracja stron przez `@page` w `doc.css`.

---

## Poprawki po drugiej weryfikacji (przyjęte przed startem)

Druga weryfikacja (osobny przebieg agenta projektowego) doczytała kod dokładniej i złapała
realne błędy w powyższym projekcie oraz zaproponowała lepszy podział na etapy. Przyjęte:

- **`Cache-Control` dla PDF.** `_no_cache` w `web/__init__.py` reaguje tylko na `text/html`/
  `application/json` — `application/pdf` nic nie dostaje. `web/_documents.py::document_response`
  musi ustawiać `no-store` jawnie.
- **Autoescape w samodzielnym Jinja Environment.** Goły `jinja2.Environment` ma domyślnie
  `autoescape=False` — inaczej niż w Flasku. `exports/documents.py` musi jawnie użyć
  `select_autoescape(["html"])`, inaczej notatka z `<` w treści popsuje/wstrzyknie się w dokument.
- **Łapanie awarii WeasyPrint.** Brak `libpango` objawia się jako `OSError` z `dlopen` w cffi
  przy imporcie `weasyprint.text.ffi`, nie `ImportError`. `exports/pdf.py::available()` łapie
  szeroko (`except Exception`), z komentarzem czemu to jedno uzasadnione miejsce na taki połów.
- **Błąd w `?print=1` na `/wyplata`.** `templates/withdrawal.html` buduje link do druku bez
  parametrów symulacji (cena/opłata/data/cel/ilość) — pusty formularz zamiast wyniku. Dokument
  symulacji musi to zrobić poprawnie od razu (patrz „Trasy" — pełny query string, nie link).
- **Podział tras: bez `routes_dokumenty.py`.** Zgodnie z regułą repo („dziel po domenie
  nawigacyjnej, nie po tabeli") trasy dokumentów wracają do istniejących modułów:
  sprzedaż → `routes_portfel.py`, symulacja → `routes_plan.py`, dossier roczny →
  `routes_podatki.py`. Wspólny kod dostarczania (mimetype, `Content-Disposition`, `no-store`,
  wybór HTML/PDF) trafia do nowego `web/_documents.py::document_response(...)` — ten sam
  wzorzec co istniejący `web/_helpers.py` (pomocnicy bez własnych tras).
- **armv7 — decyzja użytkownika stoi.** Druga weryfikacja proponowała alternatywę (zostawić
  armv7 w `arch:`, degradować sam krok `pip install` przez `--only-binary=:all: || echo`).
  Nie przyjęte — użytkownik już wybrał usunięcie armv7 z `arch:`, to zostaje bez zmian.
- **Ślad sprzedaży: kontrakt na błędy domknięcia jawny, nie połknięty.** `sale_traces()` zwraca
  `tuple[dict[str, Breakdown], list[BreakdownNotClosedError]]` (nie ciche `continue` jak w
  `withdrawal_traces`) — sprzedaż jest w pełni odtwarzalna z bazy, więc rozjazd domknięcia to
  sygnał do pokazania w dokumencie („Zastrzeżenia"), nie do ukrycia. Otwiera to też rozszerzenie
  `integrity.py::_breakdown_not_closed` (dziś tylko `/`) o sprzedaże.
- **Semafor dla renderowania PDF.** WeasyPrint trzyma całe drzewo layoutu w pamięci; waitress ma
  4 wątki. `exports/pdf.py` dostaje `threading.Semaphore(1)` — jeden render naraz, eksport
  dokumentu nie jest ścieżką wrażliwą na opóźnienie.

## Etapy

Zgodnie z `CLAUDE.md` (wywiad → etapy → punkty kontrolne): **jeden etap naraz, checkpoint
po każdym, bez startu następnego bez zgody**. Kolejność celowo odsuwa ryzyko WeasyPrint/Alpine
na sam koniec — wszystko wcześniejsze da się w pełni zweryfikować bez dotykania Dockerfile.

### Etap 0 — `breakdown.sale_traces()` (fundament, nic widocznego dla użytkownika)
- `breakdown.py`: nowa sekcja `# /sales`, 8 śladów (`sprzedaz.quantity/gross_eur/revenue_eur/
  revenue_pln/cost/income/tax/net`), kontrakt `tuple[dict[str, Breakdown], list[BreakdownNotClosedError]]`
  (jawny, nie połknięty — patrz „Poprawki" wyżej)
- obsługa przypadku `is_reported_override`: `close_formula` z jawnym składnikiem „korekta do
  wartości zgłoszonej", nie ciche `close_sum` które by tu zawsze wybuchało
- `views/sales.py`: wydzielenie `sale_detail(conn, cfg, sale_row, ctx=None)` z pętli w
  `sales_view`; nowy parametr `sales_view(conn, cfg, year, *, with_traces=False)` — domyślnie
  `False`, żeby `/sales` nie płaciło kosztu `build_ctx` (pełny `SELECT * FROM lots` +
  skan `statement_snapshots`) tam, gdzie dziś tego nie robi
- **Testy:** `tests/test_breakdown_sales.py` — wszystkie 8 kluczy na danych z `seed`;
  `sprzedaz.quantity` domyka się krzyżowo z `sales.quantity`; `sprzedaz.revenue_eur` pokazuje
  prowizję jako osobną linię; ścieżka `is_reported_override` nie podnosi wyjątku i niesie
  składnik korekty; źródła niosą i prowenniencję lotu, i numer tabeli NBP; `sales_view` bez
  zmiany wyniku (istniejące testy).
- **Do przeglądu:** 8 sformułowań i etykiet PL — to one trafią przed urzędnika, akceptacja
  treści przed zbudowaniem czegokolwiek na nich.

### Etap 1 — ślady na `/sales` w UI (wartość sama w sobie, zero nowych zależności)
- `routes_portfel.py::sales_get` woła `with_traces=True`; `sales.html`/`_alloc_detail.html`
  owijają 6 kwot istniejącym makrem `m.traced(...)`
- **Testy:** `<details class="trace">` per sprzedaż per klucz.
- **Do przeglądu:** czy ekran z 8 rozwijalnymi śladami nadal czyta się dobrze.

### Etap 2 — `integrity.py` obejmuje sprzedaże
- rozszerzenie `_breakdown_not_closed` (dziś tylko `/`) o replay `sale_traces` dla bieżącego
  roku podatkowego
- **Testy:** spreparowany rozjazd ujawnia się na karcie „Spójność danych".
- **Do przeglądu:** krótki, mechaniczny — czy karta poprawnie łapie zepsute dane.

### Etap 3 — dokumenty HTML, bez nowej zależności
- `views/documents.py`: `sale_document(conn, cfg, sale_id)`, `simulation_document(conn, cfg, params)`,
  `year_dossier(conn, cfg, year)` — czysta kompozycja istniejących silników, zero nowej matematyki
- `exports/documents.py`: samodzielny `jinja2.Environment(autoescape=select_autoescape(["html"]))`,
  filtry `money/qty/pct` zarejestrowane tak samo jak w `web/__init__.py`
- `web/_documents.py::document_response(kind, doc, *, fmt, filename_stem, download)` — wspólne
  dostarczanie (na wzór `web/_helpers.py`), jawny `Cache-Control: no-store`
- `templates/doc_base.html` (samodzielny, **nie** dziedziczy z `base.html`, brak `url_for`,
  `<style>` inline) + `doc_sale.html`, `doc_simulation.html`, `doc_dossier.html` + `_doc_macros.html`
- `static/doc.css` (~150 linii, print-first, `@page` z numeracją stron i stopką wersja+hash)
- Trasy `.html` w istniejących modułach domenowych: `GET /sales/<id>/dokument.html`
  (`routes_portfel.py`), `GET /wyplata/dokument.html` (`routes_plan.py`, z wydzielonym
  `_wyplata_params()` — naprawia przy okazji błąd brakujących parametrów w `?print=1`),
  `GET /pit38/dokumentacja.html?year=` (`routes_podatki.py`)
- blok tożsamości (wersja, schema DB, aktywna polityka, suma kontrolna SHA‑256 z modelu danych,
  `tax_disclaimer` z `_macros.html` — to samo sformułowanie co na ekranie)
- przyciski na `sales.html`, `withdrawal.html`, `pit38.html`
- **Testy:** render bez Flaska (sam moduł `exports/documents.py`); test-strażnik: brak
  `url_for(`, brak `/static/`, brak `<script`, zaczyna się od `<!DOCTYPE html>`, ma inline
  `<style>`, zawiera polskie znaki diakrytyczne (test na literalnym „ąćęłńóśźż” w treści);
  stabilność i czułość sumy kontrolnej (ta sama dla HTML i przyszłego PDF tej samej sprzedaży,
  inna po zmianie danych); test na notatce z `<script>` w treści — musi wyjść zescapowana.
- **Do przeglądu:** wszystkie trzy dokumenty otwarte w przeglądarce desktop **i** w aplikacji
  HA Companion, jeden wydrukowany przez przeglądarkę do PDF — to jest właściwy przegląd treści,
  zanim dojdzie ryzyko budowy na Alpine.

### Etap 4 — WeasyPrint + PDF
- `Dockerfile`: `so:libgobject-2.0.so.0 so:libpango-1.0.so.0 so:libharfbuzz.so.0
  so:libharfbuzz-subset.so.0 so:libfontconfig.so.1 so:libpangoft2-1.0.so.0` + `font-dejavu`
  (pokrycie polskich znaków potwierdzone; ewentualna zamiana na `font-liberation` dla mniejszego
  rozmiaru — decyzja kosmetyczna, nie blokująca)
- `config.yaml`: usunięcie `armv7` z `arch:`
- nowy `requirements-pdf.txt`: `weasyprint==69.0`
- `exports/pdf.py`: leniwy import **wewnątrz funkcji**, `except Exception` (nie tylko
  `ImportError` — realna awaria to `OSError` z `dlopen` w cffi), `available()` cache'owane,
  `threading.Semaphore(1)` wokół renderu
- trasy `.pdf` w tych samych trzech modułach; brak silnika → 503 z wyrenderowaną polską
  stroną odsyłającą do `.html`, nie goły błąd
- przyciski PDF obok już istniejących HTML
- **Testy:** `pytest.importorskip("weasyprint")` dla testów renderu (żeby suita przechodziła
  bez biblioteki), **plus zawsze uruchamiany test** monkeypatchujący import na porażkę i
  sprawdzający `available() is False` + odpowiedź 503 — ścieżka degradacji dowiedziona bez
  obecności biblioteki; bajty magiczne `b"%PDF"`, `mimetype == "application/pdf"`, nazwa w
  `Content-Disposition`, `Cache-Control: no-store`; test diakrytyków przez `pypdf.extract_text()`
  na wyrenderowanym PDF (biblioteka już jest zależnością, do odczytu).
- **Do przeglądu:** build na realnym sprzęcie HA, zmierzony czas builda i przyrost rozmiaru
  obrazu, PDF otwarty na telefonie.

### Etap 5 — dopięcie i wydanie
- zastrzeżenia w dokumencie, gdy `sale_traces` zwróci błędy domknięcia („Ślad dla pozycji…
  nie domknął się do grosza")
- footnote o nieznanym pliku wyciągu (jedna zbiorcza notka zamiast trzydziestu ostrzeżeń
  w kolumnie Źródło, gdy `statement_index` pusty)
- strażnik rozmiaru dla dużego roku (próg liczby alokacji, powyżej którego załącznik pokazuje
  tylko aktywną politykę — z jawną adnotacją, nie cichym obcięciem); zmierzony na realnym roku
- bump `nokia_tracker/config.yaml` **i** `nokia_tracker/nokia_tracker/__init__.py` → `0.29.0`
- CHANGELOG + sekcje README (Features/Entities), opublikowany (nie draft) release na GitHubie
- update przez Supervisor, potem weryfikacja Playwright na produkcji: zrzuty do
  `/config/playwright/`, `browser_console_messages(error)` po każdym zrzucie, realny PDF
  pobrany i otwarty
- **Do przeglądu:** zrzuty + otwarty PDF z produkcji, realny rok podatkowy przeczytany od
  początku do końca tak, jakby czytał go urzędnik.

---

## Ryzyka i jak je tniemy

| Ryzyko | Odpowiedź |
|---|---|
| Build Alpine pada na WeasyPrint → dodatek nie wstaje | `weasyprint` importowany **leniwie w `render_pdf()`**, nigdy na poziomie modułu. Brak biblioteki = trasy `.pdf` zwracają 503 z polskim komunikatem, cała reszta dodatku (i eksport `.html`) działa. Degradacja, nie cegła. |
| `BreakdownNotClosedError` w dokumencie podatkowym | Istniejący wzorzec `try/except … continue` pomija ślad, ale w dokumencie **drukujemy widoczną adnotację** „⚠ ślad niedostępny dla tej pozycji". Milczące pominięcie w dowodzie dla US byłoby wprowadzeniem w błąd. |
| `statement_index` pusty dla importów sprzed 0.23.0 | `provenance()` już zwraca jawne „Plik wyciągu nieznany (import sprzed 0.23.0…)" — dokument to pokazuje, nie ukrywa. |
| armv7 nie da się przypiąć razem z x86_64/aarch64 | Usunięcie `armv7` z `arch:`; alternatywa opisana wyżej. |
| Pamięć/czas WeasyPrint na dużym roku | Dzisiejsze dane to dziesiątki wierszy. Punkt obserwacyjny, nie problem; gdyby urósł — stronicowanie załącznika. |
| Cache WebView w aplikacji HA | Dokumenty to załączniki z `Content-Disposition`; `_no_cache` już daje `no-store` dla `text/html`. `doc.css` jest **wklejony w dokument**, więc nie podlega cache'owi statyk. Wersja dodatku w nagłówku dokumentu pełni rolę odznaki wersji. |
| Dłuższa aktualizacja dodatku (build lokalny) | Świadomy koszt: `apk add pango font-dejavu` + ~20 MB kół, obraz rośnie o ~60–80 MB. Do zakomunikowania przy wydaniu. |

---

## Weryfikacja końcowa

1. `cd /config/addons/nokia_tracker/nokia_tracker && python3 -m pytest` — wszystkie
   dotychczasowe testy (1425) plus nowe przechodzą.
2. Test bajtów magicznych `%PDF` na każdej trasie `.pdf` (dom styl repo: `b"PK"` dla
   zip/xlsx w `test_web_data.py:27` i `test_web_pit38.py:119`).
3. Test samowystarczalności HTML: brak `<link rel="stylesheet">`, brak `url_for`‑owych
   ścieżek `/api/` czy `/static/` w wyeksportowanym pliku.
4. Krzyżowa zgodność liczb: sumy w dossier rocznym == `to_csv`/`to_xlsx` dla tego samego roku.
5. Na produkcji po wydaniu: Playwright — `/sales`, `/wyplata`, `/pit38`, pobranie PDF‑a,
   zrzuty do `/config/playwright/`, sprawdzenie konsoli po każdym zrzucie.
