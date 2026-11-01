"""The agent core's libraries, imported by every thread through one door.

PydanticAI, its providers' SDKs, FastMCP and the MCP SDK take seconds to import, so the app
imports them after it is up, in the background (``bailey.server``), and a message that comes
sooner imports them itself. Two threads importing one package for the first time, each
through a different module, can deadlock in Python's import system: a message's
``to_history`` took the lock of ``pydantic_ai.messages`` and waited for ``pydantic_ai``,
whose ``__init__`` the background import was running and which waited for
``pydantic_ai.messages``. Python breaks the tie by raising ``_DeadlockError`` in one of them,
and a first message sent during the background import failed with it.

So every thread imports these libraries through :func:`load`, in its one order. A thread that
comes second meets the first one's module lock before it holds any lock of its own, and
waits there until that import is done.
"""

from __future__ import annotations


def load() -> None:
    """Import the agent core and the MCP libraries; after the first time, each import is a lookup."""

    import fastmcp.client.transports  # noqa: F401
    import pydantic_ai.mcp  # noqa: F401

    import bailey.agent  # noqa: F401
