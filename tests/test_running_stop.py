"""A Bailey under test that does not stop says where it is stuck, and is not left running.

Once, on a macOS runner, a Bailey under test had not exited 30 seconds after it was told to stop
(github.com/sigrix-io/bailey/actions/runs/42004426660), and the job ended with nothing saying
where it was, and with an orphaned Python process for the runner to clean up. So a stop that
times out sends SIGABRT, which ``faulthandler`` answers with every thread's stack, kills what is
left, and fails with those stacks; and so does a start that never prints the address.

A stand-in plays that Bailey here: it prints the address :class:`Running` waits for, ignores
the terminate signal, and waits in two functions named for where they wait, one in each of two
threads. Its stack is what the failure has to show. Windows sends no SIGABRT to another
process, and a process terminated there ends at once, so none of this applies to it.
"""

from __future__ import annotations

import os
import signal
import sys
import textwrap
from pathlib import Path

import pytest
import running
from running import Running, StopTimedOut

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Windows ends a terminated process at once")

#: What :class:`Running` waits for before it hands the process over.
ADDRESS = "Open it at http://127.0.0.1:9/#token=stand-in"


def stand_in(*, address: bool = True, deaf_to_abort: bool = False, exits: bool = False) -> list[str]:
    """A command for a Bailey that is stuck: it ignores the terminate signal and waits forever."""

    lines = ["import signal, threading, time"]
    if not exits:
        lines.append("signal.signal(signal.SIGTERM, signal.SIG_IGN)")
    if deaf_to_abort:
        lines.append("signal.signal(signal.SIGABRT, signal.SIG_IGN)")
    lines.append(
        textwrap.dedent(
            """
            def waits_in_a_thread_of_its_own():
                while True:
                    time.sleep(1)

            def waits_in_the_main_thread():
                while True:
                    time.sleep(1)

            threading.Thread(target=waits_in_a_thread_of_its_own, daemon=True).start()
            """
        )
    )
    if address:
        lines.append(f"print({ADDRESS!r}, flush=True)")
    lines.append("waits_in_the_main_thread()")
    return [sys.executable, "-c", "\n".join(lines)]


def test_a_stop_that_times_out_fails_with_every_threads_stack_and_ends_it(tmp_path: Path) -> None:
    bailey = Running(tmp_path / "home", command=stand_in())

    with pytest.raises(StopTimedOut) as stopped:
        bailey.stop(timeout=2)

    said = str(stopped.value)
    assert "had not exited 2 seconds after it was told to stop" in said
    # faulthandler's stack of each thread, each named by the function it waits in.
    assert "waits_in_the_main_thread" in said, said
    assert "waits_in_a_thread_of_its_own" in said, said
    # Ended by the abort that wrote the stacks, not by the kill after it.
    assert bailey.process.returncode == -signal.SIGABRT, said
    assert f"exit status {-signal.SIGABRT}" in said


def test_a_stuck_bailey_that_ignores_the_abort_too_is_killed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(running, "ABORT_TIMEOUT", 1.0)
    bailey = Running(tmp_path / "home", command=stand_in(deaf_to_abort=True))

    with pytest.raises(StopTimedOut) as stopped:
        bailey.stop(timeout=2)

    assert bailey.process.returncode == -signal.SIGKILL
    assert f"exit status {-signal.SIGKILL}" in str(stopped.value)


def test_the_stuck_bailey_can_be_looked_at_before_the_abort(tmp_path: Path) -> None:
    bailey = Running(tmp_path / "home", command=stand_in())
    seen: list[tuple[int, int | None]] = []

    with pytest.raises(StopTimedOut):
        bailey.stop(timeout=2, before_abort=lambda pid: seen.append((pid, bailey.process.poll())))

    # Handed the process while it was still running, before anything ended it.
    assert seen == [(bailey.process.pid, None)]


def test_a_start_that_never_prints_the_address_fails_with_the_stacks_and_ends_it(tmp_path: Path) -> None:
    pid_file = tmp_path / "pid"
    command = stand_in(address=False)
    command[-1] = f"import os, pathlib; pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n" + command[-1]

    with pytest.raises(AssertionError) as started:
        Running(tmp_path / "home", command=command, start_timeout=2)

    said = str(started.value)
    assert "Bailey printed no" in said
    assert "waits_in_the_main_thread" in said, said
    # Gone, and waited for: no process with that id is left, not even one waiting to be reaped.
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


def test_a_bailey_that_stops_in_time_answers_as_before(tmp_path: Path) -> None:
    """The control: the default terminate signal ends it, and no stack is asked for."""

    bailey = Running(tmp_path / "home", command=stand_in(exits=True))

    code, printed = bailey.stop(timeout=10)

    assert code == -signal.SIGTERM
    assert "Fatal Python error" not in printed + "".join(bailey.lines)
