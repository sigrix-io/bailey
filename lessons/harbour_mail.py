"""A stand-in tool server for lesson 2: the made-up bookshop's mailbox, with one tool that reads and one that writes.

A real solution's server is a package its ``mcp.json`` starts, with ``uvx`` or ``npx``. This
one is a script the recording puts on Bailey's ``PATH`` as ``harbour-mail`` (``studio.py``),
so the install screen shows the line a solution's own would, and the app starts it exactly
as it starts any server: a child process speaking MCP over stdio, seeing only the variables
its ``mcp.json`` names. Its drafts are kept in memory and go nowhere.

Built on the official MCP Python SDK, which Bailey already depends on.
"""

from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

server = MCPServer("harbour-mail")

INBOX = [
    {
        "id": "m-201",
        "from": "Annie",
        "subject": "Returning a novel",
        "text": "The novel I ordered arrived last week, but I did not enjoy it. Can I send it back?",
    },
    {
        "id": "m-198",
        "from": "Omar",
        "subject": "Delivery to Leeds",
        "text": "How long does delivery take to Leeds, and what does it cost?",
    },
    {
        "id": "m-190",
        "from": "Keiko",
        "subject": "Gift wrapping",
        "text": "Do you gift wrap? The book is for my sister's birthday.",
    },
]
DRAFTS: list[dict[str, str]] = []


def _signed_in() -> None:
    if not os.environ.get("HARBOUR_MAIL_KEY"):
        raise RuntimeError("HARBOUR_MAIL_KEY is not set: give it in this assistant's Tools.")


@server.tool()
def search_mail(query: str) -> list[dict[str, str]]:
    """Find the shop's emails that match the query. Reads only."""

    _signed_in()
    words = query.lower().split()
    found = [mail for mail in INBOX if any(word in " ".join(mail.values()).lower() for word in words)]
    return found or INBOX[:1]


@server.tool()
def save_draft(email_id: str, body: str) -> str:
    """Save a draft reply to one of the shop's emails. Changes the mailbox."""

    _signed_in()
    mail = next((mail for mail in INBOX if mail["id"] == email_id), None)
    if mail is None:
        raise ValueError(f"There is no email {email_id}.")
    DRAFTS.append({"email_id": email_id, "body": body})
    return f"Draft saved as a reply to {mail['from']}: {mail['subject']}."


if __name__ == "__main__":
    server.run()
