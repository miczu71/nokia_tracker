# Plan krok 24 — kopia zapasowa i przywracanie danych (0.8.1)

Pierwsza fala z `docs/ROADMAP.md`. Chroni realne dane podatkowe (5 lat wyciągów,
zaksięgowane sprzedaże, dywidendy) — historia tego dodatku zna dwa przypadki
wyczyszczenia `/data` przy cyklu przeinstalowania (`reference_supervisor_git_addon_rebuild`).

## Zakres

1. **`backup.py`** (nowy moduł, czyste funkcje, testowalne bez Flaska/schedulera):
   - `export_zip(db_path) -> bytes` — spójny snapshot przez `sqlite3.Connection.backup()`
     na osobnym, tymczasowym pliku (nie ingeruje w stan `conn`/WAL żywego procesu), plus
     `manifest.json` (`app_version`, `schema_version` = `PRAGMA user_version`, `exported_at`,
     `row_counts` per tabela) i czytelne CSV sześciu tabel podatkowych
     (`lots`, `sales`, `sale_allocations`, `dividends`, `grants`, `vests`) — w jednym ZIP-ie.
   - `restore_preview(current_db_path, zip_bytes) -> dict` — rozpakowuje przychodzącą bazę do
     tymczasowego pliku, porównuje zbiory `id` per tabela (te same sześć tabel) między bazą
     bieżącą a przychodzącą: `added` (pojawi się po przywróceniu), `removed` (zniknie),
     `unchanged`. Blokuje, gdy `manifest["schema_version"]` jest nowszy niż
     `db.SCHEMA_VERSION` tego wydania (backup z przyszłej wersji dodatku) —
     `IncompatibleBackupError`. Backup ze starszym schematem jest dozwolony (dociągnie go
     `migrate()` po przywróceniu).
   - `restore_apply(db_path, zip_bytes) -> dict` — **wywoływane wyłącznie pod
     `db.WRITE_LOCK`** (ten sam kontrakt co wszystkie zapisy w `main.py`/`web.py`):
     podmienia plik bazy danych na zawartość ZIP-a, usuwa sierocone `-wal`/`-shm` z poprzedniej
     bazy (inaczej WAL nowego pliku mógłby się zmieszać ze starym sidecarem), po czym
     `dbm.migrate()` dociąga do bieżącego schematu. Zwraca manifest przywróconej kopii.
   - `db.py` dostaje jedną nową stałą: `SCHEMA_VERSION = len(_MIGRATIONS)` — dziś liczone
     ad-hoc w teście jako `6`; reszta modułów (w tym `backup.py`) ma się do czego odwołać
     zamiast duplikować tę liczbę.

2. **Trasy w `web.py`** (nowa grupa „Dane” obok Importów):
   - `GET /dane/eksport.zip` — strumieniuje wynik `export_zip()`, nagłówki
     `Content-Disposition: attachment; filename=nokia_tracker_<data>.zip`, `no-store` (to samo
     traktowanie jak reszta odpowiedzi tego add-onu, patrz nagłówek modułu `web.py`).
   - `GET /dane` — strona z przyciskiem eksportu, formularzem uploadu ZIP-a i (jeśli w sesji
     jest wynik podglądu) tabelą różnicy + przyciskiem „Potwierdź przywrócenie”.
   - `POST /dane/import/preview` — przyjmuje upload, woła `restore_preview()`, renderuje diff
     bez zapisu (ten sam wzorzec co `/api/preview/*` z kroku 18 — tylko odczyt, zero zapisu do
     bazy). Plik tymczasowo trzymany w `/data/tmp_restore/<token>.zip` (nie w sesji Flask — ZIP
     z pełną bazą jest za duży na ciasteczko), token w formularzu potwierdzenia.
   - `POST /dane/import/confirm` — pod `WRITE_LOCK`, woła `restore_apply()` na zapisanym
     tokenie, usuwa plik tymczasowy, redirect z komunikatem sukcesu. Odrzuca nieznany/wygasły
     token (chroni przed CSRF-podobnym odtworzeniem cudzego uploadu).
   - Karta „Stan systemu” na `/ustawienia`: ostatni udany fetch per provider (z `http_cache`),
     stan circuit breakerów AI (`ai_usage`/istniejące liczniki), liczba nierozstrzygniętych
     `import_conflicts`, data i rozmiar ostatniej kopii (czytane z katalogu snapshotów).

3. **Nocny auto-snapshot w `main.py`:**
   - `scheduler.add_job(nightly_backup_job, "cron", hour=4, minute=0)` — zapisuje
     `export_zip()` do `${BACKUP_SHARE}/backup/nokia_YYYY-MM-DD.zip` (`/share` już zamontowane,
     `map: share:rw`, wzorzec identyczny z `auto_import_pdf_share`), potem usuwa pliki starsze
     niż 14 dni w tym katalogu.

## Co świadomie POZA tym krokiem

- Żadnego scalania/merge przy przywracaniu — to pełna podmiana bazy, nie import przyrostowy.
  Merge dwóch baz to inny problem (i inny poziom ryzyka), nieproszony w roadmapie.
- Restore nie próbuje zatrzymywać/restartować schedulera z poziomu Flaska — `WRITE_LOCK` już
  serializuje z każdym jobem, to wystarcza (żaden dotychczasowy krok tego dodatku nie robił
  nic bardziej wyrafinowanego przy równie inwazyjnych zmianach schematu).
- Szyfrowanie/hasło na ZIP-ie — plik i tak zawiera dane, które już są na dysku add-onu bez
  szyfrowania; eksport nie pogarsza tego stanu. Możliwe do dodania później, jeśli użytkownik
  zacznie trzymać eksporty poza `/share`.

## Weryfikacja

- TDD: `tests/test_backup.py` — testy dla wszystkich trzech funkcji na prawdziwych plikach
  SQLite w `tmp_path` (nie mocki), w tym: eksport zawiera wszystkie trzy wpisy w ZIP-ie z
  poprawnym `schema_version`; podgląd poprawnie liczy `added`/`removed`/`unchanged` na dwóch
  różniącymi się bazach; przywrócenie starszego schematu kończy się na bieżącym
  `PRAGMA user_version`; przywrócenie z `schema_version` wyższym niż bieżący podnosi
  `IncompatibleBackupError`; przywrócenie faktycznie zmienia zawartość pliku na dysku (nie
  tylko w pamięci).
- `pytest` — cała suita zielona przed i po (punkt odniesienia: 632 testy z 0.8.0).
- Live: eksport i pełny cykl eksport→import na produkcyjnym add-onie **nie jest bezpieczny do
  przetestowania z tej powłoki** (brak sposobu na czyste odtworzenie realnych danych po teście,
  ta sama zasada co przy `/lots` w kroku 12) — zamiast tego: (a) live proxy GET na `/dane`
  potwierdza, że strona się renderuje i przycisk eksportu prowadzi do poprawnego nagłówka
  `Content-Disposition`, (b) pełny cykl eksport→podgląd→przywrócenie zweryfikowany testami
  integracyjnymi na syntetycznych danych, (c) użytkownik może sam wykonać jeden realny cykl
  eksport→import na własnych danych, jeśli chce dodatkowej pewności — nie robię tego bez
  wyraźnej zgody.
