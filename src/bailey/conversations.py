"""Conversations in a format of Bailey's own, converted to and from the agent core's at the edge.

S1's ruling (2026-10-16): a user's history is never stored in PydanticAI's message
schema, which would tie every conversation on every machine to one library and its
versions. Each message is stored as a record in ``bailey-message/1``::

    {"format": "bailey-message/1", "role": "user" | "assistant" | "notice", "at": "<UTC time>",
     "parts": [
        {"kind": "text", "text": "..."},
        {"kind": "tool_call", "id": "...", "tool": "create_draft", "args": {...},
         "server": "mailbox", "asked": true},
        {"kind": "tool_result", "id": "...", "tool": "create_draft", "content": "...", "outcome": "denied"},
        {"kind": "retry", "id": "...", "tool": "...", "content": "..."}],
     "model": "...", "provider": "...", "usage": {...}, "estimate": {...}}

A ``user`` record carries what went to the model (the person's text, and a tool's
results); an ``assistant`` record what came back; a ``notice`` what Bailey itself said,
such as a run that failed, which is shown and never sent. :func:`to_records` and
:func:`to_history` are the only two places the library's types are named, so a version
of it that moves its messages changes this module and no stored conversation.

A tool call names its MCP server (``server``), and one the person was asked about
carries ``asked``. A result's ``outcome`` is there when it is not a success: ``denied``
when the person said no. A call that asked and has no result yet is a question the run
is waiting on (:func:`pending`), so it survives a reload and a restart.

A ``retry`` asks the model to try again, and its ``content`` says why. One that answers a
call (its arguments were wrong, or its tool asked for another try) names the call
(``id``) and its ``tool``, and goes back as that call's result, marked to be made again.
One that answers no call, as after a reply with nothing in it, has no ``tool``, and its
``id`` names nothing; it goes back as feedback on that reply. PydanticAI 2.63 split its
retry part in two, one for each, and both are stored as the one was, so a conversation
stored before the split still loads.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from bailey.core import load

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage

# The agent core's types are imported where they are converted, not here: the server
# reads and shows conversations without loading the agent core, which takes seconds.
# They are imported through bailey.core.load, which keeps a first import to one thread.

FORMAT = "bailey-message/1"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def user_record(text: str) -> dict[str, Any]:
    return {"format": FORMAT, "role": "user", "at": _now(), "parts": [{"kind": "text", "text": text}]}


def notice_record(text: str) -> dict[str, Any]:
    return {"format": FORMAT, "role": "notice", "at": _now(), "parts": [{"kind": "text", "text": text}]}


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(content)


#: The outcomes a tool's result may record; a success is left unsaid.
OUTCOMES = ("success", "failed", "denied", "interrupted")


def to_records(
    messages: Sequence[ModelMessage],
    *,
    provider: str,
    model: str,
    servers: Mapping[str, str] | None = None,
    asked: Collection[str] = (),
) -> list[dict[str, Any]]:
    """The run's messages as Bailey stores them.

    ``servers`` names each tool's MCP server, and ``asked`` holds the calls the run
    stopped to ask the person about. A thinking part is dropped: it is the model's own
    working, which nothing later in a conversation needs, and its signed form is a
    provider's."""

    load()
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        RetryFeedbackPart,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    records: list[dict[str, Any]] = []
    for message in messages:
        parts: list[dict[str, Any]] = []
        if isinstance(message, ModelRequest):
            for request_part in message.parts:
                if isinstance(request_part, UserPromptPart):
                    parts.append({"kind": "text", "text": _content_text(request_part.content)})
                elif isinstance(request_part, ToolReturnPart) and request_part.outcome == "retried":
                    # Its words alone: PydanticAI marks a retry as an error each time it sends one.
                    parts.append(
                        {
                            "kind": "retry",
                            "id": request_part.tool_call_id,
                            "tool": request_part.tool_name,
                            "content": request_part.model_response_str(wrap_if_error=False),
                        }
                    )
                elif isinstance(request_part, ToolReturnPart):
                    result: dict[str, Any] = {
                        "kind": "tool_result",
                        "id": request_part.tool_call_id,
                        "tool": request_part.tool_name,
                        "content": request_part.model_response_str(),
                    }
                    if request_part.outcome != "success":
                        result["outcome"] = request_part.outcome
                    parts.append(result)
                elif isinstance(request_part, RetryFeedbackPart):
                    # Its words alone too: PydanticAI frames feedback each time it sends it.
                    parts.append(
                        {"kind": "retry", "id": "", "tool": "", "content": _content_text(request_part.content)}
                    )
            if parts:
                records.append({"format": FORMAT, "role": "user", "at": _now(), "parts": parts})
        elif isinstance(message, ModelResponse):
            for response_part in message.parts:
                if isinstance(response_part, TextPart) and response_part.content:
                    parts.append({"kind": "text", "text": response_part.content})
                elif isinstance(response_part, ToolCallPart):
                    call: dict[str, Any] = {
                        "kind": "tool_call",
                        "id": response_part.tool_call_id,
                        "tool": response_part.tool_name,
                        "args": response_part.args_as_dict(),
                    }
                    server = (servers or {}).get(response_part.tool_name)
                    if server:
                        call["server"] = server
                    if response_part.tool_call_id in asked:
                        call["asked"] = True
                    parts.append(call)
            if parts:
                records.append(
                    {
                        "format": FORMAT,
                        "role": "assistant",
                        "at": _now(),
                        "parts": parts,
                        "provider": provider,
                        "model": model,
                    }
                )
    return records


def to_history(records: Sequence[dict[str, Any]]) -> list[ModelMessage]:
    """The stored records as the agent core's messages.

    A notice is left out, and consecutive ``user`` records become one request, as after a
    run that failed: providers expect the two sides to take turns. A record in a format
    this Bailey does not know is refused rather than half read.
    """

    load()
    from pydantic_ai.messages import (
        ModelRequest,
        ModelRequestPart,
        ModelResponse,
        ModelResponsePart,
        RetryFeedbackPart,
        RetryInfo,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    history: list[ModelMessage] = []
    for record in records:
        if record.get("format") != FORMAT:
            raise ValueError(f"A message stored in {record.get('format')!r}, which this Bailey does not read.")
        role = record.get("role")
        if role == "user":
            request_parts: list[ModelRequestPart] = []
            for part in record.get("parts", []):
                kind = part.get("kind")
                if kind == "text":
                    request_parts.append(UserPromptPart(content=str(part.get("text", ""))))
                elif kind == "tool_result":
                    outcome = part.get("outcome")
                    request_parts.append(
                        ToolReturnPart(
                            tool_name=part["tool"],
                            content=part.get("content", ""),
                            tool_call_id=part["id"],
                            outcome=outcome if outcome in OUTCOMES else "success",
                        )
                    )
                elif kind == "retry" and part.get("tool"):
                    request_parts.append(
                        ToolReturnPart(
                            tool_name=part["tool"],
                            content=part.get("content", ""),
                            tool_call_id=part["id"],
                            outcome="retried",
                        )
                    )
                elif kind == "retry":
                    # The format keeps no cause, and model_retry is the one PydanticAI gives a stored
                    # retry's words: they reach the model fenced, as the application's, not the person's.
                    request_parts.append(
                        RetryFeedbackPart(content=str(part.get("content", "")), retry=RetryInfo(cause="model_retry"))
                    )
            if not request_parts:
                continue
            if history and isinstance(history[-1], ModelRequest):
                history[-1] = ModelRequest(parts=[*history[-1].parts, *request_parts])
            else:
                history.append(ModelRequest(parts=request_parts))
        elif role == "assistant":
            response_parts: list[ModelResponsePart] = []
            for part in record.get("parts", []):
                kind = part.get("kind")
                if kind == "text":
                    response_parts.append(TextPart(content=str(part.get("text", ""))))
                elif kind == "tool_call":
                    response_parts.append(
                        ToolCallPart(tool_name=part["tool"], args=dict(part.get("args") or {}), tool_call_id=part["id"])
                    )
            if response_parts:
                history.append(ModelResponse(parts=response_parts, model_name=record.get("model")))
    return history


def pending(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The calls a paused run is waiting on: each that asked the person and has no result yet."""

    answered = {
        part.get("id")
        for record in records
        for part in record.get("parts", [])
        if part.get("kind") in ("tool_result", "retry")
    }
    return [
        {
            "id": part["id"],
            "tool": str(part.get("tool", "")),
            "server": str(part.get("server", "")),
            "args": part.get("args") or {},
        }
        for record in records
        if record.get("role") == "assistant"
        for part in record.get("parts", [])
        if part.get("kind") == "tool_call" and part.get("asked") and part.get("id") not in answered
    ]


def _added_usage(before: dict[str, Any] | None, more: dict[str, Any]) -> dict[str, Any]:
    if not before:
        return dict(more)
    return {key: int(before.get(key) or 0) + int(more.get(key) or 0) for key in {**before, **more}}


def _added_estimate(before: dict[str, str] | None, more: dict[str, str] | None) -> dict[str, str] | None:
    if not before or not more:
        return more or before
    return {"usd": str(Decimal(before["usd"]) + Decimal(more["usd"]))}


def shown_text(record: dict[str, Any]) -> str:
    """What the page shows of a record: its text parts, joined."""

    return "\n\n".join(str(part.get("text", "")) for part in record.get("parts", []) if part.get("kind") == "text")


def shown(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation as the page draws it.

    A message the person sent is one entry. Everything the model sent back for it, its
    tool calls and the text after them, is one ``assistant`` entry, naming the skills it
    loaded, each server tool it called and how that call ended, what it searched the
    documents for and the passages its answer cites, and carrying the run's usage. A notice is its own entry. A record of a tool's result went to the model and
    is not drawn; its outcome is drawn on the call.
    """

    outcomes: dict[str, str] = {}
    for record in records:
        for part in record.get("parts", []):
            if part.get("kind") == "tool_result":
                outcomes[str(part.get("id"))] = str(part.get("outcome") or "success")
            elif part.get("kind") == "retry":
                outcomes[str(part.get("id"))] = "retry"
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for record in records:
        role = record.get("role")
        text = shown_text(record)
        if role == "user":
            if text:
                entries.append({"role": "user", "text": text})
                current = None
            continue
        if role == "notice":
            entries.append({"role": "notice", "text": text})
            current = None
            continue
        if role != "assistant":
            continue
        if current is None:
            current = {
                "role": "assistant",
                "text": "",
                "skills": [],
                "tools": [],
                "searches": [],
                "citations": [],
                "usage": None,
                "estimate": None,
                "model": "",
            }
            entries.append(current)
        if text:
            current["text"] = f"{current['text']}\n\n{text}" if current["text"] else text
        for part in record.get("parts", []):
            if part.get("kind") != "tool_call":
                continue
            if part.get("tool") == "load_skill":
                name = str((part.get("args") or {}).get("name", ""))
                if name and name not in current["skills"]:
                    current["skills"].append(name)
                continue
            if part.get("tool") == "search_knowledge":
                current["searches"].append(str((part.get("args") or {}).get("query", "")))
                continue
            call_id = str(part.get("id"))
            current["tools"].append(
                {
                    "id": call_id,
                    "tool": str(part.get("tool", "")),
                    "server": str(part.get("server", "")),
                    "asked": bool(part.get("asked")),
                    "outcome": outcomes.get(call_id, "waiting" if part.get("asked") else ""),
                }
            )
        for citation in record.get("citations") or []:
            if citation not in current["citations"]:
                current["citations"].append(citation)
        if record.get("usage"):
            # A run that stopped to ask and the run its answer resumed make one entry, whose
            # usage is both runs'.
            current["usage"] = _added_usage(current["usage"], record["usage"])
            current["estimate"] = _added_estimate(current["estimate"], record.get("estimate"))
        current["model"] = str(record.get("model") or current["model"])
    return entries
