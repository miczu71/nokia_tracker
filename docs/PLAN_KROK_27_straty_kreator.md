# Krok 27 — Straty z lat ubiegłych + kreator rozliczenia (`nokia_tracker` 0.11.0)

## Context

`nokia_tracker` jest na **0.10.0** (wydane 2026-08-15, live na Supervisorze, `update_available:false`,
767 testów, slug `5f59858c_nokia_tracker`). Fale 0.8.1–0.10.0 zamknięte. Następna pozycja roadmapy
(`docs/ROADMAP.md:146-171`) to **0.11.0 / krok 27 — Podatki: straty z lat ubiegłych + kreator
rozliczenia**.

Problem: silnik podatkowy (`tax/policy.py::compute_all_policies`) już dziś liczy `income_pln`
per rok per polityka, ale gdy wychodzi ujemny, po prostu **znika** — `tax_pln = max(0, income*rate)`
zeruje podatek i strata nie jest nigdzie zapisana ani widoczna. Art. 9 ust. 3 ustawy o PIT pozwala
odliczyć taką stratę od dochodu w kolejnych latach — dziś to prawo jest martwe, bo dodatek nie wie,
że strata w ogóle wystąpiła.

Druga część fali to `/pit38/kreator` — dziś rozliczenie roczne to „otwórz `/pit38`, przeczytaj
liczby, przepisz do e-Deklaracji" bez żadnego śladu „co już sprawdziłem". Roadmapa chce checklisty,
która **sama wie**, czy dany krok jest spełniony (odpytuje bazę), nie polega na ptaszku klikniętym
ręcznie.

**Decyzja podjęta przed planowaniem (nie relitygować):** kolejność 0.11.0 vs 0.10.0 była zamienna
(`docs/ROADMAP.md:255-256`) — 0.10.0 poszło pierwsze, teraz 0.11.0. Zakres pozostaje wyłącznie Nokia,
bez rozrostu wielo-instrumentowego (jak w każdej fali).

## 0. Warunek wstępny — których lat to w ogóle dotyczy

`compute_all_policies` już istnieje i już liczy `income_pln` ujemny poprawnie (patrz
`test_tax_policy.py::test_compute_all_policies_loss_gives_zero_tax_not_negative` — test istnieje,
ale sprawdza tylko że `tax_pln==0`, nie że strata jest gdziekolwiek zapisana). Z tej powłoki
(`/config`) **nie ma dostępu do produkcyjnej `/data/nokia_tracker.db`** dodatku (dane add-onu
są poza `/config`, poza zasięgiem tokena Supervisora tej sesji — patrz `CLAUDE.md` „Environment
notes"). Sensor `sensor.nokia_tracker_pit38_income_pln` za 2026 pokazuje `0.0` (brak sprzedaży w
tym roku, cfg `tax_year=0` więc sensor liczy rok bieżący).

**Pierwszy krok implementacji, przed napisaniem `tax/losses.py`:** przez `/pit38?year=YYYY` (kolejno
dla każdego roku z `_years_with_data()`, widocznego w selektorze roku na żywym `/pit38`) odczytać
`report.policies[*].income_pln` i zanotować, które lata/polityki są faktycznie stratne. To ten sam
wzorzec co „Warunek wstępny" w 0.9.0 (gęsta seria NBP) — dane wejściowe sprawdzone empirycznie, nie
założone. Jeśli okaże się, że **żaden rok nie jest stratny** (Nokia rosła cały czas posiadania —
zgodne z „absurdalnie wysoki XIRR" z sekcji 0.9.0 roadmapy), silnik i tak trzeba zbudować poprawnie
(dane produkcyjne mogą się zmienić po korekcie importu), ale plan testów w §8 musi nieść ciężar
weryfikacji na fikstury syntetyczne, nie na danych produkcyjnych — i weryfikacja end-to-end w §9
musi to jawnie odnotować zamiast pozorować dopasowanie do liczb, których nie ma.

## 1. Migracja bazy — v8

`SCHEMA_VERSION = len(_MIGRATIONS)` (`db.py:269`) rośnie z 7 na **8**. W przeciwieństwie do kroku 26
(świadomie bez migracji) tu jest ona konieczna — strata i jej odliczenia to stan, którego nie da się
wyprowadzić z `lots`/`sales` w locie (odliczenie w danym roku to **decyzja użytkownika**, nie fakt
policzalny z wyciągu).

```sql
-- v8 — krok 27: straty z lat ubiegłych (art. 9 ust. 3-3a ustawy o PIT) i
-- zamknięcie roku podatkowego (docs/PLAN_KROK_27_straty_kreator.md).
CREATE TABLE tax_loss_carryforward (
    id INTEGER PRIMARY KEY,
    origin_year INTEGER NOT NULL,
    cost_basis_policy TEXT NOT NULL
        CHECK(cost_basis_policy IN ('own_only','own_plus_drip','all_at_acquisition')),
    loss_pln REAL NOT NULL,
    UNIQUE(origin_year, cost_basis_policy)
);

CREATE TABLE tax_loss_deductions (
    id INTEGER PRIMARY KEY,
    loss_id INTEGER NOT NULL REFERENCES tax_loss_carryforward(id) ON DELETE RESTRICT,
    used_in_year INTEGER NOT NULL,
    amount_pln REAL NOT NULL,
    UNIQUE(loss_id, used_in_year)
);

CREATE TABLE tax_year_closed (
    year INTEGER PRIMARY KEY,
    closed_at TEXT NOT NULL DEFAULT (datetime('now')),
    total_due_pln_snapshot REAL NOT NULL
);
```

`ON DELETE RESTRICT`, nie `CASCADE`, na `tax_loss_deductions.loss_id` — **świadome**. Gdyby przeliczenie
`rebuild()` (§2) chciało usunąć wiersz straty, który ma już zarejestrowane odliczenia, baza ma to
odrzucić hałaśliwym `IntegrityError`, nie po cichu skasować ślad decyzji podatkowej z zeszłorocznej
deklaracji. `rebuild()` i tak nigdy nie próbuje takiego `DELETE` (patrz §2.2) — `RESTRICT` to warstwa
obrony, nie ścieżka używana w normalnym działaniu.

## 2. `tax/losses.py` — nowy moduł

### 2.1 Nagłówek modułu — zastrzeżenie prawne

```python
"""Straty z lat ubiegłych (art. 9 ust. 3-3a ustawy o PIT, krok 27) i
zamknięcie roku podatkowego.

PODSTAWĘ PRAWNĄ POTWIERDZIĆ PRZY IMPLEMENTACJI na aktualnym tekście ustawy
(BLUEPRINT §3a: "potwierdzać podstawę prawną, nie zakładać jej"). Opis
poniżej to stan wiedzy z planowania (docs/PLAN_KROK_27_straty_kreator.md),
nie cytat z Dziennika Ustaw:
- ust. 3: odliczenie w ciągu kolejnych 5 lat podatkowych po roku straty,
  w żadnym z nich nie więcej niż 50% kwoty tej straty.
- ust. 3a (od 2019): ALBO jednorazowo do 5 000 000 zł w jednym z tych lat,
  reszta (gdyby strata przekraczała 5 mln) w pozostałych latach okresu przy
  tym samym limicie 50%.

Dla strat rzędu tysięcy/dziesiątek tysięcy złotych (skala tego portfela)
próg 5 mln nigdy nie jest wiążący, więc ust. 3a w praktyce znosi limit
50%/rok — całą stratę wolno odliczyć jednym ruchem w pierwszym dochodowym
roku okresu. `max_deduction_pln()` poniżej implementuje WYŁĄCZNIE ten
uproszczony, bezpieczny dla realnej skali przypadek (pełna kwota przy
pierwszym użyciu straty, 50%/rok gdy odliczenie już się zaczęło rozkładać
na raty) — pełna gałąź "duża strata rozłożona na raty z jednorazowym
zastrzykiem w dowolnym roku" jest świadomie POZA krokiem (§10), bo przy
tej skali portfela jest martwym kodem."""
```

### 2.2 `rebuild(conn, cfg) -> dict`

Wzorzec 1:1 z `analytics/history.py::rebuild()` (krok 25) — przeliczane po każdej zmianie danych
(job schedulera + wywołanie z `web.py` przy wejściu na `/pit38` i `/pit38/kreator`, jak
`taxdiv.backfill_pl_tax_due` już dziś robi w `_pit38_report_for_request` — `web.py:1205`).

```python
def rebuild(conn: sqlite3.Connection, cfg: dict) -> dict:
    """Przelicza tax_loss_carryforward z compute_all_policies() dla każdego
    roku z jakąkolwiek sprzedażą, dla wszystkich trzech polityk naraz.
    Nigdy nie kasuje wiersza, który ma już zarejestrowane odliczenia
    (RESTRICT + jawny guard poniżej) — zamiast tego zwraca 'conflicts' do
    pokazania w UI. Zwraca {"upserted": [(year,policy)], "conflicts": [...]}."""
```

Kroki:
1. `years = {int(r[0]) for r in conn.execute("SELECT DISTINCT strftime('%Y', sale_date) FROM sales")}`.
2. Dla każdego `year` × każdej z trzech polityk (`taxpolicy.POLICIES`): policz
   `income_pln = taxpolicy.compute_all_policies(conn, cfg, year=year)[policy]["income_pln"]`.
3. `income_pln < 0` → `loss_pln = abs(income_pln)`, `INSERT ... ON CONFLICT(origin_year, cost_basis_policy)
   DO UPDATE SET loss_pln = excluded.loss_pln WHERE loss_pln < excluded.loss_pln OR NOT EXISTS
   (SELECT 1 FROM tax_loss_deductions WHERE loss_id = tax_loss_carryforward.id)` — **nie** bezwarunkowy
   UPDATE: gdyby korekta importu zmniejszyła stratę PONIŻEJ już wykorzystanej kwoty, bezwarunkowy
   UPDATE po cichu skorumpowałby ślad („liczba w zeszłorocznej deklaracji nagle większa niż
   dostępna strata") — dokładnie błąd z `project_pv_roi_audit_0_35_4_0_35_5` (zamrożenie/aktualizacja
   bez sprawdzenia, co już z tej liczby skorzystało). Zamiast cichej korekty: jeżeli nowo policzone
   `loss_pln` < `SUM(tax_loss_deductions.amount_pln)` dla tego wiersza, dopisz do `conflicts` i
   **zostaw `loss_pln` bez zmian** — użytkownik dostaje ostrzeżenie w kreatorze (§4), nie fałszywą ciszę.
4. `income_pln >= 0` (rok już niestratny po korekcie) i wiersz istnieje **bez** odliczeń → `DELETE`.
   Z odliczeniami → zostaw, dopisz do `conflicts` (ten sam powód co w punkcie 3).
5. `conn.commit()`.

### 2.3 `available_for_year(conn, cfg, year, policy=None) -> dict`

`policy` domyślnie `cfg["cost_basis_policy"]` (aktywna). Okno 5 lat: strata z `origin_year=N` jest
dostępna w latach `N+1..N+5`, czyli dla zapytanego `year` kwalifikują się
`origin_year BETWEEN year-5 AND year-1`.

```python
def available_for_year(conn, cfg, year: int, policy: str | None = None) -> dict:
    policy = policy or cfg.get("cost_basis_policy", "own_only")
    rows = conn.execute(
        "SELECT * FROM tax_loss_carryforward WHERE cost_basis_policy = ? "
        "AND origin_year BETWEEN ? AND ? ORDER BY origin_year",
        (policy, year - 5, year - 1)).fetchall()
    items = []
    for row in rows:
        used_before = conn.execute(
            "SELECT COALESCE(SUM(amount_pln),0) FROM tax_loss_deductions "
            "WHERE loss_id = ? AND used_in_year < ?", (row["id"], year)).fetchone()[0]
        remaining = round(row["loss_pln"] - used_before, 2)
        if remaining <= 0.005:
            continue
        used_this_year = conn.execute(
            "SELECT COALESCE(SUM(amount_pln),0) FROM tax_loss_deductions "
            "WHERE loss_id = ? AND used_in_year = ?", (row["id"], year)).fetchone()[0]
        items.append({
            "loss_id": row["id"], "origin_year": row["origin_year"],
            "loss_pln": row["loss_pln"], "used_before_pln": round(used_before, 2),
            "remaining_pln": remaining,
            "max_deduction_pln": max_deduction_pln(row["loss_pln"], used_before, remaining),
            "used_this_year_pln": round(used_this_year, 2),
            "expires_after_year": row["origin_year"] + 5,
        })
    return {
        "year": year, "policy": policy, "items": items,
        "total_remaining_pln": round(sum(i["remaining_pln"] for i in items), 2),
        "total_used_this_year_pln": round(sum(i["used_this_year_pln"] for i in items), 2),
    }
```

### 2.4 `max_deduction_pln(loss_pln, used_before, remaining) -> float`

Realizacja uproszczenia z §2.1:

```python
def max_deduction_pln(loss_pln: float, used_before: float, remaining: float) -> float:
    if used_before <= 0.005 and remaining <= LUMP_SUM_CAP_PLN:
        return remaining          # ust. 3a: cała strata jednym ruchem
    return min(remaining, round(loss_pln * 0.5, 2))   # ust. 3: 50%/rok
```

`LUMP_SUM_CAP_PLN = 5_000_000.0` jako stała modułu. Wywołujący (`record_deduction`, §2.5) dodatkowo
przycina do dochodu roku, w którym odliczenie ma zajść — strata nie może zejść poniżej zera dochodu
(dokładnie tak samo jak `tax_pln = max(0, income*rate)` w `policy.py:99` nigdy nie daje ujemnego
podatku, odliczenie nie może dać ujemnego dochodu).

### 2.5 `record_deduction` / `delete_deduction`

```python
def record_deduction(conn, cfg, loss_id: int, used_in_year: int, amount_pln: float) -> None:
    """Rzuca ValueError, gdy amount_pln przekracza max_deduction_pln TEJ
    straty w TYM roku (przeliczone na świeżo, nie ufa wartości z UI) LUB
    przekracza dodatni dochód roku wg polityki tej straty (odliczenie nie
    może zrobić z dochodu liczby ujemnej)."""
```
Sprawdza `origin_year < used_in_year <= origin_year + 5` (poza oknem → `ValueError`), przelicza
dostępność przez `available_for_year` dla `policy` wiersza (nie dla aktywnej polityki cfg — strata
policzona w polityce A nie odlicza się od dochodu w polityce B, to byłoby mieszanie dwóch
niezależnych, równoległych rachunków z `tax/policy.py`), sprawdza dochód roku:
`taxpolicy.compute_all_policies(conn, cfg, year=used_in_year)[policy]["income_pln"]` musi być > 0
i `amount_pln <= income_pln`. `INSERT ... ON CONFLICT(loss_id, used_in_year) DO UPDATE` (edycja
istniejącej decyzji, nie duplikat) — **`amount_pln == 0.0`** to jedyny dozwolony wyjątek od reguły
„nie więcej niż max_deduction_pln" i zamiast `INSERT`/`UPDATE` robi `DELETE FROM tax_loss_deductions
WHERE loss_id=? AND used_in_year=?`: cofnięcie decyzji w kreatorze (ponowne przesłanie formularza z
wyzerowanym polem „Odlicz maksimum") nie potrzebuje osobnego endpointu/przycisku — ten sam
`POST /pit38/kreator/odlicz` obsługuje i zapis, i cofnięcie.

### 2.6 Zamknięcie roku

```python
def is_year_closed(conn, year: int) -> bool: ...
def close_year(conn, cfg, year: int) -> dict:
    """Zapisuje total_due_pln_snapshot = pit38.annual_report(conn, cfg, year)['total_due_pln']
    (PO odliczeniu strat — patrz §3). Idempotentne: zamknięcie już zamkniętego
    roku nadpisuje snapshot świeżą liczbą (ponowne 'oznacz jako zamknięty'
    po korekcie to świadoma akcja użytkownika, nie przypadek)."""
def reopen_year(conn, year: int) -> None:
    """DELETE z tax_year_closed. Nie dotyka lots/sales/deductions — 'miękkie'
    odblokowanie z roadmapy (ROADMAP.md:163)."""
def year_data_conflict(conn, cfg, year: int) -> dict | None:
    """Dla zamkniętego roku: przelicza total_due_pln na świeżo i porównuje
    ze snapshotem. Różnica > 0.01 zł -> {"snapshot_pln", "current_pln", "diff_pln"},
    inaczej None. Lekcja z project_pv_roi_audit_0_35_4_0_35_5: zamknięcie
    roku mrozi LICZBY RAPORTU do wglądu, nie zabrania dopisania brakującej
    transakcji — więc rozjazd musi być WIDOCZNY, nie ukryty pod martwym
    ptaszkiem "zamknięte=OK"."""
```

### 2.7 `wizard_steps(conn, cfg, year) -> list[dict]`

Zwraca uporządkowaną listę `{key, label, done, detail_pl}` — czysta funkcja, testowalna bez Flask,
używana przez `/pit38/kreator` (web.py tylko renderuje).

| `key` | `done` gdy | Zapytanie |
|---|---|---|
| `import` | istnieje `imports` z `as_of_date` w roku `year` lub późniejszym | `SELECT 1 FROM imports WHERE as_of_date >= ? LIMIT 1` (`f"{year}-01-01"`) |
| `conflicts` | zero nierozstrzygniętych konfliktów jakiegokolwiek typu | `SELECT COUNT(*) FROM import_conflicts WHERE resolved=0` |
| `balance` | zero nierozstrzygniętych konfliktów `entity_type='balance'` | jw. z filtrem `entity_type` |
| `dividends` | `report.section_g.has_estimated is False` (informacyjny — nie blokuje zamknięcia, patrz §4.2) | z `pit38.annual_report()` |
| `losses` | `available_for_year(...)['total_remaining_pln'] == 0` **lub** użytkownik jawnie zapisał decyzję (`used_this_year_pln > 0` dla każdej pozycji) **lub** brak dostępnych strat w ogóle | `available_for_year()` |

`conflicts` i `balance` **blokują** zamknięcie (`close_year` w web.py odmawia, gdy którykolwiek z
tej trójki `False` — `import`/`conflicts`/`balance`); `dividends` i `losses` są informacyjne
(pokazane, nie blokują — szacunek dywidendowy bywa jedyną dostępną liczbą, a nieużycie dostępnej
straty w danym roku to legalna, świadoma decyzja: zostawienie jej na rok z wyższym dochodem).

## 3. `tax/pit38.py` — dopięcie strat do raportu

`annual_report()` (`pit38.py:83-110`) dostaje nową sekcję i zmienioną definicję `total_due_pln`:

```python
loss_info = taxlosses.available_for_year(conn, cfg, year, policy=active_policy)
used_this_year_pln = loss_info["total_used_this_year_pln"]   # TYLKO zarejestrowane, nie hipotetyczne
income_after_loss_pln = max(0.0, policies[active_policy]["income_pln"] - used_this_year_pln)
tax_rate = cfg.get("pl_capital_gains_tax_pct", 19.0) / 100
tax_after_loss_pln = round(income_after_loss_pln * tax_rate, 2)

total_due_pln = round(tax_after_loss_pln + section_g["pl_tax_due_pln"], 2)
```

`loss_carryforward` w zwracanym słowniku: `{**loss_info, "income_after_loss_pln",
"tax_after_loss_pln", "tax_before_loss_pln": policies[active_policy]["tax_pln"]}`. **Świadomie
`used_this_year_pln`, nie `max_deduction_pln`** — dopóki użytkownik nie zapisze decyzji w
kreatorze (§2.5), `/pit38` pokazuje dostępną pulę jako informację, ale NIE zmienia kwoty do
zapłaty automatycznie. Bez tego rozróżnienia dodatek cichaczem „decydowałby" ile straty spalić w
danym roku, a to jest wybór podatnika (zostawić stratę na rok z wyższym dochodem bywa korzystniejsze
— stąd w ogóle sensowność „Optymalizatora" w §6).

`test_annual_report_poz_c_matches_compute_all_policies` (regresja, `test_tax_pit38.py:44`) musi
przejść **niezmieniony** — porównuje `report["policies"]` (poz. C surowa, bez straty) z
`compute_all_policies()` wprost; nowa sekcja `loss_carryforward` i zmieniony `total_due_pln` nie
naruszają tego pola.

## 4. Strona `/pit38` — nowa karta

Między kartą „Polityka kosztu" (`pit38.html:42-82`) a „Sekcja G" (`pit38.html:84-107`) dochodzi:

```html
<div class="card">
  <div class="card-title">Straty z lat ubiegłych ({{ policy_labels.get(cfg.cost_basis_policy) }})</div>
  {% if report.loss_carryforward.items %}
  <div class="grid stats">
    {{ stat('Dostępna strata', '%.2f'|format(report.loss_carryforward.total_remaining_pln), 'PLN') }}
    {{ stat('Odliczone w tym roku', '%.2f'|format(report.loss_carryforward.total_used_this_year_pln), 'PLN') }}
    {{ stat('Podatek po odliczeniu', '%.2f'|format(report.loss_carryforward.tax_after_loss_pln), 'PLN', cls='highlight') }}
  </div>
  <a class="btn no-print" href="{{ url_for('pit38_wizard_get', year=year) }}">Kreator rozliczenia →</a>
  {% else %}
  <p class="muted">Brak dostępnych strat z ostatnich 5 lat w polityce
    {{ policy_labels.get(cfg.cost_basis_policy) }}.</p>
  {% endif %}
</div>
```

„RAZEM DO ZAPŁATY" w karcie pierwszej (`pit38.html:38`) automatycznie pokazuje nowy `total_due_pln`
— zero zmian w tym wierszu, liczba płynie z `report`.

## 5. `/pit38/kreator` — nowa strona

Trasy w `web.py`, wzorzec z `/plan` (`web.py:761-830`, krok 26 — zawsze `conn=_conn()`/`try`/
`finally: conn.close()`, `active`/`version`):

```python
@app.get("/pit38/kreator")
def pit38_wizard_get():
    conn = _conn()
    try:
        cfg = settingsm.get_settings(conn)
        year = request.args.get("year", type=int) or cfg.get("tax_year") or datetime.now().year
        taxlosses.rebuild(conn, cfg)
        steps = taxlosses.wizard_steps(conn, cfg, year)
        report = taxpit38.annual_report(conn, cfg, year)
        closed = taxlosses.is_year_closed(conn, year)
        conflict = taxlosses.year_data_conflict(conn, cfg, year) if closed else None
        can_close = all(s["done"] for s in steps if s["key"] in ("import", "conflicts", "balance"))
        return render_template(
            "wizard.html", active="pit38", version=__version__,
            year=year, years=_years_with_data(conn), steps=steps, report=report,
            closed=closed, conflict=conflict, can_close=can_close, cfg=cfg)
    finally:
        conn.close()

@app.post("/pit38/kreator/odlicz")
def pit38_wizard_deduct():
    conn = _conn()
    year = int(request.form["year"])
    try:
        cfg = settingsm.get_settings(conn)
        with dbm.WRITE_LOCK:
            taxlosses.record_deduction(
                conn, cfg, int(request.form["loss_id"]), year,
                float(request.form["amount_pln"]))
        return redirect(url_for("pit38_wizard_get", year=year))
    except ValueError as e:
        # ten sam wzorzec co whatif_error na /pit38 (pit38.html:125-127): błąd
        # renderuje się z powrotem w formularzu jako komunikat, nigdy 500
        cfg = settingsm.get_settings(conn)
        steps = taxlosses.wizard_steps(conn, cfg, year)
        report = taxpit38.annual_report(conn, cfg, year)
        return render_template(
            "wizard.html", active="pit38", version=__version__, year=year,
            years=_years_with_data(conn), steps=steps, report=report,
            closed=taxlosses.is_year_closed(conn, year), conflict=None,
            can_close=all(s["done"] for s in steps if s["key"] in ("import", "conflicts", "balance")),
            cfg=cfg, deduct_error=str(e))
    finally:
        conn.close()

@app.post("/pit38/kreator/zamknij")
def pit38_wizard_close():
    conn = _conn()
    try:
        cfg = settingsm.get_settings(conn)
        year = int(request.form["year"])
        steps = taxlosses.wizard_steps(conn, cfg, year)
        blocking = [s for s in steps if s["key"] in ("import", "conflicts", "balance") and not s["done"]]
        if blocking:
            # brak 403 - redirect z komunikatem, ten sam wzorzec co settings_post
            # (web.py:1197) zwracający redirect z query-param zamiast wyjątku
            return redirect(url_for("pit38_wizard_get", year=year, close_error="1"))
        with dbm.WRITE_LOCK:
            taxlosses.close_year(conn, cfg, year)
        return redirect(url_for("pit38_wizard_get", year=year, closed="1"))
    finally:
        conn.close()

@app.post("/pit38/kreator/odblokuj")
def pit38_wizard_reopen():
    conn = _conn()
    try:
        year = int(request.form["year"])
        with dbm.WRITE_LOCK:
            taxlosses.reopen_year(conn, year)
        return redirect(url_for("pit38_wizard_get", year=year))
    finally:
        conn.close()
```

`templates/wizard.html` — checklist (`steps`, ikona ✓/○, `dividends`/`losses` w stylu ostrzeżenia
`.muted`, nie błędu, bo informacyjne), karta „Odlicz stratę" per pozycja `report.loss_carryforward.items`
(formularz `amount_pln` z `max="{{ item.max_deduction_pln }}"`, przycisk „Odlicz maksimum" wypełniający
pole przez mały inline `onclick`, zero nowego JS w `app.js`), przycisk „Oznacz rok jako zamknięty"
(`disabled` gdy `not can_close`, z tekstem dlaczego), baner `conflict` (czerwony, `.disclaimer.error`
jak `whatif_error` na `/pit38`) gdy zamknięty rok ma rozjazd. Nawigacja: link „Kreator" **wewnątrz**
karty PIT-38 (§4), nie osobna pozycja w `base.html` — kreator to tryb pracy nad rokiem, nie osobna
domena danych (różni się tym od `/plan`, które dostało własny wpis w nawigacji w kroku 26).

## 6. Optymalizator momentu sprzedaży — `advisor.py`

Zgodnie z podziałem z kroku 26 („`tax/` = fakt, `advisor.py` = pieniądz"), a nie `tax/losses.py` —
ta funkcja składa podatek + stratę + przepadek ESPP, dokładnie jak `advisor.overview()` już składa
grants+tax+forfeit.

```python
def optimize_sale_timing(conn, cfg, quantity: float, price_eur: float,
                         today: str | None = None) -> dict:
    """Porównuje 'sprzedaż dziś' vs 'sprzedaż 2 stycznia następnego roku
    podatkowego' dla tej samej ilości/ceny. Cena i kurs NBP założone PŁASKO
    na obu datach (ten sam disclaimer co espp_plan() z kroku 26, punkt 3) —
    to szacunek kierunku decyzji, nie prognoza rynku."""
```

Dla obu scenariuszy (`today`, `2 stycznia {rok+1}`):
- `taxwhatif.simulate_sale(conn, cfg, quantity, price_eur, sale_date=scenario_date)`, owinięte w
  `except (InsufficientLotsError, CostBasisMissingError): scenario = None` (ten sam wzorzec co
  `advisor.overview`'s `sale_today`, `advisor.py:250-251`) — nigdy 500.
- Dochód tej sprzedaży **dokłada się** do już zrealizowanego dochodu roku scenariusza
  (`taxpolicy.compute_all_policies(conn, cfg, year=scenario_year)[active]["income_pln"]`), a
  całość netuje się przez `taxlosses.available_for_year(conn, cfg, scenario_year)` — sprzedaż
  2 stycznia w NOWYM roku podatkowym startuje z zerowym już-zrealizowanym dochodem, więc może
  mieć inną dostępną pulę strat niż sprzedaż dziś w bieżącym roku.
- `advisorm.forfeit_for_quantity(conn, quantity, price_eur, eurpln_rate, today=scenario_date)` —
  przepadek zależy od tego, czy `scenario_date` jest przed czy po `free_until` konkretnego lotu.

Zwraca `{"today": {...}, "jan2_next_year": {...}, "delta_tax_pln", "delta_forfeit_pln",
"recommendation_pl"}`. `recommendation_pl` to zdanie deterministyczne z porównania sum (np. „Sprzedaż
2 stycznia {rok} kosztuje Cię {X} zł mniej podatku, ale {Y} zł więcej przepadku dopasowania —
różnica netto: {Z} zł na {kierunek}"), **nie AI** — zgodne z zasadą „Dziś warto wiedzieć" z 0.12.0
(deterministyczne, nie przez AI).

**UI:** karta na `/plan` (nie nowa strona) — pytanie „ile stracę / zyskam, czekając do stycznia"
pasuje tematycznie do doradcy z kroku 26 bardziej niż do `/pit38`. Formularz analogiczny do
„Co jeśli sprzedam teraz" (`pit38.html:109-153`): ilość + cena, GET + serwerowy render, zero nowego
JS poza istniejącym `NT.initFormPreview` (nowy endpoint podglądu `/api/preview/sale-timing`, ten sam
kontrakt HTTP 200 zawsze / `{"ok":true,"lines":[...]}` / `{"ok":false,"error":...}`).

## 7. Sensory MQTT

Nowa sekcja w `publisher._ENTITIES` (`publisher.py:44-182`):

```python
_Entity("sensor", "loss_available_pln",      "Loss Available PLN",      "PLN", "monetary", "total", "mdi:trending-down"),
_Entity("sensor", "loss_used_this_year_pln",  "Loss Used This Year PLN", "PLN", "monetary", "total", "mdi:cash-minus"),
```

`sensors.losses_values(conn, cfg) -> dict` — wzorzec `pit38_values` (`sensors.py:331-347`): rok z
`cfg.get("tax_year") or datetime.now().year`, woła `taxlosses.available_for_year(conn, cfg, year)`.
Wpięcie w `main.py::publish_sensors` (`main.py:218` okolice) jedną linią po
`values.update(sensors.pit38_values(...))`. Job `rebuild_tax_losses_job` w harmonogramie
(`main.py:550` sąsiedztwo `rebuild_portfolio_history_job`), `cron hour=5, minute=45` — po
`rebuild_portfolio_history_job` (5:30), przed niczym co go konsumuje tego samego dnia.

## 8. Pliki

**Nowe:** `nokia_tracker/tax/losses.py`, `nokia_tracker/templates/wizard.html`,
`tests/test_tax_losses.py`, `docs/PLAN_KROK_27_straty_kreator.md`.

**Zmienione:** `db.py` (migracja v8) · `tax/pit38.py` (`loss_carryforward`, `total_due_pln`) ·
`advisor.py` (`optimize_sale_timing`) · `sensors.py` (`losses_values`) · `publisher.py` (2 encje) ·
`main.py` (seed importu `tax.losses`, 1 linia `publish_sensors`, `rebuild_tax_losses_job`) ·
`web.py` (`/pit38/kreator` GET, `/pit38/kreator/odlicz` POST, `/pit38/kreator/zamknij` POST,
`/pit38/kreator/odblokuj` POST, `/api/preview/sale-timing`, karta strat na `/pit38`) ·
`templates/pit38.html` (karta „Straty z lat ubiegłych") · `templates/plan.html` (karta optymalizatora)
· `__init__.py` → `0.11.0` · `CHANGELOG.md` · `README.md`.

## 9. Plan testów (TDD — czerwony przed zielonym)

`tests/test_tax_losses.py` (nowy, fixture `_fake_nbp_rate`/`_base_cfg` skopiowane z
`test_tax_pit38.py` — ten sam wzorzec mockowania kursu):

- `rebuild()`: rok ze sprzedażą ze stratą (`add_lot` cena 10 EUR, `record_sale` cena 5 EUR) daje
  wiersz `tax_loss_carryforward` z poprawnym `loss_pln`; rok zyskowny **nie** tworzy wiersza; trzy
  polityki dają **trzy różne** `loss_pln` dla tej samej sprzedaży (lot `own` vs `matched` w koszcie);
  drugi `rebuild()` bez zmian danych jest no-opem (idempotencja, zero duplikatów dzięki
  `UNIQUE(origin_year, cost_basis_policy)`); rok, który przestał być stratny (skorygowana cena)
  **bez** odliczeń → wiersz znika; **z** odliczeniami → wiersz zostaje, `conflicts` niepuste.
- `available_for_year()`: strata z roku N dostępna w N+1..N+5, **niedostępna** w N+6; dwie straty
  (N i N+2) sumują się w roku N+3; `used_before_pln` z wcześniejszego roku pomniejsza `remaining_pln`;
  zero wierszy → `total_remaining_pln == 0.0`, `items == []`.
- `max_deduction_pln()`: strata 10 000 zł, `used_before=0` → max = **10 000** (cała, ust. 3a);
  po odliczeniu 3 000 w poprzednim roku, `used_before=3000, remaining=7000` → max =
  **min(7000, 5000)** = **5000** (50% z 10 000, bo `used_before>0` odpala tryb ratalny); strata
  hipotetyczna 8 000 000 zł, `used_before=0` → max = **4 000 000** (50%, bo `remaining>5_000_000`
  wyklucza jednorazowe użycie całości).
- `record_deduction()`: zapis w oknie 5 lat przechodzi; `used_in_year > origin_year+5` →
  `ValueError`; `amount_pln > max_deduction_pln` → `ValueError`; `amount_pln > income_pln` roku
  docelowego → `ValueError`; drugi zapis dla tej samej `(loss_id, used_in_year)` **nadpisuje**
  (`ON CONFLICT DO UPDATE`), nie duplikuje; strata z polityki `own_only` nie pomniejsza dochodu
  policzonego w polityce `all_at_acquisition` (dwa niezależne wiersze, dwa niezależne salda).
- `close_year()`/`reopen_year()`/`is_year_closed()`: zamknięcie zapisuje snapshot równy
  `total_due_pln` z `annual_report()` w tej samej chwili; `is_year_closed` przełącza się po
  zamknięciu/odblokowaniu; `year_data_conflict` zwraca `None` tuż po zamknięciu (świeże ==
  snapshot), niepusty słownik po zmianie danych źródłowych (dopisanie sprzedaży zmienia
  `total_due_pln`), `None` ponownie po ponownym `close_year()` (odświeżony snapshot).
- `wizard_steps()`: brak importu w roku → `import: False`; nierozstrzygnięty konflikt `balance`
  → `conflicts: False` i `balance: False` jednocześnie; `dividends: False` gdy `has_estimated`;
  `losses: True` gdy brak dostępnych strat; `losses: True` po zapisaniu pełnego odliczenia,
  `False` przy częściowym.

`tests/test_tax_pit38.py` (rozszerzenie istniejącego): `test_annual_report_poz_c_matches_compute_all_policies`
przechodzi **bez zmian** (regresja — nowa sekcja nie rusza `policies`); nowy
`test_annual_report_total_due_uses_recorded_deduction_not_available` (dostępna strata 5000, brak
zapisanej decyzji → `total_due_pln` **nie** spada); po `record_deduction` → spada dokładnie o
`deduction * tax_rate`; `loss_carryforward.items == []` dla roku bez dostępnej straty.

`tests/test_advisor.py` (rozszerzenie): `optimize_sale_timing` zwraca oba scenariusze przy pełnym
pokryciu; brak pokrycia w jednym scenariuszu → ten scenariusz `None`, drugi liczony dalej (nigdy
wyjątek na zewnątrz); `delta_tax_pln`/`delta_forfeit_pln` zgadzają się arytmetycznie z różnicą pól
`today`/`jan2_next_year`.

`tests/test_sensors.py`: dwa nowe klucze, zgodne z `available_for_year()` wywołanym niezależnie.

`tests/test_publisher.py`: dwie nowe encje, `monetary`+`total`.

`tests/test_web.py`: `/pit38/kreator` w parametryzacji smoke (jak `/plan` dostało w kroku 26);
checklist w HTML odzwierciedla `wizard_steps()`; `POST /pit38/kreator/odlicz` zapisuje i przelicza
`/pit38`; kwota ponad `max_deduction_pln` → formularz z błędem, **zero zapisu**
(`COUNT(*) FROM tax_loss_deductions` przed/po); `POST .../zamknij` blokowany (`can_close=False`)
gdy jest nierozstrzygnięty konflikt `balance`; po zamknięciu `POST .../odlicz` na ten sam rok nadal
działa (miękka blokada — dopisanie danych po zamknięciu jest dozwolone, patrz §2.6) ale
`year_data_conflict` staje się niepusty; `POST .../odblokuj` czyści `tax_year_closed`; karta strat
na `/pit38` pokazuje `total_remaining_pln` w HTML; `/api/preview/sale-timing` zwraca `lines` z
HTTP 200, zły input → `ok:false`, **nic nie zapisuje**.

Bilans: **767 → ~815 testów.**

## 10. Świadomie poza krokiem

Pełna gałąź ust. 3a dla strat > 5 000 000 zł rozłożonych na raty z jednorazowym zastrzykiem w
DOWOLNYM (nie tylko pierwszym) roku okresu — §2.1, martwy kod przy tej skali portfela. Krok
„zweryfikuj sekcję G" i „wyeksportuj"/„przepisz do deklaracji" jako **informacyjne**, nie
blokujące zamknięcia roku — nie ma sygnału w bazie na „user faktycznie przepisał liczby do
e-Deklaracji", więc kreator nie udaje, że to sprawdza (odstępstwo od dosłownego brzmienia
`ROADMAP.md:159-161`, uzasadnione w §2.7 i §5). Brak nowych ustawień/opcji `config.yaml` — cała
fala jest wyprowadzalna z `lots`/`sales` + decyzji zapisanych w nowych tabelach, żadnego globalnego
przełącznika (w przeciwieństwie do kroku 26, gdzie `other_net_worth_pln` wymagał sześciu miejsc).
Optymalizator nie modeluje dryfu ceny/FX między „dziś" a „2 stycznia" (ten sam kompromis co
`espp_plan` z kroku 26) — tylko kierunek decyzji, nie prognoza.

## 11. Pułapki

1. **Nigdy nie licz odliczenia automatycznie w `annual_report()`.** Dostępność ≠ użycie — `/pit38`
   pokazuje `total_due_pln` policzone z tego, co user REALNIE zapisał w kreatorze (§3), inaczej
   dodatek cichaczem decyduje za użytkownika, kiedy spalić stratę.
2. **`rebuild()` nigdy nie kasuje/zmniejsza `loss_pln` poniżej sumy istniejących odliczeń** — zamiast
   tego zgłasza konflikt (§2.2). To najostrzejsza pułapka całego kroku, bezpośrednia lekcja z
   `project_pv_roi_audit_0_35_4_0_35_5`.
3. Trzy polityki = trzy **niezależne** salda strat. Odliczenie zapisane dla `own_only` nie istnieje
   dla `all_at_acquisition` — `record_deduction` musi wiązać się z `loss_id` (który niesie
   `cost_basis_policy`), nigdy z gołym rokiem.
4. Okno 5 lat liczone od `origin_year`, nie od dziś — strata z 2020 jest martwa od 2026 (włącznie),
   test na obu krańcach (`N+5` dostępne, `N+6` nie).
5. `max_deduction_pln` musi dodatkowo przyciąć do dochodu docelowego roku — sama funkcja tego nie
   robi (nie ma dostępu do `conn`/`cfg`), robi to `record_deduction` (§2.5). Pominięcie tego kroku
   da ujemny dochód po odliczeniu, sprzeczność z `income_after_loss_pln = max(0.0, ...)` w `pit38.py`.
6. `ON DELETE RESTRICT` w migracji v8 oznacza, że **żaden** przyszły kod nie może robić
   `DELETE FROM tax_loss_carryforward` na wiersz z odliczeniami bez wcześniejszego ich usunięcia —
   inaczej `IntegrityError` w produkcji, nie w testach (SQLite domyślnie ma `foreign_keys=ON` w tej
   bazie, `db.py:281`, więc RESTRICT faktycznie egzekwuje).
7. `close_year`/`reopen_year` **nie** blokują zapisu do `lots`/`sales`/`tax_loss_deductions` — tylko
   zamrażają snapshot do porównania. Kusząca, ale błędna „poprawka": zablokować `record_sale` dla
   zamkniętych lat — to dokładnie odtworzyłoby błąd z `pv_roi` (zamrożenie danych, nie tylko liczb
   raportu).
8. Test `test_annual_report_poz_c_matches_compute_all_policies` musi przejść **nietknięty** —
   regresja krok 15 tego samego wzorca co w kroku 26 (`restricted_own_summary`).
9. `wizard_steps()['losses']['done']` przy braku dostępnych strat musi być `True` (nie `False`
   przez brak danych) — inaczej każdy rok bez straty pokazuje fałszywie nieukończony krok na
   zawsze nierozwiązywalny.
10. Zaokrąglenia groszowe w `available_for_year`: `remaining_pln <= 0.005` jako próg pominięcia
    pozycji (nie `== 0`), inaczej wygasła strata z resztką `1e-13` zaśmieca listę.

## 12. Weryfikacja

1. **Wykonać §0 przed napisaniem jednej linii `tax/losses.py`** — bez znajomości realnych lat
   stratnych (o ile istnieją) nie da się napisać sensownego testu integracyjnego na dane
   produkcyjne; fikstury syntetyczne w §9 nie zależą od tego wyniku, ale krok 3 poniżej tak.
2. **Cała sekcja `tax/` jak beton** — `test_tax_*.py` zielone przed i po zmianie `annual_report()`
   (wzorzec kroku 15/26).
3. **Sprawdzenie na realnych danych, jeśli §0 znalazło choć jeden stratny rok/politykę** — policzyć
   ręcznie oczekiwaną `loss_pln` z danych z wyciągu i porównać z tym, co pokaże `rebuild()`. Jeśli
   §0 **nie** znalazło żadnego stratnego roku (prawdopodobne przy stale rosnącej cenie Nokii),
   ten punkt jawnie odnotowuje „brak danych produkcyjnych do porównania" zamiast pozorować
   weryfikację, którą zastępują testy syntetyczne z §9.
4. **Playwright na realnym URL-u ingressu** (1920 px + 390 px + tryb ciemny): `/pit38` (nowa karta),
   `/pit38/kreator` (checklist + formularz odliczenia + przycisk zamknięcia), `/plan` (karta
   optymalizatora). Screenshot **i** `browser_console_messages(error)`. Ścieżka
   `ws_command:"supervisor/api"` → `ingress_session` → `document.cookie` (kroki 21/23/26).
5. **Sweep PII na diffie przed każdym pushem** — repo publiczne, fikstury testowe syntetyczne,
   nigdy kopiuj-wklej z realnego wyciągu.
6. **Wdrożenie bezpieczną ścieżką** (add-on trzyma realne dane podatkowe): push →
   `gh release create v0.11.0` z potwierdzeniem `isDraft:false` → `homeassistant.update_entity` na
   `update.nokia_tracker_update` → poll `ha_get_addon` aż `version_latest=="0.11.0"` →
   `ha_manage_addon(action="update")` na slugu `5f59858c_nokia_tracker`. **Nigdy** cyklu
   uninstall/remove_repository/add_repository/install — kasuje SQLite.
7. `README.md` (tabele encji + opis stron, w tym nowa `/pit38/kreator`) i `CHANGELOG.md` w tym
   samym wydaniu.

## Pierwszy krok implementacji

Skopiować ten dokument do repo jako `docs/PLAN_KROK_27_straty_kreator.md` **zanim powstanie
pierwsza linia kodu** (zasada z `feedback_plans_as_md`), commit osobno — potem §0 (sprawdzenie
realnych lat stratnych na żywym `/pit38`) jako pierwsza faktyczna czynność przed pisaniem
`tax/losses.py`.
