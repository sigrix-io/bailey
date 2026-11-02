"""``bailey check``: a solution's examples, and a grounding pair per knowledge slot, run headless in Bailey's own core.

Story S11's *done when* (sigrix-io/sigrix#6569): the check passes on the pilot's samples,
and fails, naming the case, once the pilot's grounding skill is removed. The model is the
emulator's replay (``tests/emulated_providers.py``): handed each example's reply, it loads
the skill the reply follows and gives that reply, and with the skill gone gives it without
what the skill grounds. That proves the check runs a solution through the app's own core,
judges what each answer shows, and names each case that fails. Whether a real model drops
its sources once the skill is gone is the owner's run against a real provider.

The pilot, Customer email replies (sigrix-io/sigrix#6571), is read from the sigrix checkout
``BAILEY_TEST_PILOT`` names, as ``tests/test_install.py`` reads it; without one it is
skipped, and ``tests/fixtures/grounded-replies``, a made-up shop in its shape, runs every time.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import shutil
import socket
import sqlite3
import sys
import zipfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import emulated_providers as emulator
import jsonschema
import pytest
from fakes import FakeEmbedder

from bailey import __version__, knowledge, server
from bailey.agent import DECLINED
from bailey.check import (
    MADE_UP_NAMES,
    NOT_COVERED,
    Case,
    CheckFailed,
    Outcome,
    Report,
    Setup,
    answered_question,
    cases_of,
    closing,
    judge,
    quotes,
    read_package,
    render,
    run_check,
    says_not_covered,
    setup_for,
    summed,
)
from bailey.cli import main as cli
from bailey.keys import FILE_NAME, EncryptedFileKeys
from bailey.settings import Connection, key_name
from bailey.solutions import (
    Assistant,
    Example,
    KnowledgeSlot,
    Sample,
    Server,
    Skill,
    SolutionRefused,
    assistant_from_form,
    form_of,
    read_solution,
    unzip_solution,
    zip_solution,
)
from bailey.store import STORE_FILE, Store, read_settings

FIXTURES = Path(__file__).parent / "fixtures"
GROUNDED = FIXTURES / "grounded-replies"
PILOT = os.environ.get("BAILEY_TEST_PILOT", "")
MAILBOX = Path(__file__).parent / "tool_servers" / "mailbox.py"
KEY = "sk-ant-api03-0123456789abcdefghij-WXYZ"

#: Each solution the done-when runs on, and the skill that grounds it.
SOLUTIONS = [
    pytest.param(GROUNDED, "quote-sources", id="made-up-shop"),
    pytest.param(
        Path(PILOT or "."),
        "policy-grounding",
        id="pilot",
        marks=pytest.mark.skipif(not PILOT, reason="names no sigrix checkout's pilot package"),
    ),
]

QUOTES_NONE = "Its example quotes the documents word for word, and this answer quotes none of them."
SAYS_NOT_SO = "Its example says the documents do not cover this, and this answer does not say so."


@pytest.fixture(scope="module")
def base() -> str:
    return emulator.serve_in_background()


@pytest.fixture(autouse=True)
def _no_replays() -> Iterator[None]:
    yield
    emulator.REPLAYS.clear()


def _setup(base: str) -> Setup:
    return Setup(Connection(provider="anthropic", model="claude-sonnet-5-5", api_key=KEY, base_url=base + "/anthropic"))


def _check(files: Mapping[str, bytes], base: str, **options: Any) -> Report:
    return asyncio.run(run_check(files, _setup(base), **options))


def _replaying(files: Mapping[str, bytes], skill: str) -> None:
    """Hand the replay model each example's reply, as the skill named would have it written."""
    for example in json.loads(files["assistant.json"])["examples"]:
        emulator.REPLAYS.append(emulator.Replay(example["input"], example.get("output", ""), skill))


def _without_skill(files: Mapping[str, bytes], name: str) -> dict[str, bytes]:
    """The package with a skill removed: its folder, and its entry in ``assistant.json``."""
    document = json.loads(files["assistant.json"])
    (entry,) = [entry for entry in document["skills"] if entry["name"] == name]
    folder = entry["path"].rsplit("/", 1)[0] + "/"
    document["skills"] = [entry for entry in document["skills"] if entry["name"] != name]
    kept = {path: data for path, data in files.items() if not path.startswith(folder)}
    kept["assistant.json"] = json.dumps(document).encode()
    return kept


def _names(report: Report, *, passed: bool) -> list[str]:
    return [result.case.name for result in report.results if result.passed is passed]


# ---------------------------------------------------------------------------
# The done-when
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("folder", "skill"), SOLUTIONS)
def test_with_its_grounding_skill_every_case_passes(base: str, folder: Path, skill: str) -> None:
    files = read_package(folder)
    made = read_solution(files)
    _replaying(files, skill)
    report = _check(files, base)

    assert report.passed, render(report)
    assert (report.provider, report.model, report.documents) == ("anthropic", "claude-sonnet-5-5", "whole")
    examples = [result for result in report.results if result.case.kind == "example"]
    assert [result.case.name for result in examples] == [f"Example {n}" for n in range(1, len(made.examples) + 1)]
    assert all(result.outcome.skills == [skill] for result in examples), "each example loaded the grounding skill"
    with_samples = [slot.name for slot in made.knowledge if made.sample(slot.name) is not None]
    pairs = [(result.case.kind, result.case.slot) for result in report.results if result.case.kind != "example"]
    assert pairs == [(kind, slot) for slot in with_samples for kind in ("answered", "uncovered")]
    for result in report.results:
        if result.case.kind == "answered":
            sample = made.sample(result.case.slot)
            assert sample is not None
            assert {citation["document"] for citation in result.outcome.citations} == {
                f"{sample.slot}.{sample.extension}"
            }
        if result.case.kind == "uncovered":
            assert says_not_covered(result.outcome.answer) and result.outcome.citations == []
    assert f"\nAll {len(report.results)} cases passed.\n" in render(report)


@pytest.mark.parametrize(("folder", "skill"), SOLUTIONS)
def test_without_its_grounding_skill_the_check_fails_naming_each_case_and_why(
    base: str, folder: Path, skill: str
) -> None:
    files = _without_skill(read_package(folder), skill)
    made = read_solution(files)
    assert skill not in [listed.name for listed in made.skills]
    _replaying(files, skill)
    report = _check(files, base)

    assert not report.passed
    examples = [f"Example {n}" for n in range(1, len(made.examples) + 1)]
    assert _names(report, passed=False) == examples, "every example fails, and nothing else"
    assert all(result.outcome.skills == [] for result in report.results)
    text = render(report)
    for result, example in zip(report.results, made.examples, strict=False):
        reasons = [check.reason for check in result.checks if not check.passed]
        assert reasons, result.case.name
        assert (QUOTES_NONE in reasons) is bool(quotes(example.output, [sample.text for sample in made.samples]))
        assert (SAYS_NOT_SO in reasons) is says_not_covered(example.output)
        assert f"FAIL  {result.case.name}: Customer email: " in text
        assert "\n".join(f"      {reason}" for reason in reasons) in text
    assert f"{len(examples)} of {len(report.results)} cases failed." in text


def test_the_check_prints_each_case_as_it_is_judged_and_ends_as_the_whole_report_reads(base: str) -> None:
    files = read_package(GROUNDED)
    _replaying(files, "quote-sources")
    shown: list[str] = []
    report = _check(files, base, shown=shown.append)

    assert len(shown) == 1 + len(report.results), "the opening, then each case on its own"
    assert shown[0] == "Checking Grounded replies on anthropic claude-sonnet-5-5, its documents sent whole.\n\n"
    assert shown[1].startswith("PASS  Example 1: Customer email: Hello, the novel I ordered arrived last week")
    assert "".join(shown) + closing(report) == render(report)


def test_the_report_records_the_provider_model_tokens_and_each_case(base: str) -> None:
    files = read_package(GROUNDED)
    _replaying(files, "quote-sources")
    report = _check(files, base)
    recorded = report.as_dict()

    assert recorded["solution"] == {"name": "grounded-replies", "title": "Grounded replies"}
    assert recorded["bailey"] == __version__
    assert (recorded["provider"], recorded["model"], recorded["documents"], recorded["passed"]) == (
        "anthropic",
        "claude-sonnet-5-5",
        "whole",
        True,
    )
    cases = recorded["cases"]
    assert [case["kind"] for case in cases] == ["example"] * 3 + ["answered", "uncovered"] * 2
    assert recorded["usage"]["input_tokens"] == sum(case["usage"]["input_tokens"] for case in cases) > 0
    assert recorded["usage"]["output_tokens"] == sum(case["usage"]["output_tokens"] for case in cases) > 0
    assert recorded["estimate"] is not None, "the bundled prices know the model"
    assert cases[0]["checks"] == [
        {"check": "answered", "passed": True, "reason": ""},
        {"check": "quotes", "passed": True, "reason": ""},
    ]
    assert cases[2]["checks"][-1] == {"check": "not covered", "passed": True, "reason": ""}
    assert json.loads(json.dumps(recorded)) == recorded, "the report is plain JSON"
    assert "It took " in closing(report) and "(an estimate)." in closing(report)


# ---------------------------------------------------------------------------
# Write tools, tool variables, searched documents
# ---------------------------------------------------------------------------

MAILBOX_SHOP = Assistant(
    name="mailbox-shop",
    title="Mailbox shop",
    description="Answers a shop's email from its mailbox.",
    instructions=Skill("mailbox-shop", "Its instructions.", "You answer a small shop's customer email."),
    servers=(
        Server(
            "mailbox",
            command=sys.executable,
            args=(str(MAILBOX),),
            env=("MAILBOX_DRAFTS",),
            tools=("search_threads", "create_draft", "visible_environment", "process_id"),
            write_tools=("create_draft",),
        ),
    ),
    examples=(Example("Reply to the refund request."),),
)


def test_a_write_tool_is_refused_and_never_runs_while_a_read_runs(base: str, tmp_path: Path) -> None:
    drafts = tmp_path / "drafts.jsonl"
    files = unzip_solution(zip_solution(MAILBOX_SHOP))
    report = _check(files, base, environ={"MAILBOX_DRAFTS": str(drafts)})

    (result,) = report.results
    assert result.passed, render(report)
    assert result.outcome.refused == ["create_draft"]
    assert "search_threads" in result.outcome.tools, "a tool that only reads runs"
    assert DECLINED in result.outcome.answer, "the model was told the write did not happen"
    assert not drafts.exists(), "the draft was never written"
    assert "      Refused create_draft, as a check refuses every write.\n" in render(report)


def test_a_tool_servers_variable_comes_from_the_environment_and_one_missing_stops_the_check(base: str) -> None:
    files = unzip_solution(zip_solution(MAILBOX_SHOP))
    with pytest.raises(CheckFailed) as refused:
        _check(files, base, environ={})
    assert str(refused.value) == (
        "Its tool servers need MAILBOX_DRAFTS (mailbox): set each in the environment `bailey check` runs in."
    )


def test_samples_too_large_to_send_whole_are_searched_and_the_pairs_still_hold(
    base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(knowledge, "WHOLE_LIMIT", 1)
    monkeypatch.setattr(server, "WHOLE_LIMIT", 1)
    files = read_package(GROUNDED)
    _replaying(files, "quote-sources")
    report = _check(files, base, embedder=FakeEmbedder())

    assert report.documents == "search" and report.passed, render(report)
    pairs = [result for result in report.results if result.case.kind != "example"]
    assert all(result.outcome.searches for result in pairs), "each grounding question searched the documents"
    assert "its documents searched." in render(report)


def test_searched_samples_without_the_search_model_stop_the_check_saying_how_to_get_it(
    base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(knowledge, "WHOLE_LIMIT", 1)
    monkeypatch.setattr(server, "WHOLE_LIMIT", 1)
    with pytest.raises(CheckFailed, match="Grounded replies's samples are searched, which takes the search model"):
        _check(read_package(GROUNDED), base)


def test_a_provider_refusing_the_key_stops_the_check_rather_than_failing_each_case(base: str) -> None:
    files = read_package(GROUNDED)
    refused = Setup(Connection("anthropic", "claude-sonnet-5-5", emulator.BAD_KEY, base + "/anthropic"))
    with pytest.raises(CheckFailed, match=r"^Anthropic refused the key \(401\)\. Check the key in Settings\.$"):
        asyncio.run(run_check(files, refused))


def test_a_solution_with_no_examples_and_no_samples_has_nothing_to_check(base: str) -> None:
    bare = Assistant(
        name="bare", title="Bare", description="", instructions=Skill("bare", "Its instructions.", "You help.")
    )
    with pytest.raises(CheckFailed, match="^Bare has no examples and no samples, so there is nothing to check.$"):
        _check(unzip_solution(zip_solution(bare)), base)


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("BAILEY_HOME", str(folder))
    monkeypatch.setenv("BAILEY_KEYS", "file")
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "BAILEY_PASSPHRASE", "BAILEY_SEARCH_MODEL"):
        monkeypatch.delenv(name, raising=False)
    return folder


def _local(base: str) -> list[str]:
    return ["--provider", "openai-compatible", "--address", base + "/local/v1", "--model", "qwen3:8b"]


def test_bailey_check_exits_0_when_every_case_passes_and_1_when_one_fails(
    base: str, home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _replaying(read_package(GROUNDED), "quote-sources")
    assert cli(["check", str(GROUNDED), *_local(base)]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("Checking Grounded replies on openai-compatible qwen3:8b, its documents sent whole.\n")
    assert "All 7 cases passed." in printed

    broken = tmp_path / "grounded-replies"
    shutil.copytree(GROUNDED, broken)
    for path, data in _without_skill(read_package(GROUNDED), "quote-sources").items():
        (broken / path).write_bytes(data)
    shutil.rmtree(broken / "skills" / "quote-sources")
    assert cli(["check", str(broken), *_local(base)]) == 1
    printed = capsys.readouterr().out
    assert "FAIL  Example 1: " in printed and "3 of 7 cases failed." in printed


def test_bailey_check_reads_a_zip_and_prints_json_for_scripts(
    base: str, home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    files = read_package(GROUNDED)
    _replaying(files, "quote-sources")
    package = tmp_path / "grounded-replies.zip"
    with zipfile.ZipFile(package, "w") as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    assert cli(["check", str(package), "--json", *_local(base)]) == 0
    recorded = json.loads(capsys.readouterr().out)
    assert (recorded["provider"], recorded["model"], recorded["passed"]) == ("openai-compatible", "qwen3:8b", True)
    assert len(recorded["cases"]) == 7


@pytest.mark.parametrize(
    ("arguments", "sentence"),
    [
        ([str(GROUNDED)], "Choose an AI provider: in Bailey's Settings, or with --provider."),
        (
            [str(GROUNDED), "--provider", "anthropic"],
            "Bailey has no Anthropic key: save one in Bailey's Settings, or set ANTHROPIC_API_KEY.",
        ),
        ([str(GROUNDED), "--provider", "openai-compatible", "--model", "qwen3:8b"], "Give your server's address"),
        ([str(GROUNDED), "--provider", "openai-compatible", "--address", "http://127.0.0.1:9/v1"], "Name the model"),
        ([str(GROUNDED), "--provider", "nobody"], ""),
        (["no-such-solution", "--provider", "anthropic"], "no-such-solution is neither a folder nor a ZIP file."),
    ],
)
def test_bailey_check_exits_2_saying_why_when_it_cannot_run(
    home: Path, capsys: pytest.CaptureFixture[str], arguments: list[str], sentence: str
) -> None:
    assert cli(["check", *arguments]) == 2
    printed = capsys.readouterr()
    assert printed.out == ""
    assert printed.err.startswith("bailey check: ") and sentence in printed.err


def test_the_output_survives_a_pipe_that_cannot_encode_every_character(monkeypatch: pytest.MonkeyPatch) -> None:
    from bailey import check

    written = io.BytesIO()
    pipe = io.TextIOWrapper(written, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", pipe)
    check._show("PASS  Example 1: Customer email: Hi 👋, ça va? 你好…\n")
    pipe.flush()
    assert written.getvalue().decode("cp1252") == "PASS  Example 1: Customer email: Hi ?, ça va? ??…\n"


def test_bailey_check_exits_2_when_the_provider_cannot_be_reached(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    address = f"http://127.0.0.1:{closed}/v1"
    arguments = ["check", str(GROUNDED), "--provider", "openai-compatible", "--address", address, "--model", "qwen3:8b"]
    assert cli(arguments) == 2
    printed = capsys.readouterr()
    assert printed.err == f"bailey check: Bailey could not reach the server at {address}.\n"
    assert "FAIL" not in printed.out, "no case is failed for the provider's failure"


def test_bailey_check_runs_on_baileys_own_settings_and_leaves_them_as_they_were(
    base: str, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = Store(home / STORE_FILE)
    store.set_setting("provider", "openai-compatible")
    store.set_setting("address", base + "/local/v1")
    store.set_setting("models", {"openai-compatible": "qwen3:8b"})
    store.close()
    before = (home / STORE_FILE).read_bytes()
    listed = sorted(path.name for path in home.iterdir())
    _replaying(read_package(GROUNDED), "quote-sources")

    assert cli(["check", str(GROUNDED)]) == 0
    assert "on openai-compatible qwen3:8b" in capsys.readouterr().out
    assert sorted(path.name for path in home.iterdir()) == listed, "nothing new in the data folder"
    assert (home / STORE_FILE).read_bytes() == before, "no assistant, conversation or setting of its own"


# ---------------------------------------------------------------------------
# What it runs on
# ---------------------------------------------------------------------------


def test_the_provider_model_and_key_come_from_baileys_settings_and_the_key_file(tmp_path: Path) -> None:
    store = Store(tmp_path / STORE_FILE)
    store.set_setting("provider", "anthropic")
    store.set_setting("models", {"anthropic": "claude-opus-5-5"})
    store.close()
    keys = EncryptedFileKeys(tmp_path / FILE_NAME)
    keys.create("a long passphrase")
    keys.set(key_name("anthropic"), KEY)
    environ = {"BAILEY_KEYS": "file", "BAILEY_PASSPHRASE": "a long passphrase"}

    setup = setup_for(tmp_path, environ=environ)
    assert setup.connection == Connection("anthropic", "claude-opus-5-5", KEY, "https://api.anthropic.com")
    assert setup.scripts_allowed is False
    named = setup_for(tmp_path, provider_id="openai", model="gpt-5", environ={**environ, "OPENAI_API_KEY": "sk-o"})
    assert named.connection == Connection("openai", "gpt-5", "sk-o", "https://api.openai.com/v1")


def test_a_key_in_the_environment_is_used_when_bailey_has_none_saved(tmp_path: Path) -> None:
    setup = setup_for(tmp_path, provider_id="anthropic", environ={"BAILEY_KEYS": "file", "ANTHROPIC_API_KEY": KEY})
    assert (setup.connection.api_key, setup.connection.model) == (KEY, "claude-sonnet-5-5")
    assert list(tmp_path.iterdir()) == [], "no store and no key file is made to read from"


def test_an_openai_key_in_the_environment_never_goes_to_an_openai_compatible_server(tmp_path: Path) -> None:
    setup = setup_for(
        tmp_path,
        provider_id="openai-compatible",
        model="qwen3:8b",
        address="http://192.168.1.20:11434/v1",
        environ={"BAILEY_KEYS": "file", "OPENAI_API_KEY": "sk-openai-own-key"},
    )
    assert setup.connection.api_key == ""
    assert setup.connection.base_url == "http://192.168.1.20:11434/v1"


def test_a_passphrase_that_does_not_open_the_key_file_stops_the_check(tmp_path: Path) -> None:
    keys = EncryptedFileKeys(tmp_path / FILE_NAME)
    keys.create("the right passphrase")
    with pytest.raises(CheckFailed, match="^BAILEY_PASSPHRASE does not open "):
        setup_for(tmp_path, provider_id="anthropic", environ={"BAILEY_KEYS": "file", "BAILEY_PASSPHRASE": "wrong"})


def test_reading_the_settings_neither_migrates_nor_leaves_a_file_behind(tmp_path: Path) -> None:
    store = Store(tmp_path / STORE_FILE)
    store.set_setting("provider", "openai")
    store.close()
    before = (tmp_path / STORE_FILE).read_bytes()

    assert read_settings(tmp_path / STORE_FILE) == {"provider": "openai"}
    assert sorted(path.name for path in tmp_path.iterdir()) == [STORE_FILE]
    assert (tmp_path / STORE_FILE).read_bytes() == before
    assert read_settings(tmp_path / "absent.db") == {}
    assert sorted(path.name for path in tmp_path.iterdir()) == [STORE_FILE], "a missing store is not made"
    (tmp_path / "broken.db").write_bytes(b"not a database" * 64)
    assert read_settings(tmp_path / "broken.db") == {}


def closing_connection(path: Path) -> contextlib.closing[sqlite3.Connection]:
    return contextlib.closing(sqlite3.connect(path, isolation_level=None))


def test_a_store_from_a_newer_bailey_is_read_as_it_stands(tmp_path: Path) -> None:
    store = Store(tmp_path / STORE_FILE)
    store.set_setting("provider", "openai")
    store.set_setting("models", {"openai": "gpt-5"})
    store.close()
    with closing_connection(tmp_path / STORE_FILE) as newer:
        newer.execute("PRAGMA user_version = 999")
    before = (tmp_path / STORE_FILE).read_bytes()

    setup = setup_for(tmp_path, environ={"BAILEY_KEYS": "file", "OPENAI_API_KEY": "sk-o"})
    assert (setup.connection.provider, setup.connection.model) == ("openai", "gpt-5")
    assert (tmp_path / STORE_FILE).read_bytes() == before, "neither migrated nor refused"


def test_settings_are_read_through_the_log_of_a_store_the_app_has_open(tmp_path: Path) -> None:
    store = Store(tmp_path / STORE_FILE)
    try:
        store.set_setting("provider", "anthropic")
        store.set_setting("provider", "openai")
        assert read_settings(tmp_path / STORE_FILE) == {"provider": "openai"}
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Reading the solution
# ---------------------------------------------------------------------------


def test_a_folder_and_a_zip_of_one_solution_read_the_same(tmp_path: Path) -> None:
    files = read_package(GROUNDED)
    package = tmp_path / "grounded.zip"
    with zipfile.ZipFile(package, "w") as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    assert read_package(package) == files
    assert "assistant.json" in files and "samples/policies.md" in files


def test_a_folder_leaves_out_git_and_finder_files(tmp_path: Path) -> None:
    folder = tmp_path / "grounded-replies"
    shutil.copytree(GROUNDED, folder)
    (folder / ".git").mkdir()
    (folder / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (folder / ".DS_Store").write_bytes(b"\0\1")
    assert read_package(folder) == read_package(GROUNDED)


def test_a_folder_holding_a_link_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "grounded-replies"
    shutil.copytree(GROUNDED, folder)
    try:
        (folder / "samples" / "linked.md").symlink_to(tmp_path / "elsewhere.md")
    except OSError:
        pytest.skip("this machine does not let a test make a link")
    with pytest.raises(CheckFailed, match=r"^samples/linked\.md is a link, which a package may not carry\.$"):
        read_package(folder)


def test_a_path_that_is_neither_a_folder_nor_a_zip_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CheckFailed, match="is neither a folder nor a ZIP file"):
        read_package(tmp_path / "nothing-here")


# ---------------------------------------------------------------------------
# The cases
# ---------------------------------------------------------------------------


def test_each_example_is_a_case_and_each_slot_with_a_sample_has_a_grounding_pair() -> None:
    made = read_solution(read_package(GROUNDED))
    cases = cases_of(made)
    assert [(case.kind, case.name, case.slot) for case in cases] == [
        ("example", "Example 1", ""),
        ("example", "Example 2", ""),
        ("example", "Example 3", ""),
        ("answered", "policies: a question its sample answers", "policies"),
        ("uncovered", "policies: a question no document answers", "policies"),
        ("answered", "faq: a question its sample answers", "faq"),
        ("uncovered", "faq: a question no document answers", "faq"),
    ]
    assert cases[0].message == made.examples[0].input.strip() and cases[0].expected == made.examples[0].output
    assert [case.message for case in cases[3:]] == [
        "What does your policies document say about shipping?",
        "Who runs the Zephyrine scheme?",
        "Do you buy second-hand books?",
        "Who runs the Zephyrine club?",
    ]


def _shop(*samples: Sample, slots: tuple[KnowledgeSlot, ...] = ()) -> Assistant:
    return Assistant(
        name="shop",
        title="Shop",
        description="",
        instructions=Skill("shop", "Its instructions.", "You help."),
        knowledge=slots or tuple(KnowledgeSlot(sample.slot, "", (sample.extension,)) for sample in samples),
        samples=samples,
    )


def test_the_made_up_name_is_one_no_sample_holds() -> None:
    held = Sample("notes", "md", f"# Notes\n\nThe {MADE_UP_NAMES[0]} scheme is ours.\n")
    (_, uncovered) = cases_of(_shop(held))
    assert uncovered.message == f"Who runs the {MADE_UP_NAMES[1]} scheme?"


def test_a_slot_without_a_sample_has_no_pair() -> None:
    slots = (KnowledgeSlot("policies", "", ("md",)), KnowledgeSlot("faq", "", ("md",)))
    cases = cases_of(_shop(Sample("faq", "md", "# FAQ\n\n## Do you ship abroad?\n\nNot yet.\n"), slots=slots))
    assert [case.slot for case in cases] == ["faq", "faq"]


@pytest.mark.parametrize(
    ("slot", "extension", "text", "question"),
    [
        (
            "policies",
            "md",
            "# Shop policies\n\nA sample.\n\n## Shipping\n\nWe ship in two days.\n",
            "What does your policies document say about shipping?",
        ),
        ("faq", "md", "# Questions\n\n## Do you ship abroad?\n\nNot yet.\n", "Do you ship abroad?"),
        (
            "product_catalog",
            "csv",
            "sku,name,price_usd\nMUG-12,Speckled mug 12 oz,32.00\n",
            "What does your product catalog document say about Speckled mug 12 oz?",
        ),
        ("rates", "csv", "price,colour\n12.00,blue\n", "What does your rates document say about blue?"),
        (
            "hours",
            "md",
            "# Opening hours\n\nWe open at nine.\n",
            "What does your hours document say about opening hours?",
        ),
        ("faq", "md", "## Returns\n\nWithin 30 days.\n", "What does your faq document say about returns?"),
        (
            "notes",
            "txt",
            "- We open at nine every day except Sunday, and close at six.\n",
            'What does your notes document say about "We open at nine every day except Sunday, and close"?',
        ),
    ],
    ids=[
        "a section",
        "a faq's question",
        "a table's name column",
        "a table's first word",
        "a title",
        "a lone heading",
        "words",
    ],
)
def test_the_answered_question_is_made_from_a_passage_of_the_sample(
    slot: str, extension: str, text: str, question: str
) -> None:
    assert answered_question(KnowledgeSlot(slot, "", (extension,)), Sample(slot, extension, text)) == question


# ---------------------------------------------------------------------------
# The verdicts
# ---------------------------------------------------------------------------

SOURCE = "- You can return a book in the condition it arrived — within 30 days of delivery, for any reason."


@pytest.mark.parametrize(
    ("answer", "found"),
    [
        ('As our policy says: "You can return a book in the condition it arrived."', True),
        ("As our policy says: “you can return a book in the condition”", True),
        ('"in the condition it arrived - within 30 days of delivery,"', True),
        ('"in the condition it arrived — within\n30 days"', False),
        ('"return a book"', False),
        ('"You can return a book in any condition."', False),
        ("You can return a book in the condition it arrived within 30 days.", False),
    ],
    ids=[
        "straight quotes",
        "curly quotes",
        "a dash set plainly",
        "across a line",
        "three words",
        "changed",
        "unquoted",
    ],
)
def test_a_quote_counts_when_it_is_four_words_or_more_found_word_for_word(answer: str, found: bool) -> None:
    assert bool(quotes(answer, [SOURCE])) is found


def test_a_quote_of_a_sentence_the_document_wraps_is_found_on_one_line() -> None:
    wrapped = "- You can return a book in the condition it\n  arrived within 30 days of delivery."
    assert quotes('"in the condition it arrived within 30 days"', [wrapped]) == [
        "in the condition it arrived within 30 days"
    ]


@pytest.mark.parametrize(
    ("answer", "covered"),
    [
        ("NOT COVERED: wholesale pricing.", True),
        ("The documents do not cover that.", True),
        ("Our policies don't mention gift wrapping.", True),
        ("The FAQ doesn’t say whether you can collect.", True),
        ("That is not in the documents.", True),
        ("That is in the documents.", False),
        ("Personalisation isn't mentioned anywhere.", True),
        ("There is no information about that.", True),
        ("I found nothing in the documents about it.", True),
        ("Returns are covered by our policy.", False),
        ("We do not offer gift wrapping.", False),
        ("Shipping is free on orders over £30 [P2].", False),
    ],
)
def test_saying_the_documents_do_not_cover_a_question_is_read_in_its_usual_words(answer: str, covered: bool) -> None:
    assert says_not_covered(answer) is covered
    assert (NOT_COVERED.search(answer) is not None) is covered


GROUNDING = Assistant(
    name="shop",
    title="Shop",
    description="",
    instructions=Skill("shop", "Its instructions.", "You help."),
    knowledge=(KnowledgeSlot("policies", "", ("md",)), KnowledgeSlot("faq", "", ("md",))),
    samples=(
        Sample("policies", "md", "# Policies\n\n## Returns\n\n" + SOURCE + "\n"),
        Sample("faq", "md", "# FAQ\n\n## How quickly do you answer?\n\nWe answer every email within one day.\n"),
    ),
)


def _verdicts(case: Case, outcome: Outcome) -> list[tuple[str, bool]]:
    return [(check.name, check.passed) for check in judge(case, outcome, GROUNDING)]


def test_a_run_that_failed_or_gave_nothing_fails_on_answering() -> None:
    case = Case("example", "Example 1", "Hello")
    (failed,) = judge(case, Outcome(error="The model's server refused the key."), GROUNDING)
    assert (failed.name, failed.passed) == ("answered", False)
    assert failed.reason == "The run failed: The model's server refused the key."
    (empty,) = judge(case, Outcome(answer="  \n"), GROUNDING)
    assert (empty.passed, empty.reason) == (False, "It gave no answer.")


def test_an_example_is_held_to_what_its_own_reply_shows() -> None:
    quoting = Case("example", "Example 1", "Can I return it?", expected='Yes. "You can return a book in the condition"')
    citing = Case("example", "Example 2", "Can I return it?", expected="Yes, within 30 days [P3].")
    bare = Case("example", "Example 3", "Hello", expected="Hello! How can I help?")
    unwritten = Case("example", "Example 4", "Hello")
    uncovered = Case("example", "Example 5", "Gift wrap?", expected="NOT COVERED: gift wrapping.\n\nHi.")

    assert _verdicts(quoting, Outcome(answer="Yes, within 30 days [P3].", citations=[{"document": "policies.md"}])) == [
        ("answered", True),
        ("quotes", False),
    ]
    assert _verdicts(citing, Outcome(answer="Yes [P3].", citations=[{"document": "policies.md"}])) == [
        ("answered", True),
        ("cites", True),
    ]
    assert _verdicts(citing, Outcome(answer='Yes: "in the condition it arrived".')) == [
        ("answered", True),
        ("cites", True),
    ], "a word-for-word quote is as good as a citation"
    assert _verdicts(citing, Outcome(answer="Yes.")) == [("answered", True), ("cites", False)]
    assert _verdicts(bare, Outcome(answer="Hi.")) == [("answered", True)]
    assert _verdicts(unwritten, Outcome(answer="Hi.")) == [("answered", True)]
    assert _verdicts(uncovered, Outcome(answer="Hi, we will come back to you.")) == [
        ("answered", True),
        ("not covered", False),
    ]


def test_a_question_a_sample_answers_must_cite_or_quote_that_sample() -> None:
    case = Case("answered", "policies: a question its sample answers", "Can I return a book?", slot="policies")
    assert _verdicts(case, Outcome(answer="Yes [P1].", citations=[{"document": "policies.md"}])) == [
        ("answered", True),
        ("cites its sample", True),
    ]
    assert _verdicts(case, Outcome(answer='Yes: "return a book in the condition it arrived".')) == [
        ("answered", True),
        ("cites its sample", True),
    ]
    assert _verdicts(case, Outcome(answer="Yes [P9].", citations=[{"document": "faq.md"}])) == [
        ("answered", True),
        ("cites its sample", False),
    ], "a citation of another document is not this one's"
    (_, failed) = judge(case, Outcome(answer="Yes, of course."), GROUNDING)
    assert failed.reason == "It neither cites nor quotes the policies sample, which answers this."


def test_a_question_no_document_answers_must_be_said_so_and_may_cite_what_the_reply_says() -> None:
    case = Case("uncovered", "faq: a question no document answers", "Who runs the Zephyrine club?", slot="faq")
    holding = Outcome(
        answer='NOT COVERED: the club.\n\nWe will reply: "We answer every email within one day."',
        citations=[{"document": "faq.md"}],
    )
    assert _verdicts(case, holding) == [("answered", True), ("says so", True)]
    (_, failed) = judge(
        case, Outcome(answer="Our manager runs it [P1].", citations=[{"document": "faq.md"}]), GROUNDING
    )
    assert (failed.passed, failed.reason) == (False, "It does not say that the documents do not cover this.")


def test_estimates_add_up_only_when_every_part_has_one() -> None:
    assert summed([{"usd": "0.001500"}, {"usd": "0.000250"}]) == {"usd": "0.001750"}
    assert summed([{"usd": "0.001500"}, None]) is None, "a part priced is not a price"
    assert summed([]) is None


# ---------------------------------------------------------------------------
# Examples in the format
# ---------------------------------------------------------------------------


def test_the_fixtures_examples_are_a_valid_writers_document_and_survive_an_edit() -> None:
    files = read_package(GROUNDED)
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "0.1" / "assistant.schema.json").read_text())
    jsonschema.validate(json.loads(files["assistant.json"]), schema)
    made = read_solution(files)
    assert len(made.examples) == 3
    edited = assistant_from_form({**form_of(made), "title": "Grounded replies, edited"}, made)
    assert edited.examples == made.examples, "an edit in the app keeps what the check runs"
    assert read_solution(unzip_solution(zip_solution(edited))).examples == made.examples


@pytest.mark.parametrize(
    ("examples", "sentence"),
    [
        ({"input": "Where is my order?"}, "assistant.json's examples should be a list, each with an input."),
        ([{"output": "It ships on Friday."}], "assistant.json, example 1 has no input: what the person sends."),
        ([{"input": ""}], "assistant.json, example 1 has no input: what the person sends."),
        (["Where is my order?"], "assistant.json, example 1 has no input: what the person sends."),
        ([{"input": "Hello"}, {"input": 7}], "assistant.json, example 2 has no input: what the person sends."),
        ([{"input": "Hello", "output": ["Hi."]}], "assistant.json, example 1's output should be text."),
    ],
    ids=["not a list", "no input", "an empty input", "not an object", "an input that is not text", "a list output"],
)
def test_an_example_the_schema_refuses_is_refused_with_a_sentence(examples: Any, sentence: str) -> None:
    files = read_package(GROUNDED)
    document = json.loads(files["assistant.json"])
    document["examples"] = examples
    with pytest.raises(SolutionRefused) as refused:
        read_solution({**files, "assistant.json": json.dumps(document).encode()})
    assert str(refused.value) == sentence


def test_an_examples_member_this_reader_does_not_know_is_left_alone() -> None:
    files = read_package(GROUNDED)
    document = json.loads(files["assistant.json"])
    document["examples"] = [{"input": "Hello", "output": "Hi.", "tags": ["greeting"]}]
    made = read_solution({**files, "assistant.json": json.dumps(document).encode()})
    assert made.examples == (Example("Hello", "Hi."),)


def test_examples_survive_a_zip_and_back() -> None:
    made = Assistant(
        name="shop",
        title="Shop",
        description="",
        instructions=Skill("shop", "Its instructions.", "You help."),
        examples=(Example("Where is my order?", "It ships on Friday."), Example("Hello")),
    )
    document = json.loads(unzip_solution(zip_solution(made))["assistant.json"])
    assert document["examples"] == [
        {"input": "Where is my order?", "output": "It ships on Friday."},
        {"input": "Hello"},
    ]
    assert read_solution(unzip_solution(zip_solution(made))).examples == made.examples
    assert Assistant.from_dict(made.as_dict()).examples == made.examples
    without = Assistant(name="shop", title="Shop", description="", instructions=made.instructions)
    assert "examples" not in json.loads(unzip_solution(zip_solution(without))["assistant.json"])
