"""A recorded lesson as a bundle Sigrix imports in one step, and a pack of them.

Sigrix's Learn console imports a lesson from a **bundle**: a folder holding its page (``lesson.json``)
and its media under fixed names (``video.mp4``, ``captions.vtt``, ``chapters.vtt``, ``cover.jpg``).
A **pack** is a zip of bundles with the learning path that lists them (``path.json``), and its
Import lessons page checks every lesson, shows what importing them will do, and saves them. The
format is Sigrix's (``src/services/learn_import.py`` in sigrix-io/sigrix); this module writes it.

A lesson records into a folder of its own (``studio.record``), named by its page's ``folder``, which
is its bundle: the files above, and ``chapters.txt`` for a YouTube description, which an import
passes over. Its page is ``pages.py``'s, written as ``lesson.json`` when it is recorded and again
when it is packed, so a corrected summary reaches the site with a pack and an import, and no
recording.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LESSON_FORMAT = "sigrix-lesson/1"
PATH_FORMAT = "sigrix-learning-path/1"
PAGE = "lesson.json"
VIDEO = "video.mp4"
CAPTIONS = "captions.vtt"
CHAPTERS = "chapters.vtt"
COVER = "cover.jpg"
#: The chapters as lines for a YouTube description: in the folder, never in a pack.
YOUTUBE_CHAPTERS = "chapters.txt"
#: What a lesson's folder must hold before it is packed, and what a pack carries of it.
RECORDED = (VIDEO, CAPTIONS, CHAPTERS, COVER)
PACKED = (PAGE, *RECORDED)
#: The media is compressed already; the text is not.
_STORED = frozenset({VIDEO, COVER})


@dataclass(frozen=True)
class Page:
    """A lesson's page on sigrix.io/learn, and the folder it is recorded into."""

    #: Where the recorder writes the lesson, which is also its name for a YouTube upload.
    folder: str
    #: Its address on the site, ``/learn/<slug>``: the one thing an import matches it by.
    slug: str
    title: str
    summary: str
    body: str
    category: str = "tutorials"
    tags: tuple[str, ...] = ("bailey", "video")


@dataclass(frozen=True)
class LearningPath:
    """The path that lists the lessons on sigrix.io/learn, in their order."""

    title: str
    audience: str
    description: str
    lessons: tuple[Page, ...]


def page_json(page: Page) -> dict[str, Any]:
    return {
        "format": LESSON_FORMAT,
        "slug": page.slug,
        "title": page.title,
        "summary": page.summary,
        "category": page.category,
        "tags": list(page.tags),
        "body_markdown": page.body,
    }


def path_json(path: LearningPath) -> dict[str, Any]:
    return {
        "format": PATH_FORMAT,
        "title": path.title,
        "audience": path.audience,
        "description": path.description,
        "lessons": [page.slug for page in path.lessons],
    }


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def write_page(page: Page, folder: Path) -> Path:
    """Write ``lesson.json`` into a lesson's folder."""

    target = folder / PAGE
    target.write_text(_json(page_json(page)), encoding="utf-8")
    return target


def pack(out: Path, path: LearningPath, target: Path) -> Path:
    """Zip each lesson of ``path`` recorded in ``out``, its page written afresh, with the path itself.

    Refuses, naming every file missing, if a lesson has not been recorded whole: a pack that leaves
    one out would import a path naming a lesson that is not there.
    """

    missing = [
        f"{page.folder}/{name}"
        for page in path.lessons
        for name in RECORDED
        if not (out / page.folder / name).is_file()
    ]
    if missing:
        raise FileNotFoundError("Record these lessons first; missing: " + ", ".join(missing))
    root = target.stem
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as archive:
        for page in path.lessons:
            folder = out / page.folder
            write_page(page, folder)
            for name in PACKED:
                kind = zipfile.ZIP_STORED if name in _STORED else zipfile.ZIP_DEFLATED
                archive.write(folder / name, f"{root}/{page.folder}/{name}", compress_type=kind)
        archive.writestr(f"{root}/path.json", _json(path_json(path)), compress_type=zipfile.ZIP_DEFLATED)
    return target
