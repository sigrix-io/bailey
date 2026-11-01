"""The solution layout: the templates, an export that validates and imports back, and every package refused with a sentence.

One reader serves Bailey's templates, its import and its own exports, so a writer that
drifts from the schema, or a reader that takes what it should refuse, fails here. The
schema is the one this repository publishes (``schemas/0.1/assistant.schema.json``).
"""

from __future__ import annotations

import io
import json
import stat
import zipfile
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from bailey.solutions import (
    DEFAULT_TEMPLATE,
    FORM_LIMITS,
    MAX_REFERENCE_BYTES,
    Assistant,
    Skill,
    SolutionRefused,
    assistant_from_form,
    form_of,
    parse_skill,
    plugin_name,
    read_solution,
    solution_files,
    template,
    template_ids,
    unzip_solution,
    zip_solution,
)

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "schemas" / "0.1" / "assistant.schema.json").read_text(encoding="utf-8")
)


def _zip(files: dict[str, bytes], *, symlink: str = "") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            # The name is set after the ZipInfo is made. Made from it, a backslash turns into
            # "/" on Windows, so the archive would not carry the name the case is about.
            info = zipfile.ZipInfo("entry")
            info.filename = path
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "../../etc/passwd")
    return buffer.getvalue()


def _package(**changes: Any) -> dict[str, bytes]:
    files = solution_files(template(DEFAULT_TEMPLATE))
    for path, content in changes.items():
        path = path.replace("__", "/")
        if content is None:
            files.pop(path, None)
        else:
            files[path] = content if isinstance(content, bytes) else content.encode("utf-8")
    return files


# ---------------------------------------------------------------------------
# Templates and exports
# ---------------------------------------------------------------------------


def test_the_default_template_is_the_proposals_day_one() -> None:
    assert DEFAULT_TEMPLATE in template_ids() and len(template_ids()) >= 2
    everyday = template(DEFAULT_TEMPLATE)
    assert [skill.name for skill in everyday.skills] == ["summarise", "draft-email"]
    assert everyday.instructions.body and everyday.persona and everyday.starters


@pytest.mark.parametrize("template_id", template_ids())
def test_every_template_exports_a_document_the_schema_accepts_and_imports_back_unchanged(template_id: str) -> None:
    made = template(template_id)
    files = solution_files(made)
    jsonschema.validate(json.loads(files["assistant.json"]), SCHEMA)
    plugin = json.loads(files["plugin.json"])
    assert plugin["extensions"]["org.sigrix"]["assistant"] == "assistant.json"
    assert plugin["name"] == made.name
    exported = zip_solution(made)
    assert exported == zip_solution(made), "one assistant always exports to the same bytes"
    assert read_solution(unzip_solution(exported)) == made


def test_an_edited_assistant_still_exports_a_valid_document() -> None:
    made = template(DEFAULT_TEMPLATE)
    form = form_of(made)
    form["title"] = "Shop helper"
    form["skills"].append(
        {"name": "refunds", "description": "Answer a refund request.", "body": "Refund within 30 days."}
    )
    edited = assistant_from_form(form, made)
    jsonschema.validate(json.loads(solution_files(edited)["assistant.json"]), SCHEMA)
    assert edited.name == "shop-helper"
    assert read_solution(unzip_solution(zip_solution(edited))) == edited


# ---------------------------------------------------------------------------
# What an import refuses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "sentence"),
    [
        ({"assistant.json": None}, "does not carry it"),
        ({"assistant.json": "not json"}, "is not JSON"),
        ({"assistant.json": '{"format_version": "1.0"}'}, "format 1.0, which this Bailey does not read"),
        ({"skills__summarise__SKILL.md": None}, "skills/summarise/SKILL.md, but does not carry it"),
        ({"skills__summarise__SKILL.md": "no frontmatter"}, "has no frontmatter"),
        ({"skills__summarise__SKILL.md": "---\nname: other\ndescription: d\n---\nbody"}, "names itself 'other'"),
        ({"skills__summarise__SKILL.md": "---\nname: summarise\n---\nbody"}, "has no description"),
        ({"skills__summarise__references__big.md": "x" * (MAX_REFERENCE_BYTES + 1)}, "loads whole with its skill"),
        ({"skills__summarise__SKILL.md": b"---\nname: summarise\ndescription: d\n---\n\xff"}, "is not UTF-8 text"),
    ],
    ids=[
        "no-document",
        "not-json",
        "unknown-major",
        "missing-skill",
        "no-frontmatter",
        "name-mismatch",
        "no-description",
        "reference-too-large",
        "not-utf8",
    ],
)
def test_a_package_that_cannot_be_read_is_refused_with_a_sentence(changes: dict[str, Any], sentence: str) -> None:
    with pytest.raises(SolutionRefused, match=sentence):
        read_solution(_package(**changes))


def test_a_reserved_word_or_a_bad_name_in_a_skill_is_refused() -> None:
    document = json.loads(_package()["assistant.json"])
    document["skills"].append({"name": "claude-helper", "path": "skills/claude-helper/SKILL.md"})
    files = _package(**{"assistant.json": json.dumps(document)})
    files["skills/claude-helper/SKILL.md"] = b"---\nname: claude-helper\ndescription: d\n---\nbody"
    with pytest.raises(SolutionRefused, match="anthropic or claude"):
        read_solution(files)


@pytest.mark.parametrize(
    ("archive", "sentence"),
    [
        (lambda: _zip({"../escape.md": b"x"}), "points outside the package"),
        (lambda: _zip({"/etc/passwd": b"x"}), "points outside the package"),
        (lambda: _zip({"a\\b.md": b"x"}), "points outside the package"),
        (lambda: _zip({"skills\\": b""}), "points outside the package"),
        (lambda: _zip({"assistant.json\0.md": b"x"}), "points outside the package"),
        (lambda: _zip({"assistant.json": b"{}"}, symlink="skills/link"), "is a link"),
        (lambda: _zip({f"f{i}.md": b"x" for i in range(301)}), "more than 300 files"),
        (lambda: _zip({"big.md": b"\0" * (600 * 1024)}), "larger than 512 KB"),
        (lambda: _zip({"bomb.md": b"\0" * (400 * 1024)}), "archive bomb"),
        (lambda: b"not a zip at all", "is not a zip"),
    ],
    ids=[
        "dot-dot",
        "absolute",
        "backslash",
        "backslash-folder",
        "nul",
        "symlink",
        "too-many",
        "too-large",
        "ratio",
        "not-a-zip",
    ],
)
def test_an_archive_bomb_a_link_or_a_path_outside_the_package_is_refused(archive: Any, sentence: str) -> None:
    with pytest.raises(SolutionRefused, match=sentence):
        unzip_solution(archive())


def test_a_zip_with_one_folder_around_everything_reads_as_the_folder() -> None:
    made = template(DEFAULT_TEMPLATE)
    files = unzip_solution(zip_solution(made))
    assert "assistant.json" in files and not any(path.startswith(made.name + "/") for path in files)


# ---------------------------------------------------------------------------
# SKILL.md frontmatter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        ("name: x\ndescription: plain words", "plain words"),
        ('name: x\ndescription: "quoted: with a colon"', "quoted: with a colon"),
        ("name: x\ndescription: 'it''s single'", "it's single"),
        ("name: x\ndescription: >\n  folded over\n  two lines", "folded over two lines"),
        ("name: x\ndescription: |\n  kept\n  apart", "kept\napart"),
        ("name: x\nmetadata:\n  version: 1\ndescription: after a nested value", "after a nested value"),
        ("# a comment\nname: x\ndescription: d", "d"),
    ],
    ids=["plain", "double-quoted", "single-quoted", "folded", "literal", "nested-skipped", "comment"],
)
def test_the_frontmatter_reader_takes_what_skills_write(head: str, expected: str) -> None:
    values, body = parse_skill(f"---\n{head}\n---\n\nThe body.\n", "SKILL.md")
    assert values["name"] == "x" and values["description"] == expected
    assert body == "The body.\n"


@pytest.mark.parametrize(
    "head",
    ["just a line", 'description: "unclosed', "description: 'unclosed", "- a list item"],
    ids=["no-colon", "bad-double", "bad-single", "list"],
)
def test_frontmatter_it_cannot_read_is_refused_not_guessed(head: str) -> None:
    with pytest.raises(SolutionRefused):
        parse_skill(f"---\nname: x\n{head}\n---\nbody", "SKILL.md")


# ---------------------------------------------------------------------------
# The edit form
# ---------------------------------------------------------------------------


def _form(**changes: Any) -> dict[str, Any]:
    form = form_of(template(DEFAULT_TEMPLATE))
    form.update(changes)
    return form


@pytest.mark.parametrize(
    ("changes", "sentence"),
    [
        ({"title": ""}, "The title cannot be empty."),
        ({"title": "t" * (FORM_LIMITS["title"] + 1)}, "it can be 120 at most"),
        ({"instructions": "  "}, "The instructions cannot be empty."),
        ({"starters": ["s"] * (FORM_LIMITS["starters"] + 1)}, "10 starters at most"),
        (
            {"skills": [{"name": "Not A Name", "description": "d", "body": "b"}]},
            "lowercase letters, digits and hyphens",
        ),
        (
            {
                "skills": [
                    {"name": "a", "description": "d", "body": "b"},
                    {"name": "a", "description": "d", "body": "b"},
                ]
            },
            "Two skills are named a",
        ),
        (
            {"skills": [{"name": "everyday-helper", "description": "d", "body": "b"}]},
            "Two skills are named everyday-helper",
        ),
        ({"skills": [{"name": "a", "description": "", "body": "b"}]}, "The a skill's description cannot be empty."),
        (
            {"skills": [{"name": "a", "description": "d", "body": "b" * (FORM_LIMITS["skill_body"] + 1)}]},
            "50,000 at most",
        ),
    ],
    ids=[
        "no-title",
        "long-title",
        "no-instructions",
        "many-starters",
        "bad-name",
        "duplicate",
        "instructions-name",
        "no-description",
        "long-body",
    ],
)
def test_the_form_refuses_a_field_over_its_limit_rather_than_cutting_it(changes: dict[str, Any], sentence: str) -> None:
    with pytest.raises(SolutionRefused, match=sentence):
        assistant_from_form(_form(**changes), template(DEFAULT_TEMPLATE))


def test_a_skill_keeps_its_reference_files_while_it_keeps_its_name() -> None:
    made = Assistant(
        name="a",
        title="A",
        description="",
        instructions=Skill("a-instructions", "d", "Do things."),
        skills=(Skill("faq", "Answer questions.", "Use the FAQ.", (("faq.md", "Q and A."),)),),
    )
    kept = assistant_from_form(form_of(made), made)
    assert kept.skills[0].references == (("faq.md", "Q and A."),)
    renamed_form = form_of(made)
    renamed_form["skills"][0]["name"] = "questions"
    assert assistant_from_form(renamed_form, made).skills[0].references == ()


def test_a_title_becomes_a_plugin_name() -> None:
    assert plugin_name("Shop Support 2.0!") == "shop-support-2-0"
    assert plugin_name("   ") == "assistant"
