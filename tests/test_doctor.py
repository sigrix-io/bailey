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
    check_extension_loading,
    check_full_text_search,
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
    fts, extensions = check_full_text_search(), check_extension_loading()
    assert (fts.ok, fts.required) == (False, True)
    assert (extensions.ok, extensions.required) == (False, False)
    for check in (fts, extensions):
        assert check.found == "this Python has no sqlite3 module"
        assert "built without SQLite" in check.fix


def test_extension_loading_is_optional() -> None:
    class Locked:
        pass

    check = check_extension_loading(Locked)
    assert (check.ok, check.required) == (False, False)
    assert "Keyword search still works" in check.fix
    assert check_extension_loading(type("Open", (), {"enable_load_extension": None})).ok


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
    assert "Optional and missing: uv." in text


# ---------------------------------------------------------------------------
# On this machine
# ---------------------------------------------------------------------------


def test_the_checks_come_in_a_fixed_order_and_each_says_what_it_is_for() -> None:
    checks = run_checks()
    assert [check.name for check in checks] == [
        "Python",
        "SQLite full-text search",
        "SQLite extensions",
        "uv",
        "Node.js",
        "Loopback",
    ]
    assert all(check.found and check.needed_for for check in checks)
    assert {check.name for check in checks if check.required} == {"Python", "SQLite full-text search", "Loopback"}


def test_it_reaches_nothing_beyond_this_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """The loopback check binds a port and never connects anywhere."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the doctor made a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert len(run_checks()) == 6


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
