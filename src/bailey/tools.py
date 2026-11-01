"""The tool host: a solution's MCP servers, each its own process, and what each process may see.

Design §4, and S1's findings for this story (sigrix-io/sigrix#6565):

- **Each server is built on FastMCP's base ``StdioTransport``**, never its ``uvx``, ``uv
  run`` or ``npx`` helpers, which hand the child Bailey's whole environment, provider
  keys included, as soon as one variable is named. The child receives the MCP SDK's
  short default list (``HOME``, ``PATH`` and the like), the variables its ``mcp.json``
  names, and :data:`NETWORK_VARIABLES` where Bailey has them.
- **A named variable's value comes from the keychain**, where the person saved it. It is
  never read from Bailey's own environment, and never from the package, whose values are
  names only.
- **Servers keep running between messages.** A server starts at the first message that
  needs it, which takes about a second with its handshake, and later messages reach the
  same process in milliseconds. It stops when its assistant's servers or their values
  change, when its assistant is deleted, and when Bailey stops.

Bailey 0.1 does not sandbox a server beyond its environment: it runs as the person, and
can do what they can. The import and each assistant's Tools panel say so.

FastMCP and PydanticAI's MCP toolset load here only when a server starts, so starting
the app waits for neither.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from bailey.core import load as load_core
from bailey.solutions import Server

__all__ = [
    "NETWORK_VARIABLES",
    "SANDBOX_NOTICE",
    "SIGRIX_TOKEN",
    "SIGRIX_TOKEN_KEY",
    "ToolHost",
    "ToolServerFailed",
    "environment_for",
    "missing_variables",
    "variable_key",
]

#: Non-secret variables every server receives when Bailey has them. A ``uvx`` or ``npx``
#: server downloads itself, which behind a corporate proxy it cannot do without these;
#: they describe the machine's network, not an account.
NETWORK_VARIABLES: tuple[str, ...] = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
)

#: The variable a Sigrix-delivered server's launcher reads its runner token from
#: (``uvx sigrix-launcher run <seller>/<listing-id>``), and where Bailey keeps that one
#: token for every such server.
SIGRIX_TOKEN = "SIGRIX_TOKEN"  # noqa: S105 - a variable's name, not a credential
SIGRIX_TOKEN_KEY = "sigrix/token"  # noqa: S105 - where the token is kept, not a token

#: How long a server may take to start and answer the handshake. A ``uvx`` or ``npx``
#: server downloads itself the first time it runs, which can take a while.
START_TIMEOUT = 120.0

#: What the import and the Tools panel say of every server, design §4.
SANDBOX_NOTICE = (
    "Bailey starts each tool server as a program on this computer. It hands a server only the variables its "
    "solution names, but does not otherwise limit what the program can do, so install solutions from people "
    "you trust."
)

_INSTALL_HINTS = {
    "uvx": " Install uv, which brings uvx: https://docs.astral.sh/uv/getting-started/installation/",
    "npx": " Install Node.js, which brings npx: https://nodejs.org/",
}


class ToolServerFailed(Exception):
    """A server that would not start, with the sentence that says why."""


def variable_key(assistant_id: str, server: str, variable: str) -> str:
    """Where the keychain keeps one server's variable for one assistant.

    ``SIGRIX_TOKEN`` is the one exception: every Sigrix-delivered server reads the same
    runner token, so it is kept once.
    """

    if variable == SIGRIX_TOKEN:
        return SIGRIX_TOKEN_KEY
    return f"tool/{assistant_id}/{server}/{variable}"


def missing_variables(server: Server, values: Mapping[str, str]) -> list[str]:
    """The variables the server names that the person has not given a value."""

    return [name for name in server.env if not values.get(name)]


def environment_for(server: Server, values: Mapping[str, str], environ: Mapping[str, str]) -> dict[str, str]:
    """What the server's process receives on top of the MCP SDK's default list.

    ``values`` are the person's, read from the keychain; ``environ`` is Bailey's own
    environment, from which only :data:`NETWORK_VARIABLES` are taken.
    """

    env = {name: environ[name] for name in NETWORK_VARIABLES if environ.get(name)}
    env.update({name: values[name] for name in server.env if values.get(name)})
    return env


def _signature(servers: Sequence[Server], environments: Mapping[str, Mapping[str, str]]) -> str:
    material = [[server.as_dict(), sorted(environments.get(server.name, {}).items())] for server in servers]
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


def _root_cause(error: BaseException) -> BaseException:
    seen: set[int] = set()
    while id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, BaseExceptionGroup) and error.exceptions:
            error = error.exceptions[0]
        elif error.__cause__ is not None:
            error = error.__cause__
        elif error.__context__ is not None and not error.__suppress_context__:
            error = error.__context__
        else:
            break
    return error


def _why(server: Server, error: BaseException, timeout: float) -> str:
    root = _root_cause(error)
    if isinstance(root, FileNotFoundError):
        hint = _INSTALL_HINTS.get(server.command, "")
        return f"The {server.name} tool server could not start: {server.command} is not on this computer.{hint}"
    if isinstance(root, TimeoutError | asyncio.TimeoutError):
        return f"The {server.name} tool server did not answer within {timeout:g} seconds."
    detail = str(root).strip().splitlines()[0][:200] if str(root).strip() else type(root).__name__
    return f"The {server.name} tool server could not start: {detail}"


@dataclass
class _Running:
    signature: str
    toolsets: dict[str, Any] = field(default_factory=dict)


async def _close(toolset: Any) -> None:
    with contextlib.suppress(Exception):
        await toolset.__aexit__(None, None, None)


class ToolHost:
    """The servers each assistant has running, started on first use and kept between messages."""

    def __init__(self, *, start_timeout: float = START_TIMEOUT) -> None:
        self.start_timeout = start_timeout
        self._running: dict[str, _Running] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, assistant_id: str) -> asyncio.Lock:
        return self._locks.setdefault(assistant_id, asyncio.Lock())

    def running(self, assistant_id: str) -> bool:
        return assistant_id in self._running

    async def toolsets(
        self, assistant_id: str, servers: Sequence[Server], environments: Mapping[str, Mapping[str, str]]
    ) -> dict[str, Any]:
        """Each local server's toolset by the server's name, started if it is not running.

        A server whose launch or values changed since it started is stopped and started
        again. A remote server is not connected in 0.1, and has no toolset.
        """

        local = [server for server in servers if not server.remote]
        signature = _signature(local, environments)
        async with self._lock(assistant_id):
            running = self._running.get(assistant_id)
            if running is not None and running.signature == signature:
                return running.toolsets
            await self._stop(assistant_id)
            started: dict[str, Any] = {}
            try:
                for server in local:
                    started[server.name] = await self._start(server, environments.get(server.name, {}))
            except BaseException:
                for toolset in started.values():
                    await _close(toolset)
                raise
            self._running[assistant_id] = _Running(signature, started)
            return started

    async def _start(self, server: Server, env: Mapping[str, str]) -> Any:
        load_core()
        from fastmcp.client.transports import StdioTransport
        from pydantic_ai.mcp import MCPToolset

        # keep_alive=False: the process lives exactly as long as the host holds the
        # toolset open, so stopping it here ends it, rather than leaving it to run on.
        transport = StdioTransport(command=server.command, args=list(server.args), env=dict(env), keep_alive=False)
        toolset: Any = MCPToolset(transport, init_timeout=self.start_timeout)
        try:
            await toolset.__aenter__()
        except Exception as error:
            raise ToolServerFailed(_why(server, error, self.start_timeout)) from error
        return toolset

    async def stop(self, assistant_id: str) -> None:
        """Stop the assistant's servers, as when it is deleted or its servers change."""

        async with self._lock(assistant_id):
            await self._stop(assistant_id)

    async def _stop(self, assistant_id: str) -> None:
        running = self._running.pop(assistant_id, None)
        if running is not None:
            for toolset in running.toolsets.values():
                await _close(toolset)

    async def close(self) -> None:
        """Stop every server, as Bailey stops."""

        for assistant_id in list(self._running):
            await self.stop(assistant_id)
