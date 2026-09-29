"""The command line: the version it reports, and what ``doctor`` exits with."""

from __future__ import annotations

import json
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


def test_a_command_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main([])
    assert exited.value.code == 2
    assert "doctor" in capsys.readouterr().err


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
