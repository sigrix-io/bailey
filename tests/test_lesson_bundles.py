"""A recorded lesson as the bundle Sigrix's Learn console imports (``lessons/bundle.py``, ``lessons/pages.py``).

Each lesson used to reach sigrix.io by hand: its title, slug, summary and body typed into the Learn
editor, and its video, captions, chapters and cover picked one at a time. Now its page is written
once, in ``pages.py``, the recorder writes it beside the media as ``lesson.json``, and ``pack.py``
zips the recorded lessons with their learning path into one file the console imports.

Held here: every page is one the import takes (the format is Sigrix's, and the rules it enforces
are restated where they are checked), each lesson file names its page, and a pack carries exactly the
files the import reads, its pages written afresh, and refuses a lesson not recorded whole.
"""

from __future__ import annotations

import ast
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
