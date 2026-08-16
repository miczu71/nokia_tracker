# Krok 33 — Asystent proaktywny / co-pilot (`nokia_tracker` 0.17.0)

## Context

`nokia_tracker` jest na **0.16.1** (wydane 2026-08-16, live). Kolejny punkt Roadmapy v2
(`docs/ROADMAP.md`, sekcja „0.17.0 — Asystent proaktywny (co-pilot)”): nowy dzienny job
scheduler'a, który spina już policzone gdzie indziej warunki (zbliżający się vesting,
niewykorzystana strata + zysk w bieżącym roku, zbliżająca się dywidenda) i wypycha je
jednym, złączonym push notification przez `notify.family`, narrowanym przez AI #2 z
istniejącego łańcucha czatu (0.13.0) — **zero nowej matematyki, zero nowej integracji AI**,
ten sam kontrakt „liczby liczy silnik, AI tylko narruje” co czat.

Cel: przejście asystenta z reaktywnego (czat czeka na pytanie) na częściowo proaktywne —
zgodnie z trendem rynkowym 2026 zidentyfikowanym w Roadmapie v2 (Cleo/Origin).

**Decyzje podjęte z użytkownikiem przed planowaniem (nie relitygować):**
1. **Jedna złączona wiadomość dziennie**, nie osobny push na każdy warunek — job zbiera
   0-3 aktywne warunki, narruje je razem, wysyła jednym `ha_client.notify()`.
2. **Tylko `notify.family`** w tej fali — most Telegram wciąż w budowie (czeka na token),
   Telegram zostaje w backlogu do podłączenia gdy będzie gotowy.
3. **Nowy `GET /api/preview/copilot`** (zero skutków ubocznych — nie woła AI, nie wysyła,
   nie konsumuje anti-spam cooldownu) do bezpiecznej weryfikacji na produkcji, tym samym
   wzorcem co istniejące `/api/preview/*`.

Plan przygotowany po researchu 3 równoległych agentów Explore + 1 agenta Plan +
niezależnej weryfikacji 6 najbardziej ryzykownych twierdzeń bezpośrednio w kodzie
(wszystkie potwierdzone TRUE, patrz sekcja „Decyzje projektowe”).

---

## Decyzje projektowe (i dlaczego)

**Nie brać `dbm.WRITE_LOCK` w `ai/copilot.py`.** `WRITE_LOCK` to zwykły `threading.Lock()`
(`db.py:21`), **nie reentrant** — zweryfikowane bezpośrednio w źródle. Job w `main.py`
trzyma go tak jak każdy inny job; gdyby `copilot.py` spróbował go wziąć drugi raz, to
gwarantowany deadlock w produkcji, którego żaden test by nie złapał (testy wołają
`copilot.run()` bez locka). `copilot.py` robi zwykłe `conn.execute()/commit()`, jak
`alerts.py::_fire` (wołane z `publish_sensors` pod lockiem).

**Wiadomość = narracja AI + deterministyczne zdania silnika, zawsze oba.** W czacie
`answer_pl` siedzi obok renderowanej przez Jinja tabelki `lines` — halucynacja liczby jest
widocznie sprzeczna z tabelką. Push to sam tekst — zdanie AI JEST całą wiadomością. Więc
wiadomość musi być: narracja (jeśli AI dostępne) + zawsze deterministyczne zdania z
silnika, nie „narracja ALBO fallback” jak w czacie.

**Vesting NIE używa `vest_reminder_days`.** Istniejący `main.py::check_vest_reminders`
(06:30) już wysyła zwykły tekst „Zbliża się vesting akcji Nokia” dokładnie na progu
`vest_reminder_days`. Użycie tego samego progu w co-pilocie o 07:15 dałoby DWA powiadomienia
o tej samej transzy tego samego ranka. Copilot dostaje własną stałą modułową
`_LOOKAHEAD_DAYS = 30` (styl `dashboard_insights.py` — hardkodowany próg, nie nowe
ustawienie) — przy 30-dniowym cooldownie odpala się raz, ~30 dni przed, czyli faktycznie
*proaktywnie*; 7-dniowe przypomnienie zostaje tym *pilnym*.

**Ten sam `_LOOKAHEAD_DAYS = 30` dla dywidendy + `copilot_min_interval_days = 30` jako
cooldown.** Daje: dokładnie jeden nudge na transzę vestingu, dokładnie jeden na wypłatę
dywidendy (raty Nokii ~90 dni odstępu), miesięczny nudge dla stałego warunku podatkowego.

**Vesting: `unvested_summary()` (read-only), NIE `due_for_reminder()`/`mark_reminder_sent()`.**
Te dwie są już konsumowane przez `check_vest_reminders` — drugie wywołanie
skonsumowałoby/zakłóciło tę kolejkę. Zweryfikowane: `next_vest_date`/`next_vest_qty` w
`unvested_summary()` (`tax/grants.py:510-521`) dotyczą WYŁĄCZNIE przyszłej (nie zaległej)
transzy `pending` — zaległe nigdy nie trafiają do `next_vest_*`.

**Reuse wprost, nie kopiowanie:**
- `dashboard_insights.today_worth_knowing()` — wołane z resztą sygnałów wyzerowaną
  (`change_pct_day=None`, drugi warunek `0.0`), zwraca dokładnie jedno zdanie — to jest
  reuse KODU (nie kopiuj-wklej), więc treść dashboardu i push nigdy się nie rozjadą.
  Test pinuje to wprost (`test_vesting_sentence_matches_dashboard_insights_wording`).
- `chat._market(conn)` i `chat._line(label, value, unit, emphasis)` — importowane wprost
  do `copilot.py` (private-ale-ten-sam-pakiet, z komentarzem czemu). To czyni „cienka
  warstwa nad ai/chat.py” dosłownie prawdziwym w kodzie, unika CZWARTEJ kopii
  `_PRIMARY_SYMBOL` (już zduplikowanej w main.py/web.py/ai/chat.py).
- `alerts.py::_allow_fire`/`_fire` — **promowane do publicznych** (`allow_fire`,
  `log_fired`), nie importowane jako private. Powód: `_allow_fire` parsuje `fired_at`
  przez `datetime.fromisoformat` z założeniem UTC dla naive; ręcznie napisany INSERT w
  copilot.py użyłby innego formatu czasu (np. `datetime.now().isoformat()` — naive
  LOKALNY, Europe/Warsaw) i po cichu przesunąłby cooldown o 1-2h. Wyciągnięcie zapisu
  RAZ (`log_fired`) eliminuje to ryzyko strukturalnie. `_allow_fire` nie ma żadnego
  wywołania spoza `alerts.py` — bezpieczny rename. `_fire` NIE jest reużywane wprost
  (wysyła własny natychmiastowy `ha_client.notify` per alert — złe dla „jeden złączony
  push”); `log_fired` to wyłącznie połowa zapisująca (INSERT + commit), bez MQTT/notify.

**Nowa funkcja promptu, nie reuse `chat_narration_prompt`.** Ta ostatnia hardkoduje
„Użytkownik zadał pytanie: {question}” — nieuczciwe wobec AI przy proaktywnym pushu (nikt
nie pytał). Nowa `prompts.copilot_narration_prompt(conditions)` reużywa **ten sam**
`CHAT_NARRATION_SCHEMA` (`{"answer_pl": str}`) i tę samą regułę „nie zmieniaj liczb” — to
jest właściwy poziom reuse (architektura/kontrakt), nie dosłowne wywołanie `chat.narrate()`.

**`alerts_log`, nie `chat_log`.** `chat_log.question` jest `NOT NULL` i tabela jest
przycinana do 200 wierszy przez `chat.py::_log()` — mieszanie źródeł byłoby mylące.
`alerts_log` (kind, severity, title, message, payload, fired_at) pasuje idealnie do
per-warunkowego trackingu, zero migracji.

**Bez zmian w `templates/settings.html` w tej fali.** Trzy nowe ustawienia zostają
options-only (jak `risk_free_rate_pct` z 0.16.0) — dodanie sekcji UI to dwa pliki
(`settings.html` + `web.py::settings_post`) i przejście Playwright dla funkcji czysto
schedulerowej. Kandydat do późniejszego UX passu, nie teraz.

**Znany dług do udokumentowania, nie naprawiać po cichu:** `risk_free_rate_pct` (krok 32)
trafił do `config.yaml`+`settings.py`, ale NIE do `run.sh`/`main.py::seed_from_options` —
opcja Supervisora jest martwa (DB default ją niesie). Copilot NIE może powtórzyć tego błędu
— `copilot_time` jest czytany z ENV przy budowie schedulera, więc brak eksportu w `run.sh`
przypiąłby 07:15 na stałe niezależnie od opcji. **Follow-up poza zakresem tej fali:**
naprawić `risk_free_rate_pct` osobno.

**„Ostatnie alerty” na pulpicie pokaże wpisy `copilot_*`.** `web.py:200-201` to
`SELECT * FROM alerts_log ORDER BY fired_at DESC LIMIT 5` bez filtra na `kind`. Decyzja:
zostawić — to uczciwy ślad tego, co zostało wypchnięte, `severity="info"`, tytuły czytelne.
(Alternatywa gdyby przeszkadzało: `WHERE kind NOT LIKE 'copilot_%'` w tym query.)

---

## A. `ai/copilot.py` (nowy)

Stałe: `_LOOKAHEAD_DAYS = 30`, `_MAX_TOKENS = 1500`, `KIND_VESTING = "copilot_vesting"`,
`KIND_TAX_LOSS = "copilot_tax_loss"`, `KIND_DIVIDEND = "copilot_dividend"`,
`_CERTAINTY_LABELS = {"confirmed": "potwierdzona", "announced": "zapowiedziana",
"estimated": "szacowana"}`.

- `_context(conn, today=None) -> dict` — `{"today", "year", "price_eur", "eurpln_rate"}`,
  rynek przez `chat._market(conn)`, `year = int(today[:4])` (rok kalendarzowy, celowo NIE
  `cfg["tax_year"]` — warunek to „zysk w BIEŻĄCYM roku”).
- `_days_until(date_str, today) -> int`
- `check_vesting(conn, cfg, ctx) -> dict | None` — `grantsm.unvested_summary(...)`,
  `None` gdy brak `next_vest_date` lub `_days_until(...) > 30`. Tytuł „Zbliżający się
  vesting” (celowo INNY niż tytuł joba 06:30). Zdanie =
  `dashboard_insights.today_worth_knowing(change_pct_day=None, next_vest_date=d,
  next_vest_qty=q, loss_available_pln=0.0, income_pln_this_year=0.0,
  today=ctx["today"])[0]`.
- `check_tax_loss(conn, cfg, ctx) -> dict | None` — `taxlosses.available_for_year(conn,
  cfg, ctx["year"])["total_remaining_pln"]` + `taxpolicy.compute_all_policies(conn, cfg,
  ctx["year"])[cfg.get("cost_basis_policy","own_only")]["income_pln"]`; odpala tylko gdy
  OBA > 0 (identyczny próg co `dashboard_insights.py:43`). Zdanie tą samą funkcją co wyżej,
  z argumentami vestingu `None`.
- `check_dividend(conn, cfg, ctx) -> dict | None` — `outlookm.calendar(conn, cfg,
  years_ahead=1, eurpln_rate=ctx["eurpln_rate"], today=ctx["today"])["next_event"]`
  (kwargs; `years_ahead=1` bo i tak patrzymy tylko 30 dni w przód). `None` gdy brak
  zdarzenia lub `_days_until(record_date) > 30`. Nowe zdanie (dwa warianty: z/bez
  `net_in_hand_pln`, gdy brak kursu EUR/PLN pokazać EUR zamiast PLN), z etykietą pewności z
  `_CERTAINTY_LABELS[ev["certainty"]]`.
- `CHECKS = (check_vesting, check_tax_loss, check_dividend)`
- `detect(conn, cfg, today=None) -> list[dict]` — stała kolejność, brak gatingu/zapisu.
- `narrate(conn, cfg, conditions) -> str | None` — `provider.analyze(conn, cfg,
  "copilot_narration", prompts.copilot_narration_prompt(conditions),
  prompts.CHAT_NARRATION_SCHEMA, _MAX_TOKENS)`; `except AIProviderError: return None`.
- `build_message(conditions, narration) -> str` — narracja (jeśli jest) + pusta linia +
  zawsze deterministyczne zdania (bullet `"• "` tylko gdy `len > 1`).
- `_title(conditions) -> str` — 1 warunek: `f"Nokia — {tytuł}"`; więcej:
  `f"Nokia — co-pilot ({n} sprawy)"`.
- `run(conn, cfg, today=None) -> dict` — `{"sent", "reason", "detected", "kinds", "title",
  "message", "narrated"}`, `reason ∈ {"sent","disabled","no_notify_service",
  "no_conditions","cooldown","notify_failed"}`. Kroki: (1) `copilot_enabled` falsy →
  disabled; (2) brak `notify_service` → no_notify_service; (3) `detect()` puste →
  no_conditions, zero wywołań AI/notify; (4) `allow_fire(conn, kind,
  copilot_min_interval_days*1440)` per warunek, puste → cooldown; (5) narracja (jeśli
  `ai_chat_narration_enabled`); (6) `ha_client.notify(...)` bez `url`/`clickAction`;
  (7) porażka → nic nie oznaczaj jako wysłane; (8) sukces → `log_fired(...)` per warunek.
- `preview(conn, cfg, today=None) -> dict` — zero skutków ubocznych.

## B. `ai/prompts.py`

Nowa `copilot_narration_prompt(conditions: list[dict]) -> str` po `chat_narration_prompt`
— reużywa `CHAT_NARRATION_SCHEMA` bez zmian. Framing: „Nikt o nic nie pytał — to Ty
zauważyłeś poniższe fakty”, ta sama klauzula „NIE ZMIENIAJ ANI NIE DOPISUJ ŻADNEJ LICZBY”.

## C. `alerts.py` (publiczne `allow_fire`/`log_fired`)

- `last_fired_at(conn, kind) -> str | None` — wyciągnięty `SELECT` z `_allow_fire`.
- `allow_fire(conn, kind, min_interval_minutes) -> bool` — rename `_allow_fire`.
- `log_fired(conn, kind, severity, title, message, payload=None) -> str` — wyciągnięty z
  `_fire`; `_fire` woła `log_fired`, ogon MQTT+notify bez zmian.

## D. Konfiguracja

| klucz | settings.py | default | config.yaml schema | run.sh env |
|---|---|---|---|---|
| `copilot_enabled` | `int` | `1` | `bool` | `COPILOT_ENABLED` |
| `copilot_time` | `str` | `"07:15"` | `str` | `COPILOT_TIME` |
| `copilot_min_interval_days` | `int` | `30` | `int` | `COPILOT_MIN_INTERVAL_DAYS` |

Wszystkie 5 punktów synchronizacji + `main.py::seed_from_options` (żeby ENV faktycznie
trafiał do bazy — krok, którego zabrakło przy `risk_free_rate_pct`).
`copilot_min_interval_days = 0` wyłącza anti-spam.

## E. `main.py`

Import `ai.copilot`; `seed_from_options` +3 wpisy; nowy `copilot_job()` po
`daily_digest_job` (WRITE_LOCK trzymany przez job, NIE przez copilot.py; `cfg = _ai_cfg(c)`);
parsowanie `COPILOT_TIME` jak `digest_time`; rejestracja po `rebuild_tax_losses_job`, bez
`day_of_week`.

## F. `web.py` — `/api/preview/copilot`

Nowa trasa GET po `preview_exit_plan`, `cfg = settingsm.get_settings(conn)` (bez
`_ai_keys()`), `?today=` opcjonalny z walidacją, zwraca `ai_copilot.preview(...)`.

---

## Weryfikacja

1. TDD — `tests/test_ai_copilot.py` (~30 testów) + `tests/test_prompts.py` (+3) +
   `tests/test_alerts.py` (+2) + `tests/test_web.py` (+2). RED przed implementacją, GREEN
   po. Oczekiwane: 1001 → ~1030+.
2. Pełna suita przed/po, zero regresji (szczególnie `test_alerts.py` po renamie).
3. Sprawdzenie na produkcji przez `GET /api/preview/copilot` (proxy + `python_transform`
   dla uniknięcia obcięcia) — zero realnego push podczas weryfikacji.
4. Playwright pominięty (brak zmian w templates).
5. Sweep PII przed pushem.
6. Wdrożenie: push → `gh release create` (published) → `update_entity` →
   `ha_manage_addon(action="update")` na `5f59858c_nokia_tracker`.
7. `CHANGELOG.md`, `README.md`, `docs/ROADMAP.md` (adnotacja WYDANE), wersja →`0.17.0`.

## Pliki

| Nowe | Modyfikowane |
|---|---|
| `ai/copilot.py`, `tests/test_ai_copilot.py`, `docs/PLAN_KROK_33_copilot.md` | `ai/prompts.py`, `alerts.py`, `main.py`, `web.py`, `config.yaml`, `settings.py`, `run.sh`, `CHANGELOG.md`, `README.md`, `docs/ROADMAP.md`, `tests/test_alerts.py`, `tests/test_prompts.py`, `tests/test_web.py` |

**Ryzyka:** `WRITE_LOCK` reentrancy (zaadresowane projektowo); anti-spam per-`kind` nie
per-zdarzenie (zamierzona semantyka `alerts.py`); `copilot_min_interval_days=30` × warunek
podatkowy stały = ~12 pushy/rok, akceptowalne i user-tunable.
