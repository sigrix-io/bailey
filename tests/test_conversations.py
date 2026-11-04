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
    RetryFeedbackPart,
    RetryInfo,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from bailey.conversations import FORMAT, notice_record, shown, to_history, to_records, user_record

# The conversion builds no part PydanticAI has deprecated: its next major version removes them.
pytestmark = pytest.mark.filterwarnings("error::pydantic_ai.PydanticAIDeprecationWarning")

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


def _a_run_with_retries() -> list[Any]:
    """A run as the agent core returns it with a retry of each kind: one that answers a call the model
    must make again, and one that answers a reply with nothing in it but thinking."""

    return [
        ModelRequest(parts=[UserPromptPart(content="Draft a reply to Ana.")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="create_draft", args={"to": "Ana"}, tool_call_id="call_1")],
            model_name="gpt-5-mini",
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="create_draft",
                    content="`to` takes an address, not a name.",
                    tool_call_id="call_1",
                    outcome="retried",
                    retry=RetryInfo(cause="model_retry", attempt=1, max_retries=1),
                )
            ]
        ),
        ModelResponse(
            parts=[ToolCallPart(tool_name="create_draft", args={"to": "ana@example.com"}, tool_call_id="call_2")],
            model_name="gpt-5-mini",
        ),
        ModelRequest(parts=[ToolReturnPart(tool_name="create_draft", content="Draft saved.", tool_call_id="call_2")]),
        ModelResponse(parts=[ThinkingPart(content="Saved. Now say so.")], model_name="gpt-5-mini"),
        ModelRequest(
            parts=[
                RetryFeedbackPart(
                    content="Please return text.", retry=RetryInfo(cause="no_output", attempt=1, max_retries=1)
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="Your draft to Ana is saved.")], model_name="gpt-5-mini"),
    ]


def test_a_retry_of_either_kind_is_stored_as_a_retry_and_nothing_of_the_librarys() -> None:
    records = to_records(_a_run_with_retries(), provider="openai", model="gpt-5-mini")
    for record in records:
        for part in record["parts"]:
            assert set(part) == PART_KEYS[part["kind"]], part
    assert [part for record in records for part in record["parts"] if part["kind"] == "retry"] == [
        {"kind": "retry", "id": "call_1", "tool": "create_draft", "content": "`to` takes an address, not a name."},
        {"kind": "retry", "id": "", "tool": "", "content": "Please return text."},
    ]
    stored = repr(records)
    for library_word in ("retried", "RetryInfo", "no_output", '{"error"', "retry_feedback"):
        assert library_word not in stored, f"{library_word!r} is the library's"
    assert [(tool["id"], tool["outcome"]) for tool in shown(records)[-1]["tools"]] == [
        ("call_1", "retry"),
        ("call_2", "success"),
    ]


def test_a_retry_of_either_kind_becomes_the_part_it_was_again() -> None:
    history = to_history(to_records(_a_run_with_retries(), provider="openai", model="gpt-5-mini"))
    retried = history[2].parts[0]
    assert isinstance(retried, ToolReturnPart) and retried.outcome == "retried"
    assert (retried.tool_name, retried.tool_call_id, retried.content) == (
        "create_draft",
        "call_1",
        "`to` takes an address, not a name.",
    )
    # Feedback on a reply that is stored, as an output validator's would be, comes back after it.
    turned_down = [
        ModelRequest(parts=[UserPromptPart(content="Draft a reply to Ana.")]),
        ModelResponse(parts=[TextPart(content="Here is a draft.")], model_name="gpt-5-mini"),
        ModelRequest(
            parts=[RetryFeedbackPart(content="Say which thread it is in.", retry=RetryInfo(cause="model_retry"))]
        ),
        ModelResponse(parts=[TextPart(content="Here is a draft, in Ana's thread.")], model_name="gpt-5-mini"),
    ]
    history = to_history(to_records(turned_down, provider="openai", model="gpt-5-mini"))
    assert [type(message) for message in history] == [ModelRequest, ModelResponse] * 2
    (feedback,) = history[2].parts
    assert isinstance(feedback, RetryFeedbackPart) and feedback.content == "Say which thread it is in."


def test_feedback_on_a_reply_that_is_not_stored_is_left_out() -> None:
    # A reply with nothing in it but thinking is not stored. Its feedback is, as what went to the
    # model, but sent on later it would tell the model that a response it was never shown had
    # failed (sigrix-io/bailey#31).
    history = to_history(to_records(_a_run_with_retries(), provider="openai", model="gpt-5-mini"))
    assert [type(message) for message in history] == [ModelRequest, ModelResponse] * 3
    assert [type(part) for part in history[4].parts] == [ToolReturnPart], "the draft's result, and no feedback"
    # A conversation 0.1.0 stored is read the same way, its feedback in PydanticAI 2.56's words.
    stored = [
        user_record("Hello"),
        {
            "format": FORMAT,
            "role": "user",
            "at": "2026-11-02T09:00:01+00:00",
            "parts": [
                {
                    "kind": "retry",
                    "id": "pyd_ai_0f3c9a1e6b2d4c8e9a7b5d3f1e2c4a6b",
                    "tool": "",
                    "content": "Validation feedback:\nPlease return text.\n\nFix the errors and try again.",
                }
            ],
        },
        {
            "format": FORMAT,
            "role": "assistant",
            "at": "2026-11-02T09:00:02+00:00",
            "parts": [{"kind": "text", "text": "Hello! How can I help?"}],
            "provider": "anthropic",
            "model": "claude-sonnet-5-5",
        },
    ]
    assert [[type(part) for part in message.parts] for message in to_history(stored)] == [[UserPromptPart], [TextPart]]


def test_a_retry_stored_before_pydantic_ai_split_it_in_two_still_converts() -> None:
    # As Bailey 0.1.0 stored both kinds from PydanticAI 2.56's one retry part: in the words that
    # version showed the model, and with a made-up id on the one that answers no call.
    stored = [
        {
            "format": FORMAT,
            "role": "assistant",
            "at": "2026-11-02T09:00:00+00:00",
            "parts": [{"kind": "tool_call", "id": "call_1", "tool": "create_draft", "args": {"to": "Ana"}}],
            "provider": "openai",
            "model": "gpt-5-mini",
        },
        {
            "format": FORMAT,
            "role": "user",
            "at": "2026-11-02T09:00:01+00:00",
            "parts": [
                {
                    "kind": "retry",
                    "id": "call_1",
                    "tool": "create_draft",
                    "content": "`to` takes an address, not a name.\n\nFix the errors and try again.",
                },
                {
                    "kind": "retry",
                    "id": "pyd_ai_0f3c9a1e6b2d4c8e9a7b5d3f1e2c4a6b",
                    "tool": "",
                    "content": "Validation feedback:\nPlease return text.\n\nFix the errors and try again.",
                },
            ],
        },
    ]
    retried, feedback = to_history(stored)[1].parts
    assert isinstance(retried, ToolReturnPart) and retried.outcome == "retried"
    assert (retried.tool_name, retried.tool_call_id, retried.content) == (
        "create_draft",
        "call_1",
        "`to` takes an address, not a name.\n\nFix the errors and try again.",
    )
    assert isinstance(feedback, RetryFeedbackPart)
    assert feedback.content == "Validation feedback:\nPlease return text.\n\nFix the errors and try again."


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
            "searches": [],
            "citations": [],
            "usage": {"input_tokens": 1200, "output_tokens": 20},
            "estimate": {"usd": "0.003800"},
            "model": "claude-sonnet-5-5",
        },
        {"role": "notice", "text": "The answer stopped: TimeoutError."},
    ]
