"""The local store: one SQLite file in the data folder, ``bailey.db``.

It holds what Bailey keeps for the user on this machine: settings now, and conversations,
assistants, installed solutions and permissions as the stories that write them arrive.
Each of those adds its tables as the next entry in :data:`MIGRATIONS`, so no table is
created before something writes it and no column is guessed ahead of its use.

The schema's version is SQLite's ``user_version``: opening the store applies every
migration past it, each in its own transaction with the version it brings, so a store
interrupted mid-upgrade is left at the last whole step. A store whose version is past
the newest migration this Bailey knows was written by a newer Bailey, and is refused
rather than read: an older reader cannot know what the newer columns mean.

Keys are never written here. They go to the keychain or the encrypted key file
(``bailey.keys``).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

#: The store's file name in the data folder.
STORE_FILE = "bailey.db"

#: Each migration is the statements that take the store from the version before it to
#: its own position in this tuple, counting from 1. Append; never edit one that shipped.
MIGRATIONS: tuple[tuple[str, ...], ...] = (
    # 1: settings, one JSON value per name.
    ("CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)",),
)


class NewerStore(Exception):
    """The store was written by a newer Bailey, whose schema this one does not know."""

    def __init__(self, path: Path, version: int) -> None:
        super().__init__(
            f"{path} was written by a newer Bailey (schema version {version}, and this one knows up to "
            f"{len(MIGRATIONS)}). Start that newer Bailey, or point BAILEY_HOME at another folder."
        )
        self.path = path
        self.version = version


class Store:
    """The open store. One connection, shared by the server's worker threads under a lock."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        # Autocommit, so each migration's transaction is the one written below and no other.
        self._connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        try:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._migrate()
        except BaseException:
            self._connection.close()
            raise

    @property
    def schema_version(self) -> int:
        with self._lock:
            return int(self._connection.execute("PRAGMA user_version").fetchone()[0])

    def _migrate(self) -> None:
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if version > len(MIGRATIONS):
            raise NewerStore(self.path, version)
        for number, statements in enumerate(MIGRATIONS[version:], start=version + 1):
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    self._connection.execute(statement)
                # A PRAGMA takes no parameters; `number` is an int this loop counted.
                self._connection.execute(f"PRAGMA user_version = {number:d}")
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise

    def get_setting(self, name: str) -> Any:
        """The setting's value, or ``None`` when it was never set."""

        with self._lock:
            row = self._connection.execute("SELECT value FROM settings WHERE name = ?", (name,)).fetchone()
        return None if row is None else json.loads(row[0])

    def set_setting(self, name: str, value: Any) -> None:
        encoded = json.dumps(value)
        with self._lock:
            self._connection.execute(
                "INSERT INTO settings (name, value) VALUES (?, ?) "
                "ON CONFLICT (name) DO UPDATE SET value = excluded.value",
                (name, encoded),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()
