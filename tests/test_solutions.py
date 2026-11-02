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
from dataclasses import replace
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from bailey.solutions import (
    DEFAULT_TEMPLATE,
    FORM_LIMITS,
    MAX_REFERENCE_BYTES,
    MAX_SERVERS,
    Assistant,
    Server,
    Skill,
    SolutionRefused,
    assistant_from_form,
    form_of,
    parse_skill,
    plugin_name,
    read_apps,
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
        (
            "name: x\ndescription: Answers from the shop's documents (its policies and\n  catalog), and quotes them.",
            "Answers from the shop's documents (its policies and catalog), and quotes them.",
        ),
        (
            "name: x\ndescription: 'Drafts in the shop''s voice: acknowledge,\n  answer, sign off.'",
            "Drafts in the shop's voice: acknowledge, answer, sign off.",
        ),
        ('name: x\ndescription: "first line\n  second line"', "first line second line"),
        ("name: x\ndescription: one\n\n  two", "one\ntwo"),
    ],
    ids=[
        "plain",
        "double-quoted",
        "single-quoted",
        "folded",
        "literal",
        "nested-skipped",
        "comment",
        "plain-wrapped",
        "single-quoted-wrapped",
        "double-quoted-wrapped",
        "plain-paragraphs",
    ],
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


# ---------------------------------------------------------------------------
# MCP servers and their declarations (S7)
# ---------------------------------------------------------------------------


def _with_servers() -> Assistant:
    return replace(
        template(DEFAULT_TEMPLATE),
        servers=(
            Server(
                "mailbox",
                command="uvx",
                args=("mailbox-mcp", "--folder", "Inbox"),
                env=("MAILBOX_TOKEN",),
                tools=("search_threads", "create_draft"),
                write_tools=("create_draft",),
            ),
            Server("shop", command="uvx", args=("sigrix-launcher", "run", "acme/shop-tools"), env=("SIGRIX_TOKEN",)),
            Server("docs", url="https://docs.example.com/mcp", transport="http"),
        ),
    )


def _package_with(config: object, tools: object = None) -> dict[str, bytes]:
    files = solution_files(template(DEFAULT_TEMPLATE))
    document = json.loads(files["assistant.json"])
    document["mcp"] = "mcp.json"
    if tools is not None:
        document["tools"] = tools
    files["assistant.json"] = json.dumps(document).encode("utf-8")
    files["mcp.json"] = config if isinstance(config, bytes) else json.dumps(config).encode("utf-8")
    return files


def test_a_solution_with_servers_exports_a_valid_document_and_imports_back_unchanged() -> None:
    made = _with_servers()
    files = solution_files(made)
    document = json.loads(files["assistant.json"])
    jsonschema.validate(document, SCHEMA)
    assert document["mcp"] == "mcp.json"
    assert document["tools"] == {
        "mailbox": {"tools": ["search_threads", "create_draft"], "write_tools": ["create_draft"]}
    }
    config = json.loads(files["mcp.json"])["mcpServers"]
    assert config["mailbox"] == {
        "command": "uvx",
        "args": ["mailbox-mcp", "--folder", "Inbox"],
        "env": {"MAILBOX_TOKEN": ""},
    }, "a variable is written by name, with an empty value for the person to fill"
    assert config["docs"] == {"type": "http", "url": "https://docs.example.com/mcp"}
    assert read_solution(files) == made
    assert read_solution(unzip_solution(zip_solution(made))) == made
    assert Assistant.from_dict(json.loads(json.dumps(made.as_dict()))) == made, "the stored record keeps the servers"


def test_a_tool_asks_before_it_runs_unless_its_server_declares_it_a_read() -> None:
    mailbox, shop, _ = _with_servers().servers
    assert mailbox.asks_before("search_threads") is False, "a declared read runs without asking"
    assert mailbox.asks_before("create_draft") is True, "a declared write asks"
    assert mailbox.asks_before("delete_thread") is True, "a tool the declaration leaves out is a write"
    assert shop.tools is None and shop.asks_before("anything") is True, "every tool of an undeclared server asks"


def test_a_write_declared_outside_its_tools_still_asks_and_exports_valid() -> None:
    config = {"mcpServers": {"mailbox": {"command": "uvx", "args": ["mailbox-mcp"]}}}
    read = read_solution(_package_with(config, {"mailbox": {"tools": ["search_threads"], "write_tools": ["send"]}}))
    (server,) = read.servers
    assert server.asks_before("send") is True and server.asks_before("search_threads") is False
    jsonschema.validate(json.loads(solution_files(read)["assistant.json"]), SCHEMA)


def test_a_value_a_package_gives_a_variable_is_refused_and_never_quoted() -> None:
    """Postern §4.1.3: a package carrying a value is nonconformant, and §7 says a client should refuse it."""

    config = {
        "mcpServers": {"shop": {"command": "uvx", "args": ["shop-mcp"], "env": {"SHOP_KEY": "sk-live-0123456789"}}}
    }
    with pytest.raises(SolutionRefused, match=r"server 'shop' gives SHOP_KEY a value\. A package names each key") as no:
        read_solution(_package_with(config))
    assert "sk-live-0123456789" not in str(no.value)


def test_a_variable_left_blank_or_given_a_reference_or_the_shared_client_is_taken_as_a_name() -> None:
    env = {
        "SHOP_KEY": "",
        "SHOP_REGION": None,
        "SHOP_USER": "${SHOP_USER}",
        "SHOP_HOST": "${env:SHOP_HOST}",
        "SHOP_PIN": "${input:shop-pin}",
        "GOOGLE_CLIENT": "__SIGRIX_SHARED_OAUTH__:GOOGLE_CLIENT",
    }
    read = read_solution(_package_with({"mcpServers": {"shop": {"command": "uvx", "args": ["shop-mcp"], "env": env}}}))
    assert read.servers[0].env == tuple(env)
    assert json.loads(solution_files(read)["mcp.json"])["mcpServers"]["shop"]["env"] == dict.fromkeys(env, "")


def test_the_form_keeps_an_assistants_servers() -> None:
    made = _with_servers()
    form = form_of(made)
    form["title"] = "Shop helper"
    assert assistant_from_form(form, made).servers == made.servers


@pytest.mark.parametrize(
    ("config", "tools", "sentence"),
    [
        (b"not json", None, "mcp.json is not JSON"),
        ({"servers": {}}, None, "has no mcpServers object"),
        ({"mcpServers": {"a": {"args": ["x"]}}}, None, "has no command to start it and no url"),
        ({"mcpServers": {"a": {"command": "uvx", "args": "x"}}}, None, "args should be a list of text"),
        ({"mcpServers": {"a": {"command": "uvx", "env": ["KEY"]}}}, None, "env should name each variable"),
        ({"mcpServers": {"a": {"command": "uvx", "env": {"NOT A NAME": ""}}}}, None, "is not a variable's name"),
        ({"mcpServers": {"a b": {"command": "uvx"}}}, None, "a server's name is letters"),
        ({"mcpServers": {"a": {"url": "file:///etc/passwd"}}}, None, "an http or https address"),
        (
            {"mcpServers": {f"s{i}": {"command": "uvx"} for i in range(MAX_SERVERS + 1)}},
            None,
            f"Bailey starts {MAX_SERVERS} at most",
        ),
        ({"mcpServers": {"a": {"command": "uvx"}}}, ["a"], "tools should map each server"),
        ({"mcpServers": {"a": {"command": "uvx"}}}, {"a": {"tools": "search"}}, "should be a list of tool names"),
    ],
    ids=[
        "not-json",
        "no-mcp-servers",
        "no-command",
        "args-not-list",
        "env-not-object",
        "bad-variable",
        "bad-server-name",
        "not-http",
        "too-many",
        "tools-not-object",
        "tools-not-list",
    ],
)
def test_an_mcp_configuration_bailey_cannot_start_is_refused_with_a_sentence(
    config: object, tools: object, sentence: str
) -> None:
    with pytest.raises(SolutionRefused, match=sentence):
        read_solution(_package_with(config, tools))


def test_an_mcp_file_the_package_names_but_does_not_carry_is_refused() -> None:
    files = _package_with({"mcpServers": {}})
    del files["mcp.json"]
    with pytest.raises(SolutionRefused, match="names mcp.json, but does not carry it"):
        read_solution(files)


# ---------------------------------------------------------------------------
# Knowledge slots and their samples (S8)
# ---------------------------------------------------------------------------

FIXTURE = Path(__file__).parent / "fixtures" / "shop-replies"


def _fixture_files() -> dict[str, bytes]:
    return {path.relative_to(FIXTURE).as_posix(): path.read_bytes() for path in FIXTURE.rglob("*") if path.is_file()}


def test_a_solutions_slots_and_samples_import_and_export_unchanged() -> None:
    made = read_solution(_fixture_files())
    assert [(slot.name, slot.accepts, slot.required) for slot in made.knowledge] == [
        ("policies", ("md", "txt", "pdf"), True),
        ("faq", ("md", "txt"), False),
        ("catalog", ("csv",), False),
    ]
    assert [(sample.slot, sample.path) for sample in made.samples] == [
        ("policies", "samples/policies.md"),
        ("faq", "samples/faq.md"),
        ("catalog", "samples/catalog.csv"),
    ]
    assert made.sample("policies") is not None and "## Returns" in made.sample("policies").text  # type: ignore[union-attr]
    assert made.instructions.description.endswith("from the shop's documents."), "a wrapped description reads whole"
    files = solution_files(made)
    jsonschema.validate(json.loads(files["assistant.json"]), SCHEMA)
    assert files["samples/faq.md"] == (FIXTURE / "samples" / "faq.md").read_bytes()
    assert read_solution(files) == made
    assert read_solution(unzip_solution(zip_solution(made))) == made
    assert Assistant.from_dict(json.loads(json.dumps(made.as_dict()))) == made, "the stored record keeps them"


def test_a_sample_bailey_cannot_read_as_text_is_left_for_the_persons_own_document() -> None:
    files = _fixture_files()
    files["samples/policies.md"] = b"\xff\xfe not text"
    made = read_solution(files)
    assert made.sample("policies") is None and made.sample("faq") is not None


def test_answering_only_from_the_documents_is_baileys_setting_and_stays_out_of_the_package() -> None:
    made = read_solution(_fixture_files())
    form = form_of(made)
    assert form["grounded_only"] is False
    form["grounded_only"] = True
    edited = assistant_from_form(form, made)
    assert edited.grounded_only is True and edited.knowledge == made.knowledge and edited.samples == made.samples
    assert Assistant.from_dict(edited.as_dict()).grounded_only is True
    assert "grounded_only" not in solution_files(edited)["assistant.json"].decode()
    with pytest.raises(SolutionRefused, match="grounded_only should be true or false"):
        assistant_from_form({**form, "grounded_only": "yes"}, made)


def _with_knowledge(knowledge: object) -> dict[str, bytes]:
    files = _fixture_files()
    document = json.loads(files["assistant.json"])
    document["knowledge"] = knowledge
    files["assistant.json"] = json.dumps(document).encode("utf-8")
    return files


_SLOT = {"name": "policies", "description": "Your policies.", "accepts": ["md"]}


@pytest.mark.parametrize(
    ("knowledge", "sentence"),
    [
        ({"policies": {}}, "knowledge should be a list"),
        (["policies"], "should be an object with a name"),
        ([{**_SLOT, "name": "Refund Policy"}], "lowercase letters, digits and underscores"),
        ([_SLOT, _SLOT], "policies is asked for twice"),
        ([{**_SLOT, "description": " "}], "policies has no description"),
        ([{**_SLOT, "accepts": []}], "accepts should list file extensions"),
        ([{**_SLOT, "accepts": [".pdf"]}], "accepts should list file extensions"),
        ([{**_SLOT, "required": "yes"}], "required should be true or false"),
        ([{**_SLOT, "name": f"slot_{i}"} for i in range(21)], "Bailey takes 20 at most"),
    ],
    ids=[
        "not-a-list",
        "not-an-object",
        "bad-name",
        "twice",
        "no-description",
        "no-types",
        "dotted-type",
        "required",
        "too-many",
    ],
)
def test_a_knowledge_slot_bailey_cannot_ask_for_is_refused_with_a_sentence(knowledge: object, sentence: str) -> None:
    with pytest.raises(SolutionRefused, match=sentence):
        read_solution(_with_knowledge(knowledge))


def test_a_sample_over_the_bound_is_refused() -> None:
    files = _fixture_files()
    files["samples/policies.md"] = b"x" * (256 * 1024 + 1)
    with pytest.raises(SolutionRefused, match="a sample can be 256 KB at most"):
        read_solution(files)


# ---------------------------------------------------------------------------
# Apps: a crew in apps/<name>/, found by its folder
# ---------------------------------------------------------------------------

DIGEST = Path(__file__).parent / "fixtures" / "weekly-digest"


def _digest(**changes: bytes | None) -> dict[str, bytes]:
    files = {path.relative_to(DIGEST).as_posix(): path.read_bytes() for path in DIGEST.rglob("*") if path.is_file()}
    for path, data in changes.items():
        if data is None:
            files.pop(path, None)
        else:
            files[path] = data
    return files


def test_an_app_is_read_from_its_folder_with_its_title_composition_and_files() -> None:
    (app,) = read_solution(_digest()).apps
    assert (app.name, app.title, app.summary) == (
        "digest",
        "Digest",
        "Turns the week's notes into the shop's newsletter.",
    )
    assert app.ui is not None and app.ui["run_label"] == "Write the digest"
    assert set(app.files) == {"postern.json", "ui.json", "requirements.txt", "config/agents.yaml", "config/tasks.yaml"}
    record = app.as_dict()
    assert "files" not in record and Assistant.from_dict(read_solution(_digest()).as_dict()).apps[0].title == "Digest"


def test_an_apps_env_file_and_a_runners_own_files_are_left_out() -> None:
    files = _digest(
        **{
            "apps/digest/.env": b"OPENAI_API_KEY=seller",
            "apps/digest/workspace/out.md": b"a run's output",
            "apps/digest/.last_error": b"Traceback",
            "apps/digest/sigrix_runtime/__pycache__/x.cpython-313.pyc": b"\0",
        }
    )
    (app,) = read_solution(files).apps
    assert not any(
        path.startswith((".env", "workspace/", ".last_error")) or "__pycache__" in path for path in app.files
    )
    assert read_apps(files)[1] == (
        "apps/digest/.env",
        "apps/digest/.last_error",
        "apps/digest/sigrix_runtime/__pycache__/x.cpython-313.pyc",
        "apps/digest/workspace/out.md",
    )


@pytest.mark.parametrize(
    ("changes", "sentence"),
    [
        ({"apps/digest/config/tasks.yaml": None}, "holds no crew"),
        ({"apps/Digest Two/config/agents.yaml": b"x: 1"}, "is not an app's name"),
        ({"apps/stray.yaml": b"x: 1"}, "is not inside an app's folder"),
        ({"apps/digest/postern.json": b"{not json"}, "postern.json is not JSON"),
        ({"apps/digest/postern.json": b"{}"}, "has no agent"),
        ({"apps/digest/ui.json": b"[1]"}, "ui.json should hold one JSON object"),
        ({"apps/digest/ui.json": b"{" + b" " * (64 * 1024) + b"}"}, "ui.json is over 64 KB"),
    ],
    ids=[
        "no-crew",
        "bad-name",
        "outside-a-folder",
        "describe-not-json",
        "describe-no-agent",
        "ui-not-object",
        "ui-too-big",
    ],
)
def test_an_app_bailey_cannot_run_is_refused_with_a_sentence(changes: dict[str, bytes | None], sentence: str) -> None:
    with pytest.raises(SolutionRefused, match=sentence):
        read_solution(_digest(**changes))


def test_a_solution_may_carry_ten_apps_and_no_more() -> None:
    crew = {"config/agents.yaml": b"a: {role: r, goal: g, backstory: b}", "config/tasks.yaml": b"t: {description: d}"}
    files = _digest(**{f"apps/extra-{n}/{path}": data for n in range(10) for path, data in crew.items()})
    with pytest.raises(SolutionRefused, match="carries 11 apps; Bailey takes 10 at most"):
        read_solution(files)


def test_editing_an_assistant_keeps_its_apps() -> None:
    made = read_solution(_digest())
    edited = assistant_from_form({**form_of(made), "title": "Weekly digest, edited"}, made)
    assert [app.name for app in edited.apps] == ["digest"]
