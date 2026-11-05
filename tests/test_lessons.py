"""Bailey's side of its recorded lessons: the scripted model, and the start hook Pageant films it through.

The lessons are recorded with Pageant (sigrix-io/pageant), which holds its own rules, the
captions' and chapters' among them, and are recorded by hand (``lessons/README.md``). What is
Bailey's is held here. The scripted model (``lessons/scripted_bailey.py``) answers as a lesson
says, and a script that no longer fits the app fails the recording rather than filming
something the app would not do. The key file it names is the same in every recording, whoever
records it. Its start hook is what Pageant starts for a recording: this script, the lesson's
replies beside it in the work folder, Bailey's data folder there too, and the scripted model's
address handed on to the filmed terminal, all read from what the script prints.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from types import ModuleType

import pytest

from bailey.keys import FILE_NAME, open_keys
from bailey.paths import data_folder

ROOT = Path(__file__).resolve().parents[1]


def _lessons_module(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"lessons_{name}", ROOT / "lessons" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scripted() -> ModuleType:
    return _lessons_module("scripted_bailey")


MAIL_REPLY = {
    "when": "find Annie's email",
    "calls": [
        {"tool": "search_mail", "args": {"query": "Annie"}},
        {"tool": "save_draft", "args": {"email_id": "m-201", "body": "Hi Annie"}},
    ],
    "reply": [["Saved as a draft.", None]],
}


def test_a_scripted_reply_makes_its_calls_in_order_one_a_turn_then_answers(scripted: ModuleType) -> None:
    emulator = scripted.emulator
    conversation = emulator.Conversation(user=["Please find Annie's email."], tools=["search_mail", "save_draft"])

    first = scripted.answer(conversation, [MAIL_REPLY])
    assert [(call.name, call.arguments) for call in first.calls] == [("search_mail", {"query": "Annie"})]
    conversation.got("search_mail", '[{"id": "m-201"}]')
    second = scripted.answer(conversation, [MAIL_REPLY])
    assert [(call.name, call.arguments) for call in second.calls] == [
        ("save_draft", {"email_id": "m-201", "body": "Hi Annie"})
    ]
    conversation.got("save_draft", "Draft saved.")
    last = scripted.answer(conversation, [MAIL_REPLY])
    assert (last.text, last.calls) == ("Saved as a draft.", [])


def test_a_script_calling_a_tool_the_app_did_not_offer_fails_the_recording(scripted: ModuleType) -> None:
    conversation = scripted.emulator.Conversation(user=["Find Annie's email."], tools=["search_mail"])

    first = scripted.answer(conversation, [MAIL_REPLY])
    conversation.got(first.calls[0].name, "[]")
    with pytest.raises(scripted.emulator.Violation, match="save_draft"):
        scripted.answer(conversation, [MAIL_REPLY])


def test_a_message_the_lesson_scripts_no_reply_for_is_left_to_the_emulator(scripted: ModuleType) -> None:
    conversation = scripted.emulator.Conversation(user=["Hello"], tools=["search_mail", "save_draft"])

    assert scripted.answer(conversation, [MAIL_REPLY]) is None


def test_a_scripted_reply_loads_its_skill_then_cites_the_passage_holding_its_phrase(scripted: ModuleType) -> None:
    system = (
        "Draft replies.\n\n## Skills\n\n- `quote-sources`: Quotes the documents.\n\n"
        "## Your documents\n\nBelow are the person's documents, in numbered passages.\n\n"
        "### Document: policies.md (Shop policies)\n\n#### Returns\n"
        "[P3] - You can return a book within 30 days of delivery, for any reason.\n"
    )
    conversation = scripted.emulator.Conversation(system=system, user=["Can I send it back?"], tools=["load_skill"])
    reply = {
        "when": "send it back",
        "skill": "quote-sources",
        "reply": [["Yes, within 30 days", "30 days of delivery"], [", for any reason", "for any reason"], [".", None]],
    }

    first = scripted.answer(conversation, [reply])
    assert [(call.name, call.arguments) for call in first.calls] == [("load_skill", {"name": "quote-sources"})]
    conversation.got("load_skill", "# Quote sources")
    # Once per passage: the second phrase is in the same passage, so it is not cited again.
    assert scripted.answer(conversation, [reply]).text == "Yes, within 30 days [P3], for any reason."


def test_a_reply_whose_skill_the_solution_lost_comes_without_what_the_skill_grounds(scripted: ModuleType) -> None:
    reply = {
        "when": "gift wrap",
        "skill": "quote-sources",
        "reply": [["NOT COVERED: The documents do not mention gift wrapping.\n\nHi Keiko, the team will reply.", None]],
    }
    lost = scripted.emulator.Conversation(
        system="Draft replies.\n\n## Skills\n\n- `reply-style`: Keeps it short.\n",
        user=["Do you gift wrap?"],
        tools=["load_skill"],
    )

    turn = scripted.answer(lost, [reply])
    # No load of a skill it does not have, and no note of what the documents leave out.
    assert (turn.calls, turn.text) == ([], "Hi Keiko, the team will reply.")


def test_a_recordings_key_file_is_named_from_its_work_folder(
    scripted: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bailey names its data folder in full, and a recording's is in the recorder's own temporary
    folder: the page names the key file "Bailey/keys.enc" instead (``lessons/README.md``)."""

    monkeypatch.chdir(tmp_path)
    folder = data_folder(environ={"BAILEY_HOME": "Bailey"})
    assert folder == tmp_path / "Bailey", "the canary: Bailey names the folder in full"
    keys = scripted.keys_named_from(tmp_path, open_keys)(folder, environ={"BAILEY_KEYS": "file"})
    assert keys.where == f"an encrypted file, {Path('Bailey', FILE_NAME)}"


def test_the_terminals_bailey_doctor_names_the_data_folder_from_its_folder(
    scripted: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Lesson 4's terminal runs ``bailey doctor``: its data folder reads "Bailey", as the key file does."""

    from bailey import doctor, settings

    # What cli() replaces, put back after the test.
    monkeypatch.setattr(settings, "_BY_ID", dict(settings._BY_ID))
    monkeypatch.setattr(doctor, "data_folder", doctor.data_folder)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BAILEY_HOME", "Bailey")
    monkeypatch.setenv("LESSON_MODEL_URL", "http://127.0.0.1:9")
    (tmp_path / "Bailey").mkdir()

    scripted.cli(["doctor"])
    folder_line = next(line for line in capsys.readouterr().out.splitlines() if "Data folder:" in line)
    assert folder_line.split("Data folder: ", 1)[1].split(" ", 1)[0] == "Bailey"


def test_the_start_hook_starts_this_script_on_the_lessons_replies(scripted: ModuleType) -> None:
    app = scripted.app([MAIL_REPLY], app_task_seconds=3.0)

    assert [str(part) for part in app.argv[1:]] == [str(ROOT / "lessons" / "scripted_bailey.py"), "replies.json"]
    assert json.loads(app.files["replies.json"]) == [MAIL_REPLY], "written into the work folder it starts in"
    assert app.env == {
        "BAILEY_HOME": "Bailey",
        "BAILEY_KEYS": "file",
        "BAILEY_PASSPHRASE": scripted.PASSPHRASE,
        "LESSON_APP_TASK_SECONDS": "3.0",
    }
    assert "LESSON_APP_TASK_SECONDS" not in scripted.app([]).env, "a stand-in environment only when asked"
    assert app.ready == '[data-panel="chat"]', "every lesson starts on Chat"


def test_what_the_hook_reads_is_what_bailey_prints_as_it_starts(scripted: ModuleType, tmp_path: Path) -> None:
    """Started as Pageant starts it, Bailey says where it is and where the scripted model is, in the
    work folder, with its data folder there: a launch line Bailey no longer prints fails here, not
    in a recording."""

    running = scripted.app([MAIL_REPLY]).start(tmp_path, dict(os.environ))
    try:
        assert re.fullmatch(r"http://127\.0\.0\.1:\d+/#token=\S+", running.address)
        assert running.env["LESSON_MODEL_URL"].startswith("http://127.0.0.1:"), "for the filmed terminal's bailey"
        assert running.env["BAILEY_HOME"] == "Bailey"
        assert json.loads((tmp_path / "replies.json").read_text(encoding="utf-8")) == [MAIL_REPLY]
        # The page itself answers, on this computer's own address.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(running.address.split("#", 1)[0], timeout=10) as page:
            assert page.status == 200
    finally:
        running.stop()
