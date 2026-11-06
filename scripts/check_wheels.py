"""Check what ``uvx``, ``pipx`` and ``pip`` find on each computer the README names, before a release finds it.

A resolve that succeeds proves less than it looks. An installer takes each dependency's newest release
that fits, and where that release has only a source download, it builds it: ``cryptography`` on an Intel
Mac needs a Rust toolchain for that. A resolve held to wheels walks round it by picking an older
release, so it passes on a computer where an install fails. And where a dependency has nothing at all
for a computer, an installer does not stop either: it takes the newest release of Bailey that installs,
which was the doctor-only first one, so ``uvx bailey`` on an Intel Mac printed that release's usage line
and said nothing else.

So each computer gets two resolves of this checkout's ``pyproject.toml``, by uv for that computer:

1. as an installer resolves: newest versions, source downloads allowed;
2. with wheels only, held to the versions the first one picked.

The first failing means there is nothing to install there; the second, that an install would build
something from source. A computer the README covers (``COVERED``) must pass both, on every Python an
installer might run it with. A computer the README sends to the Docker image (``SENT_TO_DOCKER``) must
fail one, so that the day one of them starts installing, the README is changed to say so.

It needs uv on ``PATH`` and the package index, and installs nothing::

    python scripts/check_wheels.py

Exit codes: 0, every computer is as the README says; 1, one is not; 2, a resolve could not run (no uv,
or an index uv could not reach). CI runs it, and so does the release before it uploads anything.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Every CPython from the project's ``requires-python`` floor to the newest release. An installer runs
#: Bailey with whichever the computer has, and the README promises Python 3.11 or newer.
PYTHONS: tuple[str, ...] = ("3.11", "3.12", "3.13", "3.14")


@dataclass(frozen=True)
class Computer:
    #: How the README names it, word for word.
    name: str
    #: uv's ``--python-platform`` for it.
    platform: str
    #: ``MACOSX_DEPLOYMENT_TARGET`` for a Mac: the oldest macOS the name covers. uv assumes 13.0 without it.
    macos: str = ""


#: The computers the README says ``uvx``, ``pipx`` and ``pip`` install Bailey on.
COVERED: tuple[Computer, ...] = (
    Computer("Linux with glibc 2.28 or newer, on an Intel or AMD chip", "x86_64-manylinux_2_28"),
    Computer("Linux with glibc 2.28 or newer, on an ARM chip", "aarch64-manylinux_2_28"),
    Computer("Windows on an Intel or AMD chip", "x86_64-pc-windows-msvc"),
    Computer("A Mac with an Apple chip, on macOS 14 or newer", "aarch64-apple-darwin", macos="14.0"),
)

#: The computers the README sends to the Docker image instead, as its sentence names them.
SENT_TO_DOCKER: tuple[Computer, ...] = (
    Computer("an Intel Mac", "x86_64-apple-darwin", macos="15.0"),
    Computer("a Mac on macOS 13 or older", "aarch64-apple-darwin", macos="13.0"),
    Computer("Linux on musl, such as Alpine", "x86_64-unknown-linux-musl"),
    Computer("Windows on an ARM chip", "aarch64-pc-windows-msvc"),
)

#: How uv opens its answer when the requirements have no solution, as opposed to a resolve that broke.
NO_SOLUTION = "No solution found"

#: What an install meets where it does not install: nothing at all, or something it would have to build.
NOTHING_TO_INSTALL = "nothing to install"
BUILDS_FROM_SOURCE = "builds from source"


class ResolveError(RuntimeError):
    """A resolve that could not run, which says nothing about the computer."""


@dataclass(frozen=True)
class Outcome:
    installs: bool
    #: ``NOTHING_TO_INSTALL`` or ``BUILDS_FROM_SOURCE`` where it does not install.
    meets: str = ""
    #: uv's own explanation, on one line.
    reason: str = ""


Run = Callable[[list[str], dict[str, str]], subprocess.CompletedProcess[str]]


def _run(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- uv, with arguments this script spells
        command, env=env, capture_output=True, text=True, check=False, timeout=600
    )


def _explanation(stderr: str) -> str:
    """uv's account of why there is no solution, its hint included, as one line."""
    lines = stderr.splitlines()
    start = next((index for index, line in enumerate(lines) if NO_SOLUTION in line), -1) + 1
    text = " ".join(line.strip() for line in lines[start:])
    return re.sub(r"\s+", " ", text.replace("╰─▶", "")).strip()


def resolve(computer: Computer, python: str, *, uv: str, run: Run | None = None) -> Outcome:
    """Resolve this checkout for ``computer`` on ``python`` as an installer would, then with wheels only."""
    runner = run if run is not None else _run
    env = {key: value for key, value in os.environ.items() if key != "MACOSX_DEPLOYMENT_TARGET"}
    if computer.macos:
        env["MACOSX_DEPLOYMENT_TARGET"] = computer.macos
    base = [uv, "pip", "compile", str(ROOT / "pyproject.toml"), "--python-platform", computer.platform]
    base += ["--python-version", python, "--no-header", "--no-annotate", "--quiet"]
    # Both resolves write into a folder of their own, never to os.devnull: uv writes an output file by
    # renaming a new one over it, which as root replaces the /dev/null device with a plain file, and as
    # anyone else fails.
    with tempfile.TemporaryDirectory() as folder:
        pins = Path(folder) / "pins.txt"
        wheels = Path(folder) / "wheels.txt"
        steps = (
            (["--output-file", str(pins)], NOTHING_TO_INSTALL),
            (["--only-binary", ":all:", "--constraints", str(pins), "--output-file", str(wheels)], BUILDS_FROM_SOURCE),
        )
        for arguments, meets in steps:
            completed = runner([*base, *arguments], env)
            if completed.returncode == 0:
                continue
            if NO_SOLUTION not in completed.stderr:
                raise ResolveError(
                    f"uv could not resolve for {computer.name} on Python {python} "
                    f"(exit {completed.returncode}): {completed.stderr.strip() or completed.stdout.strip()}"
                )
            return Outcome(installs=False, meets=meets, reason=_explanation(completed.stderr))
    return Outcome(installs=True)


def check(*, uv: str, run: Run | None = None, pythons: Sequence[str] = PYTHONS) -> tuple[list[str], list[str]]:
    """Every computer on every Python, as ``(lines to print, what is not as the README says)``."""
    lines: list[str] = []
    wrong: list[str] = []
    for covered, computers in ((True, COVERED), (False, SENT_TO_DOCKER)):
        for computer in computers:
            for python in pythons:
                outcome = resolve(computer, python, uv=uv, run=run)
                where = f"{computer.name} · Python {python}"
                if covered and outcome.installs:
                    lines.append(f"ok      {where}")
                elif covered:
                    wrong.append(f"{where}: the README says it installs, and it {outcome.meets}. {outcome.reason}")
                    lines.append(f"FAILED  {wrong[-1]}")
                elif outcome.installs:
                    wrong.append(f"{where}: the README sends it to Docker, and it installs now. Say so there.")
                    lines.append(f"FAILED  {wrong[-1]}")
                else:
                    lines.append(f"docker  {where}: {outcome.meets}")
    return lines, wrong


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)
    uv = shutil.which("uv")
    if uv is None:
        print("uv is not on PATH; install it first (python -m pip install uv).", file=sys.stderr)
        return 2
    try:
        lines, wrong = check(uv=uv)
    except (ResolveError, subprocess.TimeoutExpired, OSError) as failure:
        print(f"A resolve could not run: {failure}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    if wrong:
        print(f"\n{len(wrong)} not as the README says.", file=sys.stderr)
        return 1
    print(f"\nEvery computer is as the README says, on each Python ({len(lines)} checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
