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
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: The store's file name in the data folder.
STORE_FILE = "bailey.db"

#: Each migration is the statements that take the store from the version before it to
#: its own position in this tuple, counting from 1. Append; never edit one that shipped.
MIGRATIONS: tuple[tuple[str, ...], ...] = (
    # 1: settings, one JSON value per name.
    ("CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)",),
    # 2 (S6): assistants, each as Bailey's own record of it (bailey.solutions), and the
    # conversations with them, each message one record in bailey-message/1
    # (bailey.conversations), never the agent core's own schema.
    (
        "CREATE TABLE assistants (id TEXT PRIMARY KEY, record TEXT NOT NULL, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
        "CREATE TABLE conversations (id TEXT PRIMARY KEY, assistant_id TEXT NOT NULL, title TEXT NOT NULL, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
        "CREATE INDEX conversations_by_assistant ON conversations (assistant_id, updated_at)",
        "CREATE TABLE messages (conversation_id TEXT NOT NULL, position INTEGER NOT NULL, record TEXT NOT NULL, "
        "PRIMARY KEY (conversation_id, position))",
    ),
    # 3 (S7): the tools a person let an assistant run without asking ("Always for this
    # assistant"), one row per assistant, server and tool, which Settings lists and revokes.
    (
        "CREATE TABLE grants (assistant_id TEXT NOT NULL, server TEXT NOT NULL, tool TEXT NOT NULL, "
        "granted_at TEXT NOT NULL, PRIMARY KEY (assistant_id, server, tool))",
    ),
    # 4 (S8): each assistant's documents, split into passages (bailey.knowledge). A passage
    # keeps its heading, its page and its place in its section; its vector is stored with
    # the name of the model that made it, so a different model re-indexes instead of mixing
    # two. FTS5 searches the passages' text; no SQLite extension is loaded.
    (
        "CREATE TABLE documents (id TEXT PRIMARY KEY, assistant_id TEXT NOT NULL, slot TEXT NOT NULL, "
        "name TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, tokens INTEGER NOT NULL, added_at TEXT NOT NULL)",
        "CREATE INDEX documents_by_assistant ON documents (assistant_id, added_at)",
        "CREATE TABLE passages (id INTEGER PRIMARY KEY, document_id TEXT NOT NULL, assistant_id TEXT NOT NULL, "
        "heading TEXT NOT NULL, page INTEGER, seq INTEGER NOT NULL, body TEXT NOT NULL, text TEXT NOT NULL, "
        "model TEXT NOT NULL DEFAULT '', vector BLOB)",
        "CREATE INDEX passages_by_document ON passages (document_id)",
        "CREATE INDEX passages_by_assistant ON passages (assistant_id)",
        "CREATE VIRTUAL TABLE passages_fts USING fts5(text, tokenize='porter unicode61 remove_diacritics 2')",
    ),
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


def read_settings(path: Path) -> dict[str, Any]:
    """A store's settings, for a command that only looks: read without opening the store as the app opens it.

    Read-only, so nothing is migrated or written, and a store from another version of
    Bailey is read as it stands. A store that cannot be read gives no settings.
    """

    # A store the app has open keeps its write-ahead log beside it, which a read must go
    # through to see the latest values. One with no log is whole in its file, and is read as
    # it stands, so that this read leaves no log of its own behind.
    options = "mode=ro" if path.with_name(path.name + "-wal").exists() else "mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?{options}", uri=True)
    except sqlite3.Error:
        return {}
    try:
        rows = connection.execute("SELECT name, value FROM settings").fetchall()
    except sqlite3.Error:
        return {}
    finally:
        connection.close()
    settings: dict[str, Any] = {}
    for name, value in rows:
        try:
            settings[str(name)] = json.loads(value)
        except (TypeError, ValueError):
            continue
    return settings


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

    # -- assistants ----------------------------------------------------------

    def list_assistants(self) -> list[tuple[str, dict[str, Any]]]:
        with self._lock:
            rows = self._connection.execute("SELECT id, record FROM assistants ORDER BY created_at, id").fetchall()
        return [(row[0], json.loads(row[1])) for row in rows]

    def get_assistant(self, assistant_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT record FROM assistants WHERE id = ?", (assistant_id,)).fetchone()
        return None if row is None else json.loads(row[0])

    def put_assistant(self, assistant_id: str, record: dict[str, Any]) -> None:
        now = _now()
        with self._lock:
            self._connection.execute(
                "INSERT INTO assistants (id, record, created_at, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET record = excluded.record, updated_at = excluded.updated_at",
                (assistant_id, json.dumps(record, ensure_ascii=False), now, now),
            )

    def delete_assistant(self, assistant_id: str) -> None:
        """The assistant, every conversation with it and every tool it was allowed, in one transaction."""

        with self._lock, self._transaction():
            self._connection.execute(
                "DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE assistant_id = ?)",
                (assistant_id,),
            )
            self._connection.execute("DELETE FROM conversations WHERE assistant_id = ?", (assistant_id,))
            self._connection.execute("DELETE FROM grants WHERE assistant_id = ?", (assistant_id,))
            self._connection.execute(
                "DELETE FROM passages_fts WHERE rowid IN (SELECT id FROM passages WHERE assistant_id = ?)",
                (assistant_id,),
            )
            self._connection.execute("DELETE FROM passages WHERE assistant_id = ?", (assistant_id,))
            self._connection.execute("DELETE FROM documents WHERE assistant_id = ?", (assistant_id,))
            self._connection.execute("DELETE FROM assistants WHERE id = ?", (assistant_id,))

    # -- documents and their passages (bailey.knowledge) -------------------------

    def add_document(self, document: dict[str, Any], passages: list[dict[str, Any]]) -> None:
        """A document and its passages, in one transaction. One in the same slot is replaced."""

        with self._lock, self._transaction():
            if document["slot"]:
                for (old,) in self._connection.execute(
                    "SELECT id FROM documents WHERE assistant_id = ? AND slot = ?",
                    (document["assistant_id"], document["slot"]),
                ).fetchall():
                    self._delete_document(old)
            self._connection.execute(
                "INSERT INTO documents (id, assistant_id, slot, name, kind, title, tokens, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document["id"],
                    document["assistant_id"],
                    document["slot"],
                    document["name"],
                    document["kind"],
                    document["title"],
                    document["tokens"],
                    _now(),
                ),
            )
            for passage in passages:
                cursor = self._connection.execute(
                    "INSERT INTO passages (document_id, assistant_id, heading, page, seq, body, text) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        document["id"],
                        document["assistant_id"],
                        passage["heading"],
                        passage["page"],
                        passage["seq"],
                        passage["body"],
                        passage["text"],
                    ),
                )
                self._connection.execute(
                    "INSERT INTO passages_fts (rowid, text) VALUES (?, ?)", (cursor.lastrowid, passage["text"])
                )

    def _delete_document(self, document_id: str) -> None:
        self._connection.execute(
            "DELETE FROM passages_fts WHERE rowid IN (SELECT id FROM passages WHERE document_id = ?)", (document_id,)
        )
        self._connection.execute("DELETE FROM passages WHERE document_id = ?", (document_id,))
        self._connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))

    def delete_document(self, assistant_id: str, document_id: str) -> bool:
        """The document and every passage of it. Whether the assistant had it."""

        with self._lock, self._transaction():
            found = self._connection.execute(
                "SELECT 1 FROM documents WHERE id = ? AND assistant_id = ?", (document_id, assistant_id)
            ).fetchone()
            if found:
                self._delete_document(document_id)
        return found is not None

    def documents(self, assistant_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT d.id, d.slot, d.name, d.kind, d.title, d.tokens, d.added_at, COUNT(p.id) "
                "FROM documents d LEFT JOIN passages p ON p.document_id = d.id WHERE d.assistant_id = ? "
                "GROUP BY d.id ORDER BY d.added_at, d.id",
                (assistant_id,),
            ).fetchall()
        keys = ("id", "slot", "name", "kind", "title", "tokens", "added_at", "passages")
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def passages(self, assistant_id: str, ids: list[int] | None = None) -> list[dict[str, Any]]:
        """The assistant's passages in document order, or those of ``ids``, each with its document's name."""

        query = (
            "SELECT p.id, p.document_id, d.name, d.title, d.kind, p.heading, p.page, p.seq, p.body, p.text "
            "FROM passages p JOIN documents d ON d.id = p.document_id WHERE p.assistant_id = ?"
        )
        parameters: list[Any] = [assistant_id]
        if ids is not None:
            if not ids:
                return []
            query += f" AND p.id IN ({', '.join('?' for _ in ids)})"
            parameters += ids
        with self._lock:
            rows = self._connection.execute(query + " ORDER BY d.added_at, d.id, p.id", parameters).fetchall()
        keys = ("id", "document_id", "document", "title", "kind", "heading", "page", "seq", "body", "text")
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def keyword_search(self, assistant_id: str, match: str, limit: int) -> list[int]:
        """The assistant's passages FTS5 finds for ``match``, best first (bm25)."""

        if not match:
            return []
        with self._lock:
            rows = self._connection.execute(
                "SELECT f.rowid FROM passages_fts f JOIN passages p ON p.id = f.rowid "
                "WHERE passages_fts MATCH ? AND p.assistant_id = ? ORDER BY bm25(passages_fts) LIMIT ?",
                (match, assistant_id, limit),
            ).fetchall()
        return [int(row[0]) for row in rows]

    def unembedded(self, assistant_id: str, model: str) -> list[tuple[int, str]]:
        """The assistant's passages with no vector from ``model``: new ones, and any another model made."""

        with self._lock:
            rows = self._connection.execute(
                "SELECT id, text FROM passages WHERE assistant_id = ? AND (vector IS NULL OR model != ?) ORDER BY id",
                (assistant_id, model),
            ).fetchall()
        return [(int(row[0]), str(row[1])) for row in rows]

    def set_vectors(self, model: str, vectors: list[tuple[int, bytes]]) -> None:
        with self._lock, self._transaction():
            self._connection.executemany(
                "UPDATE passages SET model = ?, vector = ? WHERE id = ?",
                [(model, blob, passage_id) for passage_id, blob in vectors],
            )

    def vectors(self, assistant_id: str, model: str) -> list[tuple[int, bytes]]:
        """The assistant's vectors that ``model`` made, by passage."""

        with self._lock:
            rows = self._connection.execute(
                "SELECT id, vector FROM passages WHERE assistant_id = ? AND model = ? AND vector IS NOT NULL "
                "ORDER BY id",
                (assistant_id, model),
            ).fetchall()
        return [(int(row[0]), bytes(row[1])) for row in rows]

    # -- grants ----------------------------------------------------------------

    def grant(self, assistant_id: str, server: str, tool: str) -> None:
        """Let the assistant run this tool without asking, until the grant is revoked."""

        with self._lock:
            self._connection.execute(
                "INSERT INTO grants (assistant_id, server, tool, granted_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (assistant_id, server, tool) DO NOTHING",
                (assistant_id, server, tool, _now()),
            )

    def revoke(self, assistant_id: str, server: str, tool: str) -> bool:
        """Ask again before the tool runs. Whether there was a grant to revoke."""

        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM grants WHERE assistant_id = ? AND server = ? AND tool = ?", (assistant_id, server, tool)
            )
        return cursor.rowcount > 0

    def grants(self, assistant_id: str | None = None) -> list[dict[str, str]]:
        """Every grant, or one assistant's, oldest first."""

        query = "SELECT assistant_id, server, tool, granted_at FROM grants"
        parameters: tuple[str, ...] = ()
        if assistant_id is not None:
            query += " WHERE assistant_id = ?"
            parameters = (assistant_id,)
        with self._lock:
            rows = self._connection.execute(query + " ORDER BY granted_at, server, tool", parameters).fetchall()
        return [{"assistant_id": row[0], "server": row[1], "tool": row[2], "granted_at": row[3]} for row in rows]

    # -- conversations ---------------------------------------------------------

    def create_conversation(self, conversation_id: str, assistant_id: str, title: str) -> None:
        now = _now()
        with self._lock:
            self._connection.execute(
                "INSERT INTO conversations (id, assistant_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (conversation_id, assistant_id, title, now, now),
            )

    def get_conversation(self, conversation_id: str) -> dict[str, str] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT id, assistant_id, title, updated_at FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        return None if row is None else {"id": row[0], "assistant_id": row[1], "title": row[2], "updated_at": row[3]}

    def list_conversations(self, assistant_id: str) -> list[dict[str, str]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, title, updated_at FROM conversations WHERE assistant_id = ? ORDER BY updated_at DESC, id",
                (assistant_id,),
            ).fetchall()
        return [{"id": row[0], "title": row[1], "updated_at": row[2]} for row in rows]

    def rename_conversation(self, conversation_id: str, title: str) -> None:
        with self._lock:
            self._connection.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id))

    def delete_conversation(self, conversation_id: str) -> None:
        with self._lock, self._transaction():
            self._connection.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            self._connection.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))

    def append_messages(self, conversation_id: str, records: list[dict[str, Any]]) -> None:
        """Add records after the conversation's last, and mark it as just used."""

        with self._lock, self._transaction():
            last = self._connection.execute(
                "SELECT COALESCE(MAX(position), -1) FROM messages WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()[0]
            for offset, record in enumerate(records, start=1):
                self._connection.execute(
                    "INSERT INTO messages (conversation_id, position, record) VALUES (?, ?, ?)",
                    (conversation_id, int(last) + offset, json.dumps(record, ensure_ascii=False)),
                )
            self._connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), conversation_id))

    def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT record FROM messages WHERE conversation_id = ? ORDER BY position", (conversation_id,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _now() -> str:
    # Microseconds keep two writes in one second in the order they were made.
    return datetime.now(UTC).isoformat(timespec="microseconds")
