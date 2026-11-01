"""Stand-ins the tests share: a keychain held in a dict."""

from __future__ import annotations

from keyring.errors import KeyringError, PasswordDeleteError


class FakeKeychain:
    """A keychain in a dict, filed as keyring's macOS backend so Bailey accepts it as a system one.

    A plain class rather than a ``KeyringBackend``: keyring registers every subclass of
    that, and its own detection could then pick this one.
    """

    __module__ = "keyring.backends.macOS"
    priority = 5

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.entries.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.entries[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.entries:
            raise PasswordDeleteError("No such entry.")
        del self.entries[(service, username)]


class RefusingKeychain(FakeKeychain):
    """A keychain that refuses every write, as a locked one whose prompt was cancelled does."""

    def set_password(self, service: str, username: str, password: str) -> None:
        raise KeyringError("The user cancelled the keychain prompt.")
