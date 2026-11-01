"""``bailey doctor``, check by check, on machines that lack each thing it looks for.

Every check takes what it probes as an argument, so each is driven here both
ways: on what this machine has, and on a stand-in that lacks it. The last
tests run the real command on the real machine, where the answer depends on
the machine, so they assert the shape of the answer rather than the verdict.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from bailey import doctor
from bailey.doctor import (
    Check,
    check_data_folder,
    check_full_text_search,
    check_keychain,
    check_loopback,
    check_node,
    check_python,
    check_uv,
    ready,
    render,
    run_checks,
)

SRC = Path(__file__).resolve().parents[1] / "src"


# ---------------------------------------------------------------------------
# One check at a time
# ---------------------------------------------------------------------------


def test_python_older_than_the_floor_fails_and_says_how_to_get_one() -> None:
    old = check_python((3, 10, 14))
    assert (old.ok, old.required, old.found) == (False, True, "3.10.14")
    assert "3.11" in old.fix
    assert check_python((3, 11, 0)).ok


class _NoFts5:
    def execute(self, statement: str) -> None:
        raise sqlite3.OperationalError("no such module: fts5")

    def close(self) -> None:
        pass


def test_sqlite_without_fts5_fails_and_names_the_error() -> None:
    check = check_full_text_search(lambda _name: _NoFts5())
    assert (check.ok, check.required) == (False, True)
    assert "without FTS5" in check.found and "no such module: fts5" in check.found
    assert "FTS5" in check.fix


def test_sqlite_with_fts5_passes_on_a_real_connection() -> None:
    check = check_full_text_search(sqlite3.connect)
    assert check.ok, check.found
    assert check.found == f"SQLite {sqlite3.sqlite_version} with FTS5"
    assert check.fix == ""


def test_a_python_without_sqlite_is_reported_rather_than_crashing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "sqlite3", None)
    fts = check_full_text_search()
    assert (fts.ok, fts.required) == (False, True)
    assert fts.found == "this Python has no sqlite3 module"
    assert "built without SQLite" in fts.fix


@pytest.mark.parametrize("present", ["uvx", "uv"])
def test_uv_is_found_by_either_name(present: str) -> None:
    check = check_uv(lambda name: f"/opt/bin/{name}" if name == present else None)
    assert check.ok and check.found == f"/opt/bin/{present}"


def test_missing_tool_runners_are_optional_and_say_where_to_get_them() -> None:
    uv, node = check_uv(lambda _name: None), check_node(lambda _name: None)
    for check in (uv, node):
        assert (check.ok, check.required, check.found) == (False, False, "not found")
        assert check.fix
    assert "docs.astral.sh" in uv.fix
    assert "npx" in node.fix


def test_loopback_that_cannot_be_bound_fails() -> None:
    class Refused:
        def __init__(self, *_args: object) -> None:
            pass

        def __enter__(self) -> Refused:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def bind(self, _address: object) -> None:
            raise PermissionError("blocked")

    check = check_loopback(Refused)
    assert (check.ok, check.required) == (False, True)
    assert check.fix


# ---------------------------------------------------------------------------
# The verdict and the report
# ---------------------------------------------------------------------------


def _check(name: str, *, ok: bool, required: bool) -> Check:
    return Check(name=name, ok=ok, found="x", needed_for="y", required=required, fix="" if ok else f"fix {name}")


def test_an_optional_failure_keeps_the_machine_ready_and_a_required_one_does_not() -> None:
    assert ready([_check("A", ok=True, required=True), _check("B", ok=False, required=False)])
    assert not ready([_check("A", ok=False, required=True), _check("B", ok=True, required=False)])


def test_the_report_names_every_check_and_gives_a_fix_only_where_one_failed() -> None:
    checks = [
        _check("Python", ok=True, required=True),
        _check("Loopback", ok=False, required=True),
        _check("Node.js", ok=False, required=False),
    ]
    text = render(checks, version="9.9.9")
    assert text.startswith("Bailey 9.9.9 doctor\n")
    for check in checks:
        assert f"{check.name}: x" in text
    assert "fix Python" not in text
    assert "fix Loopback" in text and "fix Node.js" in text
    assert "(optional)" in text
    assert text.rstrip().endswith("This machine is missing what Bailey requires: Loopback.")


def test_the_report_names_the_optional_gaps_when_the_machine_is_ready() -> None:
    text = render([_check("Python", ok=True, required=True), _check("uv", ok=False, required=False)], version="1")
    assert "This machine has everything Bailey requires." in text
    assert "Optional and missing: uv. Bailey works without them; each says above what changes." in text
    assert "solution whose tools" not in text, "the keychain is optional too, and is no solution's tool"


def test_a_data_folder_that_can_be_made_passes_and_is_not_made(tmp_path: Path) -> None:
    folder = tmp_path / "not" / "yet" / "bailey"
    check = check_data_folder(folder)
    assert (check.ok, check.required) == (True, True)
    assert check.found == f"{folder} (made when Bailey first starts)"
    assert not (tmp_path / "not").exists(), "the doctor writes nothing"
    folder.mkdir(parents=True)
    assert check_data_folder(folder).found == str(folder)


def test_a_data_folder_this_user_cannot_write_fails_and_says_where(tmp_path: Path) -> None:
    check = check_data_folder(tmp_path / "bailey", access=lambda _path, _mode: False)
    assert (check.ok, check.required) == (False, True)
    assert check.found == f"{tmp_path / 'bailey'}: this user cannot write in {tmp_path}"
    assert "BAILEY_HOME" in check.fix


def test_a_data_folder_under_a_file_fails(tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("", encoding="utf-8")
    check = check_data_folder(blocker / "bailey")
    assert not check.ok and "is a file, not a folder" in check.found


def test_a_keychain_is_optional_and_its_absence_names_the_encrypted_file() -> None:
    present = check_keychain(lambda: "the macOS Keychain")
    assert (present.ok, present.required, present.found, present.fix) == (True, False, "the macOS Keychain", "")
    absent = check_keychain(lambda: None)
    assert (absent.ok, absent.required, absent.found) == (False, False, "none on this machine")
    assert "encrypted file" in absent.fix and "passphrase" in absent.fix


def test_a_keychain_that_cannot_be_asked_is_reported_rather_than_crashing() -> None:
    def broken() -> str | None:
        raise RuntimeError("no session bus")

    check = check_keychain(broken)
    assert not check.ok and check.found == "could not be asked (RuntimeError: no session bus)"


def test_the_whole_doctor_creates_no_data_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAILEY_HOME", str(tmp_path / "home"))
    run_checks()
    assert not (tmp_path / "home").exists()


# ---------------------------------------------------------------------------
# On this machine
# ---------------------------------------------------------------------------


def test_the_checks_come_in_a_fixed_order_and_each_says_what_it_is_for() -> None:
    checks = run_checks()
    assert [check.name for check in checks] == [
        "Python",
        "SQLite full-text search",
        "uv",
        "Node.js",
        "Loopback",
        "Data folder",
        "Keychain",
    ]
    assert all(check.found and check.needed_for for check in checks)
    assert {check.name for check in checks if check.required} == {
        "Python",
        "SQLite full-text search",
        "Loopback",
        "Data folder",
    }


def test_it_reaches_nothing_beyond_this_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """The loopback check binds a port and never connects anywhere."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the doctor made a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert len(run_checks()) == 7


def test_the_command_answers_with_json_on_a_real_process() -> None:
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}
    completed = subprocess.run(
        [sys.executable, "-m", "bailey", "doctor", "--json"],
        capture_output=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert completed.returncode in (0, 1), completed.stderr.decode("utf-8", "replace")
    checks = json.loads(completed.stdout)
    assert [entry["name"] for entry in checks] == [check.name for check in run_checks()]
    assert completed.returncode == (0 if all(entry["ok"] for entry in checks if entry["required"]) else 1)
