"""Stand-ins the tests share: a keychain held in a dict."""

from __future__ import annotations

from typing import Any

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


class FakeEmbedder:
    """A stand-in for the search model: words hashed into a vector, so texts that share words point
    the same way. Deterministic, and nothing to download; the real model's own check is
    ``tests/test_knowledge.py::test_the_real_model_finds_the_passage_a_model_would_search_for``."""

    def __init__(self, name: str = "fake/bag-of-words", floor: float = 0.1) -> None:
        from bailey.knowledge import EmbeddingModel

        self.model = EmbeddingModel(name, "a bag of words", 384, 0, floor)
        self.embedded = 0

    def ready(self) -> bool:
        return True

    def embed(self, texts: Any) -> Any:
        import re
        import zlib

        import numpy as np

        from bailey.knowledge import STOPWORDS

        vectors = np.zeros((len(texts), 384), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in re.findall(r"[a-z]+", text.lower()):
                if word not in STOPWORDS:
                    vectors[row, zlib.crc32(word[:5].encode()) % 384] += 1.0
        self.embedded += len(texts)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.where(norms == 0, 1, norms)
