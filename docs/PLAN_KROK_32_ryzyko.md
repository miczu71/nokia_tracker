# Krok 32 — Metryki ryzyka portfela (`nokia_tracker` 0.16.0)

## Context

`nokia_tracker` jest na **0.15.0** (wydane 2026-08-16, live, krok 31 — koncentracja v2).
Następna pozycja Roadmapy v2 (`docs/ROADMAP.md:288-292`) to **0.16.0 / krok 32 — Metryki
ryzyka portfela na `/wyniki`**: nowy `analytics/risk.py` z `sharpe_ratio()`,
`max_drawdown()`, `volatility_annualized()`, czysty Python (bez numpy — ta sama zasada co
`analytics/returns.py` z 0.9.0, `docs/PLAN_KROK_25_wyniki.md`, powód: musl/armv7).

**Zero migracji bazy** — wejściem jest wyłącznie już zmaterializowana `portfolio_history`
(`date`, `market_value_eur`, ...; kolumny `db.py:256-263`, wypełniana przez
`analytics/history.py::rebuild()` z kroku 25, nocnym jobem w `main.py`). **Zero nowych
sensorów MQTT** — roadmapa nie wymienia żadnych dla tej fali (w przeciwieństwie do kroków
26/30).

Decyzje podjęte przy planowaniu (nie relitygować):

- `portfolio_history` jest budowana wyłącznie z dni, dla których istnieje notowanie
  (`quotes.closes_in_range`, iterowane w `history.py::rebuild()`) — czyli już same **dni
  sesyjne**, nie kalendarzowe. Roczna liczba obserwacji to więc naturalnie ~252 (standard
  giełdowy), a nie 365. Stała `_TRADING_DAYS_PER_YEAR = 252` używana do annualizacji jest
  więc spójna z tym, co faktycznie jest w tabeli, nie osobnym założeniem.
- Wszystkie trzy metryki liczone na serii **`market_value_eur`** (nie PLN) — ta sama waluta
  bazowa co `returns.py::twr()`/`build_twr_cashflows()`, żeby efekt walutowy EUR/PLN (już
  osobno pokazany w atrybucji z 0.9.0) nie zniekształcał miary ryzyka samej pozycji.
- `risk_free_rate_pct` to **statyczna wartość konfiguracyjna, nie live-fetch** — jawna
  decyzja z `ROADMAP.md:290` (`risk_free_rate_pct, wartość statyczna, nie live-fetch`).
  Domyślnie `3.0` (przybliżenie stopy wolnej od ryzyka w EUR, rząd wielkości depo ECB/Bund
  — użytkownik może zmienić w opcjach add-onu; nie jest to porada finansowa, więc precyzja
  domyślnej wartości jest drugorzędna wobec samej możliwości konfiguracji).
- Zwracane wartości `volatility_annualized()`/`max_drawdown()` to **ułamki** (np. `-0.35`),
  tak jak `xirr()`/`twr()` — konwersja na `%` dzieje się w `web.py`, nie w `analytics/`.
  `sharpe_ratio()` zwraca liczbę bezwymiarową (nie mnożyć przez 100 w `web.py`).
- **Zastrzeżenie tekstowe pod kartą na `/wyniki`**: metryki ryzyka liczone dla pojedynczej
  spółki pracowniczej są z natury gorsze (brak dywersyfikacji) niż dla zdywersyfikowanego
  portfela/indeksu — wymóg wprost z `ROADMAP.md:291-292`.

## A. `analytics/risk.py`

Wejście dla wszystkich trzech funkcji: `daily_values: list[tuple[str, float]]` — ten sam
kształt co `returns.twr()` (`(data, market_value_eur)`, chronologicznie), budowany w
`web.py::wyniki_get` z `history_rows` dokładnie tak jak dziś budowane jest `daily_values`
dla `twr()` (bez nowego zapytania do bazy).

```python
_TRADING_DAYS_PER_YEAR = 252

def _daily_returns(daily_values: list[tuple[str, float]]) -> list[float]:
    """Dzienne stopy zwrotu r_i = (v_i - v_{i-1}) / v_{i-1}; pomija dni,
    gdzie v_{i-1} == 0 (pusty portfel na starcie)."""

def volatility_annualized(daily_values) -> float | None:
    """Odchylenie standardowe próbki dziennych zwrotów (statistics.stdev,
    n-1) * sqrt(252). None przy < 3 punktach (< 2 obserwacji zwrotu — stdev
    próbki niezdefiniowany dla n=1)."""

def max_drawdown(daily_values) -> float | None:
    """Największy spadek od bieżącego maksimum (peak-to-trough), ujemny
    ułamek. None gdy pusta seria. Nie wymaga zwrotów — działa też na 1
    punkcie (wynik 0.0)."""

def sharpe_ratio(daily_values, risk_free_rate_pct: float) -> float | None:
    """(annualizowany średni zwrot - stopa wolna od ryzyka) / annualizowana
    zmienność. None przy < 3 punktach lub zerowej zmienności (dzielenie
    przez 0)."""
```

Moduł korzysta z `statistics` (stdlib, czysty Python — nie `numpy`) i `math.sqrt`.

## B. Konfiguracja

- `config.yaml`: nowa opcja `risk_free_rate_pct: 3.0` w sekcji „wyniki” (obok istniejących),
  schema `risk_free_rate_pct: float`.
- `settings.py`: `"risk_free_rate_pct": float` w schema dict, `"risk_free_rate_pct": 3.0` w
  defaults — wzorem `concentration_alert_pct` (`settings.py:59,106`).

## C. `web.py::wyniki_get`

Po istniejącym bloku `if price_eur and eurpln_rate:` (gdzie już budowane jest
`daily_values` dla `twr()`), doliczyć:

```python
volatility_pct = risk_result = None
sharpe = analytics_risk.sharpe_ratio(daily_values, cfg["risk_free_rate_pct"])
volatility_result = analytics_risk.volatility_annualized(daily_values)
volatility_pct = volatility_result * 100 if volatility_result is not None else None
max_dd_result = analytics_risk.max_drawdown(daily_values)
max_dd_pct = max_dd_result * 100 if max_dd_result is not None else None
```

`max_drawdown()` liczony na `daily_values` **niezależnie od bloku `if price_eur and
eurpln_rate`**, bo nie potrzebuje dzisiejszej ceny (tak jak krzywa wartości i tabela
rok-po-roku wyżej w tej samej funkcji) — działa nawet gdy dzisiejszy poll jeszcze się nie
wykonał. `sharpe_ratio()`/`volatility_annualized()` zostają w bloku warunkowym, bo
potrzebują `cfg["risk_free_rate_pct"]` odczytanego tam, gdzie reszta configu (spójnie z
resztą funkcji, nie nowy powód).

Nowe zmienne przekazywane do `render_template("results.html", ...)`:
`sharpe=sharpe`, `volatility_pct=volatility_pct`, `max_dd_pct=max_dd_pct`.

## D. `templates/results.html`

Trzecia karta (po „Zwrot" i przed/po „Atrybucja zysku" — kolejność do ustalenia przy
implementacji wg naturalnego przepływu strony), wzorem istniejących:

```jinja
<div class="card">
  <div class="card-title">Ryzyko portfela</div>
  <div class="stats-row">
    {{ stat('Sharpe ratio', sharpe|round(2) if sharpe is not none else none, '') }}
    {{ stat('Zmienność (annualizowana)', volatility_pct|pct, '%') }}
    {{ stat('Maksymalny spadek (drawdown)', max_dd_pct|pct, '%') }}
  </div>
  <p class="muted">Metryki ryzyka liczone dla pojedynczej spółki pracowniczej są z natury
  gorsze (brak dywersyfikacji) niż dla zdywersyfikowanego portfela lub indeksu — punkt
  odniesienia, nie ocena.</p>
</div>
```

Dokładny markup/klasy do dopasowania do istniejącego stylu karty „Zwrot" przy
implementacji (ten sam `stat()` makro z `_macros.html`).

## Weryfikacja (ta sama procedura co każda poprzednia fala, `ROADMAP.md` sekcja „Weryfikacja”)

1. **TDD** — `tests/test_analytics_risk.py` przed `analytics/risk.py`. Kryteria twarde:
   - `max_drawdown()` na znanym ręcznie policzonym przykładzie (np. seria
     `[100, 120, 90, 110]` → drawdown `(90-120)/120 = -0.25`).
   - `volatility_annualized()`/`sharpe_ratio()` na serii ze znaną wariancją (skonstruowanej
     ręcznie, nie z produkcyjnych danych) porównane z ręcznym wyliczeniem `stdev * sqrt(252)`.
   - `sharpe_ratio()` i `volatility_annualized()` zwracają `None` przy < 3 punktach;
     `max_drawdown()` działa też na 1 punkcie.
   - `sharpe_ratio()` zwraca `None` przy zerowej zmienności (unikniecie `ZeroDivisionError`).
2. Cała suita `pytest` przed i po (punkt odniesienia: 986 testów na 0.15.0).
3. Sprawdzenie na realnych danych produkcyjnych przed wdrożeniem — policzyć oczekiwane
   Sharpe/volatility/max_drawdown ręcznie z `portfolio_history` (SQL bezpośrednio) i
   porównać z tym, co pokaże strona.
4. Playwright na realnym URL-u ingressu (1920px + 390px + tryb ciemny), screenshot **i**
   `browser_console_messages(error)`.
5. Wdrożenie: push → `gh release create` (`isDraft: false`) →
   `homeassistant.update_entity` na `update.nokia_tracker_update` → poll `ha_get_addon` →
   `ha_manage_addon(action="update")`. Nigdy cyklu uninstall/reinstall.
6. Sweep PII na diffie przed pushem.
7. Po wydaniu: `README.md` (tabela stron/encji — brak nowych encji MQTT w tej fali, ale
   opis strony `/wyniki` rośnie), `CHANGELOG.md`, adnotacja „WYDANE” w `docs/ROADMAP.md`
   przy 0.16.0 (wzorem 0.13.0/0.14.0/0.15.0).

## Pliki

| Nowe | Modyfikowane |
|---|---|
| `analytics/risk.py`, `tests/test_analytics_risk.py`, `docs/PLAN_KROK_32_ryzyko.md` | `web.py` (`wyniki_get`), `templates/results.html`, `config.yaml` (opcja + wersja), `settings.py`, `__init__.py` (wersja), `CHANGELOG.md`, `README.md`, `docs/ROADMAP.md` (adnotacja) |
