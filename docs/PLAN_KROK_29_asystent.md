# Krok 29 / 0.13.0 — Asystent: czat nad własnymi danymi (+ budżet i status AI)

## Context

`docs/ROADMAP.md` ma dokładnie jedną niezrealizowaną falę: **krok 29 — asystent czatu nad
własnymi danymi**. Wszystko przed nią jest live (0.12.0, 831 testów, ~60 encji MQTT, silniki:
FIFO/PIT-38/straty/wyniki/doradca planu). Fala jest ostatnia celowo — ma sens dopiero, gdy jest
o czym rozmawiać.

Problem, który rozwiązuje: dane są, ale rozłożone na 15 stron. Odpowiedź na „ile zapłacę podatku,
jeśli sprzedam dziś 500 akcji" wymaga dziś wejścia na `/lots`, wpisania liczby w formularz i
zrozumienia trzech polityk kosztu. Czat ma być skrótem do **istniejących** silników — bez
grama nowej matematyki.

**Kluczowe ograniczenie architektoniczne:** `ai/provider.py::analyze()` obsługuje wyłącznie
ustrukturyzowany JSON wg schematu — **nie ma pętli tool-calling i nie dorabiamy jej**. Stąd
trójstopniowość: AI rozpoznaje intencję → Python liczy → AI narratorem opisuje policzone.
Liczby renderuje Jinja z wyniku silnika, nigdy tekst modelu, więc halucynacja kwoty jest
strukturalnie niemożliwa, a nie „mało prawdopodobna".

### Druga część zakresu: budżet i status AI (dołożone po pytaniu użytkownika)

Zmierzone w tej sesji, nie założone:

- Lokalny router `freellmapi` (`192.168.0.106:3003`) jest **darmowy**, ale dzieli jeden globalny
  licznik `ai_max_calls_per_day = 40` z płatnymi ogniwami. Gorzej: `provider.py::analyze()`
  sprawdza limit **raz, przed pętlą łańcucha** — wyczerpanie puli przez płatne ogniwo blokuje
  też darmowe. To realny błąd, nie tylko niedogodność, i czat (2 wywołania na pytanie) uderzy
  w niego pierwszego dnia.
- `ratelimit.provider_status()` (circuit breaker, próg 3 porażki / cooldown 1800 s) i liczniki
  `ai_usage` **istnieją, ale nie mają żadnego konsumenta w UI** — widać je tylko przez sensor
  `ai_provider_active`. Karta „Stan systemu" obiecana w roadmapie przy 0.8.1 nigdy nie powstała.
- Router ma bogate API statystyk (trasy wyciągnięte z bundla frontendu): `/api/health`,
  `/api/health/check-all`, `/api/analytics/summary|timeline|by-model|by-key|errors`,
  `/api/fallback/token-usage|routing|penalty-inspector`, `/api/keys`. **Wszystkie zwracają
  401 „Authentication required"** bez auth, a `/api/auth/status` → `authenticated:false` przy
  `needsSetup:false` — czyli chodzi o sesję e-mail+hasło z panelu, nie o klucz `/v1`.
  Czy klucz Bearer też je otwiera — **niesprawdzone** (klasyfikator zablokował curl z kluczem
  w linii poleceń); rozstrzygamy to jednym requestem na starcie pod-kroku 3.
- `/v1/models` i `/v1/providers` to realne trasy na kluczu Bearer (401, nie 404) —
  `openai_compat.list_models()` już z pierwszej korzysta.

## Decyzje podjęte przed planowaniem (AskUserQuestion)

| Pytanie | Decyzja |
|---|---|
| Numer wersji | **0.13.0** teraz; 1.0.0 zarezerwowane na wydanie po pierwszym pełnym sezonie rozliczeniowym (wierne regule z roadmapy) |
| Zakres intencji | **8 z roadmapy + rozszerzenia** o silniki, których roadmapa nie wymieniła (straty z 0.11.0, koncentracja i optymalizator momentu z 0.10.0/0.11.0) |
| Budżet AI (na pytanie) | **2 wywołania, narracja wyłączalna** — nowe `ai_chat_narration_enabled`; przy wyczerpanym limicie automatyczny spadek do tabelki + zdania deterministycznego |
| Limity dzienne | **Per-provider** — darmowy lokalny router dostaje własny wysoki limit, płatne ogniwa zostają przy 40 |
| Statystyki routera | **Próbować istniejącym kluczem Bearer, bez nowych sekretów**; przy 401/timeout degradacja do danych lokalnych |
| Gdzie status | **Pasek nad czatem na `/asystent` + pełna karta „Stan AI" na `/ustawienia`** (domknięcie długu z 0.8.1) |
| Tryb pracy | Bezpośrednio, **TDD**, commit + pełny przebieg suite na pod-krok |

## Architektura czatu

```
pytanie  ──►  AI #1 (CHAT_INTENT_SCHEMA)  ──►  {intent, params}
                                                    │
                          walidacja paramów w Pythonie (klamrowanie, rok z danych)
                                                    │
                          rejestr HANDLERS[intent] ──► istniejący silnik (read-only)
                                                    │
                                     {ok, title, lines[], facts, detail_url}
                                        │                        │
                          Jinja renderuje lines[]        AI #2 (narracja PL)
                                        │                        │
                                        └──── strona /asystent ──┘
```

Kontrakt handlera jest **celowo tym samym kształtem**, co istniejące `/api/preview/*`
(`{"ok": bool, "lines": [{"label","value","unit","emphasis"}]}` — patrz `web.py::preview_espp`,
`preview_sale_timing`), więc `NT.initFormPreview()` w `static/app.js` renderuje wynik czatu bez
nowego kodu renderującego.

### Rejestr intencji (11 + `inne`) — każda mapuje na istniejącą funkcję

| Intencja | Silnik (istniejący) | Params |
|---|---|---|
| `podatek_ze_sprzedazy` | `tax/whatif.py::simulate_sale` | `quantity`, `price_eur?` |
| `ile_moge_sprzedac` | `tax/lots.py::open_lots` + `tax/grants.py::restricted_own_summary` | — |
| `kiedy_vesting` | `tax/grants.py::vesting_timeline` | `horizon?` |
| `ile_zarobilem` | `portfolio.py::position_values_auto` + `tax/policy.py::compute_all_policies` | `year?` |
| `dywidendy_w_roku` | `tax/pit38.py::annual_report` → `section_g` | `year` |
| `koszt_sprzedazy_teraz` | `advisor.py::forfeit_summary` / `forfeit_for_quantity` | `quantity?` |
| `porownanie_z_benchmarkiem` | `sensors.py::results_values` | — |
| `pit_za_rok` *(rozszerzenie)* | `tax/pit38.py::annual_report` (całość + `loss_carryforward`) | `year` |
| `straty_z_lat_ubieglych` *(rozszerzenie)* | `tax/losses.py::available_for_year` | `year?` |
| `koncentracja_majatku` *(rozszerzenie)* | `advisor.py::overview` → `concentration` | — |
| `kiedy_sprzedac` *(rozszerzenie)* | `advisor.py::optimize_sale_timing` | `quantity` |
| `inne` | — (deterministyczna odpowiedź „nie wiem, ale to jest na stronie X") | — |

Twarde reguły rejestru:
- **Tylko odczyt.** Żaden handler nie zapisuje do bazy. Egzekwowane testem, nie dobrą wolą:
  po każdym handlerze checksum liczności wszystkich tabel musi być identyczny.
- **Walidacja paramów przed silnikiem** — `quantity > 0`, rok z `web.py::_years_with_data`,
  brakujący rok → `tax_year`/bieżący. Model może wypluć bzdurę; silnik dostaje czystą liczbę.
- **Uczciwa porażka** — `InsufficientLotsError`/`CostBasisMissingError` lecą do
  `{"ok": False, "error": ...}` i są pokazywane jako komunikat. Nigdy zmyślona liczba zamiast błędu.
- **Nieznana intencja z modelu → `inne`**, nie wyjątek.

## Pod-kroki (jeden commit każdy, pełna suita po każdym)

### 1. Migracja v9 `chat_log` + trzy ustawienia
- `db.py`: `_MIGRATIONS` += `chat_log` (`id, created_at, question, intent, params_json,
  result_json, answer_pl, provider, ok, error`). **W tym samym commicie** podbić
  `tests/test_db.py::test_migrate_sets_user_version` (pinuje dokładny `PRAGMA user_version` —
  regresja znana z kroku 27) i dopisać `chat_log` do `test_migrate_creates_all_tables`.
- Przycinanie historii do ostatnich 200 wpisów przy zapisie (bez nowego joba).
- Nowe ustawienia pełnym sześciomiejscowym rytuałem — `ai_chat_enabled` (1),
  `ai_chat_narration_enabled` (1), `ai_max_calls_per_day_local` (500; 0 = bez limitu, zgodnie
  z istniejącą semantyką `allow()`): `nokia_tracker/config.yaml` (options+schema), `run.sh`
  (export), `main.py` (seed dict ~w. 107), `settings.py` (SETTINGS_TYPES+DEFAULTS),
  `templates/settings.html`, `web.py::settings_post`. Istniejący guard
  `set(DEFAULTS) == set(SETTINGS_TYPES)` w `test_settings.py` złapie pominięcie.

### 2. Budżet AI per-provider (naprawa realnego błędu)
- `ai/usage.py`: `calls_today(conn, provider=None)` / `tokens_today(conn, provider=None)`
  (filtr per provider, sygnatura wstecznie zgodna), `allow(conn, provider, max_per_day)`.
- `ai/provider.py::analyze()`: limit sprawdzany **w pętli, per ogniwo** — wyczerpanie puli
  płatnego ogniwa nie może blokować darmowego lokalnego. Limit dla `local` czytany
  z `ai_max_calls_per_day_local`, dla `gemini`/`anthropic` z dotychczasowego
  `ai_max_calls_per_day`. Gdy WSZYSTKIE ogniwa wyczerpane → dotychczasowy `AIProviderError`
  z jasnym komunikatem który limit padł.
- `ratelimit.py`: zapamiętanie ostatniego błędu per provider (`_last_error`, w pamięci procesu —
  ten sam wzorzec i ta sama świadoma ulotność co `_consecutive_failures`), do pokazania w UI.
- Testy: wyczerpany limit płatnego ogniwa → lokalne nadal obsługuje; `0` = bez limitu;
  ogniwo w cooldownie pomijane bez konsumowania limitu; `test_provider.py` zielony przed i po
  (scoring newsów i dzienna analiza korzystają z tej samej ścieżki — to nie jest kod czatu).

### 3. `ai/status.py` + karta „Stan AI" na `/ustawienia`
- `snapshot(conn, cfg) -> dict`: per ogniwo — wywołania i tokeny dziś, limit i ile zostało,
  stan obwodu z `ratelimit.provider_status()` + sekundy do końca cooldownu, ostatni błąd,
  czy jest klucz; plus aktywne ogniwo (`provider.active_provider()`).
- Osiągalność routera: `openai_compat.list_models()` (istniejąca funkcja, klucz Bearer) —
  liczba modeli + czy wybrany model wspiera `response_format`.
- **Rozstrzygnięcie niewiadomej z Contextu, jednym requestem na starcie tego pod-kroku:**
  `GET /api/health` i `/api/analytics/summary` kluczem Bearer. Jeśli 200 — dołożyć je do
  snapshotu (health providerów, zużycie tokenów po stronie routera). Jeśli 401 — sekcja
  `router_stats: None` z notką „wymagają logowania do panelu routera" i **żadnych nowych pól
  na hasła** (decyzja użytkownika).
- Odporność: timeout 3 s, wynik cache'owany 60 s (istniejąca tabela `http_cache` albo cache
  w pamięci procesu), każdy błąd sieci → `None`, nigdy wyjątek. **Strona musi się wyrenderować
  z wyłączonym routerem** — osobny test.
- Karta „Stan AI" w `templates/settings.html` (tabela per ogniwo) — domknięcie długu z 0.8.1.

### 4. Prompty i schematy (`ai/prompts.py`)
- `CHAT_INTENT_SCHEMA` — `{intent: enum(12 wartości), params: {...}, confidence}`,
  `additionalProperties: False` (wzorzec z `SCORE_NEWS_SCHEMA`).
- `chat_intent_prompt(question, context)` — kontekst minimalny: dzisiejsza data, lata z danymi,
  aktywna polityka kosztu. Bez danych portfela (to nie jest zadanie tego wywołania).
- `CHAT_NARRATION_SCHEMA` + `chat_narration_prompt(question, title, lines, facts)` —
  instrukcja „nie zmieniaj i nie dopisuj liczb; liczby są już wyrenderowane obok".
- `max_tokens` ≥ 1500 (zmierzone na freellmapi — niżej reasoning ucina JSON).
- Testy w `tests/test_prompts.py`: wszystkie intencje w enumie, brak liczb portfela w prompcie
  intencji, obecność klauzuli „nie zmieniaj liczb" w prompcie narracji.

### 5. `ai/chat.py` — rdzeń (największy commit, TDD)
- `HANDLERS: dict[str, Callable]` wg tabeli wyżej; handler = czysta funkcja
  `(conn, cfg, params, ctx) -> dict` z kontraktem `lines`.
- `recognize_intent()` (AI #1, degraduje do `inne`), `narrate()` (AI #2, pomijane gdy
  `ai_chat_narration_enabled == 0` albo gdy budżet ogniwa już nie przepuszcza),
  `fallback_sentence(result)` (deterministyczne zdanie z `lines`),
  `ask(conn, cfg, question)` (orkiestracja + wpis do `chat_log`).
- Degradacja bez AI (brak klucza / otwarty obwód / limit) — odpowiedź deterministyczna
  z linkiem do właściwej strony, nigdy wyjątek do użytkownika.
- Testy `tests/test_ai_chat.py`: po jednym na handler, wspólny test read-only dla wszystkich,
  ścieżki degradacji, walidacja paramów, nieznana intencja → `inne`, wpis do `chat_log`,
  narracja wyłączona → 1 wywołanie AI zamiast 2 (asercja na liczbie wywołań mocka).

### 6. Web: `/asystent`
- `GET /asystent` (historia z `chat_log` + formularz) i `POST /asystent` → `redirect(url_for(...))`
  (pełny fallback bez JS; POST-redirect-GET, żeby odświeżenie nie powtarzało pytania).
- `GET|POST /api/asystent` — JSON dla wersji z JS.
- `templates/assistant.html` + wpis do `NAV_GROUPS` w `base.html` (grupa **Portfel**, za „Plan").
- **Pasek statusu nad czatem** z `ai/status.py`: aktywne ogniwo, zużycie/limit dziś, ostrzeżenie
  o cooldownie, informacja gdy narracja jest wyłączona lub pominięta z braku budżetu.
- **Widoczne rozpoznanie**: chip „Zrozumiałem: podatek ze sprzedaży · 500 szt." nad odpowiedzią —
  błędne rozpoznanie musi być widać, a nie dawać po cichu odpowiedź na inne pytanie.
- Pod każdą odpowiedzią `tax_disclaimer()` z `_macros.html` i link „szczegóły na stronie X".
- Każda nowa trasa **musi** używać `url_for()` (`_IngressPrefixMiddleware`); zapis do `chat_log`
  pod `db.WRITE_LOCK`.
- Testy w `tests/test_web.py`: obie trasy, no-JS POST, JSON API, degradacja bez AI, render przy
  nieosiągalnym routerze, escaping tekstu z modelu (nie trafia do DOM jako HTML).

### 7. Pole szybkiego pytania na pulpicie
- Jeden input w `dashboard.html` submitujący do `/asystent` (GET z `?q=`), pod paskiem
  „Dziś warto wiedzieć" z kroku 28. Zero nowego JS-u poza istniejącym `initFormPreview`.

### 8. Dokumentacja i wydanie 0.13.0
- `README.md` (opis `/asystent`, tabela intencji, karta „Stan AI", nowe ustawienia),
  `CHANGELOG.md`, `docs/ROADMAP.md` (fala oznaczona jako wydana w 0.13.0 + notka, że 1.0.0
  czeka na sezon rozliczeniowy), `docs/PLAN_KROK_29_asystent.md` = kopia tego planu
  (`feedback_plans_as_md` — robiona **jako pierwszy krok implementacji**, przed kodem).
- Bump `nokia_tracker/config.yaml` **i** `nokia_tracker/nokia_tracker/__init__.py` → `0.13.0`.

**Zero nowych encji MQTT i zero nowej matematyki w tej fali** — czat jest warstwą UI nad
istniejącymi silnikami, status AI jest warstwą UI nad istniejącymi licznikami. (Świadoma
decyzja użytkownika: sensory `ai_calls_today`/`remaining` odrzucone jako niepotrzebne.)

## Weryfikacja

1. **TDD** — test przed implementacją każdego modułu; punkt odniesienia **831 testów** (0.12.0).
2. **`tax/` jak beton** — ta fala nie zmienia w nim nic; `pytest tests/test_tax_*.py` zielone
   przed i po, jako dowód że rejestr handlerów faktycznie tylko czyta.
3. **`test_provider.py` / `test_scoring.py` / `test_analysis.py` zielone przed i po pod-kroku 2** —
   to jedyny commit dotykający ścieżki używanej przez istniejące zadania AI.
4. **Test read-only** — snapshot `SELECT COUNT(*)` ze wszystkich tabel przed i po wywołaniu
   każdego handlera; jedyna dozwolona różnica to wiersz w `chat_log`.
5. **Zgodność ze stronami na realnych danych** — po wdrożeniu zadać przez `/asystent` pytania
   o podatek z konkretnej ilości, PIT za 2025 i przepadek dopasowania, i porównać co do grosza
   z `/lots`, `/pit38?year=2025` i `/plan` na produkcji (wzorzec z kroków 21/26: liczba
   przewidziana ręcznie, potem trafiona).
6. **Sprawdzenie budżetu na żywo** — po deployu potwierdzić w karcie „Stan AI", że lokalny router
   ma własny licznik i że kilka pytań pod rząd nie zjada puli płatnych ogniw.
7. **Playwright na realnym URL-u ingressu** (1920 px + 390 px), screenshot **i**
   `browser_console_messages(error)`. Ścieżka `ws_command:"supervisor/api"` → `ingress_session`
   → `document.cookie` (wyczyścić stare ciasteczko przed ustawieniem) → nawigacja do
   `ingress_entry`; próbować **przed** zastępczym proxy GET przez `ha_manage_addon`.
8. **Sweep PII na diffie przed pushem** — repo jest publiczne; fixtury pytań wyłącznie
   syntetyczne, żaden klucz API ani zrzut realnego `chat_log` nie może trafić do repo.
9. **Wdrożenie bezpieczną ścieżką** (add-on trzyma realne dane podatkowe): push →
   `gh release create v0.13.0` z potwierdzeniem `isDraft: false` → `homeassistant.update_entity`
   na `update.nokia_tracker_update` → poll `ha_get_addon` aż `version_latest == "0.13.0"` →
   `ha_manage_addon(action="update")`. **Nigdy** cyklu uninstall/remove_repository/add_repository —
   on kasuje SQLite (`reference_supervisor_git_addon_rebuild`).

## Ryzyka

| Ryzyko | Mitigacja |
|---|---|
| Zmiana w `ai/provider.py` psuje scoring newsów / dzienną analizę | Zmiana czysto addytywna (limit per ogniwo, domyślnie 40 dla płatnych); `test_provider/scoring/analysis` zielone przed i po |
| Złe rozpoznanie intencji daje pewnie brzmiącą odpowiedź na inne pytanie | Chip z rozpoznaną intencją i paramami nad odpowiedzią + pełny `chat_log` do debugowania |
| Odpytywanie routera spowalnia lub wywala stronę | Timeout 3 s, cache 60 s, każdy błąd → `None`; test renderowania przy nieosiągalnym routerze |
| Prywatne API panelu routera zmieni się bez zapowiedzi | Sięgamy tylko po `/api/health` i `/api/analytics/summary`, obie opcjonalnie i degradowalnie; rdzeń statusu to dane lokalne |
| Narracja AI wplecie własną liczbę w tekst | Liczby renderuje Jinja z `lines[]`; tekst modelu escapowany, osobny akapit — test na to |
| Handler przypadkiem coś zapisze (np. backfill kursu NBP po drodze) | Test checksumu liczności tabel dla wszystkich handlerów |
| Migracja v9 wywala pin `user_version` w `test_db.py` | Bump w tym samym commicie (regresja znana z kroku 27) |
