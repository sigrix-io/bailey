"""Each lesson's YouTube upload, written from its page: its title, its description and its tags.

    python lessons/youtube.py

writes ``youtube.txt`` into each recorded lesson's folder in ``lessons/out/`` (``--out`` names
another), to paste into YouTube Studio beside the folder's ``video.mp4``, ``cover.jpg`` (the
thumbnail) and ``captions.vtt``. The text is the page's in ``pages.py``, so a lesson's title, summary
and address are written once, for sigrix.io and for YouTube alike.

The description opens with the summary and a link to the lesson's page on sigrix.io: its address,
or the tracked link Sigrix's Learn console makes for the video's post, given as
``--link <slug>=https://sigrix.io/go/<code>``, so the visits YouTube sends are counted as YouTube's.
Then come the chapters, from the recording's ``chapters.txt``, which YouTube turns into the video's
chapters, that its answers are scripted, as a lesson says wherever it is published, and where to get
Bailey.

YouTube refuses a title or a description holding ``<`` or ``>``, a title over 100 characters, a
description over 5,000 bytes and tags over 500 characters, and makes no chapters of a list that does
not start at 0:00, holds fewer than three, or has one under ten seconds. Each is checked here, and a
lesson that breaks one stops the command, naming it, before anything is written: found here, not in
an upload that is refused or that shows no chapters.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import bundle
import pages
import timeline

HERE = Path(__file__).resolve().parent
SITE = "https://sigrix.io"
#: Where a viewer gets Bailey, and every lesson about it on Sigrix Learn.
BAILEY_PAGE = f"{SITE}/open-source/bailey"
LESSONS_PAGE = f"{SITE}/learn/tag/{bundle.TAG}"
ABOUT = (
    "Bailey is an open-source AI assistant that runs ready-made solutions on your own machine, with your own AI key."
)
#: What every lesson says wherever it is published (``lessons/README.md``); its page says the rest.
SCRIPTED = "Filmed in Bailey itself, on a scripted model: the AI's answers are written in advance."
TAGS = ("Bailey", "Sigrix", "AI assistant", "open source AI", "local AI", "AI agents", "tutorial")
#: The file written into each lesson's folder.
UPLOAD = "youtube.txt"

TITLE_MAX = 100
DESCRIPTION_MAX_BYTES = 5000
TAGS_MAX = 500
CHAPTERS_MIN = 3
_REFUSED = re.compile(r"[<>]")
_CHAPTER = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2}) +(\S.*)")


@dataclass(frozen=True)
class Upload:
    """What YouTube Studio asks for beside the files."""

    title: str
    description: str
    tags: tuple[str, ...]


def chapters(text: str) -> list[tuple[int, str]]:
    """``chapters.txt`` read back as (second, title), held to the rules YouTube makes chapters by."""

    found: list[tuple[int, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _CHAPTER.fullmatch(line.strip())
        if match is None:
            raise ValueError(f"a chapter line is not a time and a title: {line!r}")
        hours, minutes, seconds, title = match.groups()
        found.append((int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds), title))
    if len(found) < CHAPTERS_MIN:
        raise ValueError(f"YouTube makes chapters of {CHAPTERS_MIN} or more; this lesson has {len(found)}")
    if found[0][0] != 0:
        raise ValueError(f"the first chapter starts at {timeline.clock_time(found[0][0])}; YouTube wants 0:00")
    for (start, _), (after, title) in zip(found, found[1:], strict=False):
        if after - start < timeline.SHORTEST_CHAPTER:
            raise ValueError(
                f"the chapter before {title!r} is {after - start} s; YouTube wants each at least "
                f"{timeline.SHORTEST_CHAPTER:.0f} s"
            )
    return found


def _tags_length(tags: Sequence[str]) -> int:
    """How YouTube counts its 500: each tag, the commas between them, and the quotes a tag with a space takes."""

    return sum(len(tag) + (2 if " " in tag else 0) for tag in tags) + max(0, len(tags) - 1)


def upload_for(
    page: bundle.Page, *, number: int, path: bundle.LearningPath, chapters_text: str, link: str = ""
) -> Upload:
    """A lesson's upload, the ``number``-th of ``path``, refused where YouTube would refuse it."""

    marks = chapters(chapters_text)
    lines = [
        page.summary,
        "",
        f"Watch it with its transcript on sigrix.io: {link or f'{SITE}/learn/{page.slug}'}",
        "",
        "Chapters",
        *(f"{timeline.clock_time(start)} {title}" for start, title in marks),
        "",
        SCRIPTED,
        "",
        f"Lesson {number} of {len(path.lessons)} in {path.title}. All of them: {LESSONS_PAGE}",
        "",
        ABOUT,
        f"Get Bailey: {BAILEY_PAGE}",
    ]
    upload = Upload(title=page.title, description="\n".join(lines), tags=TAGS)
    if len(upload.title) > TITLE_MAX:
        raise ValueError(f"{page.slug}: its title is {len(upload.title)} characters; YouTube takes {TITLE_MAX}")
    for field, value in (("title", upload.title), ("description", upload.description)):
        if _REFUSED.search(value):
            raise ValueError(f"{page.slug}: its {field} holds < or >, which YouTube refuses")
    size = len(upload.description.encode("utf-8"))
    if size > DESCRIPTION_MAX_BYTES:
        raise ValueError(f"{page.slug}: its description is {size} bytes; YouTube takes {DESCRIPTION_MAX_BYTES}")
    if _tags_length(upload.tags) > TAGS_MAX:
        raise ValueError(
            f"the tags are {_tags_length(upload.tags)} characters as YouTube counts them; it takes {TAGS_MAX}"
        )
    return upload


def text_of(upload: Upload) -> str:
    """``youtube.txt``: each field under the name YouTube Studio gives it, to copy as it stands."""

    return f"TITLE\n{upload.title}\n\nDESCRIPTION\n{upload.description}\n\nTAGS\n{', '.join(upload.tags)}\n"


def write(out: Path, path: bundle.LearningPath, links: Mapping[str, str] | None = None) -> list[Path]:
    """Write ``youtube.txt`` into each recorded lesson of ``path`` in ``out``, or nothing.

    ``links`` gives a lesson's tracked link by its slug. Every lesson is read and held to YouTube's
    rules before the first file is written, so one that breaks a rule leaves none half done.
    """

    links = dict(links or {})
    unknown = sorted(set(links) - {page.slug for page in path.lessons})
    if unknown:
        raise ValueError("No lesson has this slug: " + ", ".join(unknown))
    for slug, link in links.items():
        if not link.startswith("https://"):
            raise ValueError(f"{slug}: a link starts with https://, as the Learn console gives it")
    missing = [
        f"{page.folder}/{bundle.YOUTUBE_CHAPTERS}"
        for page in path.lessons
        if not (out / page.folder / bundle.YOUTUBE_CHAPTERS).is_file()
    ]
    if missing:
        raise FileNotFoundError("Record these lessons first; missing: " + ", ".join(missing))
    uploads = [
        (
            out / page.folder / UPLOAD,
            upload_for(
                page,
                number=number,
                path=path,
                chapters_text=(out / page.folder / bundle.YOUTUBE_CHAPTERS).read_text(encoding="utf-8"),
                link=links.get(page.slug, ""),
            ),
        )
        for number, page in enumerate(path.lessons, start=1)
    ]
    for target, upload in uploads:
        target.write_text(text_of(upload), encoding="utf-8")
    return [target for target, _ in uploads]


def _link(value: str) -> tuple[str, str]:
    slug, sep, link = value.partition("=")
    if not sep or not slug or not link:
        raise argparse.ArgumentTypeError(f"give a link as <slug>=<link>, not {value!r}")
    return slug.strip(), link.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write each recorded lesson's YouTube title, description and tags.")
    parser.add_argument(
        "--out", type=Path, default=HERE / "out", help="where the lessons were recorded (default: lessons/out)"
    )
    parser.add_argument(
        "--link",
        type=_link,
        action="append",
        default=[],
        metavar="SLUG=URL",
        help="a lesson's tracked link from Sigrix's Learn console, for its description (once per lesson)",
    )
    args = parser.parse_args(argv)
    try:
        written = write(args.out, pages.PATH, dict(args.link))
    except (FileNotFoundError, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    for target in written:
        sys.stdout.write(f"{target}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
