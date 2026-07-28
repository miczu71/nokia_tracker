# Nokia Tracker — krok 14: dopięcie vestingu (reconciliation + przypomnienia)

## Context

Po kroku 13.6 (parsowanie „Vested Matching Shares" + Withhold-to-Cover Typ A jako lotów)
sprzedaż użytkownika działa poprawnie, ale `/grants` wciąż pokazuje część transz jako
„zaległe — sprawdź wyciąg" zamiast rozwiązanych. Użytkownik zapytał wprost, dlaczego —
odpowiedź: **wyciągi FAKTYCZNIE to potwierdzają** (stąd w ogóle istnieją loty `matched`/`lti`,
które umożliwiły sprzedaż), ale świadomie NIE ustawialiśmy w kroku 13.6 `vests.status='vested'`,
bo pierwotnie wydawało się to wymagać kruchego dopasowania ilościowego, które (na danych 2023/2024)
NIE rozkłada się 1:1 między harmonogramem a saldem (starsze granty matching sprzed naszych 5
plików, plan „2019-2026" a pliki od 2022).

Głębsza analiza pokazuje, że dopasowanie PO ILOŚCI JEST bezpieczne, jeśli wymagamy **dokładnego
dopasowania I jednoznaczności** (dokładnie jeden kandydat po obu stronach) — nie zgadujemy,
tylko potwierdzamy, gdy się da, i milczymy (zostaje „zaległe"), gdy się nie da:
- 2023: harmonogram miał TYLKO wpis 7.33 (alokacja 2022-10-26); saldo „Vested Matching Shares"
  z tego samego dnia miało {8.21, 7.20, 9.09, 7.33} — dokładne dopasowanie 7.33↔7.33 jest
  jednoznaczne (pozostałe trzy nie mają odpowiednika w naszym harmonogramie - to starsze
  granty sprzed 2022, poza zasięgiem naszych plików - I TAK zostają nierozwiązane, poprawnie).
- 2024: analogicznie 33.36↔33.36 jednoznaczne.
- 24.42 (alokacja 2024-10-21) nie ma dokładnego odpowiednika w żadnym saldzie — poprawnie
  zostaje „zaległe" (uczciwie, bo naprawdę nie potrafimy tego potwierdzić z danych).
- LTI: 634 i 2100 dopasowują się dokładnie i jednoznacznie do dwóch transz RS Award.

To domyka krok 14 z BLUEPRINT.md (§5): „auto-lot w dniu uwolnienia" — realizowany nie przez
scheduler liczący od dzisiejszej daty (rzeczywista data uwolnienia bywa inna niż planowana,
zobaczyliśmy to na 9 lipca vs 5/6 lipca), tylko przez reconciliation po imporcie. Drugi kawałek
kroku 14 z BLUEPRINT — `vest_reminder_days` (powiadomienie przed nadchodzącą datą vestingu) —
wciąż nieużywana opcja w `config.yaml`, do zrobienia od zera.

## 1. Reconciliation: dopasuj loty do transz po dokładnej, jednoznacznej ilości

**Plik:** `nokia_tracker/nokia_tracker/tax/grants.py`

Schemat już ma to, czego potrzeba — `vests.lot_id INTEGER REFERENCES lots(id)` istnieje od
migracji v1, **zero zmian schematu**.

```python
_PROGRAM_TO_LOT_TYPE = {"espp": "matched", "lti": "lti"}


def reconcile_vesting(conn: sqlite3.Connection, today: str | None = None) -> int:
    """Dopasowuje loty `matched`/`lti` (utworzone z 'Vested Matching Shares'/Withhold Typ A,
    krok 13.6) do transz `vests` wciąż oznaczonych 'pending' — TYLKO gdy dopasowanie po
    (program, ilość) jest DOKŁADNE i JEDNOZNACZNE po obu stronach (dokładnie jeden nierozliczony
    lot danego typu o tej ilości I dokładnie jedna pasująca oczekująca transza). W przeciwnym
    razie transza zostaje 'pending' — uczciwie, bo nie potrafimy tego udowodnić z danych PDF
    (patrz diagnoza w docs/PLAN_KROK_14_vesting_reconcile.md: część historycznych dopasowań ESPP
    sprzed 2022 nigdy nie pojawia się w naszym harmonogramie, więc ich saldo nigdy się nie
    dopasuje - to jest OK, nie próbujemy zgadywać). Zwraca liczbę rozwiązanych transz."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")
    resolved = 0
    for program, lot_type in _PROGRAM_TO_LOT_TYPE.items():
        pending_vests = conn.execute(
            "SELECT v.* FROM vests v JOIN grants g ON g.id = v.grant_id "
            "WHERE g.program = ? AND v.status = 'pending' AND v.vest_date <= ?",
            (program, today)).fetchall()
        unlinked_lots = conn.execute(
            "SELECT * FROM lots WHERE lot_type = ? AND id NOT IN "
            "(SELECT lot_id FROM vests WHERE lot_id IS NOT NULL)", (lot_type,)).fetchall()
        for vest in pending_vests:
            matches = [l for l in unlinked_lots if abs(l["quantity"] - vest["quantity"]) < 1e-9]
            vest_matches = [v for v in pending_vests
                            if abs(v["quantity"] - vest["quantity"]) < 1e-9]
            if len(matches) == 1 and len(vest_matches) == 1:
                conn.execute(
                    "UPDATE vests SET status = 'vested', lot_id = ? WHERE id = ?",
                    (matches[0]["id"], vest["id"]))
                resolved += 1
    conn.commit()
    return resolved
```

**Wywołania** (ten sam wzorzec co `taxlots.backfill_missing_rates` — wołane z web route ORAZ
codziennego joba, patrz `nokia_tracker/nokia_tracker/tax/lots.py::backfill_missing_rates` i jego
wywołania w `web.py::lots_get()` + `main.py::backfill_nbp_rates`):
- `web.py::imports_upload()` — zaraz po `computershare_pdf.import_statement(...)`, pod tym samym
  `dbm.WRITE_LOCK`.
- `main.py::auto_import_pdf_share()` — analogicznie, po `import_statement`.
- Nowy codzienny job w `main.py` (patrz punkt 2 niżej, ten sam job robi oba zadania).

## 2. Przypomnienia `vest_reminder_days`

**Migracja v2** (`nokia_tracker/nokia_tracker/db.py`): `_MIGRATIONS` to lista literałów SQL
(obecnie jeden element — schemat v1, patrz `db.py:23`). Dopisać DRUGI element listy (nie
`.append()` w runtime — to statyczna lista w kodzie):
```python
_MIGRATIONS = [
    """
    -- ... (istniejący schemat v1, bez zmian) ...
    """,
    # v2 — krok 14: przypomnienia o vestingu
    """
    ALTER TABLE vests ADD COLUMN reminder_sent_at TEXT;
    """,
]
```
`migrate()` (bez zmian, już czyta `PRAGMA user_version` i stosuje tylko brakujące wersje —
`db.py:233-238`) automatycznie zastosuje tylko ten nowy fragment na istniejących instalacjach.

**`tax/grants.py`** — dopisać `timedelta` do istniejącego importu (`from datetime import
datetime, timedelta` — obecnie tylko `datetime`, patrz `grants.py:9`), potem nowa funkcja:
```python
def due_for_reminder(conn: sqlite3.Connection, vest_reminder_days: int,
                     today: str | None = None) -> list[dict]:
    """Transze 'pending' z vest_date w oknie [dziś, dziś+vest_reminder_days], którym jeszcze
    nie wysłano przypomnienia. Zwraca też grant_date/participation_description do treści
    powiadomienia."""
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")
    horizon = (datetime.strptime(today, "%Y-%m-%d")
              + timedelta(days=vest_reminder_days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT v.*, g.program, g.grant_date, g.natural_key FROM vests v "
        "JOIN grants g ON g.id = v.grant_id "
        "WHERE v.status = 'pending' AND v.reminder_sent_at IS NULL "
        "AND v.vest_date BETWEEN ? AND ?", (today, horizon)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["participation_description"] = (
            d["natural_key"].split("lti_grant:", 1)[-1]
            if d["program"] == "lti" and d["natural_key"] else None)
        result.append(d)
    return result


def mark_reminder_sent(conn: sqlite3.Connection, vest_id: int) -> None:
    conn.execute(
        "UPDATE vests SET reminder_sent_at = ? WHERE id = ?",
        (datetime.now().isoformat(), vest_id))
    conn.commit()
```

**`main.py`** — nowy codzienny job, dołączony do harmonogramu obok `backfill_nbp_rates`:
```python
def check_vest_reminders() -> None:
    """Codziennie: reconciliation (loty -> transze) + przypomnienie o nadchodzącym vestingu
    (cfg['vest_reminder_days'] przed datą, krok 14). Reconciliation też wywoływane od razu
    po każdym imporcie (web.py/auto_import_pdf_share) - ten job to siatka bezpieczeństwa,
    ten sam wzorzec co backfill_nbp_rates."""
    with dbm.WRITE_LOCK:
        c = dbm.get_conn(db_path)
        try:
            resolved = grantsm.reconcile_vesting(c)
            if resolved:
                logger.info("Reconciliation vestingu: rozwiązano %d transz", resolved)
            cfg = settingsm.get_settings(c)
            notify_service = cfg.get("notify_service", "")
            if notify_service:
                for vest in grantsm.due_for_reminder(c, cfg["vest_reminder_days"]):
                    label = (f"LTI ({vest['participation_description']})"
                            if vest["program"] == "lti" else "ESPP (dopasowanie)")
                    ha_client.notify(
                        notify_service.replace(".", "/", 1),
                        "Zbliża się vesting akcji Nokia",
                        f"{vest['quantity']:.4f} akcji {label} — data vestingu {vest['vest_date']}.")
                    grantsm.mark_reminder_sent(c, vest["id"])
        except Exception:
            logger.exception("Reconciliation/przypomnienia vestingu nieudane")
        finally:
            c.close()
```
`scheduler.add_job(check_vest_reminders, "cron", hour=6, minute=30)` (obok
`backfill_nbp_rates` o 6:15). Import `from . import grants as grantsm` już... **UWAGA:** `grants`
to nazwa modułu w `tax/`, w `main.py` trzeba `from .tax import grants as grantsm` (tak jak
w `web.py`).

**`web.py::imports_upload()`** — dopisać `grantsm.reconcile_vesting(conn)` po
`computershare_pdf.import_statement(...)`, pod istniejącym `dbm.WRITE_LOCK`. Ten sam import
`grantsm` już istnieje w `web.py` (dodany w kroku 13.5 dla `/grants`).

**`main.py::auto_import_pdf_share()`** — tak samo, `grantsm.reconcile_vesting(c)` po
`import_statement`.

## 3. Testy

**`tests/test_tax_grants.py`**:
- `reconcile_vesting`: unikalne dokładne dopasowanie (1 pending vest + 1 unlinked lot tej samej
  ilości/programu) → rozwiązane (`status='vested'`, `lot_id` ustawiony); dwie transze tej samej
  ilości → NIE rozwiązane (niejednoznaczność); dwa nierozliczone loty tej samej ilości → NIE
  rozwiązane; transza z `vest_date` w przyszłości (> today) → NIE rozwiązane mimo dokładnego
  dopasowania; transza już `status='vested'` → pomijana (idempotencja przy ponownym wywołaniu).
- `due_for_reminder`: transza w oknie `[today, today+N]` i `reminder_sent_at IS NULL` → zwrócona;
  transza poza oknem (za daleko w przyszłości LUB już w przeszłości) → pominięta; transza z już
  ustawionym `reminder_sent_at` → pominięta (nie przypominamy drugi raz).
- `mark_reminder_sent`: ustawia `reminder_sent_at`, transza znika z kolejnego `due_for_reminder`.

**`tests/test_computershare_pdf_import.py`** (mock `extract_layout_text`, wzorzec z kroku 13.6):
- Tekst z jedną transzą ESPP w harmonogramie (`_MATCHING_LINE`, qty=29.24) + wiersz „Vested
  Matching Shares" tej samej ilości (29.24) → po `import_statement` + `reconcile_vesting`
  transza ma `status='vested'` i `lot_id` wskazujący na nowo utworzony lot.

**`tests/test_computershare_pdf_real_files.py`** (bramkowane, real dane lokalnie):
- Pełny sekwencyjny import wszystkich 5 plików + `reconcile_vesting()` → dokładnie 4 transze
  rozwiązane (7.33, 33.36, 634, 2100); `/grants` (albo bezpośrednio `list_espp`/`list_lti_grouped`)
  pokazuje 24.42/29.24/28.99/17.37 (ESPP) i 633/633 (LTI 2027/2028) WCIĄŻ jako `overdue`/`pending`
  tam gdzie to poprawne (24.42 zaległe bez potwierdzenia, reszta to przyszłe daty).

**`tests/test_web.py`**: `/imports/upload` (z zamockowanym `import_statement`) woła też
`grantsm.reconcile_vesting` — test sprawdzający, że po uploadzie strona `/grants` pokazuje
rozwiązaną transzę bez dodatkowego requestu.

## Weryfikacja end-to-end

1. `pytest` pełny zielony przebieg (z i bez `--ignore` na testach bramkowanych realnymi plikami).
2. Bump wersji (0.1.3 → 0.1.4), commit, push, **normalny update przez Supervisora** (NIE cykl
   uninstall/reinstall — patrz incydent z poprzedniej sesji, `homeassistant.update_entity` na
   `update.nokia_tracker_update` + `ha_manage_addon(action="update")`, sprawdzone dziś działające
   bez utraty danych).
3. Użytkownik ponownie wgrywa 5 PDF-ów (idempotentne) — `/grants` powinno pokazać 634 i 2100 jako
   rozwiązane (nie „zaległe"), a 7.33/33.36 (ESPP) też rozwiązane; 24.42 i przyszłe transze zostają
   bez zmian, z wyjaśnieniem dlaczego (już wbudowanym w tę rozmowę).
4. Jeśli użytkownik ustawi `notify_service` w Ustawieniach i ma jakąś transzę w oknie
   `vest_reminder_days` — sprawdzić, że powiadomienie faktycznie przychodzi (albo poczekać na
   najbliższy prawdziwy przypadek, albo tymczasowo zmniejszyć `vest_reminder_days`/dodać testowy
   lot ręcznie do weryfikacji na żywo).
