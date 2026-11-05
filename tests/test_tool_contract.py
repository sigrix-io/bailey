"""The tool contract, on every kind of provider: reads run, writes ask, each answer is honoured, and a question survives a restart.

S1's ruling made the spike's tool checks this story's contract tests, which every
PydanticAI version bump must pass before it lands: a write tool that waits for
approval, with "no" reaching the model as the tool's result, and a paused run that
survives a restart. They run on the provider emulator and a real stdio MCP server,
``tests/tool_servers/mailbox.py``: two reads and one write, whose drafts land in a file
so a test can tell whether a call ran.

Story S7's *done when* is here too: a solution with one read and
one write tool runs the read without asking, asks before the write and honours all
three answers. "Always" is honoured by the store and the server, and
``tests/test_tools_chat.py`` drives it through the routes.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from collections.abc import Collection, Coroutine, Iterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, TypeVar

import emulated_providers as emulator
import pytest
from pydantic_ai.messages import ToolReturnPart

from bailey.agent import DECLINED, answer
from bailey.conversations import pending, shown, to_history, to_records
from bailey.settings import Connection
from bailey.solutions import Assistant, Server, Skill
from bailey.store import Store
from bailey.tools import ToolHost

PROVIDERS = [
    ("anthropic", "/anthropic", "claude-sonnet-5-5"),
    ("openai", "/openai/v1", "gpt-5-mini"),
    ("openai-compatible", "/local/v1", "qwen3:8b"),
]
IDS = [provider for provider, _, _ in PROVIDERS]

MAILBOX = Path(__file__).parent / "tool_servers" / "mailbox.py"
SERVER = Server(
    "mailbox",
    command=sys.executable,
    args=(str(MAILBOX),),
    env=("MAILBOX_DRAFTS",),
    tools=("search_threads", "create_draft", "visible_environment", "process_id"),
    write_tools=("create_draft",),
)
SHOP = Assistant(
    name="shop-helper",
    title="Shop helper",
    description="",
    instructions=Skill("shop-helper", "Its instructions.", "You answer a small shop's customer email."),
    servers=(SERVER,),
)
REPLY = "Reply to the refund request."

T = TypeVar("T")


@pytest.fixture(scope="module")
def base() -> str:
    return emulator.serve_in_background()


@pytest.fixture(scope="module")
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    """One event loop for the module, so its tool server starts once and keeps running, as in the app."""
    running = asyncio.new_event_loop()
    thread = threading.Thread(target=running.run_forever, daemon=True)
    thread.start()
    yield running
    running.call_soon_threadsafe(running.stop)
    thread.join(10)
    running.close()


def _on(loop: asyncio.AbstractEventLoop, work: Coroutine[Any, Any, T]) -> T:
    return asyncio.run_coroutine_threadsafe(work, loop).result(timeout=120)


@pytest.fixture(scope="module")
def drafts(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("mailbox") / "drafts.jsonl"
    path.touch()
    return path


@pytest.fixture(scope="module")
def host(loop: asyncio.AbstractEventLoop) -> Iterator[ToolHost]:
    started = ToolHost()
    yield started
    _on(loop, started.close())


@pytest.fixture(autouse=True)
def _fresh_requests() -> Iterator[None]:
    emulator.REQUESTS.clear()
    emulator.FAIL_AFTER = ""
    yield
    emulator.REQUESTS.clear()
    emulator.FAIL_AFTER = ""


class Mailbox:
    """The module's tool host and emulator, driven a turn at a time."""

    def __init__(self, loop: asyncio.AbstractEventLoop, host: ToolHost, drafts: Path, base: str) -> None:
        self.loop, self.host, self.drafts, self.base = loop, host, drafts, base

    def connection(self, provider: str, path: str, model: str) -> Connection:
        return Connection(provider=provider, model=model, api_key="sk-test-key", base_url=self.base + path)

    def drafted(self) -> int:
        return len(self.drafts.read_text(encoding="utf-8").splitlines())

    def turn(
        self,
        connection: Connection,
        history: Sequence[Any],
        text: str | None,
        *,
        assistant: Assistant = SHOP,
        granted: Collection[tuple[str, str]] = (),
        decisions: Mapping[str, bool] | None = None,
        host: ToolHost | None = None,
    ) -> list[dict[str, Any]]:
        async def run() -> list[dict[str, Any]]:
            environments = {server.name: {"MAILBOX_DRAFTS": str(self.drafts)} for server in assistant.servers}
            toolsets = await (host or self.host).toolsets(assistant.name, assistant.servers, environments)
            return [
                event
                async for event in answer(
                    connection, assistant, list(history), text, toolsets=toolsets, granted=granted, decisions=decisions
                )
            ]

        return _on(self.loop, run())


@pytest.fixture
def mailbox(loop: asyncio.AbstractEventLoop, host: ToolHost, drafts: Path, base: str) -> Mailbox:
    return Mailbox(loop, host, drafts, base)


def _done(events: list[dict[str, Any]]) -> dict[str, Any]:
    assert events[-1]["type"] == "done", events[-1]
    return events[-1]


def _records(connection: Connection, done: dict[str, Any]) -> list[dict[str, Any]]:
    return to_records(
        done["messages"],
        provider=connection.provider,
        model=connection.model,
        servers=done["servers"],
        asked=done["asked"],
    )


def _text(events: list[dict[str, Any]]) -> str:
    return "".join(event["text"] for event in events if event["type"] == "text")


def _outcomes(events: list[dict[str, Any]]) -> dict[str, str]:
    calls = {event["id"]: event["tool"] for event in events if event["type"] == "tool"}
    return {calls.get(event["id"], event["id"]): event["outcome"] for event in events if event["type"] == "tool_result"}


# ---------------------------------------------------------------------------
# The done-when: the read runs, the write asks, each answer is honoured
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("provider", "path", "model"), PROVIDERS, ids=IDS)
def test_the_read_runs_without_asking_and_the_write_stops_the_run_to_ask(
    mailbox: Mailbox, provider: str, path: str, model: str
) -> None:
    before = mailbox.drafted()
    events = mailbox.turn(mailbox.connection(provider, path, model), [], REPLY)
    assert _outcomes(events) == {"search_threads": "success"}, "the read ran, unasked"
    (approval,) = [event for event in events if event["type"] == "approval"]
    assert [(call["tool"], call["server"]) for call in approval["calls"]] == [("create_draft", "mailbox")]
    assert approval["calls"][0]["args"]["thread_id"] == "t-1042"
    done = _done(events)
    assert done["asked"] == [approval["calls"][0]["id"]]
    assert mailbox.drafted() == before, "the write did not run before the person answered"


@pytest.mark.parametrize("allowed", [True, False], ids=["yes", "no"])
@pytest.mark.parametrize(("provider", "path", "model"), PROVIDERS, ids=IDS)
def test_each_answer_is_honoured_and_a_no_reaches_the_model_as_the_tools_result(
    mailbox: Mailbox, provider: str, path: str, model: str, allowed: bool
) -> None:
    connection = mailbox.connection(provider, path, model)
    before = mailbox.drafted()
    first = mailbox.turn(connection, [], REPLY)
    records = _records(connection, _done(first))
    (call,) = pending(records)
    assert call["tool"] == "create_draft" and call["server"] == "mailbox"

    resumed = mailbox.turn(connection, to_history(records), None, decisions={call["id"]: allowed})
    records += _records(connection, _done(resumed))
    assert pending(records) == [], "the answer settled the question"
    if allowed:
        assert mailbox.drafted() == before + 1, "a yes runs the write, once"
        assert _outcomes(resumed) == {"create_draft": "success"}
        assert "Draft created in t-1042." in _text(resumed), "the model received the write's result"
    else:
        assert mailbox.drafted() == before, "a no never runs it"
        assert DECLINED in _text(resumed), "the model received the person's no as the tool's result"
        assert DECLINED in json.dumps(emulator.REQUESTS[-1]["body"]), "and the provider was sent it"
        results = [part for record in records for part in record["parts"] if part["kind"] == "tool_result"]
        assert results[-1]["outcome"] == "denied", "the stored result says it was declined"


@pytest.mark.parametrize(("provider", "path", "model"), PROVIDERS, ids=IDS)
def test_a_tool_the_person_allowed_always_runs_without_asking(
    mailbox: Mailbox, provider: str, path: str, model: str
) -> None:
    before = mailbox.drafted()
    events = mailbox.turn(mailbox.connection(provider, path, model), [], REPLY, granted={("mailbox", "create_draft")})
    assert not [event for event in events if event["type"] == "approval"]
    assert _done(events)["asked"] == []
    assert _outcomes(events) == {"search_threads": "success", "create_draft": "success"}
    assert mailbox.drafted() == before + 1


@pytest.mark.parametrize(("provider", "path", "model"), PROVIDERS, ids=IDS)
def test_a_tool_that_fails_is_given_another_try_and_the_page_is_told_so(
    mailbox: Mailbox, tmp_path: Path, provider: str, path: str, model: str
) -> None:
    # Its drafts file is a folder, so create_draft fails in the server. PydanticAI gives the model
    # another try at a tool's error, and the page's word for that is "retry" (toolWords in app.js):
    # PydanticAI's own, "retried", is not one the page knows, and would show the call still running.
    broken = Mailbox(mailbox.loop, mailbox.host, tmp_path, mailbox.base)
    assistant = replace(SHOP, name="shop-helper-without-drafts")
    connection = mailbox.connection(provider, path, model)
    events = broken.turn(connection, [], REPLY, assistant=assistant, granted={("mailbox", "create_draft")})
    assert _done(events)["asked"] == []
    assert _outcomes(events) == {"search_threads": "success", "create_draft": "retry"}
    assert "The draft tool said:" in _text(events), "the model was given the error, and answered"

    # And stored so: as a retry, which the page draws after a reload as it did live, and which goes
    # back to the model as the call's error, so the next message goes through on it.
    records = _records(connection, _done(events))
    answers = [part["kind"] for record in records for part in record["parts"] if part.get("tool") == "create_draft"]
    assert answers == ["tool_call", "retry"]
    assert {tool["tool"]: tool["outcome"] for tool in shown(records)[-1]["tools"]} == _outcomes(events)
    history = to_history(records)
    (retried,) = [
        part
        for message in history
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "create_draft"
    ]
    assert retried.outcome == "retried", "the model is told the call failed, not that it ran"
    assert _done(broken.turn(connection, history, "Thanks.", assistant=assistant)), "the next message goes through"


def test_every_tool_of_a_server_with_no_declaration_asks(mailbox: Mailbox) -> None:
    undeclared = replace(SHOP, name="undeclared", servers=(replace(SERVER, tools=None, write_tools=()),))
    events = mailbox.turn(mailbox.connection(*PROVIDERS[0]), [], REPLY, assistant=undeclared)
    (approval,) = [event for event in events if event["type"] == "approval"]
    assert [call["tool"] for call in approval["calls"]] == ["search_threads"], "even a read asks, undeclared"
    assert _outcomes(events) == {}


# ---------------------------------------------------------------------------
# What a pause stores, and what survives a restart
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("provider", "path", "model"), PROVIDERS, ids=IDS)
def test_a_paused_run_survives_a_restart(
    mailbox: Mailbox, tmp_path: Path, provider: str, path: str, model: str
) -> None:
    connection = mailbox.connection(provider, path, model)
    before = mailbox.drafted()
    store = Store(tmp_path / "bailey.db")
    store.create_conversation("c1", "a1", "Refund")
    store.append_messages("c1", _records(connection, _done(mailbox.turn(connection, [], REPLY))))
    store.close()
    first_pid = _on(mailbox.loop, _pid(mailbox.host, mailbox.drafts))

    # The restart: the store opened again from disk, and a new tool host, whose server is a
    # new process. Nothing else carries over.
    reopened = Store(tmp_path / "bailey.db")
    records = reopened.messages("c1")
    (call,) = pending(records)
    restarted = ToolHost()
    try:
        resumed = mailbox.turn(connection, to_history(records), None, decisions={call["id"]: True}, host=restarted)
        assert _on(mailbox.loop, _pid(restarted, mailbox.drafts)) != first_pid, "the server is a new process"
    finally:
        _on(mailbox.loop, restarted.close())
        reopened.close()
    assert "Draft created in t-1042." in _text(resumed)
    assert mailbox.drafted() == before + 1


async def _pid(host: ToolHost, drafts: Path) -> int:
    toolsets = await host.toolsets(SHOP.name, SHOP.servers, {"mailbox": {"MAILBOX_DRAFTS": str(drafts)}})
    result = await toolsets["mailbox"].direct_call_tool("process_id", {})
    return int(result["result"] if isinstance(result, dict) else result)


@pytest.mark.parametrize(("provider", "path", "model"), PROVIDERS, ids=IDS)
def test_a_read_and_a_write_in_one_reply_store_and_resume_in_a_shape_the_provider_takes(
    mailbox: Mailbox, provider: str, path: str, model: str
) -> None:
    connection = mailbox.connection(provider, path, model)
    before = mailbox.drafted()
    first = mailbox.turn(connection, [], "Reply to the refund request, both calls at once.")
    records = _records(connection, _done(first))
    calls = [part for record in records if record["role"] == "assistant" for part in record["parts"]]
    assert [part["tool"] for part in calls if part["kind"] == "tool_call"] == ["search_threads", "create_draft"]
    assert _outcomes(first) == {"search_threads": "success"}, "the read in the same reply ran"
    (call,) = pending(records)

    resumed = mailbox.turn(connection, to_history(records), None, decisions={call["id"]: True})
    assert "Draft created in t-1042." in _text(resumed), "the provider took both results, in its own order"
    assert [request.get("refused") for request in emulator.REQUESTS] == [None] * len(emulator.REQUESTS)
    assert mailbox.drafted() == before + 1


def test_a_run_that_fails_after_a_write_keeps_its_result_so_it_is_never_asked_again(mailbox: Mailbox) -> None:
    connection = mailbox.connection(*PROVIDERS[0])
    before = mailbox.drafted()
    records = _records(connection, _done(mailbox.turn(connection, [], REPLY)))
    (call,) = pending(records)
    emulator.FAIL_AFTER = "create_draft"
    failed = mailbox.turn(connection, to_history(records), None, decisions={call["id"]: True})
    assert failed[-1]["type"] == "error" and "refused" not in failed[-1]["message"]
    assert mailbox.drafted() == before + 1, "the write ran before the provider failed"
    records += to_records(failed[-1]["messages"], provider="anthropic", model="m", servers=failed[-1]["servers"])
    assert pending(records) == [], "its result was kept, so the question does not come back"
    emulator.FAIL_AFTER = ""
    follow_up = mailbox.turn(connection, to_history(records), "Thanks.")
    assert _done(follow_up), "the next message goes through on the history the failure left"


# ---------------------------------------------------------------------------
# Names a model could not tell apart
# ---------------------------------------------------------------------------


def test_two_servers_offering_one_tool_are_refused_with_a_sentence(mailbox: Mailbox) -> None:
    twice = replace(SHOP, name="twice", servers=(SERVER, replace(SERVER, name="mailbox-2")))
    events = mailbox.turn(mailbox.connection(*PROVIDERS[0]), [], REPLY, assistant=twice)
    assert events[-1]["type"] == "error"
    assert "The mailbox and mailbox-2 tool servers both offer a tool named search_threads" in events[-1]["message"]
    assert emulator.REQUESTS == [], "nothing was sent to the provider"


def test_the_model_sees_the_server_environment_through_its_tool(mailbox: Mailbox) -> None:
    events = mailbox.turn(mailbox.connection(*PROVIDERS[0]), [], "What can the tool server's environment see?")
    assert _outcomes(events) == {"visible_environment": "success"}, "a declared read, so it did not ask"
    assert "MAILBOX_DRAFTS" in _text(events)
