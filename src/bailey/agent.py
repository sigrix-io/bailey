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
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Any

import pydantic_ai
from genai_prices import calc_price
from pydantic_ai import Agent, AgentRunResultEvent
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    ModelMessage,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage

from bailey.settings import Connection
from bailey.solutions import Assistant, Skill

pydantic_ai.BANNER_ENABLED = False

__all__ = ["LOAD_SKILL", "Connection", "answer", "build_agent", "build_model", "context", "estimate", "skill_text"]

#: The tool through which a skill's body reaches the model.
LOAD_SKILL = "load_skill"

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


def context(assistant: Assistant) -> str:
    """What every request opens with: the instructions, the voice, and the skills by name."""

    sections = [assistant.instructions.body.strip()]
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
    return "\n\n".join(section for section in sections if section)


def skill_text(skill: Skill) -> str:
    """What ``load_skill`` hands the model: the body, then each reference file under its name."""

    parts = [skill.body.strip()]
    for name, text in skill.references:
        parts.append(f"## references/{name}\n\n{text.strip()}")
    return "\n\n".join(parts)


def build_agent(connection: Connection, assistant: Assistant) -> Agent[None, str]:
    agent: Agent[None, str] = Agent(
        build_model(connection),
        instructions=context(assistant),
        model_settings=model_settings(connection),
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
    return f"The answer stopped: {type(error).__name__}."


async def answer(
    connection: Connection,
    assistant: Assistant,
    history: Sequence[ModelMessage],
    text: str,
) -> AsyncIterator[dict[str, Any]]:
    """Stream one answer as events for the page.

    ``text`` events carry the answer as it arrives, a ``skill`` event names each skill the
    model loads, and the last event is ``done`` (with the run's new messages, its usage
    and an estimate) or ``error`` (with a sentence).
    """

    agent = build_agent(connection, assistant)
    try:
        async with agent.run_stream_events(text, message_history=list(history)) as events:
            async for event in events:
                if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart) and event.part.content:
                    yield {"type": "text", "text": event.part.content}
                elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    if event.delta.content_delta:
                        yield {"type": "text", "text": event.delta.content_delta}
                elif isinstance(event, FunctionToolCallEvent) and event.part.tool_name == LOAD_SKILL:
                    yield {"type": "skill", "name": str(event.part.args_as_dict().get("name", ""))}
                elif isinstance(event, AgentRunResultEvent):
                    usage = event.result.usage
                    yield {
                        "type": "done",
                        "messages": event.result.new_messages(),
                        "usage": usage_of(usage),
                        "estimate": estimate(connection, usage),
                    }
    except Exception as error:  # every failure ends the stream with a sentence the page can show
        yield {"type": "error", "message": explain_failure(error, connection)}
