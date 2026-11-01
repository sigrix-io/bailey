"""The agent core: one answer from an assistant, streamed, on the provider the person chose.

Built on PydanticAI 2.56.0, pinned exactly, as S1's ruling decided (2026-10-16), with
the spike's findings applied:

- **Each provider on its own protocol.** Anthropic over the Messages API, OpenAI over
  the Responses API, and an OpenAI-compatible server, local models included, over Chat
  Completions, which is what those servers speak (``OpenAIChatModel``).
- **The base URL is always passed.** The SDKs otherwise read ``ANTHROPIC_BASE_URL`` and
  ``OPENAI_BASE_URL`` from the person's shell, which may set them for another tool, and
  send the key there.
- **The banner is off.** PydanticAI prints one about its hosted observability on stderr.

**The context** (design §3.2, step 1) is the assistant's instructions and persona, then
only the *name and one-line description* of each of its skills. A skill's body, and its
small reference files, reach the model only when it calls ``load_skill`` for that
skill, so an assistant with many skills pays for the one it uses. The instructions are
the stable start of every request, cached where the provider caches
(``anthropic_cache_instructions``), so the documents S8 adds to them stay cheap to send.

**Tools** (design §3.2, steps 3 and 5) come from the assistant's MCP servers, which the
tool host keeps running. A read its server declares runs without asking. A write, any
tool its server's declaration leaves out, and every tool of a server with no declaration
asks first, unless the person allowed it always for this assistant. A call that asks
ends the run there (``DeferredToolRequests``): the page puts the question, and the answer
resumes the run from the stored conversation, so a question survives a reload or a
restart. "No" reaches the model as the tool's result.

**Documents** (design §3.7) reach the model one of two ways (:mod:`bailey.knowledge`): a
small collection whole, in numbered passages that join the cached instructions, or a
larger one through ``search_knowledge``, whose query the model writes. Either way the
grounding clause (sigrix-io/sigrix#6190) asks it to answer from them, cite each passage it
uses as ``[P12]``, and say plainly when they do not cover the question.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Collection, Mapping, Sequence
from decimal import Decimal
from typing import Any

import pydantic_ai
from genai_prices import calc_price
from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    DeferredToolRequests,
    DeferredToolResults,
    RunContext,
    ToolApproved,
    ToolDenied,
)
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolReturnPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RunUsage

from bailey.knowledge import Documents, passage_text
from bailey.settings import Connection
from bailey.solutions import Assistant, Server, Skill

pydantic_ai.BANNER_ENABLED = False

__all__ = [
    "DECLINED",
    "LOAD_SKILL",
    "NOTHING_FOUND",
    "SEARCH_KNOWLEDGE",
    "Documents",
    "Connection",
    "ToolNameClash",
    "answer",
    "asks_first",
    "build_agent",
    "build_model",
    "context",
    "estimate",
    "skill_text",
    "tool_servers",
]

#: The tool through which a skill's body reaches the model.
LOAD_SKILL = "load_skill"

#: The tool through which a searched collection's passages reach the model.
SEARCH_KNOWLEDGE = "search_knowledge"

#: Bailey's own tools, whose names no MCP server may take.
OWN_TOOLS = (LOAD_SKILL, SEARCH_KNOWLEDGE)

#: What ``search_knowledge`` says when no passage matched.
NOTHING_FOUND = "No passage in the documents matches this. Tell the person the documents do not cover it."


#: What the model is told, as the tool's result, when the person says no.
DECLINED = "The person declined this action, so it did not run. Tell them it did not happen; do not try another way."


class ToolNameClash(Exception):
    """Two tools a model could not tell apart, with the sentence that says which."""


#: The price data's name for each provider whose prices it carries. A local server has
#: none, and its answers carry no estimate.
_PRICED_AS = {"anthropic": "anthropic", "openai": "openai"}


def build_model(connection: Connection) -> Model:
    if connection.provider == "anthropic":
        return AnthropicModel(
            connection.model, provider=AnthropicProvider(api_key=connection.api_key, base_url=connection.base_url)
        )
    # A local server often wants no key, and the SDK refuses an empty one.
    openai = OpenAIProvider(api_key=connection.api_key or "not-needed", base_url=connection.base_url)
    if connection.provider == "openai":
        return OpenAIResponsesModel(connection.model, provider=openai)
    return OpenAIChatModel(connection.model, provider=openai)


def model_settings(connection: Connection) -> ModelSettings:
    if connection.provider == "anthropic":
        return AnthropicModelSettings(anthropic_cache_instructions=True)
    return ModelSettings()


def grounding(documents: Documents) -> str:
    """The grounding clause (sigrix-io/sigrix#6190), for documents sent whole or searched."""

    if documents.mode == "whole":
        opening = "Below are the person's documents, in numbered passages. Answer from them."
        uncovered = "When the documents do not cover a question, say so plainly; do not guess what they would say."
    else:
        opening = (
            f"The person's documents are searched with `{SEARCH_KNOWLEDGE}`. Before you answer anything they "
            "might cover, search them, in your own words for what the answer needs, and search again with other "
            "words if the passages do not answer it. Answer from the passages you are given."
        )
        uncovered = (
            "When the passages do not cover a question, say so plainly; do not guess what the documents would say."
        )
    rule = (
        "Answer only from the documents: when they do not cover a question, say that they do not, and do not answer "
        "it from anything else."
        if documents.grounded_only
        else "After saying so, you may answer from what you know, saying that the answer does not come from the documents."
    )
    return (
        f"{opening} After each sentence that relies on a passage, cite it by its number in square brackets, "
        f"like [P12]. {uncovered} {rule}"
    )


def context(assistant: Assistant, documents: Documents | None = None) -> str:
    """What every request opens with: the instructions, the voice, the skills by name, and the documents.

    Documents sent whole come last, so the instructions before them stay the same while a
    collection changes, and the whole of it is one stable, cached prefix.
    """

    # The instructions skill's reference files come with it: no load_skill reaches them.
    sections = [skill_text(assistant.instructions)]
    if assistant.persona.strip():
        sections.append("## Your voice\n\n" + assistant.persona.strip())
    if assistant.skills:
        listed = "\n".join(f"- `{skill.name}`: {skill.description}" for skill in assistant.skills)
        sections.append(
            "## Skills\n\n"
            "These skills are available. Each holds detailed instructions for one kind of request. "
            f"When a request needs one, call `{LOAD_SKILL}` with its name before you answer, and follow what it "
            "returns. Load only the skill the request needs.\n\n" + listed
        )
    if documents is not None and documents.mode in ("whole", "search"):
        section = "## Your documents\n\n" + grounding(documents)
        if documents.mode == "whole":
            section += "\n\n" + documents.text
        sections.append(section)
    return "\n\n".join(section for section in sections if section)


def skill_text(skill: Skill) -> str:
    """What ``load_skill`` hands the model: the body, then each reference file under its name."""

    parts = [skill.body.strip()]
    for name, text in skill.references:
        parts.append(f"## references/{name}\n\n{text.strip()}")
    return "\n\n".join(parts)


ApprovalRule = Callable[[RunContext[None], ToolDefinition, dict[str, Any]], bool]


def asks_first(server: Server, granted: Collection[tuple[str, str]]) -> ApprovalRule:
    """Whether a call of one of this server's tools waits for the person.

    It does for a declared write, a tool the declaration leaves out and every tool of a
    server with no declaration, unless the person allowed that tool always for this
    assistant (``granted`` holds ``(server, tool)`` pairs).
    """

    def rule(ctx: RunContext[None], tool_def: ToolDefinition, args: dict[str, Any]) -> bool:
        return server.asks_before(tool_def.name) and (server.name, tool_def.name) not in granted

    return rule


async def tool_servers(toolsets: Mapping[str, Any]) -> dict[str, str]:
    """Each tool's server, by the tool's name.

    A tool two servers both offer, or one named like Bailey's own ``load_skill``, is
    refused with a sentence: the model calls a tool by its name alone.
    """

    owners: dict[str, str] = {}
    for server_name, toolset in toolsets.items():
        for tool in await toolset.list_tools():
            if tool.name in OWN_TOOLS:
                raise ToolNameClash(
                    f"The {server_name} tool server offers a tool named {tool.name}, which is Bailey's own."
                )
            if tool.name in owners:
                raise ToolNameClash(
                    f"The {owners[tool.name]} and {server_name} tool servers both offer a tool named {tool.name}, "
                    "and a model calls a tool by its name alone."
                )
            owners[tool.name] = server_name
    return owners


def build_agent(
    connection: Connection,
    assistant: Assistant,
    toolsets: Mapping[str, Any] | None = None,
    granted: Collection[tuple[str, str]] = (),
    documents: Documents | None = None,
) -> Agent[None, Any]:
    """The assistant's agent: its context, ``load_skill``, ``search_knowledge`` for a searched
    collection, and each running server's tools behind the approval rule."""

    held = [
        toolset.approval_required(asks_first(server, granted))
        for server in assistant.servers
        if (toolset := (toolsets or {}).get(server.name)) is not None
    ]
    agent: Agent[None, Any] = Agent(
        build_model(connection),
        instructions=context(assistant, documents),
        model_settings=model_settings(connection),
        toolsets=held,
        output_type=[str, DeferredToolRequests] if held else str,
    )
    names = [skill.name for skill in assistant.skills]

    def load_skill(name: str) -> str:
        """Load a skill's detailed instructions. Call it before answering a request that one of the listed skills covers.

        Args:
            name: The skill's name, exactly as listed.
        """

        skill = assistant.skill(name)
        if skill is None:
            return f"There is no skill named {name!r}. The skills are: {', '.join(names) or 'none'}."
        return skill_text(skill)

    if assistant.skills:
        agent.tool_plain(name=LOAD_SKILL)(load_skill)

    if documents is not None and documents.mode == "search" and documents.search is not None:
        search = documents.search

        def search_knowledge(query: str) -> str:
            """Search the person's documents, and get back the passages that best match, each numbered with its source.

            Args:
                query: What to look for, in your own words: the facts the answer needs, not the person's message as it stands.
            """

            hits = search(query)
            return "\n\n".join(passage_text(hit) for hit in hits) if hits else NOTHING_FOUND

        agent.tool_plain(name=SEARCH_KNOWLEDGE)(search_knowledge)
    return agent


def estimate(connection: Connection, usage: RunUsage) -> dict[str, str] | None:
    """An estimated cost in US dollars, from the price data PydanticAI ships with.

    It reads only that bundled data; nothing fetches newer prices. A model the data does
    not know, or a local server, has no estimate rather than a guessed one.
    """

    priced_as = _PRICED_AS.get(connection.provider)
    if priced_as is None:
        return None
    try:
        price = calc_price(usage, connection.model, provider_id=priced_as)
    except LookupError:
        return None
    return {"usd": str(Decimal(price.total_price).quantize(Decimal("0.000001")))}


def usage_of(usage: RunUsage) -> dict[str, int]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "requests": usage.requests,
    }


def explain_failure(error: BaseException, connection: Connection) -> str:
    """A sentence for a run that failed, naming what the person can do about it. Never the key."""

    where = {"anthropic": "Anthropic", "openai": "OpenAI"}.get(connection.provider, "the server")
    if isinstance(error, ModelHTTPError):
        status = error.status_code
        if status in (401, 403):
            return f"{where} refused the key ({status}). Check the key in Settings."
        if status == 404:
            return f"{where} does not know the model {connection.model!r} ({status}). Check its name in Settings."
        if status == 429:
            return f"{where} is limiting requests just now ({status}). Wait a moment and try again."
        return f"{where} answered with an error ({status})."
    if isinstance(error, ModelAPIError):
        return f"Bailey could not reach {where} at {connection.base_url}."
    if isinstance(error, UnexpectedModelBehavior):
        return f"The model answered in a way Bailey could not use: {error}"
    if isinstance(error, ToolNameClash):
        return str(error)
    if type(error).__module__.split(".")[0] in ("mcp", "fastmcp", "anyio"):
        return (
            f"A tool server stopped answering ({type(error).__name__}). Bailey starts it again with the next message."
        )
    return f"The answer stopped: {type(error).__name__}."


def _settled(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """A failed run's messages up to its last whole step.

    A model's reply is kept only with the request that answers it: a reply whose tool
    calls never got their results would leave calls no provider accepts unanswered. A
    tool that ran keeps its result, so a write that happened is never asked about again.
    """

    kept = list(messages)
    while kept and not isinstance(kept[-1], ModelRequest):
        kept.pop()
    return kept


def _messages_so_far(events: Any) -> list[ModelMessage]:
    if events is None:
        return []
    try:
        return list(events.new_messages())
    except Exception:  # a run that never started has none
        return []


def _call(part: Any, servers: Mapping[str, str]) -> dict[str, Any]:
    return {
        "id": part.tool_call_id,
        "tool": part.tool_name,
        "server": servers.get(part.tool_name, ""),
        "args": part.args_as_dict(),
    }


async def answer(
    connection: Connection,
    assistant: Assistant,
    history: Sequence[ModelMessage],
    text: str | None,
    *,
    toolsets: Mapping[str, Any] | None = None,
    granted: Collection[tuple[str, str]] = (),
    decisions: Mapping[str, bool] | None = None,
    documents: Documents | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream one answer as events for the page.

    ``text`` is the person's message, or ``None`` when a paused run resumes with
    ``decisions``: the person's answer to each call that asked, ``True`` to run it.

    ``text`` events carry the answer as it arrives; ``skill`` names each skill the model
    loads; ``tool`` and ``tool_result`` follow each call of a server's tool; ``approval``
    lists the calls a run stopped to ask about. The last event is ``done`` (the run's new
    messages, its usage, an estimate, the calls it asked about, and each tool's server) or
    ``error`` (a sentence, and the messages of the run's last whole step).
    """

    servers: dict[str, str] = {}
    events: Any = None
    try:
        servers = await tool_servers(toolsets or {})
        agent = build_agent(connection, assistant, toolsets, granted, documents)
        results = None
        if decisions is not None:
            results = DeferredToolResults(
                approvals={
                    call_id: ToolApproved() if allowed else ToolDenied(DECLINED)
                    for call_id, allowed in decisions.items()
                }
            )
        async with agent.run_stream_events(
            text, message_history=list(history), deferred_tool_results=results
        ) as events:
            async for event in events:
                if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart) and event.part.content:
                    yield {"type": "text", "text": event.part.content}
                elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    if event.delta.content_delta:
                        yield {"type": "text", "text": event.delta.content_delta}
                elif isinstance(event, FunctionToolCallEvent):
                    if event.part.tool_name == LOAD_SKILL:
                        yield {"type": "skill", "name": str(event.part.args_as_dict().get("name", ""))}
                    elif event.part.tool_name == SEARCH_KNOWLEDGE:
                        yield {"type": "search", "query": str(event.part.args_as_dict().get("query", ""))}
                    else:
                        yield {"type": "tool", **_call(event.part, servers)}
                elif isinstance(event, FunctionToolResultEvent) and event.part.tool_name not in (*OWN_TOOLS, None):
                    outcome = event.part.outcome if isinstance(event.part, ToolReturnPart) else "retry"
                    yield {"type": "tool_result", "id": event.part.tool_call_id, "outcome": outcome}
                elif isinstance(event, AgentRunResultEvent):
                    output = event.result.output
                    asked = list(output.approvals) if isinstance(output, DeferredToolRequests) else []
                    if asked:
                        yield {"type": "approval", "calls": [_call(call, servers) for call in asked]}
                    usage = event.result.usage
                    yield {
                        "type": "done",
                        "messages": event.result.new_messages(),
                        "usage": usage_of(usage),
                        "estimate": estimate(connection, usage),
                        "asked": [call.tool_call_id for call in asked],
                        "servers": servers,
                    }
    except Exception as error:  # every failure ends the stream with a sentence the page can show
        yield {
            "type": "error",
            "message": explain_failure(error, connection),
            "messages": _settled(_messages_so_far(events)),
            "servers": servers,
        }
