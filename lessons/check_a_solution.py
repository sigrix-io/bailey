"""Lesson 4, check a solution: ``bailey doctor`` and ``bailey check``, from a terminal.

    python lessons/check_a_solution.py [--out lessons/out]

It films the terminal (``terminal.html``), where each command is typed and then really run:
``bailey`` is Bailey's own command line, put on the terminal's PATH pointed at the scripted
model (``scripted_bailey.py --cli``), with the provider and key the setup saved in Settings.
It checks lesson 1's bookshop solution, whose seven cases pass, then a draft of it that lost
its quote-sources skill, whose examples then fail, each with its reason. The scripted answers
to the examples are the solution's own example replies; ``bailey check``'s verdicts are its own.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

import pages
from playwright.async_api import Locator
from studio import Lesson, Studio, main, provider_chosen

HERE = Path(__file__).resolve().parent
SOLUTION = HERE.parent / "tests" / "fixtures" / "grounded-replies"
DRAFT = "grounded-replies-draft"

#: Everything the lesson says, in one place (see first_solution.py).
NARRATION = {
    "title": {"text": "Bailey, lesson four: check a solution, from your terminal."},
    "doctor_intro": {
        "text": "Bailey has two commands for your terminal. The first, bailey doctor, checks this machine."
    },
    "doctor": {
        "text": "It says what Bailey needs and why, and how to fix anything missing. It reads, and changes nothing."
    },
    "keychain": {"text": "This machine has no keychain, so it says what Bailey does instead."},
    "check_intro": {
        "text": "Before you share a solution, bailey check runs it, in a Bailey of its own that keeps nothing."
    },
    "cases": {
        "text": "It runs the solution's own examples, then asks each document a question it answers, and one that nothing answers."
    },
    "passed": {"text": "Every case passed."},
    "cost": {"text": "And it says what the check cost, on the provider and key in your settings."},
    "draft_intro": {"text": "Now a draft of the same solution, which lost its quote-sources skill."},
    "fails": {
        "text": "Three cases fail, each with its reason: the examples quote the shop's documents, and these answers don't."
    },
    "ci": {
        "text": "A failed case makes it exit with an error, so a script or a CI job can stop there. With --json, it prints a report a program reads.",
        "spoken": "A failed case makes it exit with an error, so a script or a CI job can stop there. With the json option, it prints a report a program reads.",
    },
    "end": {"text": "That's lesson four. Next, with Bailey 0.2: the Store."},
}

#: The scripted answers to the solution's examples are its own example replies, which follow its
#: quote-sources skill; a draft without the skill gets them without what the skill grounds.
REPLIES = [
    {"when": example["input"], "skill": "quote-sources", "reply": [[example["output"], None]]}
    for example in json.loads((SOLUTION / "assistant.json").read_text(encoding="utf-8"))["examples"]
]


def lay_out(work: Path) -> None:
    """The solution, and its draft without the quote-sources skill, beside each other."""

    shutil.copytree(SOLUTION, work / SOLUTION.name)
    draft = work / DRAFT
    shutil.copytree(SOLUTION, draft)
    shutil.rmtree(draft / "skills" / "quote-sources")
    described = json.loads((draft / "assistant.json").read_text(encoding="utf-8"))
    described["skills"] = [skill for skill in described["skills"] if skill["name"] != "quote-sources"]
    (draft / "assistant.json").write_text(json.dumps(described, indent=2) + "\n", encoding="utf-8")


async def setup(s: Studio) -> None:
    await provider_chosen(s)
    lay_out(s.work)
    await s.terminal("bailey — ~/shop", "~/shop")


async def run(s: Studio) -> None:
    page = s.page

    def output_of(command: str) -> Locator:
        return page.locator("#screen .block", has=page.locator(".typed", has_text=command)).locator(".out")

    def line(text: str) -> Locator:
        return page.locator("#screen .line", has_text=text).last

    await s.sleep(600)
    await s.voice("title", 300)
    await s.card(None, 700)

    await s.chapter("1 · Is this machine ready?")
    await s.say("doctor_intro", 2600)
    await s.shell("bailey doctor")
    await s.spot(output_of("bailey doctor"), 8)
    await s.say("doctor")
    await s.spot(line("encrypted file instead"), 6)
    await s.say("keychain")
    await s.spot(None)
    await s.quiet()
    await s.shell("clear", 60)

    await s.chapter("2 · Check a solution")
    await s.say("check_intro", 3200)
    checking = asyncio.create_task(s.shell("bailey check grounded-replies"))
    await s.sleep(3500)
    await s.say("cases")
    await checking
    await s.spot(line("cases passed"), 6)
    await s.say("passed")
    await s.spot(line("It took"), 6)
    await s.say("cost")
    await s.spot(None)
    await s.quiet()
    await s.shell("clear", 60)

    await s.chapter("3 · When a case fails")
    await s.say("draft_intro", 2800)
    await s.shell(f"bailey check {DRAFT}")
    await s.spot(output_of(f"bailey check {DRAFT}"), 8)
    await s.say("fails")
    await s.spot(line("cases failed"), 6)
    await s.say("ci")
    await s.spot(None)
    await s.quiet()
    await s.chapter(None)
    await s.card(
        {
            "kicker": "Coming next",
            "title": "The Store, in Bailey 0.2",
            "sub": "Sign in with Sigrix, and install and update solutions in the app.",
        },
        500,
    )
    await s.voice("end", 900)


LESSON = Lesson(
    page=pages.CHECK_A_SOLUTION,
    title={
        "kicker": "Bailey · Lesson 4",
        "title": "Check a solution",
        "sub": "bailey doctor and bailey check, from your terminal.",
    },
    narration=NARRATION,
    replies=REPLIES,
    run=run,
    setup=setup,
    commands={"bailey": (HERE / "scripted_bailey.py", "--cli")},
)

if __name__ == "__main__":
    sys.exit(main(LESSON))
