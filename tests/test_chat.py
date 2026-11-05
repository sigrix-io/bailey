"""Assistants and chat through the app: what a person does from the page, against the emulated providers.

The routes are the page's whole surface, so these drive them as it does: the default
assistant a first visit gets, a new one from a template, the edit form, export and
import, and a conversation whose answer streams in, is stored in Bailey's own format, and
is read back. Every refusal answers with a sentence the page shows.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import emulated_providers as emulator
import jsonschema
import pytest
from fakes import FakeKeychain
from starlette.testclient import TestClient

from bailey.keys import FILE_NAME, EncryptedFileKeys, KeychainKeys
from bailey.server import MAX_MESSAGE_CHARACTERS, AppState, build_app
from bailey.store import Store

TOKEN = "test-session-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
KEY = "sk-ant-api03-0123456789abcdefghij-WXYZ"
SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "schemas" / "0.1" / "assistant.schema.json").read_text(encoding="utf-8")
)


@pytest.fixture(scope="module")
def provider_base() -> str:
    return emulator.serve_in_background()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    opened = Store(tmp_path / "bailey.db")
    yield opened
    opened.close()


@pytest.fixture
def state(store: Store, tmp_path: Path, provider_base: str) -> AppState:
    keys = KeychainKeys(FakeKeychain())  # type: ignore[arg-type]
    return AppState(
        token=TOKEN,
        store=store,
        keys=keys,
        data_folder=tmp_path,
        base_urls={"anthropic": provider_base + "/anthropic", "openai": provider_base + "/openai/v1"},
    )


@pytest.fixture
def client(state: AppState) -> TestClient:
    return TestClient(build_app(state), base_url="http://127.0.0.1:8123")


def _ready(client: TestClient, key: str = KEY) -> None:
    assert client.put("/api/settings", headers=AUTH, json={"provider": "anthropic"}).status_code == 200
    assert client.put("/api/keys/anthropic", headers=AUTH, json={"key": key}).status_code == 200


def _events(body: str) -> list[dict[str, Any]]:
    return [json.loads(line.removeprefix("data: ")) for line in body.splitlines() if line.startswith("data: ")]


def _conversation(client: TestClient) -> str:
    response = client.post("/api/conversations", headers=AUTH, json={})
    assert response.status_code == 200
    return str(response.json()["id"])


# ---------------------------------------------------------------------------
# Assistants
# ---------------------------------------------------------------------------


def test_a_first_visit_gets_the_default_assistant_never_an_empty_canvas(client: TestClient) -> None:
    view = client.get("/api/assistants", headers=AUTH).json()
    assert [assistant["title"] for assistant in view["assistants"]] == ["Everyday helper"]
    assert view["active"] == view["assistants"][0]["id"]
    assert view["assistants"][0]["skills"] == ["summarise", "draft-email"]
    assert {template["id"] for template in view["templates"]} >= {"everyday", "writing-editor"}
    assert client.get("/api/assistants", headers=AUTH).json()["active"] == view["active"], "made once"


def test_an_assistant_is_made_from_a_template_edited_as_a_form_and_used(client: TestClient) -> None:
    made = client.post("/api/assistants", headers=AUTH, json={"template": "writing-editor"}).json()
    assistant_id = made["id"]
    assert len(made["assistants"]) == 2, "the default and the new one"
    form = client.get(f"/api/assistants/{assistant_id}", headers=AUTH).json()
    assert form["title"] == "Writing editor" and [skill["name"] for skill in form["skills"]] == ["proofread", "shorten"]

    form["title"] = "Report editor"
    form["skills"].append({"name": "headline", "description": "Write a headline.", "body": "Eight words at most."})
    saved = client.put(f"/api/assistants/{assistant_id}", headers=AUTH, json=form)
    assert saved.status_code == 200 and saved.json()["title"] == "Report editor"

    view = client.post(f"/api/assistants/{assistant_id}/use", headers=AUTH).json()
    assert view["active"] == assistant_id
    refused = client.put(f"/api/assistants/{assistant_id}", headers=AUTH, json={**form, "title": ""})
    assert (refused.status_code, refused.json()["error"]) == (400, "The title cannot be empty.")
    assert client.post("/api/assistants", headers=AUTH, json={"template": "nope"}).status_code == 400


def test_an_export_validates_and_imports_back_as_the_same_assistant(client: TestClient) -> None:
    active = client.get("/api/assistants", headers=AUTH).json()["active"]
    exported = client.get(f"/api/assistants/{active}/export", headers=AUTH)
    assert exported.headers["content-type"] == "application/zip"
    assert exported.headers["content-disposition"] == 'attachment; filename="everyday-helper.zip"'
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        jsonschema.validate(json.loads(archive.read("everyday-helper/assistant.json")), SCHEMA)

    imported = client.post(
        "/api/assistants/import", headers={**AUTH, "Content-Type": "application/zip"}, content=exported.content
    )
    assert imported.status_code == 200
    again = client.get(f"/api/assistants/{imported.json()['id']}", headers=AUTH).json()
    original = client.get(f"/api/assistants/{active}", headers=AUTH).json()
    assert {**again, "id": ""} == {**original, "id": ""}


@pytest.mark.parametrize(
    ("content", "sentence"),
    [
        (b"not a zip", "That file is not a zip."),
        (b"", "That file is not a zip."),
    ],
)
def test_an_import_that_is_not_a_solution_is_refused_with_a_sentence(
    client: TestClient, content: bytes, sentence: str
) -> None:
    response = client.post(
        "/api/assistants/import", headers={**AUTH, "Content-Type": "application/zip"}, content=content
    )
    assert (response.status_code, response.json()["error"]) == (400, sentence)


def test_deleting_an_assistant_takes_its_conversations_and_the_default_returns_when_none_is_left(
    client: TestClient, store: Store
) -> None:
    active = client.get("/api/assistants", headers=AUTH).json()["active"]
    conversation_id = _conversation(client)
    view = client.delete(f"/api/assistants/{active}", headers=AUTH).json()
    assert store.get_conversation(conversation_id) is None and store.messages(conversation_id) == []
    assert [assistant["title"] for assistant in view["assistants"]] == ["Everyday helper"]
    assert view["active"] != active, "a new default, not the deleted one"


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


def test_an_answer_streams_in_names_its_skill_and_is_stored_in_baileys_own_format(
    client: TestClient, store: Store
) -> None:
    _ready(client)
    conversation_id = _conversation(client)
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers=AUTH,
        json={"text": "Please summarise this: the shop opens at nine and closes at five."},
    )
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    assert [event["name"] for event in events if event["type"] == "skill"] == ["summarise"]
    assert sum(event["type"] == "text" for event in events) >= 3
    done = events[-1]
    assert done["type"] == "done" and done["model"] == "claude-sonnet-5-5"
    assert done["usage"]["requests"] == 2 and float(done["estimate"]["usd"]) > 0

    read = client.get(f"/api/conversations/{conversation_id}", headers=AUTH).json()
    assert read["title"] == "Please summarise this: the shop opens at nine and closes at…"
    user, reply = read["messages"]
    assert user == {"role": "user", "text": "Please summarise this: the shop opens at nine and closes at five."}
    assert reply["role"] == "assistant" and reply["skills"] == ["summarise"]
    assert reply["text"].startswith("Following the summarise skill. It begins: # Summarising")
    assert reply["usage"] == done["usage"]
    assert {record["format"] for record in store.messages(conversation_id)} == {"bailey-message/1"}


def test_the_next_message_continues_the_conversation(client: TestClient) -> None:
    _ready(client)
    conversation_id = _conversation(client)
    client.post(f"/api/conversations/{conversation_id}/messages", headers=AUTH, json={"text": "Hello there"})
    emulator.REQUESTS.clear()
    client.post(f"/api/conversations/{conversation_id}/messages", headers=AUTH, json={"text": "And again"})
    sent = json.dumps(emulator.REQUESTS[0]["body"])
    assert "Hello there" in sent and emulator.GREETING.split("<b>")[0].strip() in sent
    listed = client.get("/api/conversations", headers=AUTH).json()["conversations"]
    assert [entry["id"] for entry in listed] == [conversation_id]


def test_a_refused_key_ends_the_stream_with_a_sentence_and_leaves_a_notice(client: TestClient) -> None:
    _ready(client, key=emulator.BAD_KEY)
    conversation_id = _conversation(client)
    events = _events(
        client.post(f"/api/conversations/{conversation_id}/messages", headers=AUTH, json={"text": "Hi"}).text
    )
    assert events == [
        {"type": "error", "message": "Anthropic refused the key (401). Check the key in Settings.", "cause": "provider"}
    ]
    messages = client.get(f"/api/conversations/{conversation_id}", headers=AUTH).json()["messages"]
    assert messages[-1] == {"role": "notice", "text": events[0]["message"]}


@pytest.mark.parametrize(
    ("setup", "text", "status", "sentence"),
    [
        ("nothing", "Hi", 409, "Choose an AI provider in Settings first."),
        ("no-key", "Hi", 409, "Save your Anthropic key in Settings first."),
        ("ready", "   ", 400, "Write a message first."),
        ("ready", "x" * (MAX_MESSAGE_CHARACTERS + 1), 400, "Bailey sends 200,000 at most in one."),
    ],
    ids=["no-provider", "no-key", "empty", "too-long"],
)
def test_a_message_that_cannot_be_sent_says_why(
    client: TestClient, setup: str, text: str, status: int, sentence: str
) -> None:
    if setup == "ready":
        _ready(client)
    elif setup == "no-key":
        client.put("/api/settings", headers=AUTH, json={"provider": "anthropic"})
    conversation_id = _conversation(client)
    response = client.post(f"/api/conversations/{conversation_id}/messages", headers=AUTH, json={"text": text})
    assert response.status_code == status
    assert sentence in response.json()["error"]


def test_a_second_message_waits_for_the_first_answer(client: TestClient, state: AppState) -> None:
    _ready(client)
    conversation_id = _conversation(client)
    state.running.add(conversation_id)
    response = client.post(f"/api/conversations/{conversation_id}/messages", headers=AUTH, json={"text": "Hi"})
    assert response.status_code == 409 and "still coming" in response.json()["error"]


def test_locked_keys_are_said_so_before_anything_is_sent(store: Store, tmp_path: Path, provider_base: str) -> None:
    EncryptedFileKeys(tmp_path / FILE_NAME).create("correct horse battery")
    state = AppState(token=TOKEN, store=store, keys=EncryptedFileKeys(tmp_path / FILE_NAME), data_folder=tmp_path)
    client = TestClient(build_app(state), base_url="http://127.0.0.1:8123")
    client.put("/api/settings", headers=AUTH, json={"provider": "anthropic"})
    conversation_id = _conversation(client)
    response = client.post(f"/api/conversations/{conversation_id}/messages", headers=AUTH, json={"text": "Hi"})
    assert (response.status_code, response.json()["error"]) == (423, "Unlock your keys with their passphrase first.")
    assert store.messages(conversation_id) == [], "nothing was stored for a message never sent"


def test_with_no_passphrase_chosen_a_server_that_needs_no_key_answers(
    store: Store, tmp_path: Path, provider_base: str
) -> None:
    """No key file means no key, so nothing to unlock: a local server answers, and a provider that
    needs a key asks for it. Bailey asked for a passphrase first, though nothing was kept under one."""

    state = AppState(token=TOKEN, store=store, keys=EncryptedFileKeys(tmp_path / FILE_NAME), data_folder=tmp_path)
    client = TestClient(build_app(state), base_url="http://127.0.0.1:8123")
    local = {"provider": "openai-compatible", "address": provider_base + "/local/v1", "model": "qwen3:8b"}
    assert client.put("/api/settings", headers=AUTH, json=local).status_code == 200
    response = client.post(f"/api/conversations/{_conversation(client)}/messages", headers=AUTH, json={"text": "Hi"})
    assert response.status_code == 200 and '"type": "done"' in response.text, response.text
    assert client.put("/api/settings", headers=AUTH, json={"provider": "anthropic"}).status_code == 200
    response = client.post(f"/api/conversations/{_conversation(client)}/messages", headers=AUTH, json={"text": "Hi"})
    assert (response.status_code, response.json()["error"]) == (409, "Save your Anthropic key in Settings first.")
    assert not (tmp_path / FILE_NAME).exists(), "nothing was written: a key is kept only under a passphrase"


def test_a_conversation_nothing_names_is_a_404(client: TestClient) -> None:
    assert client.get("/api/conversations/nope", headers=AUTH).status_code == 404
    assert client.post("/api/conversations/nope/messages", headers=AUTH, json={"text": "Hi"}).status_code == 404
    assert client.delete("/api/conversations/nope", headers=AUTH).status_code == 404


def test_the_model_is_a_setting_of_each_provider(client: TestClient) -> None:
    view = client.get("/api/settings", headers=AUTH).json()
    assert view["models"] == {"anthropic": "claude-sonnet-5-5", "openai": "gpt-5-mini", "openai-compatible": ""}
    saved = client.put("/api/settings", headers=AUTH, json={"provider": "anthropic", "model": "claude-haiku-5-5"})
    assert saved.json()["models"]["anthropic"] == "claude-haiku-5-5"
    refused = client.put("/api/settings", headers=AUTH, json={"provider": "anthropic", "model": "two words"})
    assert refused.status_code == 400 and "as the provider spells it" in refused.json()["error"]
