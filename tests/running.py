"""Bailey as a user runs it: its own process and data folder, driven over HTTP on loopback."""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[1] / "src"
LAUNCH = re.compile(r"Open it at (http://127\.0\.0\.1:(\d+)/)#token=(\S+)")

#: Stands for "this session's own token" in :meth:`Running.call`.
SESSION = object()

# A request to this computer never goes through a proxy, whatever the environment says.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def environment(home: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("BAILEY_HOME", "BAILEY_KEYS", "BAILEY_PASSPHRASE")
    }
    env.update(
        {
            "PYTHONPATH": str(SRC),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "BAILEY_HOME": str(home),
            # The file rather than the keychain: no test writes to a real keychain.
            "BAILEY_KEYS": "file",
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


class Running:
    """One Bailey process, started with ``--no-browser`` and stopped by :meth:`stop`."""

    def __init__(
        self,
        home: Path,
        *,
        extra_env: Mapping[str, str] | None = None,
        command: Sequence[str] | None = None,
    ) -> None:
        self.process = subprocess.Popen(
            list(command or [sys.executable, "-m", "bailey", "--no-browser"]),
            env=environment(home, extra_env),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        self.lines: list[str] = []
        self._queue: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        match = self.wait_for(LAUNCH)
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

    def stop(self) -> tuple[int, str]:
        """Terminate it and wait. The exit status and what it printed after starting."""

        if self.process.poll() is None:
            self.process.terminate()
        code = self.process.wait(timeout=30)
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
        return code, "".join(rest)


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
