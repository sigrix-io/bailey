"""Run against ``src/`` without an install, leave no bytecode behind, and keep every test off the system keychain."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import keyring  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402


class NoSystemKeychain(KeyringBackend):
    """Stands in for the system keychain during every test.

    A test that reaches it fails, so no test writes to a developer's own keychain. It
    is never *viable*, so keyring's own detection never picks it; a test that wants the
    real keychain asks for it (``tests/test_real_keychain.py``).
    """

    priority = 0
    viable = False

    def get_password(self, service: str, username: str) -> str | None:
        raise AssertionError(f"a test reached the system keychain ({service}, {username})")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise AssertionError(f"a test reached the system keychain ({service}, {username})")

    def delete_password(self, service: str, username: str) -> None:
        raise AssertionError(f"a test reached the system keychain ({service}, {username})")


@pytest.fixture(autouse=True)
def _no_system_keychain() -> Iterator[None]:
    previous = keyring.get_keyring()
    keyring.set_keyring(NoSystemKeychain())
    try:
        yield
    finally:
        keyring.set_keyring(previous)
