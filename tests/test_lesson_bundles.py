"""A recorded lesson as the bundle Sigrix's Learn console imports (``lessons/bundle.py``, ``lessons/pages.py``).

Each lesson used to reach sigrix.io by hand: its title, slug, summary and body typed into the Learn
editor, and its video, captions, chapters and cover picked one at a time. Now its page is written
once, in ``pages.py``, the recorder writes it beside the media as ``lesson.json``, and ``pack.py``
zips the recorded lessons with their learning path into one file the console imports.

Held here: every page is one the import takes (the format is Sigrix's, and the rules it enforces
are restated where they are checked), each lesson file names its page, and a pack carries exactly the
files the import reads, its pages written afresh, and refuses a lesson not recorded whole. Each
lesson's YouTube upload (``lessons/youtube.py``) is written from the same page and held to what
YouTube refuses, every page says what in its lesson is scripted, and the README links the lessons
in their path's order.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib.util
import json
import re
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
LESSONS = ROOT / "lessons"

# What Sigrix's import refuses a page for (src/services/learn_import.py and the Learn editor's own
# rules): a slug that is not lowercase words joined by hyphens, a missing title or summary, a category
# it does not have. The categories are the Learn index's.
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CATEGORIES = {"foundations", "tutorials", "patterns", "seller", "buyer", "case"}


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"lessons_{name}", LESSONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bundle() -> ModuleType:
    return _load("bundle")


@pytest.fixture(scope="module")
def pages(bundle: ModuleType) -> ModuleType:
    # The lessons' folder imports its modules by their bare names, as running a lesson does.
    sys.modules["bundle"] = bundle
    try:
        return _load("pages")
    finally:
        del sys.modules["bundle"]


@pytest.fixture(scope="module")
def pack_script(bundle: ModuleType, pages: ModuleType) -> ModuleType:
    sys.modules["bundle"], sys.modules["pages"] = bundle, pages
    try:
        return _load("pack")
    finally:
        del sys.modules["bundle"], sys.modules["pages"]


@pytest.fixture(scope="module")
def timeline() -> ModuleType:
    return _load("timeline")


@pytest.fixture(scope="module")
def youtube(bundle: ModuleType, pages: ModuleType, timeline: ModuleType) -> ModuleType:
    sys.modules["bundle"], sys.modules["pages"], sys.modules["timeline"] = bundle, pages, timeline
    try:
        return _load("youtube")
    finally:
        del sys.modules["bundle"], sys.modules["pages"], sys.modules["timeline"]


def _every_page(pages: ModuleType, bundle: ModuleType) -> list[object]:
    return [value for value in vars(pages).values() if isinstance(value, bundle.Page)]


def test_every_page_is_one_the_import_takes(pages: ModuleType, bundle: ModuleType) -> None:
    for page in pages.PATH.lessons:
        data = bundle.page_json(page)
        assert data["format"] == "sigrix-lesson/1"
        assert SLUG.match(data["slug"]) and len(data["slug"]) <= 120, data["slug"]
        assert data["title"].strip() and len(data["title"]) <= 200
        assert data["summary"].strip() and len(data["summary"]) <= 600
        assert data["category"] in CATEGORIES
        assert data["tags"] and all(isinstance(tag, str) and tag for tag in data["tags"])
        assert data["body_markdown"].startswith("## What you will see\n")


def test_the_path_lists_every_page_once_and_no_two_share_a_folder_or_an_address(
    pages: ModuleType, bundle: ModuleType
) -> None:
    every = _every_page(pages, bundle)

    assert sorted(id(page) for page in pages.PATH.lessons) == sorted(id(page) for page in every)
    assert len({page.folder for page in every}) == len(every)
    assert len({page.slug for page in every}) == len(every)
    assert [page.slug for page in pages.PATH.lessons] == [
        "bailey-lesson-1-your-first-solution",
        "bailey-lesson-2-tools-that-ask-before-they-act",
        "bailey-lesson-3-apps-that-run-a-crew",
        "bailey-lesson-4-check-a-solution",
    ], "a lesson already on sigrix.io is updated by its slug; a changed one is a new page"


def test_each_lesson_file_names_one_page_and_every_page_has_its_lesson(pages: ModuleType) -> None:
    named: dict[str, str] = {}
    for script in sorted(LESSONS.glob("*.py")):
        for node in ast.walk(ast.parse(script.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Lesson":
                [keyword] = [kw for kw in node.keywords if kw.arg == "page"]
                assert isinstance(keyword.value, ast.Attribute) and keyword.value.value.id == "pages", script.name
                named[script.name] = keyword.value.attr

    assert sorted(named.values()) == sorted(
        name for name, value in vars(pages).items() if value in pages.PATH.lessons
    ), named
    assert len(named) == 4, "a lesson file was found by its Lesson(...) call"


def test_every_page_carries_the_tag_sigrix_lists_the_lessons_by(pages: ModuleType, bundle: ModuleType) -> None:
    """Sigrix's ``/learn/tag/bailey`` lists the lessons by this tag, and ``/open-source/bailey`` shows the learning
    paths of the lessons that carry it (``BAILEY_LEARN_TAG`` in sigrix-io/sigrix's ``src/services/open_source.py``):
    a page without it is on neither."""

    assert bundle.TAG == "bailey"
    for page in pages.PATH.lessons:
        assert bundle.TAG in bundle.page_json(page)["tags"], page.slug


def test_every_page_says_what_in_its_lesson_is_scripted(pages: ModuleType) -> None:
    """A lesson says its answers are scripted wherever it is published (``lessons/README.md``), and its page is."""

    for page in pages.PATH.lessons:
        assert "It is filmed in Bailey itself" in page.body and "scripted" in page.body, page.slug


def test_a_youtube_link_is_written_once_a_page_has_one(pages: ModuleType, bundle: ModuleType) -> None:
    not_yet = dataclasses.replace(pages.FIRST_SOLUTION, youtube_url="")
    assert "youtube_url" not in bundle.page_json(not_yet), "left out, so an import keeps the page's link"
    uploaded = dataclasses.replace(pages.FIRST_SOLUTION, youtube_url="https://www.youtube.com/watch?v=abcdefghijk")
    assert bundle.page_json(uploaded)["youtube_url"] == "https://www.youtube.com/watch?v=abcdefghijk"


def test_every_lesson_names_its_own_video_on_youtube_as_sigrix_stores_it(pages: ModuleType) -> None:
    """Sigrix's import reads a ``youtu.be`` link too, and stores it as the watch address it links the page to; each
    lesson is a video of its own, so no two pages name one."""

    links = [page.youtube_url for page in pages.PATH.lessons]
    assert all(re.fullmatch(r"https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}", link) for link in links), links
    assert len(set(links)) == len(links)


def test_the_readme_links_each_lesson_in_the_paths_order(pages: ModuleType) -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    linked = re.findall(r"\]\(https://sigrix\.io/learn/([a-z0-9-]+)\)", readme)
    assert linked == [page.slug for page in pages.PATH.lessons], "a lesson's address is its slug"


def test_a_page_is_written_as_the_import_reads_it(tmp_path: Path, pages: ModuleType, bundle: ModuleType) -> None:
    written = bundle.write_page(pages.FIRST_SOLUTION, tmp_path)

    assert written == tmp_path / "lesson.json"
    assert json.loads(written.read_text(encoding="utf-8")) == {
        "format": "sigrix-lesson/1",
        "slug": "bailey-lesson-1-your-first-solution",
        "title": "Bailey lesson 1: your first solution",
        "summary": pages.FIRST_SOLUTION.summary,
        "category": "tutorials",
        "tags": ["bailey", "video"],
        "body_markdown": pages.FIRST_SOLUTION.body,
        "youtube_url": "https://www.youtube.com/watch?v=OWB-Dw8namc",
    }


def _recorded(out: Path, pages: ModuleType, *, leave_out: str | None = None) -> None:
    """The folders a recording leaves: media under the bundle's names, an old page, YouTube's chapters."""

    for page in pages.PATH.lessons:
        folder = out / page.folder
        folder.mkdir(parents=True)
        for name in ("video.mp4", "captions.vtt", "chapters.vtt", "cover.jpg", "chapters.txt"):
            if f"{page.folder}/{name}" != leave_out:
                (folder / name).write_bytes(f"{page.folder}/{name}".encode())
        (folder / "lesson.json").write_text('{"title": "an old page"}', encoding="utf-8")


def test_a_pack_holds_each_lesson_the_import_reads_and_the_path_in_order(
    tmp_path: Path, pages: ModuleType, bundle: ModuleType
) -> None:
    _recorded(tmp_path, pages)

    made = bundle.pack(tmp_path, pages.PATH, tmp_path / "bailey-lessons.zip")

    with zipfile.ZipFile(made) as archive:
        names = archive.namelist()
        expected = [
            f"bailey-lessons/{page.folder}/{name}"
            for page in pages.PATH.lessons
            for name in ("lesson.json", "video.mp4", "captions.vtt", "chapters.vtt", "cover.jpg")
        ]
        assert names == [*expected, "bailey-lessons/path.json"], (
            "nothing the import does not read, YouTube's chapters left out"
        )
        first = pages.FIRST_SOLUTION.folder
        assert json.loads(archive.read(f"bailey-lessons/{first}/lesson.json"))["slug"] == pages.FIRST_SOLUTION.slug, (
            "the page is written afresh, not the one left on disk"
        )
        assert archive.read(f"bailey-lessons/{first}/video.mp4") == f"{first}/video.mp4".encode()
        assert archive.getinfo(f"bailey-lessons/{first}/video.mp4").compress_type == zipfile.ZIP_STORED
        assert archive.getinfo(f"bailey-lessons/{first}/captions.vtt").compress_type == zipfile.ZIP_DEFLATED
        assert json.loads(archive.read("bailey-lessons/path.json")) == {
            "format": "sigrix-learning-path/1",
            "title": "Bailey: getting started",
            "audience": "New to Bailey",
            "description": pages.PATH.description,
            "lessons": [page.slug for page in pages.PATH.lessons],
        }


def test_a_lesson_not_recorded_whole_stops_the_pack_and_names_what_is_missing(
    tmp_path: Path, pages: ModuleType, bundle: ModuleType
) -> None:
    gone = f"{pages.CHECK_A_SOLUTION.folder}/chapters.vtt"
    _recorded(tmp_path, pages, leave_out=gone)

    with pytest.raises(FileNotFoundError, match=re.escape(f"missing: {gone}")):
        bundle.pack(tmp_path, pages.PATH, tmp_path / "bailey-lessons.zip")
    assert not (tmp_path / "bailey-lessons.zip").exists()


def test_pack_py_writes_the_pack_or_says_what_to_record(
    tmp_path: Path, pages: ModuleType, pack_script: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    assert pack_script.main(["--out", str(tmp_path)]) == 1
    assert "Record these lessons first" in capsys.readouterr().err

    _recorded(tmp_path, pages)
    assert pack_script.main(["--out", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "bailey-lessons.zip")
    assert zipfile.is_zipfile(tmp_path / "bailey-lessons.zip")


#: A recording's chapters as ``timeline.youtube_chapters`` writes them: lesson 1's.
CHAPTERS = "0:00 Finding your way\n0:19 1 · Choose your AI\n0:38 2 · Install a solution\n1:20 3 · Give it documents\n"


def _with_chapters(out: Path, pages: ModuleType, text: str = CHAPTERS) -> None:
    for page in pages.PATH.lessons:
        (out / page.folder / "chapters.txt").write_text(text, encoding="utf-8")


def test_each_lesson_gets_its_youtube_upload_written_from_its_page(
    tmp_path: Path, pages: ModuleType, youtube: ModuleType
) -> None:
    _recorded(tmp_path, pages)
    _with_chapters(tmp_path, pages)
    link = "https://sigrix.io/go/abc123"

    written = youtube.write(tmp_path, pages.PATH, {pages.TOOLS_THAT_ASK.slug: link})

    assert written == [tmp_path / page.folder / "youtube.txt" for page in pages.PATH.lessons]
    first = written[0].read_text(encoding="utf-8")
    assert first.startswith(f"TITLE\n{pages.FIRST_SOLUTION.title}\n\nDESCRIPTION\n{pages.FIRST_SOLUTION.summary}\n\n")
    assert f"on sigrix.io: https://sigrix.io/learn/{pages.FIRST_SOLUTION.slug}\n" in first, (
        "its page, with no link given"
    )
    assert "\nChapters\n0:00 Finding your way\n0:19 1 · Choose your AI\n0:38 2 · Install a solution\n" in first
    assert youtube.SCRIPTED in first, "it says its answers are scripted, as a lesson does wherever it is published"
    assert "Lesson 1 of 4 in Bailey: getting started. All of them: https://sigrix.io/learn/tag/bailey\n" in first
    assert f"Get Bailey: {youtube.BAILEY_PAGE}\n" in first
    assert first.endswith("\n\nTAGS\n" + ", ".join(youtube.TAGS) + "\n")
    second = written[1].read_text(encoding="utf-8")
    assert f"on sigrix.io: {link}\n" in second, "the tracked link the Learn console made for its post"
    assert f"sigrix.io/learn/{pages.TOOLS_THAT_ASK.slug}" not in second


def test_the_chapters_the_recorder_writes_are_ones_youtube_shows(youtube: ModuleType, timeline: ModuleType) -> None:
    marks = [
        timeline.Mark(0.4, "Finding your way"),
        timeline.Mark(19.2, "1 · Choose your AI"),
        timeline.Mark(38.0, "2 · Install a solution"),
        timeline.Mark(80.5, "3 · Give it documents"),
    ]
    written = timeline.youtube_chapters(timeline.chapters(marks, 133.0))

    assert youtube.chapters(written) == [
        (0, "Finding your way"),
        (19, "1 · Choose your AI"),
        (38, "2 · Install a solution"),
        (80, "3 · Give it documents"),
    ]


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("0:00 One\n0:30 Two\n", "3 or more"),
        ("0:05 One\n0:30 Two\n1:00 Three\n", "YouTube wants 0:00"),
        ("0:00 One\n0:30 Two\n0:35 Three\n", "at least 10 s"),
        ("0:00 One\n0:30 Two\nlater Three\n", "not a time and a title"),
    ],
)
def test_chapters_youtube_would_not_show_stop_the_command(youtube: ModuleType, text: str, says: str) -> None:
    with pytest.raises(ValueError, match=re.escape(says)):
        youtube.chapters(text)


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"title": "x" * 101}, "YouTube takes 100"),
        ({"title": "Bailey <lesson>"}, "its title holds < or >"),
        ({"summary": "a > b"}, "its description holds < or >"),
        ({"summary": "é" * 2600}, "YouTube takes 5000"),
    ],
)
def test_what_youtube_refuses_stops_the_command(
    youtube: ModuleType, pages: ModuleType, change: dict[str, str], says: str
) -> None:
    page = dataclasses.replace(pages.FIRST_SOLUTION, **change)

    with pytest.raises(ValueError, match=re.escape(says)):
        youtube.upload_for(page, number=1, path=pages.PATH, chapters_text=CHAPTERS)


def test_the_tags_are_held_to_youtubes_count(youtube: ModuleType) -> None:
    assert youtube._tags_length(("a b", "cd")) == len('"a b",cd')
    assert youtube._tags_length(youtube.TAGS) <= youtube.TAGS_MAX


def test_a_lesson_that_breaks_a_rule_leaves_no_upload_written(
    tmp_path: Path, pages: ModuleType, youtube: ModuleType
) -> None:
    _recorded(tmp_path, pages)
    _with_chapters(tmp_path, pages)
    (tmp_path / pages.CHECK_A_SOLUTION.folder / "chapters.txt").write_text("0:00 Only\n", encoding="utf-8")

    with pytest.raises(ValueError, match="3 or more"):
        youtube.write(tmp_path, pages.PATH)
    assert not list(tmp_path.rglob("youtube.txt")), "the first three are not written either"


def test_a_link_names_a_lesson_and_is_the_consoles(tmp_path: Path, pages: ModuleType, youtube: ModuleType) -> None:
    _recorded(tmp_path, pages)
    _with_chapters(tmp_path, pages)

    with pytest.raises(ValueError, match="No lesson has this slug: bailey-lesson-9"):
        youtube.write(tmp_path, pages.PATH, {"bailey-lesson-9": "https://sigrix.io/go/x"})
    with pytest.raises(ValueError, match="starts with https://"):
        youtube.write(tmp_path, pages.PATH, {pages.FIRST_SOLUTION.slug: "sigrix.io/go/x"})


def test_youtube_py_writes_the_uploads_or_says_what_to_record(
    tmp_path: Path, pages: ModuleType, youtube: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    assert youtube.main(["--out", str(tmp_path)]) == 1
    assert "Record these lessons first" in capsys.readouterr().err

    _recorded(tmp_path, pages)
    _with_chapters(tmp_path, pages)
    link = f"{pages.FIRST_SOLUTION.slug}=https://sigrix.io/go/abc"
    assert youtube.main(["--out", str(tmp_path), "--link", link]) == 0
    assert capsys.readouterr().out.split() == [
        str(tmp_path / page.folder / "youtube.txt") for page in pages.PATH.lessons
    ]
    first = (tmp_path / pages.FIRST_SOLUTION.folder / "youtube.txt").read_text(encoding="utf-8")
    assert "on sigrix.io: https://sigrix.io/go/abc\n" in first
