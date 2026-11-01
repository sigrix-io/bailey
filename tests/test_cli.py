"""The command line: the version it reports, and what ``doctor`` exits with."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import bailey
from bailey import cli
from bailey.doctor import Check

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_the_version_is_the_one_being_published(capsys: pytest.CaptureFixture[str]) -> None:
    """release.yml refuses a tag that differs from pyproject.toml; this keeps the
    package's own answer equal to both."""

    published = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert bailey.__version__ == published
    with pytest.raises(SystemExit) as exited:
        cli.main(["--version"])
    assert exited.value.code == 0
    assert capsys.readouterr().out.strip() == f"bailey {published}"


def test_with_no_command_it_starts_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[dict[str, object]] = []

    def serve(**options: object) -> int:
        started.append(options)
        return 0

    monkeypatch.setattr("bailey.server.serve", serve)
    assert cli.main([]) == 0
    assert cli.main(["--port", "8123", "--no-browser"]) == 0
    assert started == [{"port": 0, "open_browser": True}, {"port": 8123, "open_browser": False}]


def test_the_doctor_and_the_version_load_none_of_the_server() -> None:
    """The app's dependencies load only when the app starts, so a broken one cannot stop the doctor."""

    probe = (
        "import sys, contextlib, io\n"
        "from bailey import cli\n"
        "with contextlib.redirect_stdout(io.StringIO()):\n"
        "    cli.main(['doctor', '--json'])\n"
        "print(sorted(name for name in ('bailey.server', 'starlette', 'uvicorn') if name in sys.modules))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.stdout.strip() == "[]", completed.stderr


def _checks(*, required_ok: bool) -> list[Check]:
    return [
        Check("Python", ok=required_ok, found="3.x", needed_for="Bailey itself", required=True),
        Check("Node.js", ok=False, found="not found", needed_for="npm tools", required=False, fix="Install it."),
    ]


@pytest.mark.parametrize(("required_ok", "code"), [(True, 0), (False, 1)])
def test_doctor_exits_1_only_when_a_required_check_fails(
    required_ok: bool, code: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "run_checks", lambda: _checks(required_ok=required_ok))
    assert cli.main(["doctor"]) == code
    assert "Node.js: not found" in capsys.readouterr().out


def test_doctor_json_is_one_object_per_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "run_checks", lambda: _checks(required_ok=True))
    assert cli.main(["doctor", "--json"]) == 0
    entries = json.loads(capsys.readouterr().out)
    assert [entry["name"] for entry in entries] == ["Python", "Node.js"]
    assert set(entries[0]) == {"name", "ok", "found", "needed_for", "required", "fix"}
