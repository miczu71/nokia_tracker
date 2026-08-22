"""Kontekst aplikacji przekazywany modułom tras (`register_*_routes(app, ctx)`).

Zawiera WYŁĄCZNIE stan związany z konkretną instancją aplikacji: ścieżkę do
bazy i fabrykę połączeń. To, co da się policzyć z ENV albo z argumentów
(`_ai_keys`, `_backup_dir`, `_cleanup_stale_restore_files`), zostaje zwykłą
funkcją modułową w `_helpers.py`/`routes_dane.py` — wepchnięte tutaj tylko
po to, żeby "wszystko było w jednym miejscu", zamieniłoby dwa jawne pola w
worek zależności."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .. import db as dbm


@dataclass(frozen=True)
class AppContext:
    db_path: str

    def conn(self) -> sqlite3.Connection:
        return dbm.get_conn(self.db_path)
