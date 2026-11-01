"""The local store: a versioned schema that only moves forward, and settings that survive a reopen."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bailey import store as store_module
from bailey.store import MIGRATIONS, NewerStore, Store


def _version(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()


def test_a_new_store_is_at_the_newest_schema(tmp_path: Path) -> None:
    store = Store(tmp_path / "bailey.db")
    try:
        assert store.schema_version == len(MIGRATIONS) >= 1
    finally:
        store.close()


def test_settings_survive_a_reopen_and_a_reopen_applies_nothing_again(tmp_path: Path) -> None:
    path = tmp_path / "bailey.db"
    store = Store(path)
    store.set_setting("provider", "anthropic")
    store.set_setting("provider", "openai")
    store.set_setting("nested", {"a": [1, 2]})
    store.close()
    again = Store(path)
    try:
        assert again.get_setting("provider") == "openai"
        assert again.get_setting("nested") == {"a": [1, 2]}
        assert again.get_setting("never-set") is None
        assert again.schema_version == len(MIGRATIONS)
    finally:
        again.close()


def test_a_store_from_a_newer_bailey_is_refused_and_left_as_it_was(tmp_path: Path) -> None:
    path = tmp_path / "bailey.db"
    connection = sqlite3.connect(path)
    connection.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
    connection.close()
    with pytest.raises(NewerStore) as refused:
        Store(path)
    assert f"schema version {len(MIGRATIONS) + 1}" in str(refused.value)
    assert f"knows up to {len(MIGRATIONS)}" in str(refused.value)
    assert _version(path) == len(MIGRATIONS) + 1


def test_a_migration_that_fails_leaves_the_store_at_the_last_whole_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "bailey.db"
    Store(path).close()
    broken = (*MIGRATIONS, ("CREATE TABLE half_made (id INTEGER)", "THIS IS NOT SQL"))
    monkeypatch.setattr(store_module, "MIGRATIONS", broken)
    with pytest.raises(sqlite3.OperationalError):
        Store(path)
    assert _version(path) == len(MIGRATIONS)
    connection = sqlite3.connect(path)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        connection.close()
    assert "half_made" not in tables, "the failed step's first statement was rolled back with it"


def test_the_next_migration_is_applied_on_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "bailey.db"
    first = Store(path)
    first.set_setting("kept", True)
    first.close()
    monkeypatch.setattr(store_module, "MIGRATIONS", (*MIGRATIONS, ("CREATE TABLE later (id INTEGER)",)))
    later = Store(path)
    try:
        assert later.schema_version == len(MIGRATIONS) + 1
        assert later.get_setting("kept") is True
    finally:
        later.close()


def test_a_grant_is_kept_once_revoked_on_request_and_deleted_with_its_assistant(tmp_path: Path) -> None:
    store = Store(tmp_path / "bailey.db")
    try:
        store.put_assistant("a1", {"format": "bailey-assistant/1"})
        store.put_assistant("a2", {"format": "bailey-assistant/1"})
        store.grant("a1", "mailbox", "create_draft")
        store.grant("a1", "mailbox", "create_draft")
        store.grant("a2", "mailbox", "create_draft")
        assert [(g["assistant_id"], g["server"], g["tool"]) for g in store.grants("a1")] == [
            ("a1", "mailbox", "create_draft")
        ], "granting twice keeps one grant"
        assert len(store.grants()) == 2
        assert store.revoke("a1", "mailbox", "create_draft") is True
        assert store.revoke("a1", "mailbox", "create_draft") is False, "nothing left to revoke"
        store.grant("a1", "mailbox", "send")
        store.delete_assistant("a1")
        assert store.grants() == [g for g in store.grants() if g["assistant_id"] == "a2"] and len(store.grants()) == 1
    finally:
        store.close()
