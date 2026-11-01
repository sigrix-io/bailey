"""The knowledge contract, on every kind of provider: a covered question is answered with a citation, an uncovered one says so.

Story S8's *done when* (sigrix-io/sigrix#6566), on a solution's samples: the fixture
solution's three (``tests/fixtures/shop-replies/samples``), sent whole and searched. The
provider emulator answers as a grounded model should, from the passage sharing the
question's words or by saying the documents do not cover it, so these prove the plumbing:
the right passage reaches the model, and its citation comes back to its document. How well
a real model grounds an answer is the live run's to show.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import emulated_providers as emulator
import pytest
from fakes import FakeEmbedder

from bailey.agent import (
    LOAD_SKILL,
    NOTHING_FOUND,
    SEARCH_KNOWLEDGE,
    Documents,
    ToolNameClash,
    answer,
    tool_servers,
)
from bailey.knowledge import add_document, cited, index, search, whole_text
from bailey.settings import Connection
from bailey.solutions import Assistant, Skill
from bailey.store import Store

PROVIDERS = [
    ("anthropic", "/anthropic", "claude-sonnet-5-5"),
    ("openai", "/openai/v1", "gpt-5-mini"),
    ("openai-compatible", "/local/v1", "qwen3:8b"),
]
IDS = [provider for provider, _, _ in PROVIDERS]
SAMPLES = Path(__file__).parent / "fixtures" / "shop-replies" / "samples"
SHOP = Assistant(
    name="shop-replies",
    title="Shop replies",
    description="",
    instructions=Skill("shop-replies", "Its instructions.", "You answer a small bookshop's customer email."),
)
COVERED = "Can I return a book I did not like?"
UNCOVERED = "Do you offer gift wrapping?"


@pytest.fixture(scope="module")
def base() -> str:
    return emulator.serve_in_background()


@pytest.fixture(autouse=True)
def _fresh_requests() -> Iterator[None]:
    emulator.REQUESTS.clear()
    yield
    emulator.REQUESTS.clear()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    opened = Store(tmp_path / "bailey.db")
    for sample in sorted(SAMPLES.iterdir()):
        add_document(opened, "a1", sample.name, sample.read_bytes(), slot=sample.stem, kind="sample")
    yield opened
    opened.close()


def _documents(store: Store, way: str, grounded_only: bool = False) -> Documents:
    if way == "whole":
        return Documents("whole", text=whole_text(store.passages("a1")), grounded_only=grounded_only)
    embedder = FakeEmbedder()
    index(store, "a1", embedder)
    return Documents("search", search=lambda query: search(store, "a1", query, embedder), grounded_only=grounded_only)


def _run(base: str, provider: str, path: str, model: str, documents: Documents, text: str) -> list[dict[str, Any]]:
    connection = Connection(provider=provider, model=model, api_key="sk-test-key", base_url=base + path)

    async def collect() -> list[dict[str, Any]]:
        return [event async for event in answer(connection, SHOP, [], text, documents=documents)]

    return asyncio.run(collect())


def _text(events: list[dict[str, Any]]) -> str:
    return "".join(event["text"] for event in events if event["type"] == "text")


@pytest.mark.parametrize("way", ["whole", "search"])
@pytest.mark.parametrize(("provider", "path", "model"), PROVIDERS, ids=IDS)
def test_a_covered_question_is_answered_with_a_citation_of_the_passage_that_covers_it(
    base: str, store: Store, provider: str, path: str, model: str, way: str
) -> None:
    events = _run(base, provider, path, model, _documents(store, way), COVERED)
    assert events[-1]["type"] == "done", events[-1]
    ids = cited(_text(events))
    assert ids, "the answer cites a passage"
    (passage,) = store.passages("a1", ids[:1])
    assert (passage["document"], passage["heading"]) == ("policies.md", "Returns")
    assert "return a book" in passage["body"]


@pytest.mark.parametrize("way", ["whole", "search"])
@pytest.mark.parametrize(("provider", "path", "model"), PROVIDERS, ids=IDS)
def test_an_uncovered_question_is_answered_by_saying_the_documents_do_not_cover_it(
    base: str, store: Store, provider: str, path: str, model: str, way: str
) -> None:
    events = _run(base, provider, path, model, _documents(store, way), UNCOVERED)
    assert _text(events) == emulator.NOT_COVERED
    assert cited(_text(events)) == []


def test_documents_sent_whole_are_in_the_instructions_with_the_grounding_clause(base: str, store: Store) -> None:
    _run(base, *PROVIDERS[0], _documents(store, "whole"), COVERED)
    system = json.dumps(emulator.REQUESTS[0]["body"]["system"])
    assert "## Your documents" in system and "cite it by its number in square brackets" in system
    assert "say so plainly" in system
    for passage in store.passages("a1"):
        assert f"[P{passage['id']}]" in system, "every passage is numbered in the prompt"
    assert "search_knowledge" not in json.dumps(emulator.REQUESTS[0]["body"].get("tools", []))


def test_a_searched_collection_is_searched_with_the_models_own_query(base: str, store: Store) -> None:
    events = _run(base, *PROVIDERS[0], _documents(store, "search"), COVERED)
    (searched,) = [event for event in events if event["type"] == "search"]
    assert searched["query"] and searched["query"] != COVERED, "the query is the model's, not the message as it stands"
    first = json.dumps(emulator.REQUESTS[0]["body"])
    assert SEARCH_KNOWLEDGE in first and "## Your documents" in first
    assert "condition it arrived" not in first, "no passage is in the prompt before the search"


def test_a_search_that_finds_nothing_tells_the_model_the_documents_do_not_cover_it(base: str, store: Store) -> None:
    nothing = Documents("search", search=lambda query: [])
    events = _run(base, *PROVIDERS[0], nothing, UNCOVERED)
    assert _text(events) == emulator.NOT_COVERED
    (result,) = [
        block
        for message in emulator.REQUESTS[-1]["body"]["messages"]
        if isinstance(message["content"], list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]
    assert json.dumps(result).count(NOTHING_FOUND) == 1, "an empty result is said in words, not left blank"


def test_a_tool_server_may_not_take_the_name_of_one_of_baileys_own_tools() -> None:
    class Offering:
        def __init__(self, *names: str) -> None:
            self.names = names

        async def list_tools(self) -> list[Any]:
            return [type("Tool", (), {"name": name})() for name in self.names]

    for own in (LOAD_SKILL, SEARCH_KNOWLEDGE):
        with pytest.raises(ToolNameClash, match=f"offers a tool named {own}, which is Bailey's own"):
            asyncio.run(tool_servers({"mailbox": Offering("list_threads", own)}))
    with pytest.raises(ToolNameClash):
        asyncio.run(tool_servers({"one": Offering("lookup"), "two": Offering("lookup")}))
    assert asyncio.run(tool_servers({"mailbox": Offering("list_threads")})) == {"list_threads": "mailbox"}


def test_answering_only_from_the_documents_tightens_the_clause(base: str, store: Store) -> None:
    _run(base, *PROVIDERS[0], _documents(store, "whole", grounded_only=True), UNCOVERED)
    system = json.dumps(emulator.REQUESTS[0]["body"]["system"])
    assert "Answer only from the documents" in system and "you may answer from what you know" not in system
    emulator.REQUESTS.clear()
    _run(base, *PROVIDERS[0], _documents(store, "whole"), UNCOVERED)
    assert "you may answer from what you know" in json.dumps(emulator.REQUESTS[0]["body"]["system"])
