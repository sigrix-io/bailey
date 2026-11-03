"""The rules a recorded lesson's captions and chapters keep (``lessons/timeline.py``).

The recorder drives a browser and is run by hand (``lessons/README.md``); what it writes down
is pure, and held here. Chapters follow YouTube's rules, since a video that breaks them shows
none: the first starts at 0:00, and none is shorter than ten seconds.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def timeline() -> ModuleType:
    spec = importlib.util.spec_from_file_location("lessons_timeline", ROOT / "lessons" / "timeline.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
