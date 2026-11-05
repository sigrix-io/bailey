"""Each lesson's page on sigrix.io/learn, and the learning path that lists them.

A lesson's file names its page (``Lesson(page=...)``), so the folder it records into and the page it
becomes are written once, here. ``pack.py`` zips the recorded lessons with these pages, and Sigrix's
Learn console imports the pack (``bundle.py``); ``youtube.py`` writes each one's YouTube upload from
it. An import matches a lesson by its ``slug``, so a lesson already on the site is updated in place:
change a slug and it is a new page. Each page says what in its lesson is scripted or a stand-in, as a
lesson says wherever it is published.
"""

from __future__ import annotations

from bundle import LearningPath, Page

FIRST_SOLUTION = Page(
    folder="bailey-lesson-01-first-solution",
    slug="bailey-lesson-1-your-first-solution",
    youtube_url="https://www.youtube.com/watch?v=OWB-Dw8namc",
    title="Bailey lesson 1: your first solution",
    summary=(
        "Choose your AI, install a ready-made solution and give it a real job: answers from your own "
        "documents, with the source of each one."
    ),
    body=(
        "## What you will see\n\n"
        "- Choosing an AI provider and adding your own key, which stays on your computer.\n"
        "- Installing a solution from a file, and what the install screen tells you about it.\n"
        "- Giving it your documents, then a real question it answers from them, citing the passage.\n\n"
        "It is filmed in Bailey itself. Only the AI's answer is scripted, so the lesson comes out the same "
        "each time it is recorded.\n\n"
        "## Try it yourself\n\n"
        "Bailey is open source. Start it with `uvx bailey`, then install a solution from a ZIP or a folder.\n"
    ),
)

TOOLS_THAT_ASK = Page(
    folder="bailey-lesson-02-tools-that-ask",
    slug="bailey-lesson-2-tools-that-ask-before-they-act",
    youtube_url="https://www.youtube.com/watch?v=a6gthLlTU5Q",
    title="Bailey lesson 2: tools that ask before they act",
    summary=(
        "What a solution's tools may do, which of them ask first, and how you answer: once, always for "
        "this assistant, or no."
    ),
    body=(
        "## What you will see\n\n"
        "- A solution's tool server and the key it needs.\n"
        "- A tool that only reads, and runs straight away.\n"
        "- A tool that writes, and asks you first.\n"
        '- Taking an "always" back in Settings.\n\n'
        "It is filmed in Bailey itself. The AI's answers are scripted, and the mail server is a stand-in "
        "whose drafts go nowhere, so the lesson comes out the same each time it is recorded.\n"
    ),
)

APPS_THAT_RUN_A_CREW = Page(
    folder="bailey-lesson-03-apps-that-run-a-crew",
    slug="bailey-lesson-3-apps-that-run-a-crew",
    youtube_url="https://www.youtube.com/watch?v=c5B9m8V6amU",
    title="Bailey lesson 3: apps that run a crew",
    summary=(
        "A solution's app on the install screen, its libraries installed once, and a run of its crew "
        "from the form it draws."
    ),
    body=(
        "## What you will see\n\n"
        "- The app a solution carries, listed before you install it.\n"
        "- Its libraries, installed once, in an environment of its own.\n"
        "- A run of its crew of agents, from the form the app draws.\n\n"
        "It is filmed in Bailey itself. The crew's answers are scripted, and its app runs in a stand-in "
        "environment, so the lesson comes out the same each time it is recorded.\n"
    ),
)

CHECK_A_SOLUTION = Page(
    folder="bailey-lesson-04-check-a-solution",
    slug="bailey-lesson-4-check-a-solution",
    youtube_url="https://www.youtube.com/watch?v=4qbF348Y3DY",
    title="Bailey lesson 4: check a solution",
    summary=(
        "bailey doctor and bailey check, from your terminal: is this machine ready, does this solution do "
        "its job, and what a failed case looks like."
    ),
    body=(
        "## What you will see\n\n"
        "- `bailey doctor`, and what it says about a machine.\n"
        "- `bailey check` on a solution whose cases all pass, and what the check cost.\n"
        "- The same check on a draft that lost a skill: which cases fail, and why.\n\n"
        "It is filmed in Bailey itself, and its commands really run. Only the AI's answers are scripted, so "
        "the lesson comes out the same each time it is recorded.\n"
    ),
)

PATH = LearningPath(
    title="Bailey: getting started",
    audience="New to Bailey",
    description=(
        "Four short videos: your first solution, tools that ask, apps that run a crew, and checking a solution."
    ),
    lessons=(FIRST_SOLUTION, TOOLS_THAT_ASK, APPS_THAT_RUN_A_CREW, CHECK_A_SOLUTION),
)
