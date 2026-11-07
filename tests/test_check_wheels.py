"""``scripts/check_wheels.py`` holds the README's list of computers to what an installer finds on each.

A resolve that succeeds proves less than it looks: an installer builds a dependency whose newest release
has only a source download, and where nothing at all is built for a computer it quietly installs an
older Bailey. So the script resolves each computer twice, as an installer does and then with wheels
only, held to the first one's versions. These tests hold its decisions, on a stand-in for uv that
answers as uv answers:

1. a computer the README covers passes only when both resolves do, and a failure says which one and
   why, in uv's words;
2. a computer the README sends to Docker must still fail, so the README changes the day it installs;
3. a resolve that broke is never read as an answer about the computer;
4. the second resolve is held to wheels and to the first one's versions, and a Mac is resolved for the
   oldest macOS its name covers, whatever the shell's own target;
5. the README names every computer the script holds it to, and lists no other as covered; the Pythons
   start at the project's own floor, and the classifiers name the systems instead of "OS Independent".

CI's ``wheels`` job runs the script against the index itself.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: What uv prints when a computer has nothing to install, as it printed it for an Intel Mac.
NOTHING_TO_INSTALL_STDERR = """\
  × No solution found when resolving dependencies:
  ╰─▶ Because onnxruntime==1.31.1 has no wheels with a matching platform tag
      (e.g., `macosx_15_0_x86_64`) and bailey depends on onnxruntime==1.31.1,
      we can conclude that your requirements are unsatisfiable.

hint: Wheels are available for `onnxruntime` (v1.31.1) on the following platforms: `manylinux_2_28_aarch64`
"""

#: What uv prints when the second resolve finds a version the first picked with no wheel.
BUILDS_FROM_SOURCE_STDERR = """\
  × No solution found when resolving dependencies:
  ╰─▶ Because cryptography==50.0.2 has no usable wheels and bailey depends
      on cryptography==50.0.2, we can conclude that your requirements are
      unsatisfiable.
"""


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_wheels", ROOT / "scripts" / "check_wheels.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: a dataclass looks its module up by name while it is being made.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check_wheels = _script()


class FakeUv:
    """Answers ``uv pip compile`` as uv does, per platform and per step, and records each call."""

    def __init__(self, answers: dict[tuple[str, str], tuple[int, str]] | None = None) -> None:
        #: ``(platform, "install" or "wheels")`` -> (exit code, stderr); anything unnamed resolves.
        self.answers = answers or {}
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, env))
        platform = command[command.index("--python-platform") + 1]
        step = "wheels" if "--only-binary" in command else "install"
        code, stderr = self.answers.get((platform, step), (0, ""))
        if code == 0 and step == "install":
            Path(command[command.index("--output-file") + 1]).write_text("onnxruntime==1.31.1\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, code, stdout="", stderr=stderr)


def _docker_answers() -> dict[tuple[str, str], tuple[int, str]]:
    """Every computer the README sends to Docker failing as it does on the index today."""
    answers = {
        (computer.platform, "install"): (1, NOTHING_TO_INSTALL_STDERR) for computer in check_wheels.SENT_TO_DOCKER
    }
    answers[("aarch64-pc-windows-msvc", "install")] = (0, "")
    answers[("aarch64-pc-windows-msvc", "wheels")] = (1, BUILDS_FROM_SOURCE_STDERR)
    # The Apple-chip Mac on macOS 13 shares its platform with the covered one: tell them apart by macOS.
    del answers[("aarch64-apple-darwin", "install")]
    return answers


class MacAwareUv(FakeUv):
    """A stand-in that also answers by the macOS a resolve was asked for, as uv does."""

    def __call__(self, command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        platform = command[command.index("--python-platform") + 1]
        if platform == "aarch64-apple-darwin" and env.get("MACOSX_DEPLOYMENT_TARGET") == "13.0":
            self.calls.append((command, env))
            return subprocess.CompletedProcess(command, 1, stdout="", stderr=NOTHING_TO_INSTALL_STDERR)
        return super().__call__(command, env)


def _today() -> MacAwareUv:
    return MacAwareUv(_docker_answers())


# --- 1. a covered computer passes only when both resolves do ---------------------------------------


def test_the_index_as_it_is_today_is_as_the_readme_says() -> None:
    lines, wrong = check_wheels.check(uv="uv", run=_today())

    assert wrong == []
    assert sum(line.startswith("ok ") for line in lines) == len(check_wheels.COVERED) * len(check_wheels.PYTHONS)
    assert sum(line.startswith("docker ") for line in lines) == len(check_wheels.SENT_TO_DOCKER) * len(
        check_wheels.PYTHONS
    )


@pytest.mark.parametrize(
    ("step", "stderr", "meets"),
    [
        ("install", NOTHING_TO_INSTALL_STDERR, "nothing to install"),
        ("wheels", BUILDS_FROM_SOURCE_STDERR, "builds from source"),
    ],
)
def test_a_covered_computer_that_does_not_install_is_named_with_uvs_reason(step: str, stderr: str, meets: str) -> None:
    uv = _today()
    uv.answers[("x86_64-pc-windows-msvc", step)] = (1, stderr)

    _lines, wrong = check_wheels.check(uv="uv", run=uv)

    assert len(wrong) == len(check_wheels.PYTHONS)
    assert all(entry.startswith("Windows on an Intel or AMD chip · Python ") for entry in wrong)
    assert all(f"the README says it installs, and it {meets}." in entry for entry in wrong)
    package = "onnxruntime==1.31.1 has no wheels" if step == "install" else "cryptography==50.0.2 has no usable wheels"
    assert all(package in entry for entry in wrong)


# --- 2. a computer sent to Docker must still fail --------------------------------------------------


def test_a_computer_sent_to_docker_that_installs_now_fails_the_check() -> None:
    uv = _today()
    del uv.answers[("x86_64-apple-darwin", "install")]

    _lines, wrong = check_wheels.check(uv="uv", run=uv)

    assert wrong == [
        f"an Intel Mac · Python {python}: the README sends it to Docker, and it installs now. Say so there."
        for python in check_wheels.PYTHONS
    ]


# --- 3. a resolve that broke says nothing about the computer ---------------------------------------


def test_a_resolve_that_broke_is_never_read_as_a_computer_it_cannot_install_on() -> None:
    uv = _today()
    uv.answers[("x86_64-apple-darwin", "install")] = (
        2,
        "error: Failed to fetch: `https://pypi.org/simple/onnxruntime/`",
    )

    with pytest.raises(check_wheels.ResolveError, match="Failed to fetch"):
        check_wheels.check(uv="uv", run=uv)


def test_main_exits_two_when_a_resolve_breaks_or_uv_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = _today()
    broken.answers[("x86_64-manylinux_2_28", "install")] = (2, "error: no index")
    monkeypatch.setattr(check_wheels, "_run", broken)
    monkeypatch.setattr(check_wheels.shutil, "which", lambda name: "/usr/bin/uv")
    assert check_wheels.main([]) == 2
    assert "A resolve could not run" in capsys.readouterr().err

    monkeypatch.setattr(check_wheels.shutil, "which", lambda name: None)
    assert check_wheels.main([]) == 2
    assert "uv is not on PATH" in capsys.readouterr().err


def test_main_exits_zero_on_todays_index_and_one_on_a_computer_not_as_the_readme_says(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(check_wheels.shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(check_wheels, "_run", _today())
    assert check_wheels.main([]) == 0
    assert "Every computer is as the README says" in capsys.readouterr().out

    wrong = _today()
    wrong.answers[("x86_64-manylinux_2_28", "wheels")] = (1, BUILDS_FROM_SOURCE_STDERR)
    monkeypatch.setattr(check_wheels, "_run", wrong)
    assert check_wheels.main([]) == 1
    printed = capsys.readouterr()
    assert "FAILED  Linux with glibc 2.28 or newer, on an Intel or AMD chip · Python 3.11" in printed.out
    assert "not as the README says" in printed.err


# --- 4. what each resolve is asked ----------------------------------------------------------------


def test_the_second_resolve_is_held_to_wheels_and_to_the_first_ones_versions() -> None:
    uv = FakeUv()

    assert check_wheels.resolve(check_wheels.COVERED[0], "3.12", uv="uv", run=uv).installs

    (first, _), (second, _) = uv.calls
    assert first[:4] == ["uv", "pip", "compile", str(ROOT / "pyproject.toml")]
    assert "--only-binary" not in first and "--constraints" not in first
    pins = first[first.index("--output-file") + 1]
    assert second[second.index("--only-binary") + 1] == ":all:"
    assert second[second.index("--constraints") + 1] == pins
    for command in (first, second):
        assert command[command.index("--python-version") + 1] == "3.12"
        assert command[command.index("--python-platform") + 1] == "x86_64-manylinux_2_28"


def test_a_computer_with_nothing_to_install_is_not_asked_the_second_question() -> None:
    uv = FakeUv({("x86_64-apple-darwin", "install"): (1, NOTHING_TO_INSTALL_STDERR)})

    outcome = check_wheels.resolve(check_wheels.SENT_TO_DOCKER[0], "3.12", uv="uv", run=uv)

    assert (outcome.installs, outcome.meets, len(uv.calls)) == (False, "nothing to install", 1)


def test_a_mac_is_resolved_for_the_oldest_macos_its_name_covers_and_nothing_else_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MACOSX_DEPLOYMENT_TARGET", "11.0")
    uv = _today()

    check_wheels.check(uv="uv", run=uv)

    targets = {
        (command[command.index("--python-platform") + 1], env.get("MACOSX_DEPLOYMENT_TARGET"))
        for command, env in uv.calls
    }
    assert targets == {
        ("x86_64-manylinux_2_28", None),
        ("aarch64-manylinux_2_28", None),
        ("x86_64-pc-windows-msvc", None),
        ("aarch64-apple-darwin", "14.0"),
        ("x86_64-apple-darwin", "15.0"),
        ("aarch64-apple-darwin", "13.0"),
        ("x86_64-unknown-linux-musl", None),
        ("aarch64-pc-windows-msvc", None),
    }


def test_no_resolve_writes_anywhere_but_a_folder_of_its_own() -> None:
    """uv writes an output file by renaming a new one over it: aimed at os.devnull as root, that replaced the
    /dev/null device with a plain file, and as anyone else it fails. So each resolve writes in a folder of its own."""
    uv = _today()

    check_wheels.check(uv="uv", run=uv)

    outputs = [Path(command[command.index("--output-file") + 1]) for command, _env in uv.calls]
    assert outputs, "the canary: the resolves were asked to write somewhere"
    assert Path(os.devnull) not in outputs
    assert all(output.parent.parent == Path(tempfile.gettempdir()) for output in outputs)


def test_uvs_explanation_is_kept_whole_on_one_line() -> None:
    reason = check_wheels._explanation(NOTHING_TO_INSTALL_STDERR)

    assert reason.startswith("Because onnxruntime==1.31.1 has no wheels with a matching platform tag (e.g., ")
    assert "hint: Wheels are available for `onnxruntime`" in reason
    assert "╰─▶" not in reason and "\n" not in reason and "  " not in reason


# --- 5. the README, the project's floor and the classifiers ---------------------------------------


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_readme_lists_exactly_the_computers_the_check_holds_it_to() -> None:
    readme = _readme()
    lead = "`uvx`, `pipx` and `pip` install Bailey on these computers:"
    assert lead in readme, "the README no longer introduces its list as the check reads it"
    listed = re.findall(r"^- (.+)$", readme.split(lead, 1)[1].split("\n\n", 2)[1], flags=re.MULTILINE)

    assert listed == [computer.name for computer in check_wheels.COVERED]
    for computer in check_wheels.SENT_TO_DOCKER:
        assert computer.name in readme, f"the README does not send {computer.name} to Docker"


def test_the_pythons_run_from_the_projects_own_floor() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    floor = re.fullmatch(r">=\s*(3\.\d+)", project["requires-python"])
    assert floor is not None, project["requires-python"]

    first, *rest = check_wheels.PYTHONS
    assert first == floor.group(1)
    minors = [int(python.split(".")[1]) for python in check_wheels.PYTHONS]
    assert minors == list(range(minors[0], minors[0] + len(minors))), (
        "a Python between the first and the last is missing"
    )
    assert f"Python {floor.group(1)} or newer" in _readme()


def test_the_classifiers_name_the_systems_rather_than_any_system() -> None:
    classifiers = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["classifiers"]

    assert "Operating System :: OS Independent" not in classifiers
    assert {
        "Operating System :: MacOS :: MacOS X",
        "Operating System :: Microsoft :: Windows",
        "Operating System :: POSIX :: Linux",
    } <= set(classifiers)
