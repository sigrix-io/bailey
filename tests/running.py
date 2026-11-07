"""Bailey as a user runs it: its own process and data folder, driven over HTTP on loopback."""

from __future__ import annotations

import contextlib
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from bailey import NATIVE_TELEMETRY_OFF

SRC = Path(__file__).resolve().parents[1] / "src"
LAUNCH = re.compile(r"Open it at (http://127\.0\.0\.1:(\d+)/)#token=(\S+)")

#: Stands for "this session's own token" in :meth:`Running.call`.
SESSION = object()

#: How long :meth:`Running.stop` gives Bailey to exit once it is told to stop.
STOP_TIMEOUT = 30.0

#: How long a Bailey that will not stop has, once sent SIGABRT, to write its threads' stacks and end.
ABORT_TIMEOUT = 10.0

# A request to this computer never goes through a proxy, whatever the environment says.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def environment(home: Path | str, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    # Not the switches this test process set by importing Bailey either: the Bailey under test sets its own.
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("BAILEY_HOME", "BAILEY_KEYS", "BAILEY_PASSPHRASE", *NATIVE_TELEMETRY_OFF)
    }
    env.update(
        {
            "PYTHONPATH": str(SRC),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "BAILEY_HOME": str(home),
            # The file rather than the keychain: no test writes to a real keychain.
            "BAILEY_KEYS": "file",
            # A Bailey that will not stop is sent SIGABRT (Running.stop), and this makes it write
            # every thread's stack first, into the output Running reads.
            "PYTHONFAULTHANDLER": "1",
        }
    )
    env.update(extra or {})
    return env


def call(base: str, method: str, path: str, body: Any = None, token: str | None = None) -> tuple[int, Any]:
    # Bytes go as they are, as a solution's zip does; anything else as JSON.
    headers = {"Content-Type": "application/zip" if isinstance(body, bytes) else "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    data = body if isinstance(body, bytes) else None if body is None else json.dumps(body).encode("utf-8")
    # Always http://127.0.0.1:<port>/, the app under test.
    request = urllib.request.Request(base + path.lstrip("/"), data=data, method=method, headers=headers)  # noqa: S310
    try:
        with _OPENER.open(request, timeout=30) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        raw, status = error.read(), error.code
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw.decode("utf-8", "replace")


class StopTimedOut(AssertionError):
    """Bailey had not exited in time after it was told to stop. Its message says where it was."""


class Running:
    """One Bailey process, started with ``--no-browser`` and stopped by :meth:`stop`.

    ``home`` is its ``BAILEY_HOME`` as a person writes it, and ``cwd`` the folder it starts
    in, which a relative one is read from. ``start_timeout`` is how long it has to print the
    address it is serving on.
    """

    def __init__(
        self,
        home: Path | str,
        *,
        extra_env: Mapping[str, str] | None = None,
        command: Sequence[str] | None = None,
        cwd: Path | None = None,
        start_timeout: float = 60,
    ) -> None:
        self.process = subprocess.Popen(
            list(command or [sys.executable, "-m", "bailey", "--no-browser"]),
            cwd=cwd,
            env=environment(home, extra_env),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        self.lines: list[str] = []
        self._queue: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        try:
            match = self.wait_for(LAUNCH, timeout=start_timeout)
        except AssertionError as error:
            # Never printing the address is being stuck too: say where, and leave nothing running.
            self._end()
            raise AssertionError(f"{error}\n{self._where()}") from None
        self.address, self.port, self.token = match.group(1), int(match.group(2)), match.group(3)

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self._queue.put(line)
        self._queue.put(None)

    def wait_for(self, pattern: re.Pattern[str], timeout: float = 60) -> re.Match[str]:
        deadline = time.monotonic() + timeout
        while True:
            try:
                line = self._queue.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty as error:
                raise AssertionError(f"Bailey printed no {pattern.pattern!r}: {''.join(self.lines)}") from error
            if line is None:
                raise AssertionError(f"Bailey exited before {pattern.pattern!r}: {''.join(self.lines)}")
            self.lines.append(line)
            match = pattern.search(line)
            if match:
                return match

    def call(self, method: str, path: str, body: Any = None, *, token: object = SESSION) -> tuple[int, Any]:
        """A request with this session's token, or with ``token`` (``None`` for none)."""

        chosen = self.token if token is SESSION else token
        return call(self.address, method, path, body, chosen if isinstance(chosen, str) else None)

    def stop(
        self, timeout: float = STOP_TIMEOUT, *, before_abort: Callable[[int], None] | None = None
    ) -> tuple[int, str]:
        """Terminate it and wait. The exit status and what it printed after starting.

        A Bailey still running ``timeout`` seconds after it was told to stop is stuck, and the
        stop fails, saying where each of its threads was (:meth:`_end`) and leaving it ended.
        ``before_abort`` is handed its process id first, to look at it while it is still stuck.
        """

        if self.process.poll() is None:
            self.process.terminate()
        try:
            code = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if before_abort is not None:
                before_abort(self.process.pid)
            self._end()
            raise StopTimedOut(
                f"Bailey had not exited {timeout:g} seconds after it was told to stop.\n{self._where()}"
            ) from None
        return code, "".join(self._rest())

    def _end(self) -> None:
        """End a Bailey that is still running, its threads' stacks written first where they can be.

        SIGABRT makes ``faulthandler``, which :func:`environment` turns on, write every thread's
        stack into the output this reads, and then ends the process. One still running after
        that is killed, so nothing is left behind. Windows has no SIGABRT to send another
        process, and a process terminated there ends at once, so there it is only killed.
        """

        if self.process.poll() is None and os.name != "nt":
            with contextlib.suppress(ProcessLookupError):
                os.kill(self.process.pid, signal.SIGABRT)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=ABORT_TIMEOUT)
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()

    def _where(self) -> str:
        """What an ended Bailey printed, the stacks ``faulthandler`` wrote among it, with its exit status."""

        self._rest()
        return f"It was ended (exit status {self.process.returncode}). What it printed:\n{''.join(self.lines)}"

    def _rest(self) -> list[str]:
        """What it printed that nothing has read yet, until its output ends or goes quiet."""

        rest = []
        while True:
            try:
                line = self._queue.get(timeout=5)
            except queue.Empty:
                break
            if line is None:
                break
            rest.append(line)
        self.lines.extend(rest)
        return rest


def run_to_exit(home: Path, extra_env: Mapping[str, str] | None = None, *args: str) -> subprocess.CompletedProcess[str]:
    """Run Bailey expecting it to stop by itself, as it does when it cannot start."""

    return subprocess.run(
        [sys.executable, "-m", "bailey", "--no-browser", *args],
        env=environment(home, extra_env),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
