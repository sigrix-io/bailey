"""Open Bailey as a person opens it, and check what they would meet: the release's own proof.

Two ways, each starting the app and reading the address it prints, token included, as a
person reads it::

    python scripts/check_launch.py --command bailey --port 18123 --no-browser
    python scripts/check_launch.py --image sigrix/bailey:0.1.0

Either way it checks, in order:

- the page answers on 127.0.0.1, at the address the app printed;
- a request with the printed token is answered, and reports the version given with
  ``--expect-version``;
- a request without the token is refused, and so is one naming another Host, which is
  what DNS rebinding sends;
- a stop ends it cleanly: it says "Bailey has stopped." and exits 0.

With ``--image`` it runs the image as its README says (``docker run -p 127.0.0.1:...``, a
volume for ``/data``), and checks inside it too: ``node``, ``npx``, ``uv`` and ``uvx``
answer, the search model loads with the network off, and ``bailey doctor`` passes. A clean
runner is a clean machine, so CI runs this against the image it builds, and the release
against the PyPI install and against the published image pulled by name.

Standard library only: it runs on a runner's own ``python3``, before anything is installed.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import queue
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from typing import Any

LAUNCH = re.compile(r"Open it at http://127\.0\.0\.1:(\d+)/#token=([A-Za-z0-9_-]+)")
STOPPED = "Bailey has stopped."
#: Where the image listens, as its Dockerfile's EXPOSE and CMD say.
IMAGE_PORT = 8123
START_SECONDS = 180
STOP_SECONDS = 30

#: What a solution's tools need inside the image, each with the command that proves it answers.
IMAGE_TOOLS = (
    ("node", ["node", "--version"]),
    ("npx", ["npx", "--version"]),
    ("uv", ["uv", "--version"]),
    ("uvx", ["uvx", "--version"]),
)
#: Loads the search model from its folder with the network off, and searches with it once.
MODEL_CHECK = (
    "import os; from pathlib import Path; from bailey.knowledge import LocalEmbedder; "
    "model = LocalEmbedder(Path('/nonexistent'), folder=Path(os.environ['BAILEY_SEARCH_MODEL'])); "
    "assert model.ready(), 'the search model does not load'; "
    "print(model.embed(['a question']).shape)"
)


class CheckFailed(Exception):
    """A check that did not hold, with the sentence that says which."""


def _say(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


class _Process:
    """The app's process, with what it printed read as it prints it."""

    def __init__(self, command: Sequence[str], env: dict[str, str] | None = None) -> None:
        self.command = list(command)
        self.process = subprocess.Popen(  # noqa: S603 -- the command this script was given
            self.command,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.lines: list[str] = []
        self._queue: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        if self.process.stdout is None:  # never: stdout is a pipe, as Popen was asked
            return
        for line in self.process.stdout:
            self._queue.put(line)
        self._queue.put(None)

    def wait_for(self, pattern: re.Pattern[str], seconds: float) -> re.Match[str]:
        deadline = time.monotonic() + seconds
        while True:
            try:
                line = self._queue.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty as error:
                raise CheckFailed(f"It printed no {pattern.pattern!r} in {seconds:.0f} s.\n{self.printed()}") from error
            if line is None:
                raise CheckFailed(f"It stopped before printing {pattern.pattern!r}.\n{self.printed()}")
            self.lines.append(line)
            match = pattern.search(line)
            if match:
                return match

    def finish(self, seconds: float) -> int:
        code = self.process.wait(timeout=seconds)
        try:
            while (line := self._queue.get(timeout=5)) is not None:
                self.lines.append(line)
        except queue.Empty:
            pass
        return code

    def printed(self) -> str:
        return "".join(self.lines).replace(_token_of(self.lines), "<token>") if self.lines else "(nothing)"


def _token_of(lines: Sequence[str]) -> str:
    for line in lines:
        match = LAUNCH.search(line)
        if match:
            return match.group(2)
    return "\0"


def _request(port: int, path: str, *, token: str | None = None, host: str | None = None) -> tuple[int, Any]:
    """A request to 127.0.0.1, never through a proxy, whatever the environment says."""

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        status = response.status
    finally:
        connection.close()
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw.decode("utf-8", "replace")


def check_the_app(port: int, token: str, expected_version: str | None) -> None:
    """The four answers a person's browser, and a page that is not Bailey's, would get."""

    status, page = _request(port, "/")
    if status != 200 or "<title>Bailey</title>" not in str(page):
        raise CheckFailed(f"The page did not answer: {status}.")
    _say("PASS  the page answers at the address it printed")

    status, answer = _request(port, "/api/status", token=token)
    if status != 200 or not isinstance(answer, dict):
        raise CheckFailed(f"A request with the printed token was not answered: {status}.")
    if expected_version and answer.get("version") != expected_version:
        raise CheckFailed(f"It reports version {answer.get('version')!r}, not {expected_version!r}.")
    _say(f"PASS  a request with the printed token is answered, by Bailey {answer.get('version')}")

    status, _ = _request(port, "/api/status")
    if status != 401:
        raise CheckFailed(f"A request without the token was answered {status}, not refused with 401.")
    _say("PASS  a request without the token is refused")

    status, _ = _request(port, "/", host=f"bailey.example:{port}")
    if status != 403:
        raise CheckFailed(f"A request naming another Host was answered {status}, not refused with 403.")
    _say("PASS  a request naming another Host is refused")


def check_command(command: Sequence[str], expected_version: str | None) -> None:
    """Start ``command`` with a data folder of its own, check it, and stop it as a terminal's Ctrl+C would."""

    with tempfile.TemporaryDirectory(prefix="bailey-launch-") as home:
        env = {**os.environ, "BAILEY_HOME": home, "BAILEY_KEYS": "file", "PYTHONUNBUFFERED": "1"}
        for name in ("BAILEY_PASSPHRASE", "BAILEY_CONTAINER"):
            env.pop(name, None)
        app = _Process(command, env)
        try:
            match = app.wait_for(LAUNCH, START_SECONDS)
            check_the_app(int(match.group(1)), match.group(2), expected_version)
            app.process.send_signal(signal.SIGINT)
            code = app.finish(STOP_SECONDS)
        finally:
            if app.process.poll() is None:
                app.process.kill()
        _check_stopped(code, app)


def _docker(*arguments: str, seconds: float = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- docker, with arguments this script spells
        ["docker", *arguments],  # noqa: S607 -- the docker on PATH, as a person's would be
        capture_output=True,
        text=True,
        timeout=seconds,
        check=False,
    )


def check_image(image: str, expected_version: str | None) -> None:
    """Run the image as its README says, check the app and what the image carries, and stop it.

    Its port is published to the same port on 127.0.0.1, as the README's command does and as
    the app asks: the address it prints is only right when the two are the same.
    """

    port = IMAGE_PORT
    name = f"bailey-launch-{secrets.token_hex(4)}"
    volume = f"{name}-data"
    command = ["docker", "run", "--rm", "--name", name, "-p", f"127.0.0.1:{port}:{IMAGE_PORT}"]
    command += ["-v", f"{volume}:/data", image]
    app = _Process(command)
    try:
        match = app.wait_for(LAUNCH, START_SECONDS)
        if int(match.group(1)) != port:
            raise CheckFailed(f"It printed port {match.group(1)}, not {port}, which its Dockerfile exposes.")
        check_the_app(port, match.group(2), expected_version)
        for tool, argv in IMAGE_TOOLS:
            done = _docker("exec", name, *argv)
            if done.returncode != 0:
                raise CheckFailed(f"{tool} does not answer in the image: {done.stderr.strip() or done.stdout.strip()}")
            _say(f"PASS  {tool} answers in the image: {done.stdout.strip().splitlines()[-1]}")
        done = _docker("exec", "--env", "HF_HUB_OFFLINE=1", name, "python", "-c", MODEL_CHECK)
        if done.returncode != 0:
            raise CheckFailed(f"The search model does not load offline in the image:\n{done.stderr.strip()}")
        _say("PASS  the search model loads in the image with the network off")
        done = _docker("exec", name, "bailey", "doctor")
        if done.returncode != 0:
            raise CheckFailed(f"bailey doctor fails in the image:\n{done.stdout.strip()}")
        _say("PASS  bailey doctor passes in the image")
        stopped = _docker("stop", "--time", str(STOP_SECONDS), name, seconds=STOP_SECONDS + 30)
        if stopped.returncode != 0:
            raise CheckFailed(f"docker stop failed: {stopped.stderr.strip()}")
        code = app.finish(STOP_SECONDS)
    finally:
        if app.process.poll() is None:
            _docker("kill", name)
            app.process.kill()
        _docker("volume", "rm", "--force", volume)
    _check_stopped(code, app)


def _check_stopped(code: int, app: _Process) -> None:
    if code != 0 or not any(STOPPED in line for line in app.lines):
        raise CheckFailed(f"It did not stop cleanly: exit {code}.\n{app.printed()}")
    if any("Traceback" in line for line in app.lines):
        raise CheckFailed(f"It printed a traceback.\n{app.printed()}")
    _say("PASS  a stop ends it cleanly")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    way = parser.add_mutually_exclusive_group(required=True)
    way.add_argument("--image", help="an image to run as its README says, such as sigrix/bailey:0.1.0")
    way.add_argument(
        "--command", nargs=argparse.REMAINDER, help="the command that starts the app, and its arguments, last"
    )
    parser.add_argument("--expect-version", help="the version the app must report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.image:
            check_image(args.image, args.expect_version)
        else:
            check_command(args.command, args.expect_version)
    except (CheckFailed, subprocess.TimeoutExpired, OSError) as failure:
        sys.stderr.write(f"FAIL  {failure}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
