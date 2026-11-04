"""Lesson 2, tools that ask before they act: a solution's tool server, a read, a write that asks, and taking an "always" back.

    python lessons/tools_that_ask.py [--out lessons/out]

It installs ``solutions/harbour-mail-desk``, an assistant for lesson 1's bookshop that works
in its mailbox through one tool server: ``search_mail`` only reads, and ``save_draft``
changes the mailbox, so it asks first. The server is a stand-in (``harbour_mail.py``), put
on Bailey's PATH under the name the solution's ``mcp.json`` starts. As in lesson 1, the
model's answers are scripted, and wherever the lesson is published it says so.
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

from playwright.async_api import Locator
from studio import Lesson, Studio, main, provider_chosen

HERE = Path(__file__).resolve().parent
SOLUTION = HERE / "solutions" / "harbour-mail-desk"

ANNIE = (
    "Find Annie's email about returning her novel, and save a draft reply: yes, she can send it back, "
    "and we will email her a prepaid return label."
)
OMAR = "Now find Omar's email about delivery to Leeds, and save a draft: UK delivery takes 2 to 4 business days."
ANNIE_DRAFT = (
    "Hi Annie,\n\nThank you for writing. Yes, you can send the novel back: we will email you a prepaid "
    "return label today.\n\nThe team at Harbour Lane Books"
)
OMAR_DRAFT = (
    "Hi Omar,\n\nThank you for asking. UK delivery takes 2 to 4 business days.\n\nThe team at Harbour Lane Books"
)

#: Everything the lesson says, in one place (see first_solution.py).
NARRATION = {
    "title": {"text": "Bailey, lesson two: tools that ask before they act."},
    "install": {
        "text": "This assistant works in the shop's mailbox, through a tool server. Install it as before.",
    },
    "server": {
        "text": "Its tools come from an MCP server. The install screen says how that server starts…",
        "spoken": "Its tools come from an MCP server. The install screen says how that server starts,",
    },
    "kinds": {
        "text": "…which tools only read, and run without asking, and which change something, and ask you first.",
        "spoken": "which tools only read, and run without asking, and which change something, and ask you first.",
    },
    "key": {"text": "The server needs a key of its own. Bailey keeps it with your keys, and only that server sees it."},
    "notice": {"text": "A tool server is a program on your computer, so install solutions from people you trust."},
    "job": {"text": "Now ask it to answer a customer, and to save the reply as a draft."},
    "read": {
        "text": "Searching only reads, so it ran without asking. Saving a draft changes the mailbox, so it waits."
    },
    "ask": {
        "text": "Bailey asks first, and shows exactly what the tool will do: once, always for this assistant, or no."
    },
    "allowed": {"text": "Allowed once, it ran this time only. Every tool used is listed above the answer."},
    "always": {"text": "Always for this assistant, and it won't ask about this tool again."},
    "grants": {"text": "Every tool you allowed always is listed in Settings."},
    "again": {"text": "Ask again, and it asks before it runs once more."},
    "end": {"text": "That's lesson two. Next: apps that run a crew, and checking a solution from your terminal."},
}

#: The scripted answers: each searches the mailbox, then saves the draft, which asks first.
REPLIES = [
    {
        "when": "Find Annie's email",
        "calls": [
            {"tool": "search_mail", "args": {"query": "Annie returning a novel"}},
            {"tool": "save_draft", "args": {"email_id": "m-201", "body": ANNIE_DRAFT}},
        ],
        "reply": [
            [
                f'I found Annie\'s email, "Returning a novel", and saved this draft as a reply to it:\n\n{ANNIE_DRAFT}',
                None,
            ],
            ["\n\nIt waits in the mailbox until you send it.", None],
        ],
    },
    {
        "when": "find Omar's email",
        "calls": [
            {"tool": "search_mail", "args": {"query": "Omar delivery Leeds"}},
            {"tool": "save_draft", "args": {"email_id": "m-198", "body": OMAR_DRAFT}},
        ],
        "reply": [[f'Saved as a draft reply to Omar\'s email, "Delivery to Leeds":\n\n{OMAR_DRAFT}', None]],
    },
]


def zipped(folder: Path, into: Path) -> Path:
    path = into / f"{folder.name}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(folder.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(folder).as_posix())
    return path


def view(name: str) -> str:
    return f'.nav-item[data-view="{name}"]'


async def ask_and_wait(s: Studio, message: str) -> Locator:
    """Type a request and send it; the reply, once the write it reaches stops to ask."""

    page = s.page
    await s.click(page.locator("#message"))
    await s.type(page.locator("#message"), message, 14)
    await s.sleep(400)
    await s.click(page.locator("#send"))
    await page.locator("#question").wait_for(state="visible", timeout=60_000)
    await s.sleep(500)
    return page.locator("#messages .message-assistant").last


async def run(s: Studio) -> None:
    page = s.page
    await s.sleep(600)
    await s.voice("title", 300)
    await s.card(None, 700)

    await s.chapter("1 · What its tools can do")
    await s.spot(page.locator(".install-from"), 8)
    await s.say("install")
    async with page.expect_file_chooser() as chosen:
        await s.click(page.locator("label[for=install-zip]"))
    await (await chosen.value).set_files(zipped(SOLUTION, s.work))
    await page.locator("#install-card").wait_for(state="visible")
    await s.sleep(500)

    def section(title: str) -> Locator:
        return page.locator(".install-section", has=page.locator("h3", has_text=title))

    await s.spot(section("Tools").locator(".tool-server"), 8)
    await s.say("server")
    await s.spot(section("Tools").locator(".tool-list"), 8)
    await s.say("kinds")
    await s.spot(page.locator(".install-key"), 8)
    await s.say("key", 2600)
    await s.click(page.locator("input[data-variable=HARBOUR_MAIL_KEY]"))
    await s.type(page.locator("input[data-variable=HARBOUR_MAIL_KEY]"), "a-made-up-mailbox-key", 18)
    await s.sleep(1200)
    await s.spot(None)
    await s.quiet()
    await s.click(page.locator("#install-confirm"))
    item = page.locator(".assistant-item", has_text="Harbour mail desk")
    await item.wait_for(state="visible")
    await s.click(item.locator("button", has_text="Tools"))
    await page.locator("#tools-card").wait_for(state="visible")
    await s.spot(page.locator("#tools-notice"), 8)
    await s.say("notice")
    await s.spot(None)
    await s.quiet()
    use = item.locator("button", has_text=re.compile(r"^Use$"))
    if await use.count():
        await s.click(use)

    await s.chapter("2 · It asks first")
    await s.click(page.locator(view("chat")))
    await s.say("job", 1600)
    reply = await ask_and_wait(s, ANNIE)
    await s.spot(reply.locator(".message-tools"), 6)
    await s.say("read")
    await s.spot(page.locator("#question"), 8)
    await s.say("ask")
    await s.click(page.locator("#question button", has_text="Allow once"))
    await (
        page.locator("#messages .message-assistant")
        .last.filter(has_text="waits in the mailbox")
        .wait_for(timeout=60_000)
    )
    await s.sleep(600)
    await s.spot(page.locator("#messages .message-assistant").last.locator(".message-tools"), 6)
    await s.say("allowed")
    await s.spot(None)
    await s.quiet()

    await s.chapter("3 · Always, and taking it back")
    await ask_and_wait(s, OMAR)
    await s.spot(page.locator("#question"), 8)
    await s.say("always", 1800)
    await s.click(page.locator("#question button", has_text="Always for this assistant"))
    await (
        page.locator("#messages .message-assistant")
        .last.filter(has_text="Saved as a draft reply to Omar")
        .wait_for(timeout=60_000)
    )
    await s.sleep(800)
    await s.spot(None)
    await s.quiet()
    await s.click(page.locator(view("settings")))
    await page.locator("#grant-list .grant").first.wait_for(state="visible")
    await s.spot(page.locator("#grants-card"), 8)
    await s.say("grants")
    await s.spot(page.locator("#grant-list .grant").first, 6)
    await s.say("again", 2200)
    await s.click(page.locator("#grant-list button", has_text="Ask again"))
    await page.wait_for_function("() => document.querySelector('#grants-status').textContent.includes('asks again')")
    await s.spot(page.locator("#grants-card"), 8)
    await s.sleep(2000)
    await s.spot(None)
    await s.quiet()
    await s.chapter(None)
    await s.card(
        {
            "kicker": "Next lessons",
            "title": "Apps · bailey check",
            "sub": "Apps that run a crew, and checking a solution from your terminal.",
        },
        500,
    )
    await s.voice("end", 900)


LESSON = Lesson(
    slug="bailey-lesson-02-tools-that-ask",
    title={
        "kicker": "Bailey · Lesson 2",
        "title": "Tools that ask before they act",
        "sub": "What a solution's tools may do, which ask first, and how you answer.",
    },
    narration=NARRATION,
    replies=REPLIES,
    run=run,
    setup=provider_chosen,
    commands={"harbour-mail": (HERE / "harbour_mail.py",)},
)

if __name__ == "__main__":
    sys.exit(main(LESSON))
