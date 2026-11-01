"""Where the user's keys live: the OS keychain, or an encrypted file where there is none.

A key is the user's AI provider key now, and a tool's later. It is never written to the
local store, a log or a response, and goes nowhere but where it is used.

- **The keychain**, through keyring: the macOS Keychain, Windows Credential Manager, or
  the Secret Service (GNOME Keyring, KWallet) on Linux. Only keyring's own system
  backends count. A third-party one may write a plain file, or ask for a password on a
  terminal that a server cannot answer.
- **An encrypted file**, ``keys.enc`` in the data folder, where there is no keychain: a
  server, a container, some Linux setups. It opens with a passphrase the user chooses
  (the owner's decision, 2026-11-01). scrypt turns the passphrase into a key, and
  AES-256-GCM encrypts the keys under it, so the file cannot be read, or changed
  unnoticed, without the passphrase. Bailey asks for it each time it starts, or reads
  ``BAILEY_PASSPHRASE``. A forgotten passphrase means starting over: the file is
  deleted and the keys entered again.

``BAILEY_KEYS=file`` uses the file even where there is a keychain.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

import keyring
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from keyring.backend import KeyringBackend
from keyring.backends.chainer import ChainerBackend
from keyring.errors import PasswordDeleteError

#: The keychain service every entry is filed under.
SERVICE = "bailey"

#: The encrypted file's name in the data folder, and the format it is written in.
FILE_NAME = "keys.enc"
FORMAT = "bailey-keys/1"

#: A passphrase shorter than this is refused.
MIN_PASSPHRASE = 8

#: scrypt's cost: 32 MiB and about a tenth of a second, once per start.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1

#: The bounds a file's own cost must sit within, so a planted file cannot make scrypt
#: ask for gigabytes.
_SCRYPT_N_RANGE = (2**14, 2**20)

FORCE_FILE_VARIABLE = "BAILEY_KEYS"
PASSPHRASE_VARIABLE = "BAILEY_PASSPHRASE"  # noqa: S105 -- the variable's name, not a passphrase

_BACKEND_NAMES = {
    "keyring.backends.macOS": "the macOS Keychain",
    "keyring.backends.Windows": "Windows Credential Manager",
    "keyring.backends.SecretService": "the Secret Service (GNOME Keyring or KWallet)",
    "keyring.backends.kwallet": "KWallet",
    "keyring.backends.libsecret": "the Secret Service, through libsecret",
}


class KeysLocked(Exception):
    """The encrypted file has not been opened with its passphrase yet."""


class WrongPassphrase(Exception):
    """The passphrase does not open the file."""


class PassphraseRefused(ValueError):
    """A passphrase too short to choose."""


class UnreadableKeyFile(Exception):
    """The file is damaged, or was written in a format this Bailey does not know."""


class KeyStore(Protocol):
    """What the server asks of either kind of storage."""

    kind: str

    @property
    def where(self) -> str: ...

    @property
    def locked(self) -> bool: ...

    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


def _receiving(backend: KeyringBackend) -> KeyringBackend | None:
    """The backend a write lands in: the first of a chain, or the backend itself."""

    if isinstance(backend, ChainerBackend):
        chained = list(backend.backends)
        return chained[0] if chained else None
    return backend


def keychain_backend(backend: KeyringBackend) -> KeyringBackend | None:
    """``backend`` if it is a system keychain Bailey may write keys to, else ``None``.

    keyring answers its ``fail`` backend (priority 0) when there is none. A backend below
    priority 1 is one keyring itself does not recommend, and one from outside
    ``keyring.backends`` is a third party's.
    """

    receiving = _receiving(backend)
    if receiving is None:
        return None
    module = type(receiving).__module__
    if not module.startswith("keyring.backends.") or module in ("keyring.backends.fail", "keyring.backends.null"):
        return None
    try:
        priority = float(receiving.priority)
    except Exception:  # a backend whose priority cannot be read is not usable here
        return None
    return backend if priority >= 1 else None


def keychain_name(backend: KeyringBackend) -> str:
    receiving = _receiving(backend) or backend
    return _BACKEND_NAMES.get(type(receiving).__module__, str(getattr(receiving, "name", type(receiving).__name__)))


def describe_keychain(backend: KeyringBackend | None = None) -> str | None:
    """The name of the keychain this machine offers, or ``None``. Reads only; stores nothing."""

    chosen = keychain_backend(backend if backend is not None else keyring.get_keyring())
    return None if chosen is None else keychain_name(chosen)


class KeychainKeys:
    """Keys in the OS keychain, one entry per name under :data:`SERVICE`."""

    kind = "keychain"

    def __init__(self, backend: KeyringBackend) -> None:
        self._backend = backend

    @property
    def where(self) -> str:
        return keychain_name(self._backend)

    @property
    def locked(self) -> bool:
        return False

    def get(self, name: str) -> str | None:
        value = self._backend.get_password(SERVICE, name)
        return None if value is None else str(value)

    def set(self, name: str, value: str) -> None:
        self._backend.set_password(SERVICE, name, value)

    def delete(self, name: str) -> None:
        try:
            self._backend.delete_password(SERVICE, name)
        except PasswordDeleteError:  # nothing was saved under that name, which is the state asked for
            pass


def _normalised(passphrase: str) -> bytes:
    # The same passphrase typed on another keyboard or system can arrive in another
    # Unicode form; NFC makes them one.
    return unicodedata.normalize("NFC", passphrase).encode("utf-8")


def _derive(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(_normalised(passphrase))


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: object) -> bytes:
    if not isinstance(text, str):
        raise UnreadableKeyFile("A field that should be base64 is not text.")
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as error:
        raise UnreadableKeyFile("A field that should be base64 is not.") from error


class EncryptedFileKeys:
    """Keys in one file, encrypted under a key made from the user's passphrase."""

    kind = "file"

    def __init__(self, path: Path) -> None:
        self.path = path
        self._key: bytes | None = None
        self._salt = b""
        self._cost = (SCRYPT_N, SCRYPT_R, SCRYPT_P)
        self._secrets: dict[str, str] = {}

    @property
    def where(self) -> str:
        return f"an encrypted file, {self.path}"

    @property
    def has_passphrase(self) -> bool:
        """Whether a passphrase has been chosen: the file exists once one has."""

        return self.path.exists()

    @property
    def locked(self) -> bool:
        return self._key is None

    def create(self, passphrase: str) -> None:
        """Choose the passphrase, and write an empty file under it."""

        if self.has_passphrase:
            raise FileExistsError(f"{self.path} already has a passphrase.")
        if len(passphrase) < MIN_PASSPHRASE:
            raise PassphraseRefused(f"Choose a passphrase of at least {MIN_PASSPHRASE} characters.")
        salt, cost = os.urandom(16), (SCRYPT_N, SCRYPT_R, SCRYPT_P)
        key = _derive(passphrase, salt, *cost)
        self._write(key, salt, cost, {})
        self._key, self._salt, self._cost, self._secrets = key, salt, cost, {}

    def unlock(self, passphrase: str) -> None:
        envelope = self._read_envelope()
        kdf = envelope.get("kdf")
        cipher = envelope.get("cipher")
        if not isinstance(kdf, dict) or not isinstance(cipher, dict):
            raise UnreadableKeyFile(f"{self.path} is damaged: its header is incomplete.")
        if kdf.get("name") != "scrypt" or cipher.get("name") != "AES-256-GCM":
            raise UnreadableKeyFile(f"{self.path} uses a method this Bailey does not know.")
        n, r, p = (self._whole(kdf.get(field)) for field in ("n", "r", "p"))
        if not (_SCRYPT_N_RANGE[0] <= n <= _SCRYPT_N_RANGE[1] and n & (n - 1) == 0 and 1 <= r <= 16 and 1 <= p <= 4):
            raise UnreadableKeyFile(f"{self.path} asks for a cost outside the range Bailey writes.")
        salt = _unb64(kdf.get("salt"))
        nonce = _unb64(cipher.get("nonce"))
        ciphertext = _unb64(envelope.get("ciphertext"))
        key = _derive(passphrase, salt, n, r, p)
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, FORMAT.encode("ascii"))
        except InvalidTag as error:
            # A wrong passphrase and a changed file read alike, and both mean the file
            # cannot be trusted with this passphrase.
            raise WrongPassphrase("That passphrase does not open the key file.") from error
        try:
            secrets = json.loads(plaintext.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise UnreadableKeyFile(f"{self.path} opened, but what it holds is not readable.") from error
        if not isinstance(secrets, dict) or not all(
            isinstance(name, str) and isinstance(value, str) for name, value in secrets.items()
        ):
            raise UnreadableKeyFile(f"{self.path} opened, but what it holds is not a set of keys.")
        self._key, self._salt, self._cost, self._secrets = key, salt, (n, r, p), dict(secrets)

    def lock(self) -> None:
        self._key = None
        self._secrets = {}

    def reset(self) -> None:
        """Start over: delete the file, and every key in it."""

        self.lock()
        self.path.unlink(missing_ok=True)

    def get(self, name: str) -> str | None:
        self._require_unlocked()
        return self._secrets.get(name)

    def set(self, name: str, value: str) -> None:
        self._replace({**self._secrets, name: value})

    def delete(self, name: str) -> None:
        self._require_unlocked()
        if name in self._secrets:
            self._replace({stored: value for stored, value in self._secrets.items() if stored != name})

    def _replace(self, secrets: dict[str, str]) -> None:
        # What is held in memory changes only once the file holding it has been written,
        # so a write that fails leaves both as they were.
        key = self._require_unlocked()
        self._write(key, self._salt, self._cost, secrets)
        self._secrets = secrets

    def _require_unlocked(self) -> bytes:
        if self._key is None:
            raise KeysLocked("Unlock the key file with its passphrase first.")
        return self._key

    def _whole(self, value: object) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise UnreadableKeyFile(f"{self.path} is damaged: its cost is not three whole numbers.")
        return value

    def _read_envelope(self) -> dict[str, Any]:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError as error:
            raise UnreadableKeyFile(f"{self.path} does not exist yet: choose a passphrase first.") from error
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise UnreadableKeyFile(f"{self.path} is damaged: it is not the JSON Bailey writes.") from error
        if not isinstance(envelope, dict) or envelope.get("format") != FORMAT:
            raise UnreadableKeyFile(
                f"{self.path} is not in the format this Bailey writes ({FORMAT}); a newer Bailey may have written it."
            )
        return envelope

    def _write(self, key: bytes, salt: bytes, cost: tuple[int, int, int], secrets: dict[str, str]) -> None:
        nonce = os.urandom(12)
        plaintext = json.dumps(secrets, sort_keys=True).encode("utf-8")
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, FORMAT.encode("ascii"))
        n, r, p = cost
        envelope = {
            "format": FORMAT,
            "kdf": {"name": "scrypt", "n": n, "r": r, "p": p, "salt": _b64(salt)},
            "cipher": {"name": "AES-256-GCM", "nonce": _b64(nonce)},
            "ciphertext": _b64(ciphertext),
        }
        data = (json.dumps(envelope, indent=2) + "\n").encode("utf-8")
        # Written beside the file and moved over it, so a crash leaves the old file or the
        # new one and never half of either; created readable by this user alone.
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.unlink(missing_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


def open_keys(
    folder: Path,
    *,
    environ: Mapping[str, str] = os.environ,
    backend: KeyringBackend | None = None,
) -> KeychainKeys | EncryptedFileKeys:
    """The storage this machine gives: the keychain if it has one, else the encrypted file.

    With ``BAILEY_PASSPHRASE`` set, the file is opened with it (or created under it), and a
    passphrase that does not open it raises :class:`WrongPassphrase` rather than starting
    with the keys locked: a container told the wrong passphrase should say so at once.
    """

    if environ.get(FORCE_FILE_VARIABLE, "").strip().lower() != "file":
        chosen = keychain_backend(backend if backend is not None else keyring.get_keyring())
        if chosen is not None:
            return KeychainKeys(chosen)
    keys = EncryptedFileKeys(folder / FILE_NAME)
    passphrase = environ.get(PASSPHRASE_VARIABLE, "")
    if passphrase:
        if keys.has_passphrase:
            keys.unlock(passphrase)
        else:
            keys.create(passphrase)
    return keys
