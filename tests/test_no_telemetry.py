"""No telemetry: the app, run as a user runs it, connects to nothing but this computer.

Python's audit hooks see every connection a process opens and every name it looks up,
whatever library asks. The app runs under a hook that writes each one down, every route
it serves is exercised, and the test fails on any address that is not loopback. The one
exception the rule allows is the provider the person chose: the chat run below has it
on this computer too (the emulated providers, as an OpenAI-compatible server), so even
a whole chat through the agent core, its SDKs and its price data connects to nothing
else. The tools run does it again with a solution's MCP server, through FastMCP and the
MCP SDK, asking before a write and answered. The knowledge run fills a solution's slots
with its samples, converts an HTML page through MarkItDown, cites a passage, and grows a
collection past what goes whole, so the search model is looked for on this computer and,
not being here, is only announced: nothing fetches it unasked. A canary run plants an outbound connection
first, so a recorder that records nothing cannot pass for an app that sends nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import emulated_providers as emulator
from running import Running

from bailey.solutions import Assistant, Server, Skill, read_solution, zip_solution

PASSPHRASE = "correct horse battery"

_RECORDER = """
import json, sys

log = open(sys.argv[1], "a", buffering=1, encoding="utf-8")


def hook(event, args):
    if event == "socket.connect":
        address = args[1]
        if isinstance(address, tuple):
            log.write(json.dumps({"event": event, "host": str(address[0]), "port": address[1]}) + "\\n")
        else:
            log.write(json.dumps({"event": event, "local": str(address)}) + "\\n")
    elif event == "socket.getaddrinfo":
        host = args[0]
        log.write(json.dumps({"event": event, "host": host.decode() if isinstance(host, bytes) else host}) + "\\n")


sys.addaudithook(hook)
if sys.argv[2] == "canary":
    import socket

    try:
        socket.create_connection(("192.0.2.1", 9), timeout=0.05).close()  # TEST-NET-1: routes nowhere
    except OSError:
        pass

from bailey.cli import main

raise SystemExit(main(["--no-browser"]))
"""


def _is_loopback(host: object) -> bool:
    return host in (None, "localhost", "::1") or (isinstance(host, str) and host.startswith(("127.", "::ffff:127.")))


def outbound(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The events that name somewhere other than this computer. A local socket (D-Bus) is not one."""

    return [event for event in events if "host" in event and not _is_loopback(event["host"])]


def _chat(bailey: Running) -> None:
    """A whole chat with the default assistant, on a provider served from this computer."""

    provider = emulator.serve_in_background()
    settings = {"provider": "openai-compatible", "address": provider + "/local/v1", "model": "qwen3:8b"}
    assert bailey.call("PUT", "/api/settings", settings)[0] == 200
    status, conversation = bailey.call("POST", "/api/conversations", {})
    assert status == 200
    status, streamed = bailey.call(
        "POST", f"/api/conversations/{conversation['id']}/messages", {"text": "Please summarise this: rain at noon."}
    )
    assert status == 200 and '"type": "done"' in streamed, streamed
    assert '"name": "summarise"' in streamed


def _tool_chat(bailey: Running, drafts: Path) -> None:
    """A reply through a solution's MCP server: a read, a write that asks, and the answer once."""

    provider = emulator.serve_in_background()
    settings = {"provider": "openai-compatible", "address": provider + "/local/v1", "model": "qwen3:8b"}
    assert bailey.call("PUT", "/api/settings", settings)[0] == 200
    mailbox = Server(
        "mailbox",
        command=sys.executable,
        args=(str(Path(__file__).parent / "tool_servers" / "mailbox.py"),),
        env=("MAILBOX_DRAFTS",),
        tools=("search_threads", "create_draft"),
        write_tools=("create_draft",),
    )
    shop = Assistant(
        name="shop-helper",
        title="Shop helper",
        description="",
        instructions=Skill("shop-helper", "Its instructions.", "You answer a small shop's email."),
        servers=(mailbox,),
    )
    status, imported = bailey.call("POST", "/api/assistants/import", zip_solution(shop))
    assert status == 200, imported
    variable = f"/api/assistants/{imported['id']}/tools/mailbox/variables/MAILBOX_DRAFTS"
    assert bailey.call("PUT", variable, {"value": str(drafts)})[0] == 200
    status, conversation = bailey.call("POST", "/api/conversations", {"assistant": imported["id"]})
    assert status == 200
    url = f"/api/conversations/{conversation['id']}"
    status, streamed = bailey.call("POST", url + "/messages", {"text": "Reply to the refund request."})
    assert status == 200 and '"type": "approval"' in streamed, streamed
    (call,) = bailey.call("GET", url)[1]["pending"]
    status, streamed = bailey.call("POST", url + "/answers", {"answers": {call["id"]: "once"}})
    events = [json.loads(line[6:]) for line in streamed.splitlines() if line.startswith("data: ")]
    text = "".join(event["text"] for event in events if event["type"] == "text")
    assert status == 200 and "Draft created in t-1042." in text, streamed


def _knowledge_chat(bailey: Running) -> None:
    """A solution's samples in its slots, a document converted, a cited answer, and a collection grown past whole."""

    provider = emulator.serve_in_background()
    settings = {"provider": "openai-compatible", "address": provider + "/local/v1", "model": "qwen3:8b"}
    assert bailey.call("PUT", "/api/settings", settings)[0] == 200
    fixture = Path(__file__).parent / "fixtures" / "shop-replies"
    files = {path.relative_to(fixture).as_posix(): path.read_bytes() for path in fixture.rglob("*") if path.is_file()}
    status, imported = bailey.call("POST", "/api/assistants/import", zip_solution(read_solution(files)))
    assert status == 200, imported
    url = f"/api/assistants/{imported['id']}/knowledge"
    for slot in ("policies", "faq", "catalog"):
        assert bailey.call("POST", f"{url}/samples/{slot}")[0] == 200
    page = b"<html><body><h1>Events</h1><p>Author readings on the first Friday of each month.</p></body></html>"
    assert bailey.call("POST", f"{url}?name=events.html", page)[0] == 200
    status, conversation = bailey.call("POST", "/api/conversations", {"assistant": imported["id"]})
    path = f"/api/conversations/{conversation['id']}/messages"
    status, streamed = bailey.call("POST", path, {"text": "Can I return a book I did not like?"})
    assert status == 200 and '"heading": "Returns"' in streamed, streamed
    filler = " ".join(f"note{n}" for n in range(12_000))
    assert bailey.call("POST", f"{url}?name=archive.md", f"# Archive\n\n{filler}".encode())[0] == 200
    status, panel = bailey.call("GET", url)
    assert status == 200 and panel["mode"] == "search" and panel["model"]["ready"] is False
    status, refused = bailey.call("POST", path, {"text": "Can I return a book I did not like?"})
    assert status == 409 and "downloads once from Hugging Face" in refused["error"]


def _run(tmp_path: Path, mode: str) -> list[dict[str, Any]]:
    recorder = tmp_path / "recorder.py"
    recorder.write_text(_RECORDER, encoding="utf-8")
    log = tmp_path / f"{mode}.jsonl"
    bailey = Running(
        tmp_path / f"home-{mode}",
        extra_env={"BAILEY_PASSPHRASE": PASSPHRASE},
        command=[sys.executable, str(recorder), str(log), mode],
    )
    try:
        exercised = [
            bailey.call("GET", "/", token=None),
            bailey.call("GET", "/static/app.js", token=None),
            bailey.call("GET", "/static/app.css", token=None),
            bailey.call("GET", "/static/icon.svg", token=None),
            bailey.call("GET", "/api/status"),
            bailey.call("GET", "/api/settings"),
            bailey.call("PUT", "/api/settings", {"provider": "openai-compatible", "address": "http://localhost:1/v1"}),
            bailey.call("PUT", "/api/keys/anthropic", {"key": "sk-ant-0123456789abcdef"}),
            bailey.call("DELETE", "/api/keys/anthropic"),
            bailey.call("POST", "/api/keys/unlock", {"passphrase": PASSPHRASE}),
            bailey.call("GET", "/api/settings", token=None),
        ]
        assert [status for status, _ in exercised] == [200] * 10 + [401]
        if mode == "chat":
            _chat(bailey)
        elif mode == "tools":
            _tool_chat(bailey, tmp_path / "drafts.jsonl")
        elif mode == "knowledge":
            _knowledge_chat(bailey)
    finally:
        bailey.stop()
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_the_recorder_catches_a_planted_outbound_connection(tmp_path: Path) -> None:
    caught = outbound(_run(tmp_path, "canary"))
    assert {"event": "socket.connect", "host": "192.0.2.1", "port": 9} in caught


def test_the_app_connects_to_nothing_but_this_computer(tmp_path: Path) -> None:
    events = _run(tmp_path, "app")
    assert outbound(events) == []


def test_a_whole_chat_connects_to_nothing_but_this_computer_and_the_chosen_provider(tmp_path: Path) -> None:
    events = _run(tmp_path, "chat")
    assert any(event.get("event") == "socket.connect" for event in events), "the canary: the chat reached its provider"
    assert outbound(events) == []


def test_a_chat_through_a_tool_server_connects_to_nothing_but_this_computer(tmp_path: Path) -> None:
    events = _run(tmp_path, "tools")
    assert any(event.get("event") == "socket.connect" for event in events), "the canary: the chat reached its provider"
    assert outbound(events) == []
    assert (tmp_path / "drafts.jsonl").read_text(encoding="utf-8").count("t-1042") == 1, "the write ran, once"


def test_documents_cited_and_searched_connect_to_nothing_and_fetch_no_model_unasked(tmp_path: Path) -> None:
    events = _run(tmp_path, "knowledge")
    assert any(event.get("event") == "socket.connect" for event in events), "the canary: the chat reached its provider"
    assert outbound(events) == []
