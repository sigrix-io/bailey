"""Three model providers, emulated on their own wire protocols, so the agent core is tested without keys.

Ported from the S1 spike (``spike/s1/agent/providers.py``), whose checks S1's ruling made
this story's contract tests: Anthropic over the Messages API, OpenAI over the Responses
API, and an OpenAI-compatible server over Chat Completions, each streamed the way the
real service streams, so the official SDKs parse the replies through the same code
PydanticAI runs against the real thing. It refuses what the real services refuse (a
tool call left without its result, a result for a call never made, a tool the request
did not offer), with a 400.

The scripted model here answers as an assistant with skills should: a request naming a
skill's subject gets a ``load_skill`` call for that skill, then an answer that quotes
the first line of what the call returned, so a test can tell which skill's body reached
the model. Offered the mailbox's tools (``tests/tool_servers/mailbox.py``), a request to
reply searches the threads, then drafts the reply, then answers by quoting what the
draft tool returned, so a test can tell whether a "no" reached the model; asked to do it
"at once", it makes both calls in one reply, as models do. Every request is kept in
``REQUESTS``.

This proves the plumbing, never how well a model chooses a skill. The live run against
the real providers is the owner's step before S6 merges (S1's ruling, guard 1).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

#: The delay between two streamed pieces, so a stream arrives in steps.
CHUNK_DELAY = 0.005

#: Carries markup on purpose: the page must show it as text and never build an element from it.
GREETING = "Hello! I am a scripted model. <b>Markup</b> in an answer stays text, and this arrives in pieces."

#: Which skill a request's words call for, in the order they are tried.
SKILL_WORDS = (("summar", "summarise"), ("email", "draft-email"))

REQUESTS: list[dict[str, Any]] = []


class Violation(Exception):
    """A request the real service would refuse."""


@dataclass
class Call:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Turn:
    text: str = ""
    calls: list[Call] = field(default_factory=list)


@dataclass
class Conversation:
    """What the scripted model reads from a request, whichever protocol carried it."""

    system: str = ""
    user: list[str] = field(default_factory=list)
    results: list[tuple[str, str]] = field(default_factory=list)  # (tool name, result as text)
    tools: list[str] = field(default_factory=list)
    #: The results since the person's latest message: what this turn has done so far.
    turn_results: list[tuple[str, str]] = field(default_factory=list)

    def said(self, text: str) -> None:
        self.user.append(text)
        self.turn_results = []

    def got(self, tool: str, result: str) -> None:
        self.results.append((tool, result))
        self.turn_results.append((tool, result))


_ids = itertools.count(1)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{next(_ids):04d}"


#: The reply the scripted model drafts with the mailbox's write tool.
DRAFT_BODY = "Sorry about the seam. We will refund order 5531 today."


def _mailbox_turn(conversation: Conversation, asked: str) -> Turn:
    done = dict(conversation.turn_results)
    search = Call(_new_id("call"), "search_threads", {"query": "refund"})
    draft = Call(_new_id("call"), "create_draft", {"thread_id": "t-1042", "body": DRAFT_BODY})
    if "create_draft" in done:
        return Turn(f"The draft tool said: {done['create_draft']}")
    if "at once" in asked and "search_threads" not in done:
        return Turn("", [search, draft])
    if "search_threads" not in done:
        return Turn("", [search])
    return Turn("", [draft])


def next_turn(conversation: Conversation) -> Turn:
    """The scripted model: a reply through the mailbox's tools, or a skill loaded and answered from."""
    asked = conversation.user[-1].lower() if conversation.user else ""
    wanted = next((skill for word, skill in SKILL_WORDS if word in asked), None)
    turn: Turn
    if "reply" in asked and {"search_threads", "create_draft"} <= set(conversation.tools):
        turn = _mailbox_turn(conversation, asked)
    elif "environment" in asked and "visible_environment" in conversation.tools:
        done = dict(conversation.turn_results)
        if "visible_environment" in done:
            turn = Turn(f"The tool server sees: {done['visible_environment']}")
        else:
            turn = Turn("", [Call(_new_id("call"), "visible_environment", {})])
    elif wanted is not None and "load_skill" in conversation.tools:
        loaded = [result for name, result in conversation.results if name == "load_skill"]
        if not loaded:
            turn = Turn("", [Call(_new_id("call"), "load_skill", {"name": wanted})])
        else:
            first_line = loaded[-1].strip().splitlines()[0] if loaded[-1].strip() else ""
            turn = Turn(f"Following the {wanted} skill. It begins: {first_line}")
    else:
        turn = Turn(GREETING)
    for call in turn.calls:
        if call.name not in conversation.tools:
            raise Violation(f"the model was not offered {call.name!r}; offered: {conversation.tools}")
    return turn


def _pieces(text: str, size: int = 6) -> Iterator[str]:
    for start in range(0, len(text), size):
        yield text[start : start + size]


def _text_of(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_text_of(part) for part in content)
    if isinstance(content, dict):
        return str(content.get("text") or content.get("output") or "")
    return str(content)


def _usage(body: dict[str, Any], turn: Turn) -> tuple[int, int]:
    return len(json.dumps(body)) // 4, max(
        1, (len(turn.text) + sum(len(json.dumps(call.arguments)) for call in turn.calls)) // 4
    )


# ---------------------------------------------------------------------------------------------
# Anthropic Messages
# ---------------------------------------------------------------------------------------------


def anthropic_conversation(body: dict[str, Any]) -> Conversation:
    conversation = Conversation(tools=[tool["name"] for tool in body.get("tools", [])])
    conversation.system = _text_of(body.get("system"))
    pending: dict[str, str] = {}
    for message in body["messages"]:
        content = message["content"]
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        if message["role"] == "assistant":
            if pending:
                raise Violation(f"tool_use ids without a tool_result immediately after: {sorted(pending)}")
            for block in blocks:
                if block["type"] == "tool_use":
                    pending[block["id"]] = block["name"]
            continue
        for block in blocks:
            if block["type"] == "tool_result":
                if block["tool_use_id"] not in pending:
                    raise Violation(
                        f"tool_result for a tool_use that is not in the previous message: {block['tool_use_id']}"
                    )
                conversation.got(pending.pop(block["tool_use_id"]), _text_of(block.get("content")))
            elif block["type"] == "text":
                conversation.said(block["text"])
        if pending:
            raise Violation(f"tool_use ids without a tool_result immediately after: {sorted(pending)}")
    return conversation


async def anthropic_stream(body: dict[str, Any], turn: Turn) -> AsyncIterator[bytes]:
    def event(name: str, data: dict[str, Any]) -> bytes:
        return f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()

    tokens_in, tokens_out = _usage(body, turn)
    message = {
        "id": _new_id("msg"),
        "type": "message",
        "role": "assistant",
        "model": body["model"],
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": tokens_in, "output_tokens": 1},
    }
    yield event("message_start", {"type": "message_start", "message": message})
    index = 0
    if turn.text:
        yield event(
            "content_block_start",
            {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}},
        )
        for piece in _pieces(turn.text):
            await asyncio.sleep(CHUNK_DELAY)
            delta = {"type": "text_delta", "text": piece}
            yield event("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta})
        yield event("content_block_stop", {"type": "content_block_stop", "index": index})
        index += 1
    for call in turn.calls:
        block = {"type": "tool_use", "id": call.id, "name": call.name, "input": {}}
        yield event("content_block_start", {"type": "content_block_start", "index": index, "content_block": block})
        for piece in _pieces(json.dumps(call.arguments), 16):
            delta = {"type": "input_json_delta", "partial_json": piece}
            yield event("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta})
        yield event("content_block_stop", {"type": "content_block_stop", "index": index})
        index += 1
    stop = "tool_use" if turn.calls else "end_turn"
    yield event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop, "stop_sequence": None},
            "usage": {"output_tokens": tokens_out},
        },
    )
    yield event("message_stop", {"type": "message_stop"})


def anthropic_message(body: dict[str, Any], turn: Turn) -> dict[str, Any]:
    tokens_in, tokens_out = _usage(body, turn)
    content: list[dict[str, Any]] = []
    if turn.text:
        content.append({"type": "text", "text": turn.text})
    for call in turn.calls:
        content.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments})
    return {
        "id": _new_id("msg"),
        "type": "message",
        "role": "assistant",
        "model": body["model"],
        "content": content,
        "stop_reason": "tool_use" if turn.calls else "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
    }


# ---------------------------------------------------------------------------------------------
# OpenAI Responses
# ---------------------------------------------------------------------------------------------


def responses_conversation(body: dict[str, Any]) -> Conversation:
    conversation = Conversation(
        tools=[tool["name"] for tool in body.get("tools", []) if tool.get("type") == "function"]
    )
    conversation.system = _text_of(body.get("instructions"))
    items = body["input"] if isinstance(body["input"], list) else [{"role": "user", "content": body["input"]}]
    pending: dict[str, str] = {}
    for item in items:
        kind = item.get("type", "message")
        if kind == "function_call":
            pending[item["call_id"]] = item["name"]
        elif kind == "function_call_output":
            if item["call_id"] not in pending:
                raise Violation(f"function_call_output for a call that was never made: {item['call_id']}")
            conversation.got(pending.pop(item["call_id"]), _text_of(item.get("output")))
        elif kind == "message":
            if pending:
                raise Violation(f"No tool output found for function call {sorted(pending)}")
            if item.get("role") == "user":
                conversation.said(_text_of(item.get("content")))
            elif item.get("role") in ("system", "developer"):
                conversation.system += _text_of(item.get("content"))
    if pending:
        raise Violation(f"No tool output found for function call {sorted(pending)}")
    return conversation


def _responses_output(turn: Turn) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if turn.text:
        part = {"type": "output_text", "text": turn.text, "annotations": []}
        output.append(
            {"id": _new_id("msg"), "type": "message", "status": "completed", "role": "assistant", "content": [part]}
        )
    for call in turn.calls:
        output.append(
            {
                "id": _new_id("fc"),
                "type": "function_call",
                "status": "completed",
                "call_id": call.id,
                "name": call.name,
                "arguments": json.dumps(call.arguments),
            }
        )
    return output


def _response(body: dict[str, Any], turn: Turn, status: str, output: list[dict[str, Any]]) -> dict[str, Any]:
    tokens_in, tokens_out = _usage(body, turn)
    return {
        "id": "resp_" + str(abs(hash(json.dumps(body))) % 10**8),
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": body["model"],
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": body.get("tools", []),
        "error": None,
        "incomplete_details": None,
        "usage": {
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


async def responses_stream(body: dict[str, Any], turn: Turn) -> AsyncIterator[bytes]:
    sequence = itertools.count()

    def event(data: dict[str, Any]) -> bytes:
        data["sequence_number"] = next(sequence)
        return f"event: {data['type']}\ndata: {json.dumps(data)}\n\n".encode()

    output = _responses_output(turn)
    yield event({"type": "response.created", "response": _response(body, turn, "in_progress", [])})
    for index, item in enumerate(output):
        if item["type"] == "message":
            text = item["content"][0]["text"]
            started = {**item, "status": "in_progress", "content": []}
            yield event({"type": "response.output_item.added", "output_index": index, "item": started})
            empty = {"type": "output_text", "text": "", "annotations": []}
            where = {"item_id": item["id"], "output_index": index, "content_index": 0}
            yield event({"type": "response.content_part.added", **where, "part": empty})
            for piece in _pieces(text):
                await asyncio.sleep(CHUNK_DELAY)
                yield event({"type": "response.output_text.delta", **where, "delta": piece, "logprobs": []})
            yield event({"type": "response.output_text.done", **where, "text": text, "logprobs": []})
            yield event({"type": "response.content_part.done", **where, "part": item["content"][0]})
        else:
            yield event(
                {
                    "type": "response.output_item.added",
                    "output_index": index,
                    "item": {**item, "status": "in_progress", "arguments": ""},
                }
            )
            for piece in _pieces(item["arguments"], 16):
                yield event(
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": item["id"],
                        "output_index": index,
                        "delta": piece,
                    }
                )
            yield event(
                {
                    "type": "response.function_call_arguments.done",
                    "item_id": item["id"],
                    "output_index": index,
                    "arguments": item["arguments"],
                }
            )
        yield event({"type": "response.output_item.done", "output_index": index, "item": item})
    yield event({"type": "response.completed", "response": _response(body, turn, "completed", output)})


# ---------------------------------------------------------------------------------------------
# Chat Completions (OpenAI-compatible local servers)
# ---------------------------------------------------------------------------------------------


def chat_conversation(body: dict[str, Any]) -> Conversation:
    conversation = Conversation(tools=[tool["function"]["name"] for tool in body.get("tools", [])])
    pending: dict[str, str] = {}
    for message in body["messages"]:
        if message["role"] == "tool":
            if message["tool_call_id"] not in pending:
                raise Violation(f"a tool message for a call that was never made: {message['tool_call_id']}")
            conversation.got(pending.pop(message["tool_call_id"]), _text_of(message.get("content")))
            continue
        if pending:
            raise Violation(f"tool_call_ids without a tool message: {sorted(pending)}")
        if message["role"] == "assistant":
            for call in message.get("tool_calls") or []:
                pending[call["id"]] = call["function"]["name"]
        elif message["role"] == "user":
            conversation.said(_text_of(message.get("content")))
        elif message["role"] in ("system", "developer"):
            conversation.system += _text_of(message.get("content"))
    if pending:
        raise Violation(f"tool_call_ids without a tool message: {sorted(pending)}")
    return conversation


async def chat_stream(body: dict[str, Any], turn: Turn) -> AsyncIterator[bytes]:
    completion_id = _new_id("chatcmpl")

    def chunk(delta: dict[str, Any], finish: str | None = None) -> bytes:
        data = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": body["model"],
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(data)}\n\n".encode()

    yield chunk({"role": "assistant", "content": ""})
    for piece in _pieces(turn.text):
        await asyncio.sleep(CHUNK_DELAY)
        yield chunk({"content": piece})
    for position, call in enumerate(turn.calls):
        start = {
            "index": position,
            "id": call.id,
            "type": "function",
            "function": {"name": call.name, "arguments": ""},
        }
        yield chunk({"tool_calls": [start]})
        for piece in _pieces(json.dumps(call.arguments), 16):
            yield chunk({"tool_calls": [{"index": position, "function": {"arguments": piece}}]})
    yield chunk({}, "tool_calls" if turn.calls else "stop")
    if (body.get("stream_options") or {}).get("include_usage"):
        tokens_in, tokens_out = _usage(body, turn)
        usage = {"prompt_tokens": tokens_in, "completion_tokens": tokens_out, "total_tokens": tokens_in + tokens_out}
        data = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": body["model"],
        }
        yield f"data: {json.dumps({**data, 'choices': [], 'usage': usage})}\n\n".encode()
    yield b"data: [DONE]\n\n"


def chat_completion(body: dict[str, Any], turn: Turn) -> dict[str, Any]:
    tokens_in, tokens_out = _usage(body, turn)
    message: dict[str, Any] = {"role": "assistant", "content": turn.text or None}
    if turn.calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in turn.calls
        ]
    return {
        "id": _new_id("chatcmpl"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body["model"],
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if turn.calls else "stop"}],
        "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out, "total_tokens": tokens_in + tokens_out},
    }


# ---------------------------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------------------------


#: The key the emulator refuses, as a real service refuses a revoked one.
BAD_KEY = "sk-revoked-key"

#: A tool after whose result the next request is refused, as a provider that fails mid-run
#: would; empty for none. A test sets it to see what a run that failed after a tool keeps.
FAIL_AFTER = ""


async def _answer(request: Request, protocol: str) -> Response:
    body = await request.json()
    sent_key = request.headers.get("x-api-key") or request.headers.get("authorization", "").removeprefix("Bearer ")
    REQUESTS.append({"protocol": protocol, "path": request.url.path, "body": body, "at": time.time(), "key": sent_key})
    if sent_key == BAD_KEY:
        error = {"type": "authentication_error", "message": "invalid x-api-key"}
        return JSONResponse({"type": "error", "error": error}, status_code=401)
    try:
        if protocol == "anthropic":
            conversation = anthropic_conversation(body)
        elif protocol == "responses":
            conversation = responses_conversation(body)
        else:
            conversation = chat_conversation(body)
        if FAIL_AFTER and any(name == FAIL_AFTER for name, _ in conversation.turn_results):
            error = {"type": "invalid_request_error", "message": f"emulated failure after {FAIL_AFTER}"}
            return JSONResponse({"type": "error", "error": error}, status_code=400)
        turn = next_turn(conversation)
    except Violation as refused:
        REQUESTS[-1]["refused"] = str(refused)
        return JSONResponse({"error": {"type": "invalid_request_error", "message": str(refused)}}, status_code=400)
    if body.get("stream"):
        streams = {"anthropic": anthropic_stream, "responses": responses_stream, "chat": chat_stream}
        return StreamingResponse(streams[protocol](body, turn), media_type="text/event-stream")
    if protocol == "anthropic":
        return JSONResponse(anthropic_message(body, turn))
    if protocol == "responses":
        return JSONResponse(_response(body, turn, "completed", _responses_output(turn)))
    return JSONResponse(chat_completion(body, turn))


async def anthropic_messages(request: Request) -> Response:
    return await _answer(request, "anthropic")


async def openai_responses(request: Request) -> Response:
    return await _answer(request, "responses")


async def chat_completions(request: Request) -> Response:
    return await _answer(request, "chat")


app = Starlette(
    routes=[
        Route("/anthropic/v1/messages", anthropic_messages, methods=["POST"]),
        Route("/openai/v1/responses", openai_responses, methods=["POST"]),
        Route("/local/v1/chat/completions", chat_completions, methods=["POST"]),
    ]
)


def serve_in_background() -> str:
    """Start the emulator on a free loopback port in a daemon thread, and return its base URL."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("the provider emulator did not start")
        time.sleep(0.02)
    return f"http://127.0.0.1:{port}"
