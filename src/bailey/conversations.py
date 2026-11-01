"""Conversations in a format of Bailey's own, converted to and from the agent core's at the edge.

S1's ruling (2026-10-16): a user's history is never stored in PydanticAI's message
schema, which would tie every conversation on every machine to one library and its
versions. Each message is stored as a record in ``bailey-message/1``::

    {"format": "bailey-message/1", "role": "user" | "assistant" | "notice", "at": "<UTC time>",
     "parts": [
        {"kind": "text", "text": "..."},
        {"kind": "tool_call", "id": "...", "tool": "load_skill", "args": {...}},
        {"kind": "tool_result", "id": "...", "tool": "load_skill", "content": "..."},
        {"kind": "retry", "id": "...", "tool": "...", "content": "..."}],
     "model": "...", "provider": "...", "usage": {...}, "estimate": {...}}

A ``user`` record carries what went to the model (the person's text, and a tool's
results); an ``assistant`` record what came back; a ``notice`` what Bailey itself said,
such as a run that failed, which is shown and never sent. :func:`to_records` and
:func:`to_history` are the only two places the library's types are named, so a version
of it that moves its messages changes this module and no stored conversation.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage

# The agent core's types are imported where they are converted, not here: the server
# reads and shows conversations without loading the agent core, which takes seconds.

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


def to_records(messages: Sequence[ModelMessage], *, provider: str, model: str) -> list[dict[str, Any]]:
    """The run's messages as Bailey stores them. A thinking part is dropped: it is the
    model's own working, which nothing later in a conversation needs, and its signed form
    is a provider's."""

    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        RetryPromptPart,
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
                elif isinstance(request_part, ToolReturnPart):
                    parts.append(
                        {
                            "kind": "tool_result",
                            "id": request_part.tool_call_id,
                            "tool": request_part.tool_name,
                            "content": request_part.model_response_str(),
                        }
                    )
                elif isinstance(request_part, RetryPromptPart):
                    parts.append(
                        {
                            "kind": "retry",
                            "id": request_part.tool_call_id,
                            "tool": request_part.tool_name or "",
                            "content": request_part.model_response(),
                        }
                    )
            if parts:
                records.append({"format": FORMAT, "role": "user", "at": _now(), "parts": parts})
        elif isinstance(message, ModelResponse):
            for response_part in message.parts:
                if isinstance(response_part, TextPart) and response_part.content:
                    parts.append({"kind": "text", "text": response_part.content})
                elif isinstance(response_part, ToolCallPart):
                    parts.append(
                        {
                            "kind": "tool_call",
                            "id": response_part.tool_call_id,
                            "tool": response_part.tool_name,
                            "args": response_part.args_as_dict(),
                        }
                    )
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

    from pydantic_ai.messages import (
        ModelRequest,
        ModelRequestPart,
        ModelResponse,
        ModelResponsePart,
        RetryPromptPart,
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
                    request_parts.append(
                        ToolReturnPart(tool_name=part["tool"], content=part.get("content", ""), tool_call_id=part["id"])
                    )
                elif kind == "retry":
                    request_parts.append(
                        RetryPromptPart(
                            content=part.get("content", ""), tool_name=part.get("tool") or None, tool_call_id=part["id"]
                        )
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


def shown_text(record: dict[str, Any]) -> str:
    """What the page shows of a record: its text parts, joined."""

    return "\n\n".join(str(part.get("text", "")) for part in record.get("parts", []) if part.get("kind") == "text")


def shown(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation as the page draws it.

    A message the person sent is one entry. Everything the model sent back for it, its
    tool calls and the text after them, is one ``assistant`` entry, naming the skills it
    loaded and carrying the run's usage. A notice is its own entry. A record of a tool's
    result went to the model and is not drawn.
    """

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
            current = {"role": "assistant", "text": "", "skills": [], "usage": None, "estimate": None, "model": ""}
            entries.append(current)
        if text:
            current["text"] = f"{current['text']}\n\n{text}" if current["text"] else text
        for part in record.get("parts", []):
            if part.get("kind") == "tool_call" and part.get("tool") == "load_skill":
                name = str((part.get("args") or {}).get("name", ""))
                if name and name not in current["skills"]:
                    current["skills"].append(name)
        if record.get("usage"):
            current["usage"] = record["usage"]
            current["estimate"] = record.get("estimate")
        current["model"] = str(record.get("model") or current["model"])
    return entries
