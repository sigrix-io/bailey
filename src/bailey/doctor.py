"""``bailey doctor``: does this machine have what Bailey needs?

Each check says what it found, what Bailey uses it for, and what to do when it
is missing. A *required* check is one the assistant cannot start without. The
others are needed by some solutions only: a tool published as an npm package
needs Node.js, and nothing else does. So they are reported, and they never
fail the command.

It reads this machine and nothing else: no network call, no file written, and
no environment variable printed. Every check takes what it probes as an
argument, so the tests can hand it a machine that lacks something.
"""

from __future__ import annotations

import shutil
import socket
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

try:
    import sqlite3
except ImportError:  # a Python compiled without SQLite's headers, which some source builds are
    sqlite3 = None  # type: ignore[assignment]

MINIMUM_PYTHON = (3, 11)

GET_PYTHON = "Install Python from python.org or with `uv python install`, then run `bailey doctor` again to confirm."


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    found: str
    needed_for: str
    required: bool
    fix: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_python(version_info: Sequence[int] = sys.version_info) -> Check:
    ok = tuple(version_info[:2]) >= MINIMUM_PYTHON
    return Check(
        name="Python",
        ok=ok,
        found=".".join(str(part) for part in version_info[:3]),
        needed_for="Bailey itself",
        required=True,
        fix="" if ok else "Install Python 3.11 or newer, or start Bailey with `uvx`, which fetches one.",
    )


def _without_sqlite(name: str, needed_for: str, *, required: bool) -> Check:
    return Check(
        name=name,
        ok=False,
        found="this Python has no sqlite3 module",
        needed_for=needed_for,
        required=required,
        fix="This Python was built without SQLite. " + GET_PYTHON,
    )


def check_full_text_search(connect: Callable[[str], Any] | None = None) -> Check:
    """FTS5 is the keyword half of document search, and a compile-time option of SQLite."""

    name, needed_for = "SQLite full-text search", "searching your documents by keyword"
    if connect is None:
        if sqlite3 is None:
            return _without_sqlite(name, needed_for, required=True)
        connect = sqlite3.connect
    version = sqlite3.sqlite_version if sqlite3 is not None else "unknown"
    connection = connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
        found = f"SQLite {version} with FTS5"
        ok = True
    except Exception as error:  # the probe's only question is whether this statement runs
        found = f"SQLite {version} without FTS5 ({error})"
        ok = False
    finally:
        connection.close()
    return Check(
        name=name,
        ok=ok,
        found=found,
        needed_for=needed_for,
        required=True,
        fix="" if ok else "This Python's SQLite was built without FTS5. " + GET_PYTHON,
    )


def check_extension_loading(connection_type: type | None = None) -> Check:
    """The vector index is a SQLite extension, and loading one is also a build option."""

    name, needed_for = "SQLite extensions", "the vector index, which finds passages by meaning rather than by word"
    if connection_type is None:
        if sqlite3 is None:
            return _without_sqlite(name, needed_for, required=False)
        connection_type = sqlite3.Connection
    ok = hasattr(connection_type, "enable_load_extension")
    return Check(
        name=name,
        ok=ok,
        found="can be loaded" if ok else "cannot be loaded",
        needed_for=needed_for,
        required=False,
        fix=""
        if ok
        else (
            "This Python was built without SQLite extension loading (the Python that ships with macOS is one). "
            "Keyword search still works; for search by meaning, run Bailey on a Python that allows extensions."
        ),
    )


def check_uv(which: Callable[[str], str | None] = shutil.which) -> Check:
    path = which("uvx") or which("uv")
    return Check(
        name="uv",
        ok=bool(path),
        found=path or "not found",
        needed_for="tools published as Python packages, which start with `uvx`",
        required=False,
        fix="" if path else "Install uv: https://docs.astral.sh/uv/getting-started/installation/",
    )


def check_node(which: Callable[[str], str | None] = shutil.which) -> Check:
    path = which("npx")
    return Check(
        name="Node.js",
        ok=bool(path),
        found=path or "not found",
        needed_for="tools published as npm packages, which start with `npx`",
        required=False,
        fix="" if path else "Install Node.js, which includes `npx`, if a solution you use has such a tool.",
    )


def check_loopback(socket_factory: Callable[..., socket.socket] = socket.socket) -> Check:
    """The app is a server on 127.0.0.1. Binding port 0 asks for any free port."""

    try:
        with socket_factory(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
        ok = True
    except OSError:
        ok = False
    return Check(
        name="Loopback",
        ok=ok,
        found="127.0.0.1 is available" if ok else "cannot listen on 127.0.0.1",
        needed_for="the app's local server, which only this computer can reach",
        required=True,
        fix=""
        if ok
        else "Something on this machine refuses local connections; a firewall may need to let Python listen on 127.0.0.1.",
    )


def run_checks() -> list[Check]:
    return [
        check_python(),
        check_full_text_search(),
        check_extension_loading(),
        check_uv(),
        check_node(),
        check_loopback(),
    ]


def ready(checks: Sequence[Check]) -> bool:
    return all(check.ok for check in checks if check.required)


def render(checks: Sequence[Check], *, version: str) -> str:
    lines = [f"Bailey {version} doctor", ""]
    for check in checks:
        mark = "ok" if check.ok else ("FAIL" if check.required else "--")
        lines.append(f"  {mark:<5}{check.name}: {check.found}")
        lines.append(f"       needed for {check.needed_for}" + ("" if check.required else " (optional)"))
        if not check.ok and check.fix:
            lines.append(f"       {check.fix}")
    lines.append("")
    failed = [check.name for check in checks if check.required and not check.ok]
    optional = [check.name for check in checks if not check.required and not check.ok]
    if failed:
        lines.append("This machine is missing what Bailey requires: " + ", ".join(failed) + ".")
    else:
        lines.append("This machine has everything Bailey requires.")
        if optional:
            lines.append(
                "Optional and missing: "
                + ", ".join(optional)
                + ". They matter only to a solution whose tools use them."
            )
    return "\n".join(lines) + "\n"
