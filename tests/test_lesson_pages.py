"""Bailey's lesson pages (``lessons/pages.py``): what Sigrix's import takes, what the site lists them by, what YouTube shows.

Each lesson used to reach sigrix.io by hand. Now its page is written once, in ``pages.py``, and
Pageant (sigrix-io/pageant), which records the lessons, writes it beside the media as
``lesson.json``, zips the recorded lessons with their learning path into one pack the Learn
console imports (``pageant pack lessons/pages.py``), and writes each lesson's YouTube upload from
the same page and Bailey's ``PRODUCT`` (``pageant youtube-text lessons/pages.py``). How it writes
them is Pageant's, and held there; what is Bailey's is held here.

Every page, and the path that lists them, is one the import takes: each is held to the format's
JSON Schema as Pageant ships it (``pageant.schema``, and its ``SPEC.md`` in words), never to a copy
of the import's rules kept here, which would drift from them without failing. The rest is
Bailey's own: each lesson file names its page, every page carries the tag the site lists the
lessons by, opens with what its lesson shows and says what in it is scripted, each names its own
video, the README links the lessons in their path's order, and every upload is one YouTube takes.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest
from pageant import bundle, schema, youtube

ROOT = Path(__file__).resolve().parents[1]
LESSONS = ROOT / "lessons"

#: A recording's chapters as Pageant writes them for a YouTube description: lesson 1's.
CHAPTERS = "0:00 Finding your way\n0:19 1 · Choose your AI\n0:38 2 · Install a solution\n1:20 3 · Give it documents\n"


@pytest.fixture(scope="module")
def pages() -> ModuleType:
    spec = importlib.util.spec_from_file_location("lessons_pages", LESSONS / "pages.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _every_page(pages: ModuleType) -> list[bundle.Page]:
    return [value for value in vars(pages).values() if isinstance(value, bundle.Page)]


def test_every_page_and_the_path_are_ones_the_import_takes(pages: ModuleType) -> None:
    """Held to the schemas of the Pageant ``lessons/requirements.txt`` pins: each page, the path, and then the two
    together, as ``pageant pack`` holds them before it zips anything (``bundle.check``, which gives every reason)."""

    lesson = schema.load(schema.LESSON)
    for page in pages.PATH.lessons:
        assert schema.problems(bundle.page_json(page), lesson) == [], page.slug
    assert schema.problems(bundle.path_json(pages.PATH), schema.load(schema.PATH)) == []
    bundle.check(pages.PATH)


def test_the_path_lists_every_page_once_and_no_two_share_a_folder_or_an_address(pages: ModuleType) -> None:
    every = _every_page(pages)

    assert sorted(id(page) for page in pages.PATH.lessons) == sorted(id(page) for page in every)
    assert len({page.folder for page in every}) == len(every)
    assert len({page.slug for page in every}) == len(every)
    assert [page.slug for page in pages.PATH.lessons] == [
        "bailey-lesson-1-your-first-solution",
        "bailey-lesson-2-tools-that-ask-before-they-act",
        "bailey-lesson-3-apps-that-run-a-crew",
        "bailey-lesson-4-check-a-solution",
    ], "a lesson already on sigrix.io is updated by its slug; a changed one is a new page"
    assert pages.PATH.site == "https://sigrix.io"


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


def test_every_page_carries_the_tag_sigrix_lists_the_lessons_by(pages: ModuleType) -> None:
    """Sigrix's ``/learn/tag/bailey`` lists the lessons by this tag, and ``/open-source/bailey`` shows the learning
    paths of the lessons that carry it: a page without it is on neither."""

    assert pages.TAG == "bailey"
    for page in pages.PATH.lessons:
        assert bundle.page_json(page)["tags"] == ["bailey", "video"], page.slug


def test_every_page_opens_with_what_its_lesson_shows(pages: ModuleType) -> None:
    for page in pages.PATH.lessons:
        assert bundle.page_json(page)["body_markdown"].startswith("## What you will see\n"), page.slug


def test_every_page_says_what_in_its_lesson_is_scripted(pages: ModuleType) -> None:
    """A lesson says its answers are scripted wherever it is published (``lessons/README.md``), and its page is."""

    for page in pages.PATH.lessons:
        assert "It is filmed in Bailey itself" in page.body and "scripted" in page.body, page.slug


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


def test_a_page_is_written_as_the_import_reads_it(tmp_path: Path, pages: ModuleType) -> None:
    """Byte for byte what the pack imported on sigrix.io carried, so a new pack updates each lesson in place."""

    written = bundle.write_page(pages.FIRST_SOLUTION, tmp_path)

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


def test_each_lessons_youtube_upload_is_one_youtube_takes_and_says_what_is_scripted(pages: ModuleType) -> None:
    assert "on a scripted model" in pages.PRODUCT.scripted, "every description says the answers are scripted"
    for number, page in enumerate(pages.PATH.lessons, start=1):
        upload = youtube.upload_for(page, number=number, path=pages.PATH, product=pages.PRODUCT, chapters_text=CHAPTERS)
        assert upload.title == page.title
        assert f"on sigrix.io: https://sigrix.io/learn/{page.slug}\n" in upload.description
        assert pages.PRODUCT.scripted in upload.description, "as a lesson says wherever it is published"
        assert (
            f"Lesson {number} of 4 in Bailey: getting started. All of them: https://sigrix.io/learn/tag/bailey\n"
            in (upload.description)
        )
        assert upload.description.endswith("Get Bailey: https://sigrix.io/open-source/bailey")
        assert upload.tags == pages.PRODUCT.tags
