"""The spike's agent checks, run on each kind of provider and printed as one JSON report.

    python check.py            # against the emulated providers (providers.py): no keys, no network
    python check.py --live     # against the real ones: ANTHROPIC_API_KEY, OPENAI_API_KEY, and
                               # LOCAL_BASE_URL with LOCAL_MODEL for an OpenAI-compatible server

For each provider:

- `environment`: the stdio tool server sees the one variable its config names and the MCP SDK's
  short default list (PATH, HOME and so on), and not a decoy secret set in the app's environment.
- `streaming`: the answer arrives as text deltas while the run is still going.
- `approve_inline`, `deny_inline`: a write tool pauses a *streaming* run, a handler answers, and
  the same run carries on. "Yes" runs the tool. "No" does not, and the model receives the denial
  as the tool's result, which the emulator checks the way each real service checks it.
- `approve_after_restart`, `deny_after_restart`: the run ends at the approval; its messages are
  written to JSON, read back into a new agent with a new tool server process, and resumed with
  the answer, as an app restarted mid-approval would.

Also `traps`: which of FastMCP's stdio transports hand a child the app's whole environment.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import platform
import sys
import tempfile
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from fastmcp.client.transports import NpxStdioTransport, StdioTransport, UvxStdioTransport
from mcp.client.stdio import DEFAULT_INHERITED_ENV_VARS
from pydantic_ai import (
    AgentRunResultEvent,
    DeferredToolRequests,
    DeferredToolResults,
    RunContext,
    ToolApproved,
    ToolDenied,
)
from pydantic_ai.capabilities import HandleDeferredToolCalls
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolReturnPart,
)

sys.path.insert(0, str(Path(__file__).parent))

import core  # noqa: E402
import providers  # noqa: E402

DECOY = "BAILEY_SPIKE_DECOY_SECRET"
DENIAL = "The user declined this action. Do not try it again."
REFUND_REQUEST = "Draft a reply to the latest refund request."
#: Variables a process gains on its own: PEP 538's locale coercion, and CoreFoundation on macOS.
SELF_ADDED = {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"}

Check = Callable[[core.Provider, Path], Coroutine[Any, Any, dict[str, Any]]]

#: Every address this process connects to or looks up, from Python's audit hooks: the evidence
#: that nothing in the core (PydanticAI, the SDKs, the price data) calls anywhere on its own.
CONNECTIONS: list[str] = []
LOOPBACK = ("127.0.0.1", "::1", "localhost")


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if event == "socket.connect" and isinstance(args[1], tuple):
        CONNECTIONS.append(str(args[1][0]))
    elif event == "socket.getaddrinfo":
        CONNECTIONS.append(str(args[0]))


sys.addaudithook(_audit)


def tool_returns(messages: list[ModelMessage], tool_name: str) -> list[Any]:
    return [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == tool_name
    ]


def drafts_written(drafts: Path) -> int:
    return len(drafts.read_text(encoding="utf-8").splitlines()) if drafts.exists() else 0


async def check_environment(provider: core.Provider, workdir: Path) -> dict[str, Any]:
    result = await core.build_agent(provider, workdir / "drafts.jsonl").run(
        "What environment does the tool server see?"
    )
    returned = tool_returns(result.all_messages(), "visible_environment")
    seen = returned[0] if returned else []
    if isinstance(seen, str):
        seen = json.loads(seen)
    if isinstance(seen, dict):  # structured content arrives wrapped as {"result": [...]}
        seen = seen.get("result", [])
    allowed = set(DEFAULT_INHERITED_ENV_VARS) | {core.NAMED_VARIABLE} | SELF_ADDED
    unexpected = sorted(set(seen) - allowed)
    return {
        "ok": bool(seen) and DECOY not in seen and core.NAMED_VARIABLE in seen and not unexpected,
        "server_sees": seen,
        "unexpected": unexpected,
        "decoy_reached_server": DECOY in seen,
    }


async def check_streaming(provider: core.Provider, workdir: Path) -> dict[str, Any]:
    agent = core.build_agent(provider, workdir / "drafts.jsonl")
    started = time.perf_counter()
    arrivals: list[float] = []
    output: Any = None
    async with agent.run_stream_events("Say hello.") as events:
        async for event in events:
            text_started = isinstance(event, PartStartEvent) and isinstance(event.part, TextPart)
            text_delta = isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta)
            if text_started or text_delta:
                arrivals.append(time.perf_counter() - started)
            elif isinstance(event, AgentRunResultEvent):
                output = event.result.output
    finished = time.perf_counter() - started
    return {
        "ok": len(arrivals) >= 3 and arrivals[0] < finished - 0.1 and isinstance(output, str),
        "text_events": len(arrivals),
        "first_text_s": round(arrivals[0], 3) if arrivals else None,
        "run_s": round(finished, 3),
        "output": output,
    }


def answer_for(requests: DeferredToolRequests, approve: bool) -> DeferredToolResults:
    decision = ToolApproved() if approve else ToolDenied(DENIAL)
    return DeferredToolResults(approvals={call.tool_call_id: decision for call in requests.approvals})


#: Against the emulator the scripted model repeats the tool's result, which proves the provider
#: received it. A real model words its answer its own way, so `--live` judges by the run's record.
EMULATED = True


def judged(approve: bool, messages: list[ModelMessage], output: Any, drafts: Path) -> bool:
    outcomes = [
        (part.outcome, str(part.content))
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "create_draft"
    ]
    expected = "Draft created in t-1042" if approve else DENIAL
    recorded = any(
        outcome == ("success" if approve else "denied") and expected in content for outcome, content in outcomes
    )
    repeated = not EMULATED or (isinstance(output, str) and expected in output)
    return drafts_written(drafts) == (1 if approve else 0) and recorded and repeated


async def check_inline(provider: core.Provider, workdir: Path, approve: bool) -> dict[str, Any]:
    drafts = workdir / "drafts.jsonl"
    asked: list[list[str]] = []

    async def handler(ctx: RunContext[None], requests: DeferredToolRequests) -> DeferredToolResults:
        asked.append([call.tool_name for call in requests.approvals])
        await asyncio.sleep(0.2)  # a person takes a moment; the run must wait, not time out
        return answer_for(requests, approve)

    agent = core.build_agent(provider, drafts)
    events_seen: list[str] = []
    output: Any = None
    messages: list[ModelMessage] = []
    capabilities = [HandleDeferredToolCalls(handler=handler)]
    async with agent.run_stream_events(REFUND_REQUEST, capabilities=capabilities) as events:
        async for event in events:
            events_seen.append(type(event).__name__)
            if isinstance(event, AgentRunResultEvent):
                output = event.result.output
                messages = event.result.all_messages()
    return {
        "ok": judged(approve, messages, output, drafts) and asked == [["create_draft"]],
        "asked_for": asked,
        "drafts_written": drafts_written(drafts),
        "output": output,
        "events": sorted(set(events_seen)),
    }


async def check_restart(provider: core.Provider, workdir: Path, approve: bool) -> dict[str, Any]:
    drafts = workdir / "drafts.jsonl"
    first = await core.build_agent(provider, drafts).run(REFUND_REQUEST)
    if not isinstance(first.output, DeferredToolRequests):
        return {"ok": False, "why": f"the run did not stop for approval: {first.output!r}"}
    saved = workdir / "paused_run.json"
    saved.write_bytes(first.all_messages_json())
    pending = first.output
    # The restart: a new agent, a new tool server process, and the history read back from disk.
    history = ModelMessagesTypeAdapter.validate_json(saved.read_bytes())
    second = await core.build_agent(provider, drafts).run(
        message_history=history, deferred_tool_results=answer_for(pending, approve)
    )
    return {
        "ok": judged(approve, second.all_messages(), second.output, drafts),
        "paused_on": [call.tool_name for call in pending.approvals],
        "saved_bytes": saved.stat().st_size,
        "drafts_written": drafts_written(drafts),
        "output": second.output,
    }


async def approve_inline(provider: core.Provider, workdir: Path) -> dict[str, Any]:
    return await check_inline(provider, workdir, approve=True)


async def deny_inline(provider: core.Provider, workdir: Path) -> dict[str, Any]:
    return await check_inline(provider, workdir, approve=False)


async def approve_after_restart(provider: core.Provider, workdir: Path) -> dict[str, Any]:
    return await check_restart(provider, workdir, approve=True)


async def deny_after_restart(provider: core.Provider, workdir: Path) -> dict[str, Any]:
    return await check_restart(provider, workdir, approve=False)


CHECKS: dict[str, Check] = {
    "environment": check_environment,
    "streaming": check_streaming,
    "approve_inline": approve_inline,
    "deny_inline": deny_inline,
    "approve_after_restart": approve_after_restart,
    "deny_after_restart": deny_after_restart,
}


def traps() -> dict[str, bool]:
    """Whether each FastMCP stdio transport would hand the child the decoy, given one named variable."""
    named = {"SOME_TOOL_KEY": "value"}
    built = {
        "StdioTransport": StdioTransport(command="uvx", args=["some-mcp-server"], env=named),
        "UvxStdioTransport": UvxStdioTransport(tool_name="some-mcp-server", env_vars=named),
        "NpxStdioTransport": NpxStdioTransport(package="some-mcp-server", env_vars=named),
    }
    return {name: DECOY in (transport.env or {}) for name, transport in built.items()}


async def mcp_start_seconds(workdir: Path, runs: int = 3) -> list[float]:
    """From nothing to the server's tool list: the child's start, the handshake and `tools/list`."""
    timings = []
    for _ in range(runs):
        started = time.perf_counter()
        async with core.tool_server(workdir / "drafts.jsonl") as toolset:
            await toolset.list_tools()
        timings.append(round(time.perf_counter() - started, 3))
    return timings


def denials_as_sent() -> dict[str, Any]:
    """The element that carried the denial back to each emulated provider, as the agent sent it."""
    found: dict[str, Any] = {}
    for request in providers.REQUESTS:
        body = request["body"]
        elements = body.get("messages") or body.get("input") or []
        for element in elements:
            if isinstance(element, dict) and DENIAL in json.dumps(element) and request["protocol"] not in found:
                found[request["protocol"]] = element
    return found


def emulated_providers() -> list[core.Provider]:
    base = providers.serve_in_background()
    return [
        core.Provider("anthropic", "claude-haiku-4-5", "emulated", f"{base}/anthropic"),
        core.Provider("openai", "gpt-5-mini", "emulated", f"{base}/openai/v1"),
        core.Provider("openai-compatible", "qwen3:8b", "emulated", f"{base}/local/v1"),
    ]


def live_providers() -> list[core.Provider]:
    found = []
    if key := os.environ.get("ANTHROPIC_API_KEY"):
        found.append(
            core.Provider(
                "anthropic", os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5"), key, "https://api.anthropic.com"
            )
        )
    if key := os.environ.get("OPENAI_API_KEY"):
        found.append(
            core.Provider("openai", os.environ.get("OPENAI_MODEL", "gpt-5-mini"), key, "https://api.openai.com/v1")
        )
    if base := os.environ.get("LOCAL_BASE_URL"):
        found.append(core.Provider("openai-compatible", os.environ.get("LOCAL_MODEL", "qwen3:8b"), "local", base))
    return found


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--live", action="store_true", help="run against the real providers")
    args = parser.parse_args()
    global EMULATED
    EMULATED = not args.live
    os.environ[DECOY] = "if this value reaches the tool server, the check fails"
    chosen = live_providers() if args.live else emulated_providers()
    if not chosen:
        parser.error("--live found no provider: set ANTHROPIC_API_KEY, OPENAI_API_KEY or LOCAL_BASE_URL")
    report: dict[str, Any] = {
        "mode": "live" if args.live else "emulated",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "versions": {
            name: importlib.metadata.version(name)
            for name in ("pydantic-ai-slim", "fastmcp-slim", "mcp", "anthropic", "openai")
        },
        "traps": traps(),
        "providers": {},
    }
    with tempfile.TemporaryDirectory() as scratch:
        report["mcp_start_s"] = await mcp_start_seconds(Path(scratch))
        for provider in chosen:
            results: dict[str, Any] = {}
            for name, check in CHECKS.items():
                workdir = Path(scratch) / provider.kind / name
                workdir.mkdir(parents=True)
                started = time.perf_counter()
                try:
                    results[name] = await check(provider, workdir)
                except Exception as failure:  # a spike reports every failure rather than stopping at the first
                    results[name] = {"ok": False, "error": f"{type(failure).__name__}: {failure}"}
                results[name]["s"] = round(time.perf_counter() - started, 3)
            report["providers"][f"{provider.kind} ({provider.model})"] = results
    report["outbound"] = sorted({host for host in CONNECTIONS if host not in LOOPBACK})
    if not args.live:
        report["denials_as_sent"] = denials_as_sent()
        report["refused_by_emulator"] = [r["refused"] for r in providers.REQUESTS if "refused" in r]
        report["requests_by_protocol"] = {
            p: sum(r["protocol"] == p for r in providers.REQUESTS) for p in ("anthropic", "responses", "chat")
        }
    report["all_ok"] = all(result["ok"] for checks in report["providers"].values() for result in checks.values()) and (
        args.live or not report["outbound"]
    )
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
