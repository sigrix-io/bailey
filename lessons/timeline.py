"""The times a recording keeps, written out: its captions, its chapters, and the MP4's chapter markers.

Pure functions, so the rules they hold are tested without a browser (``tests/test_lessons.py``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

#: YouTube shows a video's chapters only when the first starts at 0:00 and none is shorter than this.
SHORTEST_CHAPTER = 10.0


@dataclass(frozen=True)
class Cue:
    """A stretch of the video with its text: a caption, or a chapter."""

    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Mark:
    """Where a lesson said a chapter begins."""

    start: float
    title: str


def vtt_time(seconds: float) -> str:
    """``01:02:05.500``, as WebVTT writes a time; a time before the video's start is its start."""

    ms = max(0, round(seconds * 1000))
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def clock_time(seconds: float) -> str:
    """``1:19``, or ``1:02:05`` past the hour: a time as a video's description gives it."""

    whole = max(0, int(seconds))
    hours, minutes, rest = whole // 3600, whole // 60 % 60, whole % 60
    return f"{hours}:{minutes:02d}:{rest:02d}" if hours else f"{minutes}:{rest:02d}"


def webvtt(cues: Iterable[Cue]) -> str:
    """A WebVTT file of the cues, numbered in the order they start."""

    lines = ["WEBVTT", ""]
    for number, cue in enumerate(sorted(cues, key=lambda cue: cue.start), start=1):
        lines += [str(number), f"{vtt_time(cue.start)} --> {vtt_time(cue.end)}", cue.text, ""]
    return "\n".join(lines)


def chapters(marks: Sequence[Mark], seconds: float, shortest: float = SHORTEST_CHAPTER) -> list[Cue]:
    """The chapters of a video ``seconds`` long, as YouTube reads them.

    The first starts at 0:00, whenever the lesson marked it. A chapter shorter than
    ``shortest`` is not one: the chapter before it runs on over it. When that would leave
    the first one short, the next takes its place from 0:00, since what comes first is
    the title card.
    """

    kept: list[Mark] = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start if index + 1 < len(marks) else seconds
        if not kept:
            kept.append(Mark(0.0, mark.title))
        elif end - mark.start < shortest:
            continue
        elif len(kept) == 1 and mark.start < shortest:
            kept[0] = Mark(0.0, mark.title)
        else:
            kept.append(mark)
    return [
        Cue(mark.start, kept[index + 1].start if index + 1 < len(kept) else seconds, mark.title)
        for index, mark in enumerate(kept)
    ]


def youtube_chapters(parts: Iterable[Cue]) -> str:
    """The chapters as lines to paste into a YouTube description: ``0:19 Choose your AI``."""

    return "".join(f"{clock_time(part.start)} {part.text}\n" for part in parts)


def _escaped(text: str) -> str:
    """FFmpeg's metadata format reads ``=``, ``;``, ``#``, a backslash and a new line as syntax unless escaped."""

    return "".join("\\" + character if character in "=;#\\\n" else character for character in text)


def ffmetadata(title: str, parts: Iterable[Cue]) -> str:
    """The MP4's title and chapter markers, in the format ``ffmpeg -map_chapters`` reads."""

    lines = [";FFMETADATA1", f"title={_escaped(title)}", ""]
    for part in parts:
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={round(part.start * 1000)}",
            f"END={round(part.end * 1000)}",
            f"title={_escaped(part.text)}",
            "",
        ]
    return "\n".join(lines)
