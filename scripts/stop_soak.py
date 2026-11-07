"""Start Bailey and stop it at once, many times, and count the stops that do not end in time.

Once, on a macOS runner, a Bailey under test had not exited 30 seconds after it was told to
stop, just as it printed its address (github.com/sigrix-io/bailey/actions/runs/42004426660).
A run of these stops found why: Bailey's handler raised an exception from the signal, wherever the main
thread was, and one that landed inside importlib's weakref callback was swallowed there. The
stop was lost, importlib's lock stayed held, and the thread loading the agent core waited on
it for ever while Bailey served on. Bailey's handler now asks the server to shut down, as
uvicorn's own does, and is in place before the ready line, so every stop after it ends with
"Bailey has stopped." and exit status 0.

The agent core loads in a thread of its own meanwhile (``_warm_up`` in ``bailey.server``),
far more slowly on a macOS runner. So a third of the stops come at the ready line, as the
test's did; a third at a random moment in the 50 milliseconds after it, where the lost stop
was found; and a third at one in the three seconds after it, while that load can still be
running::

    python scripts/stop_soak.py --times 300

Each Bailey gets a data folder of its own, as each test's does, and is stopped the way the
tests stop it (``tests/running.py``). One still running 30 seconds after it was told to stop
is looked at first with ``sample`` where the computer has it (macOS: the native stack of every
thread, which ``faulthandler`` cannot see), then made to write its Python threads' stacks and
ended, and the soak goes on. CI runs it from ``.github/workflows/stop-soak.yml``.

A stop that ends any other way is printed whole, with what Bailey printed.

Exit codes: 0, every stop ended in time and cleanly; 1, at least one did not, printed above.
"""

from __future__ import annotations

import argparse
import collections
import platform
import random
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from running import STOP_TIMEOUT, Running, StopTimedOut  # noqa: E402

#: The latest an early stop and a late one come after the ready line, in seconds.
EARLY_WINDOW = 0.05
LATE_WINDOW = 3.0

#: How a stop ends, by exit status. Any other ending is one to look at.
ENDINGS = {0: "stopped cleanly (exit 0)"}


def look_at(pid: int) -> None:
    """Print the native stack of every thread of ``pid``, where this computer has ``sample``."""

    tool = shutil.which("sample")
    if tool is None:
        return
    # macOS's sampler, over three seconds; -mayDie because the process is about to be ended.
    looked = subprocess.run(  # noqa: S603 -- a tool found on PATH, with arguments this script spells
        [tool, str(pid), "3", "-mayDie"], capture_output=True, text=True, timeout=120, check=False
    )
    print(f"sample {pid}:\n{looked.stdout}{looked.stderr}", flush=True)


def soak(times: int, *, seed: int) -> int:
    """Start and stop Bailey ``times`` times. The exit code: 1 when any stop or start was stuck,
    or a stop ended another way than cleanly."""

    chooser = random.Random(seed)  # noqa: S311 -- when to stop, not a secret
    endings: collections.Counter[str] = collections.Counter()
    slowest, slowest_stop = 0.0, 0
    stuck: list[int] = []
    unexpected: list[int] = []
    with tempfile.TemporaryDirectory() as scratch:
        for number in range(1, times + 1):
            delay = (0.0, chooser.uniform(0, EARLY_WINDOW), chooser.uniform(0, LATE_WINDOW))[number % 3]
            try:
                bailey = Running(Path(scratch) / f"home-{number}")
            except AssertionError as error:
                stuck.append(number)
                print(f"Start {number} never printed the address:\n{error}", flush=True)
                continue
            time.sleep(delay)
            began = time.monotonic()
            try:
                code, _ = bailey.stop(before_abort=look_at)
            except StopTimedOut as error:
                stuck.append(number)
                print(f"Stop {number}, {delay:.2f}s after the ready line:\n{error}", flush=True)
                continue
            took = time.monotonic() - began
            if took > slowest:
                slowest, slowest_stop = took, number
            endings[ENDINGS.get(code, f"exit {code}")] += 1
            if code not in ENDINGS:
                unexpected.append(number)
                print(
                    f"Stop {number}, {delay:.2f}s after the ready line, ended with exit status {code}. "
                    f"What it printed:\n{''.join(bailey.lines)}",
                    flush=True,
                )
            if number % 25 == 0:
                print(f"{number} of {times} stopped; the slowest took {slowest:.2f}s", flush=True)
    print(
        f"\n{times} starts and stops on {platform.system()} {platform.machine()}, Python "
        f"{platform.python_version()}, seed {seed}: a third at the ready line, a third up to "
        f"{EARLY_WINDOW * 1000:g}ms after it and a third up to {LATE_WINDOW:g}s after it."
    )
    for ending, count in endings.most_common():
        print(f"  {count} {ending}")
    if unexpected:
        print(f"  Stops that ended another way, printed above: {unexpected}")
    if stuck:
        print(f"  {len(stuck)} still running {STOP_TIMEOUT:g}s after they were told to stop: {stuck}")
    if stuck or unexpected:
        return 1
    print(f"Every stop ended in time; the slowest, stop {slowest_stop}, took {slowest:.2f}s.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--times", type=int, default=300, help="how many times to start and stop Bailey")
    parser.add_argument("--seed", type=int, default=None, help="for the moments the later stops come at")
    arguments = parser.parse_args(argv)
    seed = arguments.seed if arguments.seed is not None else random.SystemRandom().randrange(2**32)
    return soak(arguments.times, seed=seed)


if __name__ == "__main__":
    raise SystemExit(main())
