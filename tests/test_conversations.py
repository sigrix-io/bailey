"""Conversations are stored in Bailey's own format, and only converted to the agent core's at the edge.

S1's ruling: a history stored in PydanticAI's schema would tie every user's conversations
to one library and its versions. These hold the stored shape to ``bailey-message/1``,
whatever the library's messages carry, and the conversion back to its messages.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from bailey.conversations import FORMAT, notice_record, shown, to_history, to_records, user_record

RECORD_KEYS = {"format", "role", "at", "parts", "provider", "model", "usage", "estimate"}
PART_KEYS = {
    "text": {"kind", "text"},
    "tool_call": {"kind", "id", "tool", "args"},
    "tool_result": {"kind", "id", "tool", "content"},
    "retry": {"kind", "id", "tool", "content"},
}


def _a_run() -> list[Any]:
    """A run as the agent core returns it: the prompt, a skill loaded, an answer, and the parts Bailey drops."""

    return [
        ModelRequest(parts=[UserPromptPart(content="Summarise this: rain at noon.")], instructions="the context"),
        ModelResponse(
            parts=[
                ThinkingPart(content="The person wants a summary.", signature="sig"),
                ToolCallPart(tool_name="load_skill", args={"name": "summarise"}, tool_call_id="call_1"),
            ],
            model_name="claude-sonnet-5-5",
        ),
        ModelRequest(parts=[ToolReturnPart(tool_name="load_skill", content="# Summarising", tool_call_id="call_1")]),
        ModelResponse(parts=[TextPart(content="Rain is due at noon.")], model_name="claude-sonnet-5-5"),
    ]


def test_a_run_is_stored_in_bailey_message_1_and_nothing_of_the_librarys() -> None:
    records = to_records(_a_run(), provider="anthropic", model="claude-sonnet-5-5")
    assert [record["role"] for record in records] == ["user", "assistant", "user", "assistant"]
    for record in records:
        assert record["format"] == FORMAT
        assert set(record) <= RECORD_KEYS, set(record) - RECORD_KEYS
        for part in record["parts"]:
            assert set(part) == PART_KEYS[part["kind"]], part
    stored = repr(records)
    for library_word in ("part_kind", "ModelRequest", "instructions", "signature", "The person wants a summary."):
        assert library_word not in stored, f"{library_word!r} is the library's, or the model's own working"


def test_stored_records_become_the_same_conversation_again() -> None:
    records = to_records(_a_run(), provider="anthropic", model="claude-sonnet-5-5")
    history = to_history(records)
    assert [type(message) for message in history] == [ModelRequest, ModelResponse, ModelRequest, ModelResponse]
    call = history[1].parts[0]
    assert isinstance(call, ToolCallPart) and (call.tool_name, call.tool_call_id) == ("load_skill", "call_1")
    assert call.args_as_dict() == {"name": "summarise"}
    result = history[2].parts[0]
    assert isinstance(result, ToolReturnPart) and result.content == "# Summarising"
    assert isinstance(history[3].parts[0], TextPart)


def test_a_notice_is_shown_and_never_sent_and_two_user_turns_become_one_request() -> None:
    records = [
        user_record("Hello?"),
        notice_record("Anthropic refused the key (401). Check the key in Settings."),
        user_record("Hello again."),
    ]
    history = to_history(records)
    assert len(history) == 1 and isinstance(history[0], ModelRequest)
    assert [part.content for part in history[0].parts if isinstance(part, UserPromptPart)] == [
        "Hello?",
        "Hello again.",
    ]


def test_a_retry_survives_the_round_trip() -> None:
    run = [
        ModelRequest(parts=[RetryPromptPart(content="bad arguments", tool_name="load_skill", tool_call_id="c2")]),
    ]
    history = to_history(to_records(run, provider="openai", model="gpt-5-mini"))
    part = history[0].parts[0]
    assert isinstance(part, RetryPromptPart) and part.tool_call_id == "c2" and part.tool_name == "load_skill"


def test_a_record_in_a_format_this_bailey_does_not_know_is_refused() -> None:
    with pytest.raises(ValueError, match="bailey-message/2"):
        to_history([{"format": "bailey-message/2", "role": "user", "parts": []}])


def test_the_page_sees_one_entry_per_turn_with_the_skills_it_loaded_and_its_usage() -> None:
    records = [user_record("Summarise this: rain at noon.")]
    run = to_records(_a_run(), provider="anthropic", model="claude-sonnet-5-5")[1:]
    run[-1]["usage"] = {"input_tokens": 1200, "output_tokens": 20}
    run[-1]["estimate"] = {"usd": "0.003800"}
    records += run
    records.append(notice_record("The answer stopped: TimeoutError."))
    entries = shown(records)
    assert entries == [
        {"role": "user", "text": "Summarise this: rain at noon."},
        {
            "role": "assistant",
            "text": "Rain is due at noon.",
            "skills": ["summarise"],
            "tools": [],
            "usage": {"input_tokens": 1200, "output_tokens": 20},
            "estimate": {"usd": "0.003800"},
            "model": "claude-sonnet-5-5",
        },
        {"role": "notice", "text": "The answer stopped: TimeoutError."},
    ]
