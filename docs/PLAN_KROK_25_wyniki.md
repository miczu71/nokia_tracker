# Plan krok 25 — Wyniki: XIRR, krzywa wartości, atrybucja, benchmark (0.9.0)

Druga fala z `docs/ROADMAP.md`. Największa merytoryczna dziura dodatku: dziś jest tylko
`unrealized_pnl_pct`/`total_return_pct` (punktowe), zero miary zwrotu w czasie i zero
rozbicia na to, co go faktycznie napędza (kurs akcji / dopłata ESPP / efekt EUR-PLN).

## Zakres

### 1. Warunek wstępny — gęsta seria kursów NBP (zrobione)

`fx_nbp.backfill_range(conn, start, end)` — dzieli okno na kawałki ≤367 dni, zapisuje
KAŻDĄ publikację NBP kluczowaną własną `effective_date` (nie leniwie per zapytanie
podatkowe jak `rate_on_or_before`). Krzywa wartości portfela czyta „ostatni kurs ≤ D"
czystym `SELECT ... ORDER BY effective_date DESC LIMIT 1` — bez sieci per dzień.

### 2. `portfolio_history` (migracja v7)

```sql
CREATE TABLE portfolio_history (
    date TEXT PRIMARY KEY,
    position_qty REAL NOT NULL,
    price_eur REAL,
    eurpln_rate REAL,
    market_value_eur REAL,
    market_value_pln REAL
);
```

Materializowana, w pełni przeliczana od zera przy każdym `rebuild()` (DELETE + INSERT) —
tańsze i bezpieczniejsze niż różnicowe update'y przy imporcie/korekcie starych lotów.

### 3. `analytics/history.py::rebuild(conn, instrument_id) -> int`

Rekonstrukcja **bez sieci** — czyta wyłącznie to, co już jest w bazie (loty, alokacje
sprzedaży, `quotes`, gęste `nbp_rates`):

1. Zdarzenia ilości: `+quantity` w dniu `lots.acquired_date` (WSZYSTKIE typy lotu —
   `own`/`matched`/`lti`/`dividend_drip`, ta sama definicja `position_qty` co
   `portfolio.lots_based_position_values()`), `-quantity` w dniu `sales.sale_date` dla
   każdej `sale_allocations`. Posortowane, sumowane kroczaco.
2. Dla każdego dnia z `quotes.closes_in_range(instrument_id, "daily")` **od pierwszego
   nabycia w górę**: `position_qty` = suma zdarzeń ≤ D, `market_value_eur = qty * close`,
   kurs NBP = ostatni ≤ D z `nbp_rates`, `market_value_pln` = pochodna (albo `NULL`, gdy
   kurs jeszcze niedostępny — nigdy nie zgadujemy).
3. Wiersz tylko w dniach z notowaniem (giełda w Helsinkach zamknięta = brak świecy = brak
   wiersza) — stąd „~1300 wierszy" na 5 lat (dni sesyjne), nie 1825 dni kalendarzowych.

### 4. `analytics/returns.py::xirr()` / `twr()`

**Przepływy pieniężne dla XIRR na własnych wpłatach** (jedna, jawna definicja — inne
narzędzia tego nie rozróżniają, więc trzeba było zdecydować samemu):

| Zdarzenie | Znak | Kwota (EUR) |
|---|---|---|
| Lot `own` nabyty | `-` (wydatek) | `quantity*price_eur + fee_eur` |
| Lot `matched`/`lti`/`dividend_drip` nabyty | brak przepływu | akcje za darmo, nie gotówka — wchodzą tylko do wartości końcowej |
| Sprzedaż | `+` (wpływ) | `quantity*price_eur - fee_eur` |
| Dywidenda **niereinwestowana** (`reinvested_lot_id IS NULL`) | `+` (wpływ) | `net_received_eur` |
| Dywidenda reinwestowana (DRIP) | brak przepływu | pieniądze zostały w portfelu jako nowy lot |
| Dziś | `+` (wartość końcowa) | `position_qty_dziś * cena_dziś` |

`xirr()` = Newton (start r=0.1) z bisekcją jako fallback przy braku zbieżności (czysty
Python, zero `numpy`/`scipy` — `BLUEPRINT` §1 wyklucza je na musl/armv7).

**TWR** (time-weighted, neutralizuje moment wpłat — jedyna miara uczciwie porównywalna z
indeksem): dzienne stopy zwrotu z `portfolio_history` między kolejnymi dniami, korygowane
o przepływ tego dnia (`r_dzień = (V_koniec − V_początek − CF_dzień) / V_początek`),
łańcuchowo mnożone `Π(1+r_dzień) − 1`.

### 5. `analytics/attribution.py::decompose()`

Rozbicie całkowitego zysku w PLN na pięć składników — **kryterium akceptacji jest
twarde**: suma składników musi się równać całkowitemu zyskowi z dokładnością do grosza,
inaczej to ozdobnik, nie liczba (test to sprawdza wprost):

(a) zmiana kursu akcji (na stałym EUR/PLN), (b) dopłata ESPP 50% (wartość lotów `matched`
w dniu nabycia), (c) akcje LTI (wartość lotów `lti` w dniu nabycia), (d) dywidendy netto
(gotówka + DRIP), (e) efekt walutowy EUR/PLN (reszta — różnica między zyskiem w EUR
przeliczonym po kursie początkowym i końcowym). Świadomie licząc (e) jako resztę
("plug"), a nie osobną formułą — to jedyny sposób, żeby suma zawsze się zgadzała co do
grosza z definicji, zamiast zaokrągleń psujących rekoncyliację.

### 6. `analytics/benchmark.py::counterfactual()`

„Gdyby te same wpłaty (co do dnia i kwoty — te same przepływy `own`, co w XIRR) poszły w
OMXH25 / Ericsson zamiast Nokii": symuluje zakup jednostek benchmarku po cenie z dnia
każdej rzeczywistej wpłaty `own`, wycenia dzisiejszą wartość po dzisiejszej cenie
benchmarku. Dzienne notowania obu już backfillowane 5 lat w `quotes` (0.1.0) — zero
nowych źródeł danych.

### 7. UI i sensory

Nowa strona `/wyniki`: krzywa wartości (Chart.js, PLN/EUR, zakresy jak na pulpicie),
kafelki XIRR/TWR/zysk całkowity, wykres słupkowy atrybucji, krzywa benchmarku na tym
samym wykresie, tabela zwrotów rok po roku. 4 nowe sensory MQTT: `xirr_own_pct`,
`twr_pct`, `fx_effect_pln`, `benchmark_omxh25_counterfactual_pln`.

Nowe joby schedulera: `backfill_nbp_range_job` (jednorazowy backfill 5 lat przy starcie,
jeśli `nbp_rates` puste w tym oknie, plus nocne domykanie ostatnich dni) i
`rebuild_portfolio_history_job` (nocnie, po `backfill_nbp_rates`).

## Co świadomie POZA tym krokiem

- Brak per-lot IRR — tylko poziom całego portfela (per-lot to inny, znacznie droższy
  problem: FIFO miesza loty, więc "IRR jednego lota" nie ma jednoznacznej definicji bez
  arbitralnej alokacji sprzedaży).
- TWR liczone dziennie z `portfolio_history`, nie miesięcznie — prostsze, bo dane już są
  dzienne; miesięczna agregacja to czysta prezentacja, może dojść w 0.12.0 (UX).
- Brak wykresu porównania SEKTOROWEGO (inne spółki telekomowe) — nie proszone, nie ma
  źródła danych bez nowego API.

## Weryfikacja

- TDD: `test_fx_nbp.py` (`backfill_range`, zrobione), `test_analytics_history.py`,
  `test_analytics_returns.py`, `test_analytics_attribution.py`, `test_analytics_benchmark.py`
  — każdy moduł testowany na realnych obliczeniach (nie mockach), w tym: `xirr()` na znanym
  przepływie z arkusza kalkulacyjnego jako punkt odniesienia; `attribution.decompose()`
  suma == total z dokładnością 0.01 PLN na kilku różnych scenariuszach (sam zysk, sama
  strata, mix dywidend gotówkowych i DRIP); `history.rebuild()` wartość na dzień T zgodna
  z `position_values()` liczonym niezależnie dla tego samego dnia.
- `pytest` — cała suita zielona przed i po (punkt odniesienia: 645 testów z 0.8.1).
- Live: po backfillu na produkcji sprawdzić ręcznie XIRR/TWR na rzeczywistych danych
  użytkownika i porównać z ręcznym przeliczeniem na 2-3 punktach kontrolnych przed
  deployem (wzorzec z kroku 21).
- Playwright na `/wyniki` (1920/390/dark), screenshot + `browser_console_messages(error)`.
