"""The browser half of the spike: an answer streamed to a page, and a write tool the page approves.

    python web.py            # serves http://127.0.0.1:<port>/ against the emulated providers

The shape Bailey's app server would take, cut to what the spike has to show:

- **Loopback, with a per-session token.** The page receives a random token when it loads and
  sends it on every API request; a request without it is refused, and so is one whose `Host` is
  not a loopback name (DNS rebinding). Any web page can reach a loopback port, so this is there
  from the first commit.
- **One run, streamed.** `POST /api/runs` starts a run; `GET /api/runs/{id}/events` streams it as
  server-sent events, read with `fetch` because `EventSource` cannot send the token.
- **Approval inside the run.** When the model calls a write tool, the run's handler sends an
  `approval` event and waits; `POST /api/runs/{id}/approvals` answers it, and the same run goes
  on streaming. "Always" is remembered for the session, so the next call does not ask.
- **Text, never markup.** The page writes model output with `textContent` only, and the CSP
  allows no inline script.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import socket
import sys
import tempfile
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn
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
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

sys.path.insert(0, str(Path(__file__).parent))

import core  # noqa: E402
import providers  # noqa: E402

HERE = Path(__file__).parent
DENIAL = "The user declined this action. Do not try it again."
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")


@dataclass
class Run:
    events: asyncio.Queue[dict[str, Any] | None] = field(default_factory=asyncio.Queue)
    pending: dict[str, asyncio.Future[str]] = field(default_factory=dict)
    task: asyncio.Task[None] | None = None


@dataclass
class Session:
    token: str
    providers: dict[str, core.Provider]
    drafts: Path
    runs: dict[str, Run] = field(default_factory=dict)
    always: set[str] = field(default_factory=set)


SESSION: Session


def _host_is_loopback(request: Request) -> bool:
    host = request.headers.get("host", "")
    name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
    return name in LOOPBACK_HOSTS


def _authorised(request: Request) -> bool:
    sent = request.headers.get("authorization", "")
    return _host_is_loopback(request) and secrets.compare_digest(sent, f"Bearer {SESSION.token}")


def _refused() -> Response:
    return JSONResponse({"error": "This request did not come from Bailey's page."}, status_code=401)


async def page(request: Request) -> Response:
    if not _host_is_loopback(request):
        return _refused()
    html = (HERE / "page.html").read_text(encoding="utf-8").replace("__SESSION_TOKEN__", SESSION.token)
    return Response(html, media_type="text/html", headers={"Content-Security-Policy": CSP, "Cache-Control": "no-store"})


async def asset(request: Request) -> Response:
    name = request.path_params["name"]
    if name not in ("page.js", "page.css"):
        return Response(status_code=404)
    return FileResponse(HERE / name, headers={"Content-Security-Policy": CSP})


async def drive(run: Run, provider: core.Provider, message: str) -> None:
    async def approve(ctx: RunContext[None], requests: DeferredToolRequests) -> DeferredToolResults:
        answers: dict[str, ToolApproved | ToolDenied] = {}
        for call in requests.approvals:
            if call.tool_name in SESSION.always:
                answers[call.tool_call_id] = ToolApproved()
                await run.events.put({"type": "approved_always", "tool": call.tool_name})
                continue
            decision: asyncio.Future[str] = asyncio.get_running_loop().create_future()
            run.pending[call.tool_call_id] = decision
            await run.events.put(
                {"type": "approval", "id": call.tool_call_id, "tool": call.tool_name, "args": call.args_as_dict()}
            )
            answer = await decision
            if answer == "always":
                SESSION.always.add(call.tool_name)
            answers[call.tool_call_id] = ToolDenied(DENIAL) if answer == "no" else ToolApproved()
        return DeferredToolResults(approvals=answers)

    agent = core.build_agent(provider, SESSION.drafts)
    try:
        async with agent.run_stream_events(message, capabilities=[HandleDeferredToolCalls(handler=approve)]) as events:
            async for event in events:
                if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart) and event.part.content:
                    await run.events.put({"type": "text", "text": event.part.content})
                elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    await run.events.put({"type": "text", "text": event.delta.content_delta})
                elif isinstance(event, FunctionToolCallEvent):
                    await run.events.put({"type": "tool_call", "tool": event.part.tool_name})
                elif isinstance(event, FunctionToolResultEvent):
                    await run.events.put({"type": "tool_result", "tool": event.part.tool_name})
                elif isinstance(event, AgentRunResultEvent):
                    usage = event.result.usage
                    await run.events.put(
                        {"type": "done", "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}
                    )
    except Exception as failure:  # the page shows the failure rather than hanging
        await run.events.put({"type": "error", "message": f"{type(failure).__name__}: {failure}"})
    finally:
        await run.events.put(None)


async def start_run(request: Request) -> Response:
    if not _authorised(request):
        return _refused()
    body = await request.json()
    provider = SESSION.providers.get(body.get("provider", ""))
    if provider is None or not str(body.get("message", "")).strip():
        return JSONResponse({"error": "Choose a provider and write a message."}, status_code=400)
    run_id = secrets.token_hex(8)
    run = SESSION.runs[run_id] = Run()
    run.task = asyncio.create_task(drive(run, provider, body["message"]))
    return JSONResponse({"run": run_id})


async def run_events(request: Request) -> Response:
    if not _authorised(request):
        return _refused()
    run = SESSION.runs.get(request.path_params["run_id"])
    if run is None:
        return JSONResponse({"error": "No such run."}, status_code=404)

    async def stream() -> AsyncIterator[bytes]:
        while (event := await run.events.get()) is not None:
            yield f"data: {json.dumps(event)}\n\n".encode()

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


async def answer_approval(request: Request) -> Response:
    if not _authorised(request):
        return _refused()
    run = SESSION.runs.get(request.path_params["run_id"])
    body = await request.json()
    decision = run.pending.pop(str(body.get("id")), None) if run else None
    if decision is None or decision.done():
        return JSONResponse({"error": "Nothing is waiting for that answer."}, status_code=404)
    if body.get("answer") not in ("once", "always", "no"):
        return JSONResponse({"error": "Answer once, always or no."}, status_code=400)
    decision.set_result(body["answer"])
    return JSONResponse({"ok": True})


def build_app(session: Session) -> Starlette:
    global SESSION
    SESSION = session
    return Starlette(
        routes=[
            Route("/", page),
            Route("/static/{name}", asset),
            Route("/api/runs", start_run, methods=["POST"]),
            Route("/api/runs/{run_id}/events", run_events),
            Route("/api/runs/{run_id}/approvals", answer_approval, methods=["POST"]),
        ]
    )


def emulated_session(drafts: Path) -> Session:
    base = providers.serve_in_background()
    return Session(
        token=secrets.token_urlsafe(32),
        providers={
            "anthropic": core.Provider("anthropic", "claude-haiku-4-5", "emulated", f"{base}/anthropic"),
            "openai": core.Provider("openai", "gpt-5-mini", "emulated", f"{base}/openai/v1"),
            "openai-compatible": core.Provider("openai-compatible", "qwen3:8b", "emulated", f"{base}/local/v1"),
        },
        drafts=drafts,
    )


def serve_in_background(session: Session) -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(build_app(session), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.02)
    return f"http://127.0.0.1:{port}"


if __name__ == "__main__":
    drafts = Path(tempfile.mkdtemp()) / "drafts.jsonl"
    url = serve_in_background(emulated_session(drafts))
    print(f"Bailey spike: {url}/  (drafts land in {drafts})", flush=True)
    threading.Event().wait()
