"""Where keys live: the keychain when there is one, and otherwise a file only its passphrase opens.

The encrypted file is the owner's choice for a machine with no keychain (2026-11-01):
scrypt makes a key from the passphrase and AES-256-GCM encrypts the keys under it. These
hold it to what that promises: the file never holds a key or the passphrase in any
readable form, a wrong passphrase and a changed file are both refused, and nothing in
it can be read or written before it is unlocked.
"""

from __future__ import annotations

import base64
import json
import os
import stat
import unicodedata
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeKeychain
from keyring.backends import fail, null
from keyring.backends.chainer import ChainerBackend

from bailey import keys as keys_module
from bailey.keys import (
    FILE_NAME,
    MIN_PASSPHRASE,
    SERVICE,
    EncryptedFileKeys,
    KeychainKeys,
    KeysLocked,
    PassphraseRefused,
    UnreadableKeyFile,
    WrongPassphrase,
    describe_keychain,
    keychain_backend,
    open_keys,
)

KEY = "sk-ant-api03-0123456789abcdefghij-WXYZ"
PASSPHRASE = "correct horse battery"


def _backend(module: str, priority: Any) -> object:
    """A backend as keychain_backend sees one: its module and its priority, nothing else."""

    kind = type("Backend", (), {"__module__": module, "priority": priority, "name": module})
    return kind()


class _PriorityRaises:
    __module__ = "keyring.backends.SecretService"

    @property
    def priority(self) -> float:
        raise RuntimeError("no session bus")


# ---------------------------------------------------------------------------
# Which storage a machine gets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("backend", "accepted"),
    [
        (fail.Keyring(), False),
        (null.Keyring(), False),
        (_backend("keyring.backends.macOS", 5), True),
        (_backend("keyring.backends.Windows", 5), True),
        (_backend("keyring.backends.SecretService", 5), True),
        (_backend("keyring.backends.macOS", 0.5), False),
        (_backend("keyrings.alt.file", 5), False),
        (_backend("someones.plugin", 10), False),
        (_PriorityRaises(), False),
    ],
)
def test_only_a_system_keychain_keyring_recommends_is_used(backend: object, accepted: bool) -> None:
    assert (keychain_backend(backend) is backend) is accepted  # type: ignore[arg-type]


def test_a_chain_is_judged_by_the_backend_a_write_lands_in(monkeypatch: pytest.MonkeyPatch) -> None:
    system, plain = _backend("keyring.backends.SecretService", 5), _backend("keyrings.alt.file", 0.5)
    chain = ChainerBackend()
    monkeypatch.setattr(ChainerBackend, "backends", [system, plain])
    assert keychain_backend(chain) is chain
    monkeypatch.setattr(ChainerBackend, "backends", [plain, system])
    assert keychain_backend(chain) is None
    monkeypatch.setattr(ChainerBackend, "backends", [])
    assert keychain_backend(chain) is None


def test_the_keychain_is_named_for_the_page() -> None:
    assert describe_keychain(FakeKeychain()) == "the macOS Keychain"  # type: ignore[arg-type]
    assert describe_keychain(fail.Keyring()) is None


def test_a_machine_with_a_keychain_keeps_keys_in_it(tmp_path: Path) -> None:
    keys = open_keys(tmp_path, environ={}, backend=FakeKeychain())  # type: ignore[arg-type]
    assert isinstance(keys, KeychainKeys)
    assert (keys.kind, keys.where, keys.locked) == ("keychain", "the macOS Keychain", False)
    assert not (tmp_path / FILE_NAME).exists()


def test_a_machine_without_one_gets_the_file_locked_until_a_passphrase_is_chosen(tmp_path: Path) -> None:
    keys = open_keys(tmp_path, environ={}, backend=fail.Keyring())
    assert isinstance(keys, EncryptedFileKeys)
    assert keys.locked and not keys.has_passphrase
    assert keys.where == f"an encrypted file, {tmp_path / FILE_NAME}"


def test_bailey_keys_file_uses_the_file_even_beside_a_keychain(tmp_path: Path) -> None:
    keys = open_keys(tmp_path, environ={"BAILEY_KEYS": "file"}, backend=FakeKeychain())  # type: ignore[arg-type]
    assert isinstance(keys, EncryptedFileKeys)


def test_bailey_passphrase_creates_the_file_then_opens_it(tmp_path: Path) -> None:
    environ = {"BAILEY_PASSPHRASE": PASSPHRASE}
    first = open_keys(tmp_path, environ=environ, backend=fail.Keyring())
    assert isinstance(first, EncryptedFileKeys) and not first.locked
    first.set("provider/anthropic", KEY)
    second = open_keys(tmp_path, environ=environ, backend=fail.Keyring())
    assert second.get("provider/anthropic") == KEY


def test_a_wrong_bailey_passphrase_is_an_error_not_a_locked_start(tmp_path: Path) -> None:
    EncryptedFileKeys(tmp_path / FILE_NAME).create(PASSPHRASE)
    with pytest.raises(WrongPassphrase):
        open_keys(tmp_path, environ={"BAILEY_PASSPHRASE": "not the passphrase"}, backend=fail.Keyring())


# ---------------------------------------------------------------------------
# The keychain
# ---------------------------------------------------------------------------


def test_keychain_keys_are_filed_under_bailey_by_name() -> None:
    chain = FakeKeychain()
    keys = KeychainKeys(chain)  # type: ignore[arg-type]
    keys.set("provider/anthropic", KEY)
    assert chain.entries == {(SERVICE, "provider/anthropic"): KEY}
    assert KeychainKeys(chain).get("provider/anthropic") == KEY  # type: ignore[arg-type]
    keys.delete("provider/anthropic")
    keys.delete("provider/anthropic")  # nothing left to delete is the state asked for
    assert keys.get("provider/anthropic") is None


# ---------------------------------------------------------------------------
# The encrypted file
# ---------------------------------------------------------------------------


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / FILE_NAME


def test_choosing_a_passphrase_writes_an_empty_file_and_unlocks_it(path: Path) -> None:
    keys = EncryptedFileKeys(path)
    keys.create(PASSPHRASE)
    assert path.exists() and keys.has_passphrase and not keys.locked
    assert keys.get("provider/anthropic") is None


def test_a_short_passphrase_is_refused_and_writes_nothing(path: Path) -> None:
    with pytest.raises(PassphraseRefused, match=str(MIN_PASSPHRASE)):
        EncryptedFileKeys(path).create("x" * (MIN_PASSPHRASE - 1))
    assert not path.exists()


def test_a_second_passphrase_cannot_replace_the_first(path: Path) -> None:
    EncryptedFileKeys(path).create(PASSPHRASE)
    with pytest.raises(FileExistsError):
        EncryptedFileKeys(path).create("another passphrase")


def test_a_key_survives_a_restart_and_opens_with_the_passphrase_alone(path: Path) -> None:
    keys = EncryptedFileKeys(path)
    keys.create(PASSPHRASE)
    keys.set("provider/anthropic", KEY)
    restarted = EncryptedFileKeys(path)
    assert restarted.locked
    with pytest.raises(WrongPassphrase):
        restarted.unlock("correct horse battery staple")
    assert restarted.locked
    restarted.unlock(PASSPHRASE)
    assert restarted.get("provider/anthropic") == KEY


def test_nothing_is_read_or_written_while_locked(path: Path) -> None:
    EncryptedFileKeys(path).create(PASSPHRASE)
    locked = EncryptedFileKeys(path)
    before = path.read_bytes()
    for attempt in (
        lambda: locked.get("provider/anthropic"),
        lambda: locked.set("provider/anthropic", KEY),
        lambda: locked.delete("provider/anthropic"),
    ):
        with pytest.raises(KeysLocked):
            attempt()
    assert path.read_bytes() == before


def test_the_file_holds_no_key_and_no_passphrase_in_any_readable_form(path: Path) -> None:
    keys = EncryptedFileKeys(path)
    keys.create(PASSPHRASE)
    keys.set("provider/anthropic", KEY)
    keys.set("provider/openai", "sk-proj-another-secret-value-1234")
    raw = path.read_bytes()
    for secret in (KEY, "sk-proj-another-secret-value-1234", PASSPHRASE):
        for form in (secret.encode(), base64.b64encode(secret.encode()), secret.encode().hex().encode()):
            assert form not in raw
    # Not even the names of the keys it holds.
    assert b"provider/anthropic" not in raw


def test_each_write_takes_a_fresh_nonce(path: Path) -> None:
    keys = EncryptedFileKeys(path)
    keys.create(PASSPHRASE)
    keys.set("provider/anthropic", KEY)
    first = json.loads(path.read_text(encoding="utf-8"))
    keys.set("provider/anthropic", KEY)
    second = json.loads(path.read_text(encoding="utf-8"))
    assert first["cipher"]["nonce"] != second["cipher"]["nonce"]
    assert first["kdf"] == second["kdf"], "the salt and cost stay with the passphrase"


@pytest.mark.parametrize("field", ["ciphertext", "nonce", "salt"])
def test_a_changed_file_is_refused_as_a_wrong_passphrase_would_be(path: Path, field: str) -> None:
    keys = EncryptedFileKeys(path)
    keys.create(PASSPHRASE)
    keys.set("provider/anthropic", KEY)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    place = {"ciphertext": envelope, "nonce": envelope["cipher"], "salt": envelope["kdf"]}[field]
    data = bytearray(base64.b64decode(place[field]))
    data[0] ^= 0x01
    place[field] = base64.b64encode(bytes(data)).decode("ascii")
    path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(WrongPassphrase):
        EncryptedFileKeys(path).unlock(PASSPHRASE)


@pytest.mark.parametrize(
    "damage",
    [
        lambda envelope: "not json at all",
        lambda envelope: json.dumps({**envelope, "format": "bailey-keys/99"}),
        lambda envelope: json.dumps({**envelope, "kdf": {**envelope["kdf"], "n": 2**25}}),
        lambda envelope: json.dumps({**envelope, "kdf": {**envelope["kdf"], "n": 3}}),
        lambda envelope: json.dumps({**envelope, "kdf": {**envelope["kdf"], "r": True}}),
        lambda envelope: json.dumps({**envelope, "kdf": {**envelope["kdf"], "name": "pbkdf2"}}),
        lambda envelope: json.dumps({**envelope, "ciphertext": "not base64!"}),
        lambda envelope: json.dumps({key: value for key, value in envelope.items() if key != "cipher"}),
    ],
    ids=[
        "not-json",
        "newer-format",
        "cost-too-high",
        "cost-not-a-power-of-two",
        "cost-not-a-number",
        "other-kdf",
        "bad-base64",
        "no-cipher",
    ],
)
def test_a_damaged_or_foreign_file_is_unreadable_not_a_crash(path: Path, damage: Any) -> None:
    EncryptedFileKeys(path).create(PASSPHRASE)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(damage(envelope), encoding="utf-8")
    with pytest.raises(UnreadableKeyFile):
        EncryptedFileKeys(path).unlock(PASSPHRASE)


def test_the_passphrase_is_the_same_however_its_letters_were_typed(path: Path) -> None:
    composed = unicodedata.normalize("NFC", "café au lait")
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    EncryptedFileKeys(path).create(composed)
    EncryptedFileKeys(path).unlock(decomposed)


def test_starting_over_deletes_the_file_and_every_key_in_it(path: Path) -> None:
    keys = EncryptedFileKeys(path)
    keys.create(PASSPHRASE)
    keys.set("provider/anthropic", KEY)
    keys.reset()
    assert not path.exists() and keys.locked and not keys.has_passphrase
    keys.create("a new passphrase")
    assert keys.get("provider/anthropic") is None


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_the_file_is_readable_by_this_user_alone(path: Path) -> None:
    keys = EncryptedFileKeys(path)
    keys.create(PASSPHRASE)
    keys.set("provider/anthropic", KEY)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_write_that_fails_leaves_the_last_whole_file(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keys = EncryptedFileKeys(path)
    keys.create(PASSPHRASE)
    keys.set("provider/anthropic", KEY)

    def interrupted(source: object, target: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(keys_module.os, "replace", interrupted)
    with pytest.raises(OSError, match="disk full"):
        keys.set("provider/anthropic", "sk-ant-a-newer-key-that-never-landed")
    monkeypatch.undo()
    assert keys.get("provider/anthropic") == KEY, "memory still agrees with the file"
    assert not path.with_name(FILE_NAME + ".tmp").exists()
    reopened = EncryptedFileKeys(path)
    reopened.unlock(PASSPHRASE)
    assert reopened.get("provider/anthropic") == KEY
