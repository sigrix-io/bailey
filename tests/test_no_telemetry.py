"""No telemetry: the app, run as a user runs it, connects to nothing but this computer.

Python's audit hooks see every connection a process opens and every name it looks up,
whatever library asks. The app runs under a hook that writes each one down, every route
it serves is exercised, and the test fails on any address that is not loopback. Story S6
adds the one exception the rule allows, the provider the user chose; until then there is
none. A canary run plants an outbound connection first, so a recorder that records
nothing cannot pass for an app that sends nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from running import Running

PASSPHRASE = "correct horse battery"

_RECORDER = """
import json, sys

log = open(sys.argv[1], "a", buffering=1, encoding="utf-8")


def hook(event, args):
    if event == "socket.connect":
        address = args[1]
        if isinstance(address, tuple):
            log.write(json.dumps({"event": event, "host": str(address[0]), "port": address[1]}) + "\\n")
        else:
            log.write(json.dumps({"event": event, "local": str(address)}) + "\\n")
    elif event == "socket.getaddrinfo":
        host = args[0]
        log.write(json.dumps({"event": event, "host": host.decode() if isinstance(host, bytes) else host}) + "\\n")


sys.addaudithook(hook)
if sys.argv[2] == "canary":
    import socket

    try:
        socket.create_connection(("192.0.2.1", 9), timeout=0.05).close()  # TEST-NET-1: routes nowhere
    except OSError:
        pass

from bailey.cli import main

raise SystemExit(main(["--no-browser"]))
"""


def _is_loopback(host: object) -> bool:
    return host in (None, "localhost", "::1") or (isinstance(host, str) and host.startswith(("127.", "::ffff:127.")))


def outbound(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The events that name somewhere other than this computer. A local socket (D-Bus) is not one."""

    return [event for event in events if "host" in event and not _is_loopback(event["host"])]


def _run(tmp_path: Path, mode: str) -> list[dict[str, Any]]:
    recorder = tmp_path / "recorder.py"
    recorder.write_text(_RECORDER, encoding="utf-8")
    log = tmp_path / f"{mode}.jsonl"
    bailey = Running(
        tmp_path / f"home-{mode}",
        extra_env={"BAILEY_PASSPHRASE": PASSPHRASE},
        command=[sys.executable, str(recorder), str(log), mode],
    )
    try:
        exercised = [
            bailey.call("GET", "/", token=None),
            bailey.call("GET", "/static/app.js", token=None),
            bailey.call("GET", "/static/app.css", token=None),
            bailey.call("GET", "/static/icon.svg", token=None),
            bailey.call("GET", "/api/status"),
            bailey.call("GET", "/api/settings"),
            bailey.call("PUT", "/api/settings", {"provider": "openai-compatible", "address": "http://localhost:1/v1"}),
            bailey.call("PUT", "/api/keys/anthropic", {"key": "sk-ant-0123456789abcdef"}),
            bailey.call("DELETE", "/api/keys/anthropic"),
            bailey.call("POST", "/api/keys/unlock", {"passphrase": PASSPHRASE}),
            bailey.call("GET", "/api/settings", token=None),
        ]
        assert [status for status, _ in exercised] == [200] * 10 + [401]
    finally:
        bailey.stop()
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_the_recorder_catches_a_planted_outbound_connection(tmp_path: Path) -> None:
    caught = outbound(_run(tmp_path, "canary"))
    assert {"event": "socket.connect", "host": "192.0.2.1", "port": 9} in caught


def test_the_app_connects_to_nothing_but_this_computer(tmp_path: Path) -> None:
    events = _run(tmp_path, "app")
    assert outbound(events) == []
