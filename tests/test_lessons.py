"""The rules a recorded lesson keeps: its captions and chapters (``lessons/timeline.py``), and its scripted model.

The recorder drives a browser and is run by hand (``lessons/README.md``); what it writes down
is pure, and held here. Chapters follow YouTube's rules, since a video that breaks them shows
none: the first starts at 0:00, and none is shorter than ten seconds. The scripted model
(``lessons/scripted_bailey.py``) answers as a lesson says, and a script that no longer fits
the app fails the recording rather than filming something the app would not do.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _lessons_module(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"lessons_{name}", ROOT / "lessons" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def timeline() -> ModuleType:
    return _lessons_module("timeline")


@pytest.fixture(scope="module")
def scripted() -> ModuleType:
    return _lessons_module("scripted_bailey")


def test_a_time_is_written_as_webvtt_writes_it_and_as_a_description_gives_it(timeline: ModuleType) -> None:
    assert timeline.vtt_time(0) == "00:00:00.000"
    assert timeline.vtt_time(79.6034) == "00:01:19.603"
    assert timeline.vtt_time(3725.5) == "01:02:05.500"
    assert timeline.vtt_time(-0.2) == "00:00:00.000"
    assert timeline.clock_time(0) == "0:00"
    assert timeline.clock_time(79.6) == "1:19"
    assert timeline.clock_time(3725) == "1:02:05"


def test_captions_are_numbered_in_the_order_they_start(timeline: ModuleType) -> None:
    on_screen = timeline.Cue(5.73, 14.28, "It has three places: Chat, Assistants and Settings.")
    over_the_card = timeline.Cue(0.76, 3.54, "Bailey, lesson one: your first solution.")

    assert timeline.webvtt([on_screen, over_the_card]) == (
        "WEBVTT\n\n"
        "1\n00:00:00.760 --> 00:00:03.540\nBailey, lesson one: your first solution.\n\n"
        "2\n00:00:05.730 --> 00:00:14.280\nIt has three places: Chat, Assistants and Settings.\n"
    )


def test_chapters_start_at_zero_and_a_short_one_runs_into_the_one_before(timeline: ModuleType) -> None:
    Mark, Cue = timeline.Mark, timeline.Cue
    marks = [
        Mark(4.6, "Finding your way"),
        Mark(19.6, "Choose your AI"),
        Mark(38.3, "Install a solution"),
        Mark(79.3, "A moment"),
        Mark(84.0, "Give it a job"),
        Mark(128.0, "Next lessons"),
    ]

    assert timeline.chapters(marks, 133.0) == [
        Cue(0.0, 19.6, "Finding your way"),
        Cue(19.6, 38.3, "Choose your AI"),
        Cue(38.3, 84.0, "Install a solution"),
        Cue(84.0, 133.0, "Give it a job"),
    ]


def test_a_first_chapter_too_short_gives_way_to_the_next(timeline: ModuleType) -> None:
    Mark, Cue = timeline.Mark, timeline.Cue
    marks = [Mark(0.5, "The title card"), Mark(6.0, "Finding your way"), Mark(30.0, "Choose your AI")]

    assert timeline.chapters(marks, 60.0) == [Cue(0.0, 30.0, "Finding your way"), Cue(30.0, 60.0, "Choose your AI")]
    assert timeline.chapters([], 60.0) == []


def test_the_chapters_go_into_a_description_and_into_the_mp4(timeline: ModuleType) -> None:
    parts = timeline.chapters([timeline.Mark(4.6, "Finding your way"), timeline.Mark(19.6, "Choose your AI")], 79.0)

    assert timeline.youtube_chapters(parts) == "0:00 Finding your way\n0:19 Choose your AI\n"
    metadata = timeline.ffmetadata("Lesson 1: a=b; #1 \\ more", parts)
    # "=", ";", "#" and a backslash are ffmpeg's syntax, so a title carrying one escapes it.
    assert metadata.startswith(";FFMETADATA1\ntitle=Lesson 1: a\\=b\\; \\#1 \\\\ more\n")
    assert "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=19600\ntitle=Finding your way\n" in metadata
    assert "[CHAPTER]\nTIMEBASE=1/1000\nSTART=19600\nEND=79000\ntitle=Choose your AI\n" in metadata


MAIL_REPLY = {
    "when": "find Annie's email",
    "calls": [
        {"tool": "search_mail", "args": {"query": "Annie"}},
        {"tool": "save_draft", "args": {"email_id": "m-201", "body": "Hi Annie"}},
    ],
    "reply": [["Saved as a draft.", None]],
}


def test_a_scripted_reply_makes_its_calls_in_order_one_a_turn_then_answers(scripted: ModuleType) -> None:
    emulator = scripted.emulator
    conversation = emulator.Conversation(user=["Please find Annie's email."], tools=["search_mail", "save_draft"])

    first = scripted.answer(conversation, [MAIL_REPLY])
    assert [(call.name, call.arguments) for call in first.calls] == [("search_mail", {"query": "Annie"})]
    conversation.got("search_mail", '[{"id": "m-201"}]')
    second = scripted.answer(conversation, [MAIL_REPLY])
    assert [(call.name, call.arguments) for call in second.calls] == [
        ("save_draft", {"email_id": "m-201", "body": "Hi Annie"})
    ]
    conversation.got("save_draft", "Draft saved.")
    last = scripted.answer(conversation, [MAIL_REPLY])
    assert (last.text, last.calls) == ("Saved as a draft.", [])


def test_a_script_calling_a_tool_the_app_did_not_offer_fails_the_recording(scripted: ModuleType) -> None:
    conversation = scripted.emulator.Conversation(user=["Find Annie's email."], tools=["search_mail"])

    first = scripted.answer(conversation, [MAIL_REPLY])
    conversation.got(first.calls[0].name, "[]")
    with pytest.raises(scripted.emulator.Violation, match="save_draft"):
        scripted.answer(conversation, [MAIL_REPLY])


def test_a_message_the_lesson_scripts_no_reply_for_is_left_to_the_emulator(scripted: ModuleType) -> None:
    conversation = scripted.emulator.Conversation(user=["Hello"], tools=["search_mail", "save_draft"])

    assert scripted.answer(conversation, [MAIL_REPLY]) is None


def test_a_scripted_reply_loads_its_skill_then_cites_the_passage_holding_its_phrase(scripted: ModuleType) -> None:
    system = (
        "Draft replies.\n\n## Skills\n\n- `quote-sources`: Quotes the documents.\n\n"
        "## Your documents\n\nBelow are the person's documents, in numbered passages.\n\n"
        "### Document: policies.md (Shop policies)\n\n#### Returns\n"
        "[P3] - You can return a book within 30 days of delivery, for any reason.\n"
    )
    conversation = scripted.emulator.Conversation(system=system, user=["Can I send it back?"], tools=["load_skill"])
    reply = {
        "when": "send it back",
        "skill": "quote-sources",
        "reply": [["Yes, within 30 days", "30 days of delivery"], [", for any reason", "for any reason"], [".", None]],
    }

    first = scripted.answer(conversation, [reply])
    assert [(call.name, call.arguments) for call in first.calls] == [("load_skill", {"name": "quote-sources"})]
    conversation.got("load_skill", "# Quote sources")
    # Once per passage: the second phrase is in the same passage, so it is not cited again.
    assert scripted.answer(conversation, [reply]).text == "Yes, within 30 days [P3], for any reason."


def test_a_reply_whose_skill_the_solution_lost_comes_without_what_the_skill_grounds(scripted: ModuleType) -> None:
    reply = {
        "when": "gift wrap",
        "skill": "quote-sources",
        "reply": [["NOT COVERED: The documents do not mention gift wrapping.\n\nHi Keiko, the team will reply.", None]],
    }
    lost = scripted.emulator.Conversation(
        system="Draft replies.\n\n## Skills\n\n- `reply-style`: Keeps it short.\n",
        user=["Do you gift wrap?"],
        tools=["load_skill"],
    )

    turn = scripted.answer(lost, [reply])
    # No load of a skill it does not have, and no note of what the documents leave out.
    assert (turn.calls, turn.text) == ([], "Hi Keiko, the team will reply.")
