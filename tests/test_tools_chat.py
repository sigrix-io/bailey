"""Tools through the app: an import that says what it runs, a Tools panel, a question in the chat, and each answer honoured.

The routes are the page's whole surface, so these drive them as it does, with a real
stdio server (``tests/tool_servers/mailbox.py``) behind the emulated provider. Story
S7's *done when* (sigrix-io/sigrix#6565) runs end to end here: the read runs without
asking, the write asks, and once, always and no are each honoured, "always" until it is
revoked.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import emulated_providers as emulator
import pytest
from fakes import FakeKeychain
from starlette.testclient import TestClient

from bailey.agent import DECLINED
from bailey.keys import KeychainKeys
from bailey.server import AppState, build_app, command_line
from bailey.solutions import Assistant, Server, Skill, zip_solution
from bailey.store import Store
from bailey.tools import SANDBOX_NOTICE, SIGRIX_TOKEN, SIGRIX_TOKEN_KEY, variable_key

TOKEN = "test-session-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
KEY = "sk-ant-api03-0123456789abcdefghij-WXYZ"
MAILBOX = Path(__file__).parent / "tool_servers" / "mailbox.py"
REPLY = "Reply to the refund request."

SHOP = Assistant(
    name="shop-helper",
    title="Shop helper",
    description="Answers the shop's email.",
    instructions=Skill("shop-helper", "Its instructions.", "You answer a small shop's customer email."),
    servers=(
        Server(
            "mailbox",
            command=sys.executable,
            args=(str(MAILBOX),),
            env=("MAILBOX_DRAFTS",),
            tools=("search_threads", "create_draft", "visible_environment", "process_id"),
            write_tools=("create_draft",),
        ),
        Server("docs", url="https://docs.example.com/mcp", transport="http"),
    ),
)


@pytest.fixture(scope="module")
def provider_base() -> str:
    return emulator.serve_in_background()


@pytest.fixture
def keychain() -> FakeKeychain:
    return FakeKeychain()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    opened = Store(tmp_path / "bailey.db")
    yield opened
    opened.close()


def _state(store: Store, keychain: FakeKeychain, folder: Path, provider_base: str) -> AppState:
    return AppState(
        token=TOKEN,
        store=store,
        keys=KeychainKeys(keychain),  # type: ignore[arg-type]
        data_folder=folder,
        base_urls={"anthropic": provider_base + "/anthropic"},
    )


@pytest.fixture
def state(store: Store, keychain: FakeKeychain, tmp_path: Path, provider_base: str) -> AppState:
    return _state(store, keychain, tmp_path, provider_base)


@pytest.fixture
def client(state: AppState) -> Iterator[TestClient]:
    # As a context manager: one event loop for the client's life, as the app has, so a
    # tool server started by one request answers the next; and its shutdown stops it.
    with TestClient(build_app(state), base_url="http://127.0.0.1:8123") as c:
        yield c


def _events(body: str) -> list[dict[str, Any]]:
    return [json.loads(line.removeprefix("data: ")) for line in body.splitlines() if line.startswith("data: ")]


def _import_shop(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/assistants/import", headers=AUTH, content=zip_solution(SHOP))
    assert response.status_code == 200, response.text
    return dict(response.json())


def _ready_shop(client: TestClient, drafts: Path) -> tuple[str, str]:
    assert client.put("/api/settings", headers=AUTH, json={"provider": "anthropic"}).status_code == 200
    assert client.put("/api/keys/anthropic", headers=AUTH, json={"key": KEY}).status_code == 200
    assistant_id = _import_shop(client)["id"]
    saved = client.put(
        f"/api/assistants/{assistant_id}/tools/mailbox/variables/MAILBOX_DRAFTS",
        headers=AUTH,
        json={"value": str(drafts)},
    )
    assert saved.status_code == 200, saved.text
    assert client.post(f"/api/assistants/{assistant_id}/use", headers=AUTH).status_code == 200
    conversation = client.post("/api/conversations", headers=AUTH, json={"assistant": assistant_id}).json()["id"]
    return assistant_id, conversation


def _say(client: TestClient, conversation: str, text: str = REPLY) -> list[dict[str, Any]]:
    response = client.post(f"/api/conversations/{conversation}/messages", headers=AUTH, json={"text": text})
    assert response.status_code == 200, response.text
    return _events(response.text)


def _answer(client: TestClient, conversation: str, given: str) -> list[dict[str, Any]]:
    waiting = client.get(f"/api/conversations/{conversation}", headers=AUTH).json()["pending"]
    response = client.post(
        f"/api/conversations/{conversation}/answers",
        headers=AUTH,
        json={"answers": {call["id"]: given for call in waiting}},
    )
    assert response.status_code == 200, response.text
    return _events(response.text)


def _drafted(drafts: Path) -> int:
    return len(drafts.read_text(encoding="utf-8").splitlines()) if drafts.exists() else 0


def _text(events: list[dict[str, Any]]) -> str:
    return "".join(event["text"] for event in events if event["type"] == "text")


def test_a_solution_with_tools_imports_saying_what_it_runs_and_lists_its_tools(client: TestClient) -> None:
    imported = _import_shop(client)
    assert imported["notice"] == SANDBOX_NOTICE, "the person is told a tool runs as a program on this computer"
    panel = client.get(f"/api/assistants/{imported['id']}/tools", headers=AUTH).json()
    assert panel["notice"] == SANDBOX_NOTICE
    mailbox, docs = panel["servers"]
    assert mailbox["tools"] == [
        {"name": "search_threads", "writes": False},
        {"name": "create_draft", "writes": True},
        {"name": "visible_environment", "writes": False},
        {"name": "process_id", "writes": False},
    ]
    assert mailbox["variables"] == [{"name": "MAILBOX_DRAFTS", "saved": False, "shared": False}]
    assert mailbox["launch"].endswith("mailbox.py") and not mailbox["remote"]
    assert docs == {
        "name": "docs",
        "remote": True,
        "launch": "https://docs.example.com/mcp",
        "declared": False,
        "tools": [],
        "variables": [],
    }


def test_a_message_waits_for_the_variables_its_servers_need(client: TestClient, store: Store) -> None:
    client.put("/api/settings", headers=AUTH, json={"provider": "anthropic"})
    client.put("/api/keys/anthropic", headers=AUTH, json={"key": KEY})
    assistant_id = _import_shop(client)["id"]
    conversation = client.post("/api/conversations", headers=AUTH, json={"assistant": assistant_id}).json()["id"]
    response = client.post(f"/api/conversations/{conversation}/messages", headers=AUTH, json={"text": REPLY})
    assert response.status_code == 409
    assert response.json()["error"] == (
        "The mailbox tool server needs MAILBOX_DRAFTS. Give it in this assistant's Tools, under Assistants."
    )
    assert store.messages(conversation) == [], "nothing was stored for a message never sent"


def test_the_read_runs_the_write_asks_and_once_always_and_no_are_each_honoured(
    client: TestClient, tmp_path: Path
) -> None:
    drafts = tmp_path / "drafts.jsonl"
    _, conversation = _ready_shop(client, drafts)

    events = _say(client, conversation)
    assert events[0] == {"type": "status", "text": "Starting this assistant's tools…"}
    assert [(event["tool"], event["server"]) for event in events if event["type"] == "tool"] == [
        ("search_threads", "mailbox"),
        ("create_draft", "mailbox"),
    ]
    assert [event["outcome"] for event in events if event["type"] == "tool_result"] == ["success"]
    (approval,) = [event for event in events if event["type"] == "approval"]
    assert events[-1]["type"] == "done" and events[-1]["paused"] is True
    read = client.get(f"/api/conversations/{conversation}", headers=AUTH).json()
    assert [call["id"] for call in read["pending"]] == [approval["calls"][0]["id"]]
    assert [(t["tool"], t["outcome"]) for t in read["messages"][-1]["tools"]] == [
        ("search_threads", "success"),
        ("create_draft", "waiting"),
    ]
    refused = client.post(f"/api/conversations/{conversation}/messages", headers=AUTH, json={"text": "Hello?"})
    assert refused.status_code == 409 and "Answer the question above first" in refused.json()["error"]

    declined = _answer(client, conversation, "no")
    assert DECLINED in _text(declined) and _drafted(drafts) == 0, "no: it never runs, and the model is told"
    assert [event["outcome"] for event in declined if event["type"] == "tool_result"] == ["denied"]
    shown = client.get(f"/api/conversations/{conversation}", headers=AUTH).json()
    assert shown["pending"] == [] and shown["messages"][-1]["tools"][-1]["outcome"] == "denied"

    _say(client, conversation)
    once = _answer(client, conversation, "once")
    assert "Draft created in t-1042." in _text(once) and _drafted(drafts) == 1, "once: it runs"
    assert client.get("/api/grants", headers=AUTH).json()["grants"] == [], "and nothing is kept"

    _say(client, conversation)
    _answer(client, conversation, "always")
    assert _drafted(drafts) == 2
    grants = client.get("/api/grants", headers=AUTH).json()["grants"]
    assert [(g["assistant_title"], g["server"], g["tool"]) for g in grants] == [
        ("Shop helper", "mailbox", "create_draft")
    ]

    unasked = _say(client, conversation)
    assert not [event for event in unasked if event["type"] == "approval"], "always: it runs without asking"
    assert unasked[-1]["paused"] is False and _drafted(drafts) == 3
    assert not [event for event in unasked if event["type"] == "status"], "the server kept running"

    grant = grants[0]
    revoked = client.post(
        "/api/grants/revoke",
        headers=AUTH,
        json={"assistant": grant["assistant_id"], "server": grant["server"], "tool": grant["tool"]},
    )
    assert revoked.status_code == 200 and revoked.json()["grants"] == []
    asks_again = _say(client, conversation)
    assert [event for event in asks_again if event["type"] == "approval"], "revoked: it asks again"


def test_an_answer_names_each_waiting_call_and_only_those(client: TestClient, tmp_path: Path) -> None:
    _, conversation = _ready_shop(client, tmp_path / "drafts.jsonl")
    url = f"/api/conversations/{conversation}/answers"
    nothing = client.post(url, headers=AUTH, json={"answers": {"call_1": "once"}})
    assert (
        nothing.status_code == 409
        and nothing.json()["error"] == "Nothing in this conversation is waiting for an answer."
    )
    _say(client, conversation)
    wrong = client.post(url, headers=AUTH, json={"answers": {"call_nope": "once"}})
    assert wrong.status_code == 400 and "and only those" in wrong.json()["error"]
    unknown = client.post(url, headers=AUTH, json={"answers": {"call_1": "maybe"}})
    assert unknown.status_code == 400 and unknown.json()["error"] == "Answer each question with once, always or no."


def test_a_question_survives_a_restart_of_the_app(
    store: Store, keychain: FakeKeychain, tmp_path: Path, provider_base: str
) -> None:
    drafts = tmp_path / "drafts.jsonl"
    first = _state(store, keychain, tmp_path, provider_base)
    with TestClient(build_app(first), base_url="http://127.0.0.1:8123") as c:
        assistant_id, conversation = _ready_shop(c, drafts)
        assert _say(c, conversation)[-1]["paused"] is True
        assert first.tools.running(assistant_id)
    assert not first.tools.running(assistant_id), "the app's shutdown stopped its tool servers"
    store.close()

    reopened = Store(tmp_path / "bailey.db")
    try:
        app = build_app(_state(reopened, keychain, tmp_path, provider_base))
        with TestClient(app, base_url="http://127.0.0.1:8123") as again:
            waiting = again.get(f"/api/conversations/{conversation}", headers=AUTH).json()["pending"]
            assert [call["tool"] for call in waiting] == ["create_draft"], "the question is there after the restart"
            assert "Draft created in t-1042." in _text(_answer(again, conversation, "once"))
    finally:
        reopened.close()
    assert _drafted(drafts) == 1


def test_a_tool_server_that_cannot_start_says_so_in_the_chat(client: TestClient, store: Store) -> None:
    broken = Assistant(
        name="broken",
        title="Broken",
        description="",
        instructions=Skill("broken", "Its instructions.", "You answer."),
        servers=(Server("ghost", command="bailey-no-such-command"),),
    )
    client.put("/api/settings", headers=AUTH, json={"provider": "anthropic"})
    client.put("/api/keys/anthropic", headers=AUTH, json={"key": KEY})
    assistant_id = client.post("/api/assistants/import", headers=AUTH, content=zip_solution(broken)).json()["id"]
    conversation = client.post("/api/conversations", headers=AUTH, json={"assistant": assistant_id}).json()["id"]
    events = _say(client, conversation, "Hi")
    assert events[-1]["type"] == "error" and "bailey-no-such-command is not on this computer" in events[-1]["message"]
    shown = client.get(f"/api/conversations/{conversation}", headers=AUTH).json()["messages"]
    assert shown[-1] == {"role": "notice", "text": events[-1]["message"]}


def test_the_sigrix_token_is_given_once_for_every_assistant(client: TestClient, keychain: FakeKeychain) -> None:
    paid = Server("shop", command="uvx", args=("sigrix-launcher", "run", "acme/shop-tools"), env=(SIGRIX_TOKEN,))
    package = zip_solution(replace(SHOP, servers=(paid,)))
    first = client.post("/api/assistants/import", headers=AUTH, content=package).json()["id"]
    second = client.post("/api/assistants/import", headers=AUTH, content=package).json()["id"]
    client.put(f"/api/assistants/{first}/tools/shop/variables/{SIGRIX_TOKEN}", headers=AUTH, json={"value": "sgx_rt_1"})
    panel = client.get(f"/api/assistants/{second}/tools", headers=AUTH).json()
    assert panel["servers"][0]["variables"] == [{"name": SIGRIX_TOKEN, "saved": True, "shared": True}]
    assert variable_key(first, "shop", SIGRIX_TOKEN) == SIGRIX_TOKEN_KEY
    client.delete(f"/api/assistants/{first}", headers=AUTH)
    assert client.get(f"/api/assistants/{second}/tools", headers=AUTH).json()["servers"][0]["variables"][0]["saved"], (
        "deleting one assistant leaves the token every Sigrix server shares"
    )


def test_deleting_an_assistant_takes_its_tool_values_grants_and_running_servers(
    client: TestClient, state: AppState, store: Store, keychain: FakeKeychain, tmp_path: Path
) -> None:
    drafts = tmp_path / "drafts.jsonl"
    assistant_id, conversation = _ready_shop(client, drafts)
    _say(client, conversation)
    _answer(client, conversation, "always")
    kept = variable_key(assistant_id, "mailbox", "MAILBOX_DRAFTS")
    assert store.grants(assistant_id) and keychain.get_password("bailey", kept) and state.tools.running(assistant_id)
    assert client.delete(f"/api/assistants/{assistant_id}", headers=AUTH).status_code == 200
    assert store.grants(assistant_id) == []
    assert keychain.get_password("bailey", kept) is None
    assert not state.tools.running(assistant_id), "its server stopped with it"


@pytest.mark.parametrize(
    ("system", "words", "shown"),
    [
        ("posix", ["uvx", "shop-mcp", "--name", "My Shop"], "uvx shop-mcp --name 'My Shop'"),
        (
            "nt",
            [r"C:\hostedtoolcache\Python\python.exe", r"D:\a\bailey\tests\tool_servers\mailbox.py"],
            r"C:\hostedtoolcache\Python\python.exe D:\a\bailey\tests\tool_servers\mailbox.py",
        ),
        (
            "nt",
            [r"C:\Program Files\nodejs\npx.cmd", "-y", "shop-mcp"],
            r'"C:\Program Files\nodejs\npx.cmd" -y shop-mcp',
        ),
    ],
    ids=["posix", "windows-paths", "windows-space"],
)
def test_a_launch_is_shown_as_the_persons_own_shell_would_write_it(system: str, words: list[str], shown: str) -> None:
    assert command_line(words, system) == shown
