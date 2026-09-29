"""``bailey``: the command line."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from bailey import __version__
from bailey.doctor import ready, render, run_checks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bailey",
        description="An open-source AI assistant that runs ready-made solutions on your own machine.",
    )
    parser.add_argument("--version", action="version", version=f"bailey {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser(
        "doctor",
        help="check that this machine has what Bailey needs",
        description=(
            "Check that this machine has what Bailey needs: a recent Python, SQLite with full-text search, "
            "and a loopback address for the app's local server; and, for some solutions, uv and Node.js. "
            "Reads this machine only. Exits 1 when a required check fails."
        ),
    )
    doctor.add_argument("--json", action="store_true", help="print the checks as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    checks = run_checks()
    if args.json:
        sys.stdout.write(json.dumps([check.as_dict() for check in checks], indent=2) + "\n")
    else:
        sys.stdout.write(render(checks, version=__version__))
    return 0 if ready(checks) else 1
