"""A stdio MCP server for the tests: a small shop's mailbox, with two reads and one write.

Bailey starts it as a child process, as it starts a solution's tools. Three of its tools
exist to be checked rather than used:

- ``create_draft`` appends a line to the file ``MAILBOX_DRAFTS`` names, the variable its
  configuration names, so a test can tell whether a call the person declined ran;
- ``visible_environment`` returns the names, never the values, of the variables the
  process can see, so a test can prove a secret in Bailey's environment did not reach it;
- ``process_id`` says which process answered, so a test can tell a server kept running
  between messages from one started again.

Built on the official MCP Python SDK, as the spike's server was.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

server = MCPServer("test-mailbox")

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
    found = [thread for thread in THREADS if any(word in json.dumps(thread).lower() for word in words)]
    return found or THREADS[:1]


@server.tool()
def create_draft(thread_id: str, body: str) -> str:
    """Create a reply draft in a thread. Changes the mailbox."""
    with Path(os.environ["MAILBOX_DRAFTS"]).open("a", encoding="utf-8") as drafts:
        drafts.write(json.dumps({"thread_id": thread_id, "body": body}) + "\n")
    return f"Draft created in {thread_id}."


@server.tool()
def visible_environment() -> list[str]:
    """The names of the environment variables this server can see. Never their values."""
    return sorted(os.environ)


@server.tool()
def process_id() -> int:
    """The id of the process answering."""
    return os.getpid()


if __name__ == "__main__":
    server.run()
