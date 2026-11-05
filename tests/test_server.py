"""The app server: what a request needs to be answered, what every answer carries, and the settings API.

A loopback port is reachable from any web page the user visits (design §4, risk 5), so
the token is the boundary. The sweep below reads the app's own route table rather than a
list written here, so a route added later is swept without anyone remembering to add it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeEmbedder, FakeKeychain, RefusingKeychain
from starlette.routing import Route
from starlette.testclient import TestClient

from bailey.keys import FILE_NAME, EncryptedFileKeys, KeychainKeys
from bailey.server import CSP, PUBLIC_PATHS, SECURITY_HEADERS, STATIC_FILES, AppState, build_app
from bailey.store import Store

TOKEN = "test-session-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
KEY = "sk-ant-api03-0123456789abcdefghij-WXYZ"
PASSPHRASE = "correct horse battery"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    opened = Store(tmp_path / "bailey.db")
    yield opened
    opened.close()


def _state(store: Store, keys: Any, folder: Path) -> AppState:
    # The stand-in search model: with the real one, a request to POST /api/knowledge/model fetches it.
    return AppState(token=TOKEN, store=store, keys=keys, data_folder=folder, embedder=FakeEmbedder())


@pytest.fixture
def state(store: Store, tmp_path: Path) -> AppState:
    return _state(store, KeychainKeys(FakeKeychain()), tmp_path)  # type: ignore[arg-type]


def _client(state: AppState, host: str = "127.0.0.1:8123") -> TestClient:
    return TestClient(build_app(state), base_url=f"http://{host}")


def _routes(state: AppState) -> list[tuple[str, str]]:
    """Every (method, path) the app answers, its path parameters filled with a real value."""

    app = build_app(state).app  # type: ignore[attr-defined]
    found = []
    for route in app.routes:
        assert isinstance(route, Route)
        path = re.sub(r"\{[^}]+\}", "anthropic", route.path)
        for method in sorted(route.methods or ()):
            if method != "HEAD":
                found.append((method, path))
    return found


# ---------------------------------------------------------------------------
# What a request needs
# ---------------------------------------------------------------------------


def test_every_route_but_the_page_refuses_a_request_without_the_token(state: AppState) -> None:
    client = _client(state)
    guarded = [(method, path) for method, path in _routes(state) if path not in PUBLIC_PATHS]
    assert len(guarded) >= 8, "the canary: the sweep reads the app's routes, which hold the whole API"
    for method, path in guarded:
        for headers in ({}, {"Authorization": "Bearer not-the-token"}, {"Authorization": TOKEN}):
            response = client.request(method, path, headers=headers, json={})
            assert response.status_code == 401, (method, path, headers)
            assert "did not come from Bailey's page" in response.json()["error"]
    # And with the token, each reaches its handler: the 401s above came from the guard.
    for method, path in guarded:
        assert client.request(method, path, headers=AUTH, json={}).status_code != 401, (method, path)
    assert ("POST", "/api/knowledge/model") in guarded, "the canary: the sweep asked for the search model"
    assert not (state.data_folder / "models").exists(), "nothing was fetched: the search model is a stand-in"


def test_a_path_nothing_answers_is_refused_before_it_is_looked_up(state: AppState) -> None:
    client = _client(state)
    assert client.get("/api/not-a-route").status_code == 401
    assert client.get("/static/../api/settings").status_code == 401
    assert client.get("/api/not-a-route", headers=AUTH).status_code == 404


def test_only_the_page_and_its_static_files_answer_without_the_token(state: AppState) -> None:
    assert {"/", *(f"/static/{name}" for name in STATIC_FILES)} == PUBLIC_PATHS
    client = _client(state)
    page = client.get("/")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert TOKEN not in page.text, "the page itself carries no secret"
    for name, kind in STATIC_FILES.items():
        response = client.get(f"/static/{name}")
        assert response.status_code == 200 and response.headers["content-type"] == kind


@pytest.mark.parametrize(
    "host",
    ["testserver", "evil.example", "127.0.0.1.evil.example", "localhost.evil.example", "[::2]:8123", "0.0.0.0:8123"],
)
def test_a_host_that_is_not_this_computers_own_name_is_refused(state: AppState, host: str) -> None:
    client = _client(state, host)
    for path in ("/", "/api/settings"):
        response = client.get(path, headers=AUTH)
        assert response.status_code == 403, (host, path)
        assert response.json()["error"] == "Bailey answers only on this computer's own address."


@pytest.mark.parametrize("host", ["127.0.0.1:8123", "localhost:8123", "[::1]:8123", "LOCALHOST:8123"])
def test_each_loopback_name_is_answered(state: AppState, host: str) -> None:
    assert _client(state, host).get("/api/settings", headers=AUTH).status_code == 200


def test_every_answer_carries_the_security_headers(state: AppState) -> None:
    client = _client(state)
    answers = [
        client.get("/"),
        client.get("/static/app.js"),
        client.get("/api/settings", headers=AUTH),
        client.get("/api/settings"),
        client.get("/api/not-a-route", headers=AUTH),
        _client(state, "evil.example").get("/"),
    ]
    for response in answers:
        for name, value in SECURITY_HEADERS:
            assert response.headers.get(name.decode()) == value.decode(), (response.url, name)


def test_the_csp_lets_nothing_in_from_another_origin_and_nothing_inline() -> None:
    directives = dict(part.strip().split(" ", 1) for part in CSP.split(";"))
    assert directives["default-src"] == "'none'"
    assert directives["frame-ancestors"] == "'none'"
    for source in directives.values():
        assert source in ("'none'", "'self'"), source


# ---------------------------------------------------------------------------
# Settings and keys
# ---------------------------------------------------------------------------


def test_the_settings_name_every_provider_from_the_servers_own_list(state: AppState) -> None:
    from bailey.settings import PROVIDERS

    view = _client(state).get("/api/settings", headers=AUTH).json()
    assert [entry["id"] for entry in view["providers"]] == [provider.id for provider in PROVIDERS]
    assert view["provider"] is None and view["address"] == ""
    assert view["keys"] == {"kind": "keychain", "where": "the macOS Keychain", "locked": False}
    assert all(entry["key"] == {"saved": False, "hint": ""} for entry in view["providers"])


def test_a_saved_key_is_shown_as_saved_with_a_hint_and_never_returned(state: AppState, store: Store) -> None:
    client = _client(state)
    answers = [
        client.put("/api/settings", headers=AUTH, json={"provider": "anthropic"}),
        client.put("/api/keys/anthropic", headers=AUTH, json={"key": f"  {KEY}\n"}),
        client.get("/api/settings", headers=AUTH),
        client.get("/api/status", headers=AUTH),
    ]
    assert all(response.status_code == 200 for response in answers)
    view = answers[2].json()
    assert view["provider"] == "anthropic"
    assert view["providers"][0]["key"] == {"saved": True, "hint": "…WXYZ"}
    for response in answers:
        assert KEY not in response.text and "0123456789abcdefghij" not in response.text
    assert state.keys.get("provider/anthropic") == KEY, "saved trimmed, in the keychain"
    assert KEY.encode() not in store.path.read_bytes(), "and never in the local store"

    removed = client.delete("/api/keys/anthropic", headers=AUTH)
    assert removed.json()["providers"][0]["key"] == {"saved": False, "hint": ""}


def test_an_openai_compatible_server_needs_its_address(state: AppState) -> None:
    client = _client(state)
    refused = client.put("/api/settings", headers=AUTH, json={"provider": "openai-compatible", "address": "ollama"})
    assert refused.status_code == 400
    assert "http://localhost:11434/v1" in refused.json()["error"]
    saved = client.put(
        "/api/settings",
        headers=AUTH,
        json={"provider": "openai-compatible", "address": " http://localhost:11434/v1/ "},
    )
    assert saved.json()["address"] == "http://localhost:11434/v1"
    # Choosing another provider keeps the address for coming back to.
    other = client.put("/api/settings", headers=AUTH, json={"provider": "openai", "address": "ignored"})
    assert (other.json()["provider"], other.json()["address"]) == ("openai", "http://localhost:11434/v1")


@pytest.mark.parametrize(
    ("method", "path", "body", "status", "sentence"),
    [
        ("PUT", "/api/settings", {"provider": "someone-else"}, 400, "Choose one of the providers listed."),
        ("PUT", "/api/keys/someone-else", {"key": KEY}, 404, "Bailey knows no provider by that name."),
        ("PUT", "/api/keys/anthropic", {"key": ""}, 400, "Paste the key first."),
        ("PUT", "/api/keys/anthropic", {"key": "sk-ant one two"}, 400, "no spaces"),
        ("PUT", "/api/keys/anthropic", {"key": "k" * 513}, 400, "longer than any provider's key"),
        ("PUT", "/api/keys/anthropic", ["not", "an", "object"], 400, "Send a JSON object."),
        ("POST", "/api/keys/unlock", {"passphrase": PASSPHRASE}, 409, "has no passphrase of Bailey's"),
        ("POST", "/api/keys/passphrase", {"passphrase": PASSPHRASE}, 409, "has no passphrase of Bailey's"),
        ("POST", "/api/keys/reset", {}, 409, "has no passphrase of Bailey's"),
    ],
)
def test_a_refusal_says_why_in_a_sentence(
    state: AppState, method: str, path: str, body: Any, status: int, sentence: str
) -> None:
    response = _client(state).request(method, path, headers=AUTH, json=body)
    assert response.status_code == status
    assert sentence in response.json()["error"]


def test_a_body_that_is_not_json_or_too_large_is_refused(state: AppState) -> None:
    client = _client(state)
    not_json = client.put("/api/keys/anthropic", headers=AUTH, content=b"key=sk-ant")
    assert (not_json.status_code, not_json.json()["error"]) == (400, "Send the request as JSON.")
    huge = client.put("/api/keys/anthropic", headers=AUTH, content=json.dumps({"key": "k" * 70_000}).encode())
    assert huge.status_code == 413


def test_a_keychain_that_refuses_is_said_so(store: Store, tmp_path: Path) -> None:
    state = _state(store, KeychainKeys(RefusingKeychain()), tmp_path)  # type: ignore[arg-type]
    response = _client(state).put("/api/keys/anthropic", headers=AUTH, json={"key": KEY})
    assert response.status_code == 503
    assert response.json()["error"] == "The keychain refused: The user cancelled the keychain prompt."


# ---------------------------------------------------------------------------
# A machine with no keychain: the encrypted file
# ---------------------------------------------------------------------------


def test_the_file_is_locked_until_a_passphrase_is_chosen_and_opens_after_a_restart(
    store: Store, tmp_path: Path
) -> None:
    state = _state(store, EncryptedFileKeys(tmp_path / FILE_NAME), tmp_path)
    client = _client(state)
    view = client.get("/api/settings", headers=AUTH).json()
    assert view["keys"] == {
        "kind": "file",
        "where": f"an encrypted file, {tmp_path / FILE_NAME}",
        "locked": True,
        "has_passphrase": False,
    }
    assert all(entry["key"] is None for entry in view["providers"]), "nothing is known of a locked file"

    locked = client.put("/api/keys/anthropic", headers=AUTH, json={"key": KEY})
    assert (locked.status_code, locked.json()["error"]) == (423, "Unlock your keys with their passphrase first.")
    early = client.post("/api/keys/unlock", headers=AUTH, json={"passphrase": PASSPHRASE})
    assert early.status_code == 409 and "Choose one first" in early.json()["error"]
    short = client.post("/api/keys/passphrase", headers=AUTH, json={"passphrase": "short"})
    assert short.status_code == 400 and "at least 8" in short.json()["error"]

    chosen = client.post("/api/keys/passphrase", headers=AUTH, json={"passphrase": PASSPHRASE})
    assert chosen.json()["keys"]["locked"] is False and chosen.json()["keys"]["has_passphrase"] is True
    again = client.post("/api/keys/passphrase", headers=AUTH, json={"passphrase": "another passphrase"})
    assert again.status_code == 409
    assert client.put("/api/keys/anthropic", headers=AUTH, json={"key": KEY}).status_code == 200

    # A restart: a new process holds the same file, locked.
    restarted = _client(_state(store, EncryptedFileKeys(tmp_path / FILE_NAME), tmp_path))
    assert restarted.get("/api/settings", headers=AUTH).json()["keys"]["locked"] is True
    wrong = restarted.post("/api/keys/unlock", headers=AUTH, json={"passphrase": "not the passphrase"})
    assert (wrong.status_code, wrong.json()["error"]) == (400, "That passphrase does not open your key file.")
    opened = restarted.post("/api/keys/unlock", headers=AUTH, json={"passphrase": PASSPHRASE})
    assert opened.json()["providers"][0]["key"] == {"saved": True, "hint": "…WXYZ"}

    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes(), path


def test_starting_over_deletes_the_file(store: Store, tmp_path: Path) -> None:
    keys = EncryptedFileKeys(tmp_path / FILE_NAME)
    keys.create(PASSPHRASE)
    keys.set("provider/anthropic", KEY)
    client = _client(_state(store, EncryptedFileKeys(tmp_path / FILE_NAME), tmp_path))
    view = client.post("/api/keys/reset", headers=AUTH).json()
    assert view["keys"]["has_passphrase"] is False and view["keys"]["locked"] is True
    assert not (tmp_path / FILE_NAME).exists()
