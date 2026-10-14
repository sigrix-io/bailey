"""A stdio MCP server for the spike: a mailbox with two read tools and one write tool.

The agent core starts it as a child process, as Bailey starts a solution's tools. Two of its tools
exist to be checked rather than used:

- `visible_environment` returns the names, never the values, of the environment variables the
  server can see, so a check can prove a secret in the app's environment did not reach it;
- `create_draft` appends a line to the file named by `SPIKE_DRAFTS_FILE`, the one variable the
  config names, so a check can prove whether a call the user declined ran.

Built on the official MCP Python SDK, which is what the tool host would use.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

server = MCPServer("spike-mailbox")

THREADS = [
    {
        "id": "t-1042",
        "from": "dana@example.com",
        "subject": "Refund for order 5531",
        "snippet": "The rain jacket arrived with a torn seam. Can I get my money back?",
    },
    {
        "id": "t-1039",
        "from": "sam@example.com",
        "subject": "Where is my parcel?",
        "snippet": "I ordered a headlamp last Monday and it has not arrived.",
    },
]


@server.tool()
def search_threads(query: str) -> list[dict[str, str]]:
    """Search the mailbox for threads matching the query. Reads only."""
    words = query.lower().split()
    found = [t for t in THREADS if any(w in (t["subject"] + " " + t["snippet"]).lower() for w in words)]
    return found or THREADS[:1]


@server.tool()
def create_draft(thread_id: str, body: str) -> str:
    """Create a reply draft in a thread. Changes the mailbox."""
    path = Path(os.environ["SPIKE_DRAFTS_FILE"])
    with path.open("a", encoding="utf-8") as drafts:
        drafts.write(json.dumps({"thread_id": thread_id, "body": body}) + "\n")
    return f"Draft created in {thread_id}."


@server.tool()
def visible_environment() -> list[str]:
    """The names of the environment variables this server can see. Never their values."""
    return sorted(os.environ)


if __name__ == "__main__":
    server.run()
