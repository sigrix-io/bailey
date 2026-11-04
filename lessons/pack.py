"""Pack the recorded lessons into one zip that Sigrix's Learn console imports in one step.

    python lessons/pack.py

reads each lesson of ``pages.PATH`` from ``lessons/out/<folder>/`` (where recording it put it), writes
its page afresh from ``pages.py``, and writes ``lessons/out/bailey-lessons.zip`` with the learning
path. Upload that zip on sigrix.io at Moderator > Learn > Import lessons (``bundle.py`` says what it
holds). A lesson not yet recorded stops the pack, naming what is missing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import bundle
import pages

HERE = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pack the recorded lessons for Sigrix's Learn import.")
    parser.add_argument(
        "--out", type=Path, default=HERE / "out", help="where the lessons were recorded (default: lessons/out)"
    )
    parser.add_argument("--zip", type=Path, help="the pack to write (default: bailey-lessons.zip in --out)")
    args = parser.parse_args(argv)
    try:
        made = bundle.pack(args.out, pages.PATH, args.zip or args.out / "bailey-lessons.zip")
    except FileNotFoundError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(f"{made}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
