"""The settings a user chooses: which AI provider Bailey talks to, its address, and its key.

:data:`PROVIDERS` is the one list of providers. The page draws its choices from the
server's answer rather than from a copy of its own, so a provider added here appears
there. A key is stored by :func:`key_name` in the keychain or the key file, never in the
local store; the store keeps the provider's id and, for an OpenAI-compatible server, its
address.
"""

from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

#: A key longer than this is not one any provider issues; it is a paste gone wrong.
MAX_KEY_LENGTH = 512


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    #: Whether Bailey needs a key to talk to it. A local server often takes none.
    key_required: bool
    #: Where to get a key, or ``""``.
    key_url: str
    #: Whether the user gives the server's address.
    needs_address: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


PROVIDERS: tuple[Provider, ...] = (
    Provider("anthropic", "Anthropic", True, "https://console.anthropic.com/settings/keys", False),
    Provider("openai", "OpenAI", True, "https://platform.openai.com/api-keys", False),
    Provider("openai-compatible", "An OpenAI-compatible server, local models included", False, "", True),
)

_BY_ID = {provider.id: provider for provider in PROVIDERS}


class SettingRefused(ValueError):
    """A value Bailey will not save, with the sentence that says why."""


def provider(provider_id: object) -> Provider:
    if not isinstance(provider_id, str) or provider_id not in _BY_ID:
        raise SettingRefused("Choose one of the providers listed.")
    return _BY_ID[provider_id]


def key_name(provider_id: str) -> str:
    """The name a provider's key is stored under."""

    return f"provider/{provider_id}"


def clean_key(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise SettingRefused("Paste the key first.")
    key = raw.strip()
    if any(character.isspace() or unicodedata.category(character).startswith("C") for character in key):
        raise SettingRefused("A key is one line with no spaces in it. Check that only the key was pasted.")
    if len(key) > MAX_KEY_LENGTH:
        raise SettingRefused("That is longer than any provider's key. Check that only the key was pasted.")
    return key


def clean_address(raw: object) -> str:
    text = raw.strip() if isinstance(raw, str) else ""
    try:
        parts = urlsplit(text)
        has_host = bool(parts.hostname)
    except ValueError:
        parts, has_host = None, False
    if parts is None or parts.scheme not in ("http", "https") or not has_host or parts.query or parts.fragment:
        raise SettingRefused(
            "Give the server's address, starting with http:// or https://, such as http://localhost:11434/v1."
        )
    return text.rstrip("/")


def key_hint(key: str) -> str:
    """What the page may show of a saved key: its last four characters, when it is long enough to spare them."""

    return "…" + key[-4:] if len(key) >= 12 else "saved"
