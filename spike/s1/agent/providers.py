"""Three model providers, emulated on their own wire protocols, so the spike runs without keys.

Bailey talks to three kinds of provider, and each speaks a different protocol:

- Anthropic, over the Messages API: `POST <base>/v1/messages`;
- OpenAI, over the Responses API, which PydanticAI's `openai:` models use: `POST <base>/responses`;
- any OpenAI-compatible endpoint (Ollama, LM Studio, llama.cpp, vLLM), over Chat Completions:
  `POST <base>/chat/completions`.

The emulator answers each as a scripted model would (`next_turn`) and streams the way the real
service streams, so the official `anthropic` and `openai` SDKs parse its replies through the same
code PydanticAI runs against the real thing. It also **refuses what the real services refuse**,
with a 400: a tool call whose result never came back, a result for a call never made, a tool the
request did not offer. A denied write tool therefore has to reach the model as a proper tool
result on every protocol, or the check fails here before it fails in front of a user.

Every request is kept in `REQUESTS`, so a check can read exactly what the agent sent.

This is a fixture, not a model: it proves the plumbing (streaming, tool calls, approval, resume)
and nothing about how well any model uses tools. `check.py --live` runs the same checks against
the real providers.
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

#: The delay between two streamed pieces, so a reader can see a stream arrive in steps.
CHUNK_DELAY = 0.03

DRAFT = "Hi Dana, I'm sorry the jacket arrived damaged. I've started a full refund for order 5531."
#: Carries markup on purpose: the page must show it as text and never build an element from it.
GREETING = "Hello! I am a scripted model. <b>Markup</b> in an answer stays text, and this sentence arrives in pieces."

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
    call: Call | None = None


@dataclass
class Conversation:
    """What the scripted model reads from a request, whichever protocol carried it."""

    user: list[str] = field(default_factory=list)
    results: list[tuple[str, str]] = field(default_factory=list)  # (tool name, result as text)
    tools: list[str] = field(default_factory=list)


_ids = itertools.count(1)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{next(_ids):04d}"


def next_turn(conversation: Conversation) -> Turn:
    """The scripted model. Three scenarios, chosen by the latest user message."""
    asked = conversation.user[-1].lower() if conversation.user else ""
    done = [name for name, _ in conversation.results]
    turn: Turn
    if "environment" in asked:
        if "visible_environment" not in done:
            turn = Turn("Checking what the tool server can see.", Call(_new_id("call"), "visible_environment", {}))
        else:
            turn = Turn("The tool server sees these variables: " + conversation.results[-1][1])
    elif "refund" in asked:
        if "search_threads" not in done:
            turn = Turn(
                "Looking for the latest refund request.", Call(_new_id("call"), "search_threads", {"query": "refund"})
            )
        elif "create_draft" not in done:
            turn = Turn("", Call(_new_id("call"), "create_draft", {"thread_id": "t-1042", "body": DRAFT}))
        else:
            turn = Turn("create_draft answered: " + conversation.results[-1][1])
    else:
        turn = Turn(GREETING)
    if turn.call and turn.call.name not in conversation.tools:
        raise Violation(f"the model was not offered {turn.call.name!r}; offered: {conversation.tools}")
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
        1, (len(turn.text) + len(json.dumps(turn.call.arguments if turn.call else {}))) // 4
    )


# ---------------------------------------------------------------------------------------------
# Anthropic Messages
# ---------------------------------------------------------------------------------------------


def anthropic_conversation(body: dict[str, Any]) -> Conversation:
    conversation = Conversation(tools=[tool["name"] for tool in body.get("tools", [])])
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
                conversation.results.append((pending.pop(block["tool_use_id"]), _text_of(block.get("content"))))
            elif block["type"] == "text":
                conversation.user.append(block["text"])
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
    if turn.call:
        block = {"type": "tool_use", "id": turn.call.id, "name": turn.call.name, "input": {}}
        yield event("content_block_start", {"type": "content_block_start", "index": index, "content_block": block})
        for piece in _pieces(json.dumps(turn.call.arguments), 16):
            delta = {"type": "input_json_delta", "partial_json": piece}
            yield event("content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta})
        yield event("content_block_stop", {"type": "content_block_stop", "index": index})
    stop = "tool_use" if turn.call else "end_turn"
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
    if turn.call:
        content.append({"type": "tool_use", "id": turn.call.id, "name": turn.call.name, "input": turn.call.arguments})
    return {
        "id": _new_id("msg"),
        "type": "message",
        "role": "assistant",
        "model": body["model"],
        "content": content,
        "stop_reason": "tool_use" if turn.call else "end_turn",
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
    items = body["input"] if isinstance(body["input"], list) else [{"role": "user", "content": body["input"]}]
    pending: dict[str, str] = {}
    for item in items:
        kind = item.get("type", "message")
        if kind == "function_call":
            pending[item["call_id"]] = item["name"]
        elif kind == "function_call_output":
            if item["call_id"] not in pending:
                raise Violation(f"function_call_output for a call that was never made: {item['call_id']}")
            conversation.results.append((pending.pop(item["call_id"]), _text_of(item.get("output"))))
        elif kind == "message":
            if pending:
                raise Violation(f"No tool output found for function call {sorted(pending)}")
            if item.get("role") == "user":
                conversation.user.append(_text_of(item.get("content")))
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
    if turn.call:
        output.append(
            {
                "id": _new_id("fc"),
                "type": "function_call",
                "status": "completed",
                "call_id": turn.call.id,
                "name": turn.call.name,
                "arguments": json.dumps(turn.call.arguments),
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
            conversation.results.append((pending.pop(message["tool_call_id"]), _text_of(message.get("content"))))
            continue
        if pending:
            raise Violation(f"tool_call_ids without a tool message: {sorted(pending)}")
        if message["role"] == "assistant":
            for call in message.get("tool_calls") or []:
                pending[call["id"]] = call["function"]["name"]
        elif message["role"] == "user":
            conversation.user.append(_text_of(message.get("content")))
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
    if turn.call:
        start = {
            "index": 0,
            "id": turn.call.id,
            "type": "function",
            "function": {"name": turn.call.name, "arguments": ""},
        }
        yield chunk({"tool_calls": [start]})
        for piece in _pieces(json.dumps(turn.call.arguments), 16):
            yield chunk({"tool_calls": [{"index": 0, "function": {"arguments": piece}}]})
    yield chunk({}, "tool_calls" if turn.call else "stop")
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
    if turn.call:
        function = {"name": turn.call.name, "arguments": json.dumps(turn.call.arguments)}
        message["tool_calls"] = [{"id": turn.call.id, "type": "function", "function": function}]
    return {
        "id": _new_id("chatcmpl"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body["model"],
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if turn.call else "stop"}],
        "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out, "total_tokens": tokens_in + tokens_out},
    }


# ---------------------------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------------------------


async def _answer(request: Request, protocol: str) -> Response:
    body = await request.json()
    REQUESTS.append({"protocol": protocol, "path": request.url.path, "body": body, "at": time.time()})
    try:
        if protocol == "anthropic":
            turn = next_turn(anthropic_conversation(body))
        elif protocol == "responses":
            turn = next_turn(responses_conversation(body))
        else:
            turn = next_turn(chat_conversation(body))
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
