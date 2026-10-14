"""The agent core under test: PydanticAI with one stdio MCP server and approval before a write.

What Bailey's core would do, and nothing else:

- one model per kind of provider, each on the protocol that provider speaks (`build_model`);
- a solution's MCP server started as a child process that receives only the variables its
  config names (`tool_server`), through the base `StdioTransport`: FastMCP's `uvx`, `uv run`
  and `npx` helpers copy the whole parent environment once any variable is named, which is the
  trap `check.py` records;
- the write tools the *solution* declares (`assistant.json`'s `write_tools`) wrapped so that
  calling one defers the call for a person to approve (`build_agent`). The server's own
  description of a tool is never what decides.
"""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pydantic_ai
from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import ToolDefinition

#: PydanticAI prints a first-run banner advertising its hosted observability on stderr. An app
#: owns its own output, so Bailey turns it off before any run.
pydantic_ai.BANNER_ENABLED = False

HERE = Path(__file__).parent
TOOLS_SERVER = HERE / "tools_server.py"

#: The tools this solution declares as writing, as `assistant.json` would.
WRITE_TOOLS = frozenset({"create_draft"})

#: The one variable the tool server's config names.
NAMED_VARIABLE = "SPIKE_DRAFTS_FILE"

INSTRUCTIONS = (
    "You answer a small shop's customer email. Find the thread with search_threads, then write the "
    "reply with create_draft. When asked about the tool server's environment, call visible_environment."
)

Kind = Literal["anthropic", "openai", "openai-compatible"]
Approver = Callable[[RunContext[None], DeferredToolRequests], Awaitable[DeferredToolResults | None]]


@dataclass(frozen=True)
class Provider:
    kind: Kind
    model: str
    api_key: str
    base_url: str | None = None


def build_model(provider: Provider) -> Model:
    """One model per kind of provider. The base URL is always passed, never left to the SDK's
    environment fallback (`ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`), which a user's shell may set
    for some other tool."""
    if provider.kind == "anthropic":
        return AnthropicModel(
            provider.model, provider=AnthropicProvider(api_key=provider.api_key, base_url=provider.base_url)
        )
    openai = OpenAIProvider(api_key=provider.api_key, base_url=provider.base_url)
    if provider.kind == "openai":
        return OpenAIResponsesModel(provider.model, provider=openai)
    return OpenAIChatModel(provider.model, provider=openai)


def tool_server(drafts_file: Path) -> MCPToolset[None]:
    """The solution's MCP server, started over stdio with only the variable its config names."""
    transport = StdioTransport(
        command=sys.executable,
        args=[str(TOOLS_SERVER)],
        env={NAMED_VARIABLE: str(drafts_file)},
        keep_alive=False,
    )
    return MCPToolset(transport)


def is_write_tool(ctx: RunContext[None], tool_def: ToolDefinition, args: dict[str, Any]) -> bool:
    return tool_def.name in WRITE_TOOLS


def build_agent(provider: Provider, drafts_file: Path) -> Agent[None, str | DeferredToolRequests]:
    """An agent whose write tools pause the run for approval.

    Called with `capabilities=[HandleDeferredToolCalls(handler=...)]`, a run waits inside itself
    for the answer and carries on streaming; without it, the run ends with `DeferredToolRequests`
    as its output and resumes later from its message history, which `check.py` writes to JSON and
    reads back in a new agent, as an app restarted mid-approval would.
    """
    return Agent(
        build_model(provider),
        instructions=INSTRUCTIONS,
        toolsets=[tool_server(drafts_file).approval_required(is_write_tool)],
        output_type=[str, DeferredToolRequests],
    )
