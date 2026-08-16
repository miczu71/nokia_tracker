"""Kopia zapasowa i przywracanie danych (krok 24, docs/PLAN_KROK_24_backup.md).

Eksport = spójny snapshot SQLite (`Connection.backup()`, nie ingeruje w stan
żywego procesu) + `manifest.json` + czytelne CSV sześciu tabel podatkowych,
w jednym ZIP-ie. Przywracanie nigdy nie nadpisuje w ciemno: `restore_preview()`
liczy różnicę per tabela przed zapisem, `restore_apply()` wykonuje faktyczną
podmianę i musi być wołane pod `db.WRITE_LOCK` (patrz web.py) — ten sam
kontrakt co importer Computershare.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from . import db as dbm

DB_ENTRY = "nokia.db"
MANIFEST_ENTRY = "manifest.json"

# Sześć tabel podatkowych/portfelowych — jedyne, których dotyczy podgląd
# różnicy i eksport CSV. Reszta (quotes/news/ai_usage/...) jest odtwarzalna
# z zewnętrznych źródeł przy najbliższym pollu, więc nie jest "danymi
# użytkownika" w sensie, w jakim są nimi loty/sprzedaże/dywidendy.
_CSV_TABLES: dict[str, list[str]] = {
    "lots": ["id", "acquired_date", "lot_type", "quantity", "price_eur", "fee_eur",
              "nbp_rate", "nbp_rate_date", "cost_pln", "grant_id", "source",
              "natural_key", "qty_remaining", "notes"],
    "sales": ["id", "sale_date", "quantity", "price_eur", "fee_eur", "nbp_rate",
               "nbp_rate_date", "revenue_pln", "notes"],
    "sale_allocations": ["id", "sale_id", "lot_id", "quantity", "cost_pln", "revenue_pln"],
    "grants": ["id", "program", "grant_date", "declared_amount_eur", "quantity",
                "match_pct", "natural_key", "notes"],
    "vests": ["id", "grant_id", "vest_date", "quantity", "status", "lot_id", "natural_key"],
    "dividends": ["id", "pay_date", "gross_per_share_eur", "quantity", "gross_eur",
                   "withholding_pct", "withholding_paid_eur", "net_received_eur",
                   "nbp_rate", "nbp_rate_date", "gross_pln", "pl_tax_due_pln",
                   "reinvested_lot_id", "natural_key", "notes"],
    # krok 30 (0.14.0): ogłoszony harmonogram dywidend — dane wpisane ręcznie przez
    # użytkownika (WZA), nie odtwarzalne z żadnego zewnętrznego źródła.
    "dividend_schedule": ["id", "fiscal_year", "instalment", "record_date",
                            "payment_date", "ex_date", "gross_per_share_eur",
                            "currency", "dates_confirmed", "announced_on", "source",
                            "status", "matched_dividend_id", "notes"],
}


class IncompatibleBackupError(Exception):
    """Kopia zapasowa jest z nowszej wersji schematu niż to wydanie dodatku."""


def _table_to_csv(conn: sqlite3.Connection, table: str, columns: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    cols_sql = ", ".join(columns)
    for row in conn.execute(f"SELECT {cols_sql} FROM {table} ORDER BY id"):
        writer.writerow(row)
    return buf.getvalue()


def _build_manifest(conn: sqlite3.Connection) -> dict:
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in _CSV_TABLES
    }
    return {
        "app_version": __version__,
        "schema_version": conn.execute("PRAGMA user_version").fetchone()[0],
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "row_counts": counts,
    }


def export_zip(db_path: str) -> bytes:
    """Spójny zrzut bieżącej bazy jako ZIP w pamięci (bytes), gotowy do
    strumieniowania z Flaska lub zapisu na dysk (nocny snapshot)."""
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = Path(tmp) / "snapshot.db"
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(str(snapshot_path))
        src.backup(dst)
        dst.close()
        src.close()

        conn = sqlite3.connect(str(snapshot_path))
        conn.row_factory = sqlite3.Row
        manifest = _build_manifest(conn)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot_path, DB_ENTRY)
            zf.writestr(MANIFEST_ENTRY, json.dumps(manifest, indent=2, ensure_ascii=False))
            for table, columns in _CSV_TABLES.items():
                zf.writestr(f"{table}.csv", _table_to_csv(conn, table, columns))
        conn.close()
        return buf.getvalue()


def _extract(zip_bytes: bytes) -> tuple[dict, bytes]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        manifest = json.loads(zf.read(MANIFEST_ENTRY))
        db_bytes = zf.read(DB_ENTRY)
    return manifest, db_bytes


def _check_schema_compatible(manifest: dict) -> None:
    if manifest["schema_version"] > dbm.SCHEMA_VERSION:
        raise IncompatibleBackupError(
            f"Kopia pochodzi z nowszej wersji schematu (v{manifest['schema_version']}) "
            f"niż obsługiwana przez to wydanie dodatku (v{dbm.SCHEMA_VERSION}) — "
            "zaktualizuj dodatek przed przywróceniem tej kopii.")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def restore_preview(current_db_path: str, zip_bytes: bytes) -> dict:
    """Różnica per tabela PRZED zapisem — ile wierszy przybędzie/zniknie po
    przywróceniu. Nie modyfikuje żadnej z baz.

    Kopia starsza niż bieżące wydanie może nie mieć jeszcze jakiejś tabeli z
    `_CSV_TABLES` (np. `dividend_schedule` dodane w kroku 30/v10) — traktujemy jej
    brak jak pustą tabelę po stronie kopii, zamiast wywalać się `OperationalError:
    no such table`. `_check_schema_compatible` wyżej odrzuca tylko kopie z NOWSZEGO
    schematu niż to wydanie — ten guard pokrywa odwrotny, częstszy przypadek."""
    manifest, db_bytes = _extract(zip_bytes)
    _check_schema_compatible(manifest)

    with tempfile.TemporaryDirectory() as tmp:
        incoming_path = Path(tmp) / "incoming.db"
        incoming_path.write_bytes(db_bytes)
        incoming = sqlite3.connect(str(incoming_path))
        current = sqlite3.connect(current_db_path)

        diff = {}
        for table in _CSV_TABLES:
            current_ids = {r[0] for r in current.execute(f"SELECT id FROM {table}")}
            if _table_exists(incoming, table):
                incoming_ids = {r[0] for r in incoming.execute(f"SELECT id FROM {table}")}
            else:
                incoming_ids = set()
            diff[table] = {
                "added": len(incoming_ids - current_ids),
                "removed": len(current_ids - incoming_ids),
                "unchanged": len(current_ids & incoming_ids),
            }
        incoming.close()
        current.close()

    return {"manifest": manifest, "diff": diff}


def restore_apply(db_path: str, zip_bytes: bytes) -> dict:
    """Podmienia CAŁĄ bazę na zawartość ZIP-a i migruje do bieżącego schematu.

    Wołać wyłącznie pod `db.WRITE_LOCK` — patrz web.py. Usuwa sierocone
    -wal/-shm poprzedniej bazy, żeby WAL nowego pliku nie zmieszał się ze
    starym sidecarem po podmianie."""
    manifest, db_bytes = _extract(zip_bytes)
    _check_schema_compatible(manifest)

    for suffix in ("-wal", "-shm"):
        stale = db_path + suffix
        if os.path.exists(stale):
            os.remove(stale)

    Path(db_path).write_bytes(db_bytes)

    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    conn.close()

    return manifest
