"""The tool host: a server sees only what its configuration names, keeps running between messages, and says why when it cannot start.

S1's first requirement for this story (sigrix-io/sigrix#6565): FastMCP's ``uvx`` and
``npx`` transports hand the child Bailey's whole environment once one variable is named.
These tests start a real stdio server, ``tests/tool_servers/mailbox.py``, with a decoy
secret and a provider key in Bailey's environment, and ask it what it can see.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from mcp.client.stdio import DEFAULT_INHERITED_ENV_VARS

from bailey.solutions import Server
from bailey.tools import (
    NETWORK_VARIABLES,
    SIGRIX_TOKEN,
    SIGRIX_TOKEN_KEY,
    ToolHost,
    ToolServerFailed,
    _why,  # noqa: PLC2701 -- the sentence for a missing command, which no runner here lacks
    environment_for,
    missing_variables,
    variable_key,
)

MAILBOX = Path(__file__).parent / "tool_servers" / "mailbox.py"

DECOY = "BAILEY_TEST_DECOY_SECRET"


def _mailbox(**changes: Any) -> Server:
    server = Server(
        "mailbox",
        command=sys.executable,
        args=(str(MAILBOX),),
        env=("MAILBOX_DRAFTS",),
        tools=("search_threads", "create_draft", "visible_environment", "process_id"),
        write_tools=("create_draft",),
    )
    return replace(server, **changes)


def _value(result: Any) -> Any:
    """A tool's return value, whether the server sent it structured or as text."""
    if isinstance(result, dict) and set(result) == {"result"}:
        return result["result"]
    return result


async def _call(toolset: Any, tool: str, args: Mapping[str, Any] | None = None) -> Any:
    return _value(await toolset.direct_call_tool(tool, dict(args or {})))


async def _gone(pid: int) -> bool:
    """Whether the process has ended, asked while the event loop that started it still runs.

    Closing the loop ends every process it started, so a check after it proves nothing.
    """
    if os.name != "posix":  # os.kill(pid, 0) means something else on Windows
        return True
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        await asyncio.sleep(0.05)
    return False


def test_a_server_sees_only_the_variables_its_configuration_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DECOY, "sk-decoy-0123456789")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-for-tools")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    server = _mailbox()
    env = environment_for(server, {"MAILBOX_DRAFTS": str(tmp_path / "drafts.jsonl")}, os.environ)

    async def scenario() -> list[str]:
        host = ToolHost()
        try:
            toolsets = await host.toolsets("a1", [server], {"mailbox": env})
            return list(await _call(toolsets["mailbox"], "visible_environment"))
        finally:
            await host.close()

    names = asyncio.run(scenario())
    assert "MAILBOX_DRAFTS" in names, "the canary: the variable the configuration names arrives"
    assert "HTTPS_PROXY" in names, "a proxy setting arrives, so a uvx or npx server can download itself"
    assert DECOY not in names and "ANTHROPIC_API_KEY" not in names, "nothing else of Bailey's environment"
    allowed = set(DEFAULT_INHERITED_ENV_VARS) | set(NETWORK_VARIABLES) | {"MAILBOX_DRAFTS"}
    unexpected = [name for name in names if name not in allowed and not name.startswith(("LC_", "__CF_"))]
    assert unexpected == [], "the only other names are those the system and Python add to every process"


def test_a_named_variable_is_never_taken_from_baileys_environment() -> None:
    shell = {"MAILBOX_DRAFTS": "/from/the/shell"}
    assert environment_for(_mailbox(), {}, shell) == {}, "a value comes from the keychain or not at all"
    assert missing_variables(_mailbox(), {}) == ["MAILBOX_DRAFTS"]
    assert missing_variables(_mailbox(), {"MAILBOX_DRAFTS": "/kept"}) == []


def test_a_server_keeps_running_between_messages_and_starts_again_when_its_values_change(tmp_path: Path) -> None:
    server = _mailbox()
    first_env = {"MAILBOX_DRAFTS": str(tmp_path / "one.jsonl")}
    second_env = {"MAILBOX_DRAFTS": str(tmp_path / "two.jsonl")}

    async def scenario() -> dict[str, Any]:
        host = ToolHost()
        try:
            first = (await host.toolsets("a1", [server], {"mailbox": first_env}))["mailbox"]
            pid = await _call(first, "process_id")
            again = (await host.toolsets("a1", [server], {"mailbox": first_env}))["mailbox"]
            same = await _call(again, "process_id")
            changed = (await host.toolsets("a1", [server], {"mailbox": second_env}))["mailbox"]
            restarted = await _call(changed, "process_id")
            first_ended = not first.is_running and await _gone(pid)
            await host.stop("a1")
            return {
                "pid": pid,
                "same": same,
                "restarted": restarted,
                "reused": again is first,
                "first_ended": first_ended,
                "stopped": not changed.is_running and not host.running("a1") and await _gone(restarted),
            }
        finally:
            await host.close()

    seen = asyncio.run(scenario())
    assert seen["reused"] and seen["same"] == seen["pid"], "the next message reaches the same process"
    assert seen["restarted"] != seen["pid"], "a changed value starts it again"
    assert seen["first_ended"], "and the process it replaced has ended, while Bailey runs on"
    assert seen["stopped"], "a stopped server's process has ended, while Bailey runs on"


def test_a_server_that_cannot_start_says_why() -> None:
    async def start(server: Server) -> None:
        host = ToolHost(start_timeout=20)
        try:
            await host.toolsets("a1", [server], {})
        finally:
            await host.close()

    with pytest.raises(ToolServerFailed, match="bailey-no-such-command is not on this computer"):
        asyncio.run(start(Server("missing", command="bailey-no-such-command")))
    with pytest.raises(ToolServerFailed, match="The quits tool server could not start"):
        asyncio.run(start(Server("quits", command=sys.executable, args=("-c", "raise SystemExit(3)"))))
    assert "Install uv" in _why(Server("shop", command="uvx"), FileNotFoundError("uvx"), 120)


def test_a_remote_server_is_listed_and_not_connected() -> None:
    async def scenario() -> dict[str, Any]:
        host = ToolHost()
        try:
            return await host.toolsets("a1", [Server("docs", url="https://docs.example.com/mcp")], {})
        finally:
            await host.close()

    assert asyncio.run(scenario()) == {}


def test_the_sigrix_token_is_kept_once_for_every_server() -> None:
    assert variable_key("a1", "shop", SIGRIX_TOKEN) == variable_key("a2", "tools", SIGRIX_TOKEN) == SIGRIX_TOKEN_KEY
    assert variable_key("a1", "shop", "SHOP_KEY") != variable_key("a2", "shop", "SHOP_KEY")
