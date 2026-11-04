"""Lesson 3, apps that run a crew: a solution's app, its environment set up once, and a run of its crew.

    python lessons/apps_that_run_a_crew.py [--out lessons/out]

It installs ``solutions/harbour-weekly-digest``, lesson 1's bookshop again, whose app is a
crew of two: one agent picks the week's highlights, the other writes the newsletter. The
app's environment is a stand-in, made in seconds rather than downloaded (``app_task_seconds``
below, and ``scripted_bailey.py``), and its crew's answers are scripted, as the model's are
in every lesson; wherever the lesson is published it says so.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

from playwright.async_api import Locator
from studio import Lesson, Studio, main, provider_chosen

HERE = Path(__file__).resolve().parent
SOLUTION = HERE / "solutions" / "harbour-weekly-digest"

NOTES = (
    "New this week: The Lighthouse Keeper by Mara Quinn.\n"
    "This Saturday at 11am: story time for under-sevens.\n"
    "Staff pick: Salt and Stone, a walking book about the Cornish coast."
)
NEWSLETTER = (
    "Hello from Harbour Lane Books.\n\n"
    "New on our shelves this week is The Lighthouse Keeper by Mara Quinn.\n\n"
    "This Saturday at 11am there is story time for under-sevens: bring a little one and settle in.\n\n"
    "Our staff pick is Salt and Stone, a walking book about the Cornish coast, for anyone with a long walk "
    "in mind.\n\n"
    "See you in the shop,\nThe team at Harbour Lane Books"
)

#: Everything the lesson says, in one place (see first_solution.py).
NARRATION = {
    "title": {"text": "Bailey, lesson three: apps that run a crew."},
    "install": {"text": "Some solutions bring an app: a small program whose crew of AI agents shares one job."},
    "apps": {"text": "The install screen names each app, and what it needs: an environment of its own, set up once."},
    "key": {"text": "The app runs on your own AI key, which Bailey hands it from Settings."},
    "trust": {"text": "An app is a program its seller wrote, so install solutions from people you trust."},
    "prepare": {
        "text": "Before its first run, Bailey asks to install the app's libraries: about 250 MB, downloaded once.",
        "spoken": "Before its first run, Bailey asks to install the app's libraries: about 250 megabytes, downloaded once.",
    },
    "installing": {
        "text": "They install in the background, in a place of their own. A first install takes a few minutes."
    },
    "open": {"text": "Once they are in, open the app."},
    "form": {"text": "The app draws its own form. Give it the week's notes."},
    "crew": {
        "text": "Its crew works a step at a time: one agent picks the highlights, the other writes the newsletter."
    },
    "result": {"text": "The newsletter, ready to send, and under it what the run used, on your own key."},
    "end": {"text": "That's lesson three. Next: checking a solution from your terminal."},
}

#: The crew's scripted answers: each of its two tasks reaches the model as a message.
REPLIES = [
    {
        "when": "Pick this week's three highlights",
        "reply": [
            [
                "1. New arrival: The Lighthouse Keeper by Mara Quinn.\n"
                "2. Event: story time for under-sevens, this Saturday at 11am.\n"
                "3. Staff pick: Salt and Stone, a walking book about the Cornish coast.",
                None,
            ]
        ],
    },
    {"when": "Write this week's newsletter", "reply": [[NEWSLETTER, None]]},
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
    await s.sleep(600)
    await s.voice("title", 300)
    await s.card(None, 700)

    await s.chapter("1 · An app in a solution")
    await s.spot(page.locator(".install-from"), 8)
    await s.say("install")
    async with page.expect_file_chooser() as chosen:
        await s.click(page.locator("label[for=install-zip]"))
    await (await chosen.value).set_files(zipped(SOLUTION, s.work))
    await page.locator("#install-card").wait_for(state="visible")
    await s.sleep(500)

    def section(title: str) -> Locator:
        return page.locator(".install-section", has=page.locator("h3", has_text=title))

    await s.spot(section("Apps").locator(".app-entry"), 8)
    await s.say("apps")
    await s.spot(page.locator(".install-key", has_text="ANTHROPIC_API_KEY"), 8)
    await s.say("key")
    await s.spot(page.locator("#install-notices .notice-card", has_text="apps is a program"), 8)
    await s.say("trust")
    await s.spot(None)
    await s.quiet()
    await s.click(page.locator("#install-confirm"))

    await s.chapter("2 · Set it up once")
    entry = page.locator("#apps-list .app-entry", has_text="Digest")
    await entry.wait_for(state="visible")
    await s.sleep(400)
    await s.spot(entry, 8)
    await s.say("prepare", 4200)
    await s.click(entry.locator("button", has_text="Install its libraries"))
    await entry.filter(has_text="Installing its libraries").wait_for(timeout=30_000)
    await s.spot(entry, 8)
    await s.say("installing")
    opener = entry.locator("button", has_text="Open")
    await opener.wait_for(state="visible", timeout=120_000)
    await s.spot(opener, 6)
    await s.say("open", 1800)
    await s.click(opener)
    await page.locator("#app-run .run-field-control").first.wait_for(state="visible", timeout=60_000)
    await s.spot(None)
    await s.quiet()

    await s.chapter("3 · Run its crew")
    field = page.locator("#app-run .run-field").first
    await s.spot(field, 8)
    await s.say("form", 2400)
    control = field.locator(".run-field-control")
    await s.click(control)
    await s.type(control, NOTES, 12)
    await s.sleep(500)
    await s.spot(None)
    await s.click(page.locator("#app-run button.run-go"))
    await page.locator("#app-run .run-timeline-entry").first.wait_for(state="visible", timeout=60_000)
    await s.spot(page.locator("#app-run .run-timeline"), 8)
    await s.say("crew")
    result = page.locator("#app-run .run-result-rendered")
    await result.filter(has_text="See you in the shop").wait_for(timeout=120_000)
    await s.sleep(600)
    await s.spot(page.locator("#app-run .run-output"), 8)
    await s.say("result")
    await s.spot(None)
    await s.quiet()
    await s.chapter(None)
    await s.card(
        {
            "kicker": "Next lesson",
            "title": "bailey check",
            "sub": "Checking a solution from your terminal: its examples, run before anyone installs it.",
        },
        500,
    )
    await s.voice("end", 900)


LESSON = Lesson(
    slug="bailey-lesson-03-apps-that-run-a-crew",
    title={
        "kicker": "Bailey · Lesson 3",
        "title": "Apps that run a crew",
        "sub": "A solution's app, set up once, and a run of its crew.",
    },
    narration=NARRATION,
    replies=REPLIES,
    run=run,
    setup=provider_chosen,
    app_task_seconds=3.0,
)

if __name__ == "__main__":
    sys.exit(main(LESSON))
