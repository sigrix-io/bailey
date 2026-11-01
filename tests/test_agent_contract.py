"""The agent core's contract, on every kind of provider: it streams, it loads the right skill, and nothing else.

S1's ruling made the spike's checks this story's contract tests, which every PydanticAI
version bump must pass before it lands. They run on the spike's emulator, ported to
``tests/emulated_providers.py``, which speaks each provider's own wire protocol and
refuses what the real services refuse.

Story S6's *done when* (sigrix-io/sigrix#6563's sibling, #6564) is the middle of this
file: an assistant with two skills answers from the right one, loads only that skill's
body, and streams on all three kinds of provider. Each skill's body opens with a marker,
so a test can tell whose body reached the model by reading what was sent.

The tool-server and approval checks join with S7, which builds the tool host they test.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import Iterator, Sequence
from typing import Any

import emulated_providers as emulator
import pydantic_ai
import pytest
from pydantic_ai.usage import RunUsage

from bailey.agent import answer, context, estimate, skill_text
from bailey.conversations import to_history, to_records
from bailey.settings import Connection
from bailey.solutions import Assistant, Skill, template

PROVIDERS = [
    ("anthropic", "/anthropic", "claude-sonnet-5-5"),
    ("openai", "/openai/v1", "gpt-5-mini"),
    ("openai-compatible", "/local/v1", "qwen3:8b"),
]

SUMMARISE = Skill(
    "summarise",
    "Summarise a text the person pastes. Load it when they ask for a summary.",
    "MARKER-SUMMARISE: open with one sentence, then list the points.",
    (("example.md", "A worked summary."),),
)
DRAFT_EMAIL = Skill(
    "draft-email",
    "Draft an email or a reply. Load it when they ask you to write an email.",
    "MARKER-EMAIL: subject line, greeting, answer, sign-off.",
)
ASSISTANT = Assistant(
    name="test-assistant",
    title="Test assistant",
    description="",
    instructions=Skill("test-instructions", "Its instructions.", "You are a test assistant."),
    persona="Plain and brief.",
    skills=(SUMMARISE, DRAFT_EMAIL),
)


@pytest.fixture(scope="module")
def base() -> str:
    return emulator.serve_in_background()


@pytest.fixture(autouse=True)
def _fresh_requests() -> Iterator[None]:
    emulator.REQUESTS.clear()
    yield
    emulator.REQUESTS.clear()


def _connection(base: str, provider: str, path: str, model: str, key: str = "sk-test-key") -> Connection:
    return Connection(provider=provider, model=model, api_key=key, base_url=base + path)


def _run(connection: Connection, assistant: Assistant, text: str, history: Sequence[Any] = ()) -> list[dict[str, Any]]:
    async def collect() -> list[dict[str, Any]]:
        return [event async for event in answer(connection, assistant, list(history), text)]

    return asyncio.run(collect())


def _sent() -> list[str]:
    return [json.dumps(request["body"]) for request in emulator.REQUESTS]


def _text(events: list[dict[str, Any]]) -> str:
    return "".join(event["text"] for event in events if event["type"] == "text")


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("provider", "path", "model"), PROVIDERS, ids=[p[0] for p in PROVIDERS])
def test_an_answer_streams_on_every_kind_of_provider(base: str, provider: str, path: str, model: str) -> None:
    events = _run(_connection(base, provider, path, model), ASSISTANT, "Hello there")
    kinds = [event["type"] for event in events]
    assert kinds[-1] == "done", events[-1]
    assert kinds.count("text") >= 3, "the answer arrives in pieces, not in one"
    assert kinds.index("done") > max(index for index, kind in enumerate(kinds) if kind == "text")
    assert _text(events) == emulator.GREETING, "the pieces join into the whole answer, markup and all as text"
    done = events[-1]
    assert done["usage"]["input_tokens"] > 0 and done["usage"]["output_tokens"] > 0


# ---------------------------------------------------------------------------
# The done-when: the right skill, and only its body
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("provider", "path", "model"), PROVIDERS, ids=[p[0] for p in PROVIDERS])
@pytest.mark.parametrize(
    ("message", "wanted", "marker", "other_marker"),
    [
        ("Please summarise this: the shop opens at nine.", "summarise", "MARKER-SUMMARISE", "MARKER-EMAIL"),
        ("Write an email to the landlord about the boiler.", "draft-email", "MARKER-EMAIL", "MARKER-SUMMARISE"),
    ],
    ids=["summarise", "draft-email"],
)
def test_the_assistant_answers_from_the_right_skill_and_loads_only_its_body(
    base: str, provider: str, path: str, model: str, message: str, wanted: str, marker: str, other_marker: str
) -> None:
    events = _run(_connection(base, provider, path, model), ASSISTANT, message)
    assert [event["name"] for event in events if event["type"] == "skill"] == [wanted]
    assert marker in _text(events), "the answer was written from the loaded skill's body"
    sent = _sent()
    assert len(sent) == 2, "one request to choose the skill, one to answer with it"
    first, second = sent
    for skill in ASSISTANT.skills:
        assert skill.name in first and skill.description in first, "the first request names every skill"
        assert skill.body not in first, "and carries no skill's body"
    assert marker in second, "the second carries the loaded skill's body, as the tool's result"
    assert all(other_marker not in request for request in sent), "the other skill's body is sent in no request"


@pytest.mark.parametrize(("provider", "path", "model"), PROVIDERS, ids=[p[0] for p in PROVIDERS])
def test_the_default_assistant_answers_a_summary_from_its_summarise_skill(
    base: str, provider: str, path: str, model: str
) -> None:
    everyday = template("everyday")
    events = _run(_connection(base, provider, path, model), everyday, "Summarise this for me: rain is due at noon.")
    assert [event["name"] for event in events if event["type"] == "skill"] == ["summarise"]
    assert "# Summarising" in _text(events)
    sent = "".join(_sent())
    assert "Match the tone of the email being answered" not in sent, "draft-email's body stayed on this machine"


def test_a_loaded_skill_brings_its_reference_files() -> None:
    loaded = skill_text(SUMMARISE)
    assert loaded.startswith(SUMMARISE.body)
    assert "## references/example.md" in loaded and "A worked summary." in loaded


def test_the_context_names_the_skills_and_holds_no_skill_body() -> None:
    opening = context(ASSISTANT)
    assert opening.startswith("You are a test assistant.")
    assert "Plain and brief." in opening
    for skill in ASSISTANT.skills:
        assert f"`{skill.name}`: {skill.description}" in opening
        assert skill.body not in opening


def test_an_assistant_without_skills_is_offered_no_tool(base: str) -> None:
    bare = Assistant(name="bare", title="Bare", description="", instructions=ASSISTANT.instructions)
    events = _run(_connection(base, "anthropic", "/anthropic", "claude-sonnet-5-5"), bare, "Please summarise this")
    assert [event["type"] for event in events if event["type"] == "skill"] == []
    assert not emulator.REQUESTS[0]["body"].get("tools")


# ---------------------------------------------------------------------------
# What the ruling and the spike's findings asked of every request
# ---------------------------------------------------------------------------


def test_anthropic_caches_the_instructions_as_a_stable_prefix(base: str) -> None:
    _run(_connection(base, "anthropic", "/anthropic", "claude-sonnet-5-5"), ASSISTANT, "Hello")
    system = emulator.REQUESTS[0]["body"]["system"]
    assert isinstance(system, list) and any("cache_control" in block for block in system)


@pytest.mark.parametrize(("provider", "path", "model"), PROVIDERS[:2], ids=[p[0] for p in PROVIDERS[:2]])
def test_the_base_url_is_always_passed_whatever_the_shell_says(
    base: str, provider: str, path: str, model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # TEST-NET-1 routes nowhere: a request sent there would never be answered.
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://192.0.2.1:9")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://192.0.2.1:9/v1")
    events = _run(_connection(base, provider, path, model), ASSISTANT, "Hello")
    assert events[-1]["type"] == "done" and emulator.REQUESTS


def test_the_banner_is_off() -> None:
    assert pydantic_ai.BANNER_ENABLED is False


# ---------------------------------------------------------------------------
# A conversation, usage, and failures
# ---------------------------------------------------------------------------


def test_the_next_message_carries_the_conversation_so_far(base: str) -> None:
    connection = _connection(base, "openai-compatible", "/local/v1", "qwen3:8b")
    first = _run(connection, ASSISTANT, "Please summarise this: the shop opens at nine.")
    records = to_records(first[-1]["messages"], provider="openai-compatible", model="qwen3:8b")
    emulator.REQUESTS.clear()
    _run(connection, ASSISTANT, "Thanks. Hello again", to_history(records))
    later = _sent()[0]
    assert "the shop opens at nine" in later and "MARKER-SUMMARISE" in later


def test_the_estimate_comes_from_the_bundled_prices_and_never_guesses() -> None:
    usage = RunUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    priced = estimate(Connection("anthropic", "claude-sonnet-5-5", "", ""), usage)
    assert priced is not None and float(priced["usd"]) > 0
    assert estimate(Connection("openai", "gpt-5-mini", "", ""), usage) is not None
    assert estimate(Connection("openai-compatible", "qwen3:8b", "", ""), usage) is None, "a local model has no price"
    assert estimate(Connection("anthropic", "no-such-model", "", ""), usage) is None, "an unknown model is not guessed"


@pytest.mark.parametrize(("provider", "path", "model"), PROVIDERS, ids=[p[0] for p in PROVIDERS])
def test_a_refused_key_ends_the_answer_with_a_sentence(base: str, provider: str, path: str, model: str) -> None:
    events = _run(_connection(base, provider, path, model, key=emulator.BAD_KEY), ASSISTANT, "Hello")
    assert [event["type"] for event in events] == ["error"]
    assert "refused the key (401)" in events[0]["message"]
    assert emulator.BAD_KEY not in events[0]["message"]


def test_a_server_that_cannot_be_reached_is_said_so() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    connection = Connection("openai-compatible", "qwen3:8b", "", f"http://127.0.0.1:{closed}/v1")
    events = _run(connection, ASSISTANT, "Hello")
    assert [event["type"] for event in events] == ["error"]
    assert events[0]["message"] == f"Bailey could not reach the server at http://127.0.0.1:{closed}/v1."
