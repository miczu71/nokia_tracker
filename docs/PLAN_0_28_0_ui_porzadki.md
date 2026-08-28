# nokia_tracker 0.28.0 — porządki w UI: kolejność dywidend, wyjaśnienie polityk, koniec ręcznego wpisywania

## Kontekst

Cztery zgłoszenia użytkownika, po interview sprowadzone do jednego wydania z czterema etapami:

1. **Kolejność dywidend na „Stan konta"** — po rozwinięciu kafelka „Dywidendy netto" (ślad E8,
   `<details class="trace">`) lista idzie od najstarszej. Chce od najnowszej.
2. **Trzy polityki kosztu na /loty pokazują to samo** — pytanie, nie zgłoszenie buga.
   **Przyczyna ustalona empirycznie:** jedyna sprzedaż (id=1, 2025-10-27, 784 szt.) ma ustawione
   ręczne nadpisanie „Zgłoszona wartość" na `/sales`
   (`reported_revenue_pln=17631,72`, `reported_cost_pln=7500,66`).
   `tax/policy.py:89-95` — gdy nadpisanie istnieje, ta sama kwota kosztu wchodzi do **wszystkich
   trzech** polityk (arkusz użytkownika nie ma trzech wariantów, więc silnik nie zgaduje).
   Policzone na snapshocie bazy: bez nadpisania byłoby 1741,32 / 1692,93 / 910,88 zł podatku;
   z nadpisaniem wszystkie trzy pokazują 1924,90 zł.
   **Decyzja: dane zostają nietknięte** (deklaracja jest już złożona), UI ma to wyjaśnić.
3. **Koniec ręcznego wprowadzania danych** — jedynym źródłem prawdy mają być wyciągi Computershare;
   poza tym aplikacja robi już tylko symulacje. Ścieżka statementowa jest kompletna:
   importer tworzy loty i dywidendy (`importers/computershare_pdf.py`), a sprzedaż wykrytą w PDF
   księguje jednym klikiem `POST /imports/conflicts/<id>/confirm-sale`
   (`web/routes_dane.py:97-124`) — usunięcie formularzy niczego nie odcina.
4. **„Co jeśli sprzedam teraz" na /pit38 dubluje /wyplata** — i to gorszą matematyką: karta liczy
   uproszczony podatek pojedynczej sprzedaży (`taxwhatif.simulate_sale`), a `/wyplata` model
   roczny ze stratą z lat ubiegłych (`views/withdrawal.py`, `annual_net_for_quantity`).
   Zostaje jedna linia z linkiem.

Cel: aplikacja czyta dane wyłącznie z wyciągów, a wszystko poza tym jest symulacją — bez
formularzy, które mogą po cichu rozjechać saldo z wyciągiem (trzy takie incydenty w historii:
0.24.2, E9/0.25.0, 0.27.0).

Wersja docelowa: **0.28.0**, jedno wydanie na końcu, checkpoint po każdym etapie.

## Zrealizowane etapy

**Etap 1 — dywidendy od najnowszej.** `breakdown.py::_portfel_dividends_net()`:
`ORDER BY pay_date DESC, id DESC` zamiast `ORDER BY pay_date`. Kolejność `components` nie wpływa
na `close_sum()` (suma liczona niezależnie z `dividends_net_eur`). Zweryfikowane na kopii
produkcyjnej bazy: 2026-07-24 → 2023-02-20.

**Etap 2 — wyjaśnienie „dlaczego trzy polityki są takie same".** Nowa
`tax/policy.py::reported_override_summary(conn, cfg, year)` — zwraca
`{overridden, total, flattens_comparison, sale_dates}`, zero nowej matematyki (tylko odczyt
`sales.reported_cost_pln`). Nowe makro `_macros.html::policy_override_note(summary)`, wołane z
`/loty` (nad tabelą porównania) i `/pit38` (w karcie „Polityka kosztu"). Nic się nie renderuje,
gdy `overridden == 0`.

**Etap 3 — koniec ręcznego wprowadzania danych.** Usunięte trasy: `POST /lots`, `POST /lots/sell`,
`POST /portfolio` (`routes_portfel.py`), `POST /dividends` (`routes_dywidendy.py`), oraz ich
podglądy JSON `GET /api/preview/lot`, `/sale`, `/dividend`. `/sales` (usuwanie sprzedaży,
„Zgłoszona wartość") i `/dividends/harmonogram` (prognoza z ogłoszenia WZA) **zostają** — to
narzędzia korekty/prognozy, nie wprowadzania nowych danych z zewnątrz. `tax/lots.py`,
`tax/dividends.py` (silnik) bez zmian — nadal używane przez `importers/computershare_pdf.py` i
`imports_confirm_sale`. Szablony `lots.html`/`portfolio.html`/`dividends.html` stracily formularze
zapisu; `portfolio.html` przy braku lotów pokazuje `empty_state` z linkiem do Importów zamiast
formularza. Log w `computershare_pdf.py` (Withhold-to-Cover Typ B) wskazuje teraz przycisk
„Zatwierdź jako sprzedaż" na Importach, nie nieistniejące już `/lots/sell`.

Testy: `tests/conftest.py` dostał fixture `db_path` (wspólna ścieżka dla `conn`/`client`/`seed`)
i `seed` (`.lot()`, `.sale()`, `.dividend()`, `.position()` — wołają wprost `tax/lots.py`,
`tax/dividends.py`, `settings.py`, te same funkcje co usunięte trasy). Sześć plików
(`test_web_lots_sales.py`, `test_web_pit38.py`, `test_web_cash.py`, `test_web_dividends.py`,
`test_web_preview_api.py`, `test_web_portfolio.py`) przepisanych: zasiew przez `seed` zamiast
`client.post`, nowe testy regresji (405/404 na usuniętych trasach, brak `<form method="post">` w
HTML), testy czystego HTTP-zachowania usuniętych tras skasowane (silnikowe pokrycie zostaje w
`test_tax_lots.py`, web-owe przez wciąż istniejącą `imports_confirm_sale`).

**Etap 4 — /pit38 bez „Co jeśli sprzedam teraz".** Sfałdowany do Etapu 3, bo usunięcie
`preview_sale` (jego jedyny pozostały wołający) i usunięcie karty musiały iść razem — inaczej
`pit38.html` renderowałby `BuildError` na `url_for('preview_sale')`. `routes_podatki.py::pit38_get`
stracił blok `whatif_*`/`current_price` i porzucone importy (`sensors`, `_ids`, `taxwhatif`,
`taxlots`). `pit38.html` — karta zastąpiona jedną linią z linkiem do `/wyplata`. Bez zmian:
`tax/whatif.py`, sensor MQTT „co jeśli sprzedam teraz" (`sensors.py`), `/plan`, `/wyplata`.

## Weryfikacja końcowa i wydanie

1. `cd /config/addons/nokia_tracker/nokia_tracker && python3 -m pytest tests/ -q` — zero
   niepowodzeń, liczba testów zaraportowana wobec bazowych 1414.
2. Bump **0.28.0** w `nokia_tracker/config.yaml` **i** `nokia_tracker/nokia_tracker/__init__.py`.
3. CHANGELOG.md + README.md — opis czterech zmian (wiersze Portfel/Loty/Dywidendy/PIT-38 w
   tabeli stron już zaktualizowane w ramach implementacji).
4. `git push` do `miczu71/nokia_tracker`, **opublikowany** (nie draft) release `0.28.0`
   z pełnymi notatkami (nie auto-generowanymi).
5. Odświeżenie sklepu Supervisora (`homeassistant.update_entity` na `update.<addon>_update` + poll),
   update add-onu przez `ha_manage_addon`, weryfikacja `/info` że wersja == tag.
6. Playwright na produkcji (MCP, `--disable-gpu`), zrzuty do `/config/playwright/`
   + `browser_console_messages(error)` po każdym:
   - `/` — rozwinięty kafelek „Dywidendy netto": najnowsza dywidenda na górze;
   - `/loty` — brak „Dodaj lot"/„Zarejestruj sprzedaż", widoczne wyjaśnienie polityk;
   - `/portfolio` — brak formularza, karta z lotów renderuje się;
   - `/dywidendy` — brak „Dodaj wypłatę", harmonogram nadal edytowalny;
   - `/pit38` — brak karty whatif, jest link do Wypłaty; wyjaśnienie polityk;
   - `/wyplata` — nadal liczy (kierunek „Mam N akcji"), czyli ścieżka symulacji nie ucierpiała;
   - `/imports` — przycisk „Zatwierdź jako sprzedaż" nadal obecny (ścieżka statementowa żywa);
   - badge wersji w nav pokazuje **0.28.0** (weryfikacja cache-bustingu).
7. Sanity na produkcji: `/` pokazuje tę samą wartość portfela i to samo uzgodnienie z wyciągiem
   co przed wydaniem — ten refaktor nie miał zmienić ani jednej liczby poza kolejnością wierszy.

## Ryzyka

- **Utrata awaryjnej ścieżki wpisu.** Gdy wyciąg czegoś nie zawiera, nie da się już tego dopisać
  z UI. Świadoma decyzja użytkownika; `tax/lots.py`/`tax/dividends.py` zostają, więc awaryjnie
  zawsze zostaje `data_fixes.py` (wzorzec z 0.24.2/0.27.0).
- **Cache WebView.** Zmiana HTML+JS — obowiązkowe `?v=0.28.0` na statykach (już jest w `base.html`)
  i weryfikacja badge'a wersji na telefonie, nie tylko w Chromium.
- **`portfolio_post` znika, a `cfg.position_qty`/`avg_cost_eur` zostają w bazie i w `account.py`.**
  Gdyby ktoś miał zero lotów, aplikacja pokazywałaby zamrożone stare wartości — dlatego test
  „`/portfolio` przy pustej bazie" i `empty_state` odsyłający do Importów.
