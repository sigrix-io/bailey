"""``bailey``: the command line. On its own it starts the app; ``bailey doctor`` checks the machine."""

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
        description=(
            "An open-source AI assistant that runs ready-made solutions on your own machine. "
            "With no command, start the app on this computer's own address and open it in the browser."
        ),
    )
    parser.add_argument("--version", action="version", version=f"bailey {__version__}")
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="the port to listen on, on 127.0.0.1 only (default: any free one)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="print the address to open instead of opening a browser",
    )
    commands = parser.add_subparsers(dest="command")
    doctor = commands.add_parser(
        "doctor",
        help="check that this machine has what Bailey needs",
        description=(
            "Check that this machine has what Bailey needs: a recent Python, SQLite with full-text search, "
            "a loopback address for the app's local server and a data folder it can write in; and a keychain "
            "for your keys, and uv and Node.js for some solutions. Reads this machine only. "
            "Exits 1 when a required check fails."
        ),
    )
    doctor.add_argument("--json", action="store_true", help="print the checks as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        checks = run_checks()
        if args.json:
            sys.stdout.write(json.dumps([check.as_dict() for check in checks], indent=2) + "\n")
        else:
            sys.stdout.write(render(checks, version=__version__))
        return 0 if ready(checks) else 1
    # Imported here, so `bailey doctor` and `bailey --version` load none of the server.
    from bailey.server import serve

    return serve(port=args.port, open_browser=not args.no_browser)
