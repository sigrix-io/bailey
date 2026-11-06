"""Lesson 1, your first solution: choose an AI, install a solution from a file, give it documents and a job.

    pageant record lessons/first_solution.py [--out lessons/out]

It installs the bookshop solution from Bailey's own tests (``tests/fixtures/grounded-replies``),
zipped as a download is, and gives it the first of that solution's own examples to answer.
The answer is scripted: the lesson shows where things are and what they do, not how well a
model writes, and wherever it is published it says so.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pages
import scripted_bailey
from pageant.studio import Lesson, Studio
from playwright.async_api import Locator

REPO = Path(__file__).resolve().parents[1]
SOLUTION = REPO / "tests" / "fixtures" / "grounded-replies"
EMAIL = (
    "Customer email:\nHello, the novel I ordered arrived last week, but I did not enjoy it. Can I send it back?\nAnnie"
)

#: Everything the lesson says, in one place: the caption shown, and what the voice says where the
#: caption's spelling would be misread (the voice spells out "ZIP"). A translation is this table.
NARRATION = {
    "title": {"text": "Bailey, lesson one: your first solution."},
    "nav": {
        "text": "Bailey runs on your computer and opens in your browser. "
        "It has three places: Chat, Assistants and Settings."
    },
    "setup": {"text": "Before the first chat, it needs to know which AI to use."},
    "provider": {"text": "Pick a provider: Anthropic, OpenAI, or a server of your own, local models included."},
    "key": {"text": "Paste your key. It stays on this computer: in the keychain, or in an encrypted file."},
    "install": {
        "text": "A solution is a ready-made package for one job. Install one from a ZIP file or a folder.",
        "spoken": "A solution is a ready-made package for one job. Install one from a zip file, or a folder.",
    },
    "screen": {
        "text": "Before anything is written, Bailey tells you what the package does…",
        "spoken": "Before anything is written, Bailey tells you what the package does:",
    },
    "keys": {
        "text": "…which keys it needs, and what reads them…",
        "spoken": "which keys it needs, and what reads them,",
    },
    "docs": {"text": "…the documents it asks you for…", "spoken": "the documents it asks you for,"},
    "skills": {"text": "…and the skills it brings.", "spoken": "and the skills it brings."},
    "unverified": {"text": "It came from a file, so it is marked Unverified: nobody has checked it yet."},
    "installed": {"text": "Installed. Each assistant keeps its skills, documents and tools in one place."},
    "samples": {"text": "It asks for the shop's policies and FAQ. Try it on the made-up samples first."},
    "indexed": {"text": "Your documents are indexed here, on your computer."},
    "job": {"text": "Now give it a real job: a customer's email."},
    "skill": {"text": "It loads the skill this job needs, then answers from your documents."},
    "meta": {"text": "Under the answer: the skill it used, and what the answer cost on your own key."},
    "cite": {"text": "Every claim points to the passage it came from in your documents."},
    "open": {"text": "Open one to check it."},
    "end": {
        "text": "That's lesson one. Next: tools that ask before they act, apps, "
        "and checking a solution from your terminal."
    },
}

#: The scripted answer to the email: the solution's own example, citing the passage it rests on.
REPLIES = [
    {
        "when": "Can I send it back?",
        "skill": "quote-sources",
        "reply": [
            ["Hi Annie,\n\nThank you for writing, and sorry the novel was not for you. ", None],
            [
                "Yes, you can send it back within 30 days of delivery, in the condition it arrived, for any reason",
                "30 days of delivery",
            ],
            [". Reply to your order confirmation and we will send you a prepaid return label", "prepaid return label"],
            [
                ", and we refund your original payment method within 5 business days of the book reaching us",
                "5 business days of receiving",
            ],
            [".\n\nThe team at Harbour Lane Books", None],
        ],
    }
]


def zipped(folder: Path, into: Path) -> Path:
    path = into / f"{folder.name}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(folder.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(folder).as_posix())
    return path


async def run(s: Studio) -> None:
    page = s.page

    def view(name: str) -> str:
        return f'.nav-item[data-view="{name}"]'

    await s.sleep(600)
    await s.voice("title", 300)
    await s.card(None, 700)

    await s.chapter("Finding your way")
    await s.spot(page.locator("#nav"), 6)
    await s.say("nav")
    await s.spot(page.locator("#setup-needed"), 6)
    await s.say("setup")
    await s.spot(None)
    await s.click(page.locator("#open-settings"))

    await s.chapter("1 · Choose your AI")
    await s.spot(page.locator("#provider-form fieldset"), 8)
    await s.say("provider")
    await s.click(page.locator("#provider-choices label", has_text="Anthropic"))
    await s.click(page.locator("#provider-form button[type=submit]"))
    await page.locator("#key-card").wait_for(state="visible")
    await s.spot(page.locator("#key-card"), 8)
    await s.say("key", 1800)
    await s.click(page.locator("#key"))
    await s.type(page.locator("#key"), "a-made-up-key-for-the-recording", 18)
    await s.click(page.locator("#key-form button[type=submit]"))
    await page.wait_for_function("() => document.querySelector('#key-status').textContent.trim().length > 0")
    await s.sleep(1600)
    await s.spot(None)
    await s.quiet()

    await s.chapter("2 · Install a solution")
    await s.click(page.locator(view("assistants")))
    await s.spot(page.locator(".install-from"), 8)
    await s.say("install")
    async with page.expect_file_chooser() as chosen:
        await s.click(page.locator("label[for=install-zip]"))
    await (await chosen.value).set_files(zipped(SOLUTION, s.work))
    await page.locator("#install-card").wait_for(state="visible")
    await s.sleep(500)
    await s.spot(page.locator("#install-title"), 10)
    await s.say("screen")

    def section(title: str) -> Locator:
        return page.locator(".install-section", has=page.locator("h3", has_text=title))

    await s.spot(section("Keys it needs"), 8)
    await s.say("keys")
    await s.spot(section("Documents it asks you for"), 8)
    await s.say("docs")
    await s.spot(section("Skills"), 8)
    await s.say("skills")
    await s.spot(page.locator("#install-title .unverified"), 6)
    await s.say("unverified")
    await s.spot(None)
    await s.click(page.locator("#install-confirm"))
    item = page.locator(".assistant-item", has_text="Grounded replies")
    await item.wait_for(state="visible")
    await s.spot(item, 8)
    await s.say("installed")
    await s.quiet()

    await s.chapter("3 · Give it documents")
    await s.click(item.locator("button", has_text="Documents"))
    await page.locator("#knowledge-card").wait_for(state="visible")
    await s.spot(page.locator("#knowledge-slots"), 8)
    await s.say("samples", 2600)
    samples = page.locator("#knowledge-slots button", has_text="Use the sample")
    for left in range(await samples.count(), 0, -1):
        await s.click(samples.first)
        await page.wait_for_function(
            "left => [...document.querySelectorAll('#knowledge-slots button')]"
            ".filter(button => button.textContent.includes('Use the sample')).length < left",
            arg=left,
        )
        await s.sleep(500)
    await s.say("indexed")
    await s.spot(None)
    await s.quiet()
    use = item.locator("button", has_text=re.compile(r"^Use$"))
    if await use.count():
        await s.click(use)

    await s.chapter("4 · Give it a job")
    await s.click(page.locator(view("chat")))
    await s.say("job", 1600)
    await s.click(page.locator("#message"))
    await s.type(page.locator("#message"), EMAIL, 16)
    await s.sleep(400)
    await s.click(page.locator("#send"))
    await s.quiet()
    reply = page.locator("#messages .message-assistant").last
    await reply.wait_for(state="visible")
    await s.spot(reply, 8)
    await s.say("skill")
    citations = reply.locator(".citations")
    await citations.wait_for(state="visible", timeout=60_000)
    await s.sleep(300)
    await s.spot(reply.locator(".message-meta"), 6)
    await s.say("meta")
    await s.spot(citations, 8)
    await s.say("cite", 3000)
    await s.click(citations.locator("summary").first)
    await s.say("open")
    await s.sleep(600)
    await s.spot(None)
    await s.quiet()
    await s.chapter(None)
    await s.card(
        {
            "kicker": "Next lessons",
            "title": "Tools · Apps · bailey check",
            "sub": "Tools that ask before they act, apps that run a crew, and checking a solution from your terminal.",
        },
        500,
    )
    await s.voice("end", 900)


LESSON = Lesson(
    page=pages.FIRST_SOLUTION,
    title={
        "kicker": "Bailey · Lesson 1",
        "title": "Your first solution",
        "sub": "Choose your AI, install a solution, and give it a real job.",
    },
    narration=NARRATION,
    run=run,
    app=scripted_bailey.app(REPLIES),
    mark=scripted_bailey.mark(),
)
