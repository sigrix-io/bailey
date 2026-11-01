"""``bailey`` run as a user runs it: it starts, refuses what did not come from its page, keeps a key across a restart without writing it in the clear, and says why when it cannot start.

These are story S5's *done when* (sigrix-io/sigrix#6563), on every system CI runs:
the app starts on a clean machine, a request without the token is refused, and a key
saved in settings survives a restart without being written to a plain file. They use the
encrypted file, which every machine has; the keychain's own round trip is
``tests/test_real_keychain.py``.
"""

from __future__ import annotations

import base64
import os
import re
import signal
import socket
import sqlite3
from pathlib import Path

import pytest
from running import Running, run_to_exit

from bailey.keys import FILE_NAME, EncryptedFileKeys
from bailey.server import _open_when_ready  # noqa: PLC2701 -- the browser half, driven alone
from bailey.store import MIGRATIONS

KEY = "sk-ant-api03-0123456789abcdefghij-WXYZ"
PASSPHRASE = "correct horse battery"


def test_it_starts_on_a_clean_machine_and_refuses_a_request_without_the_token(tmp_path: Path) -> None:
    home = tmp_path / "home"
    bailey = Running(home)
    try:
        assert home.is_dir(), "the data folder is made at the first start"
        assert bailey.call("GET", "/", token=None)[0] == 200
        status, answer = bailey.call("GET", "/api/settings", token=None)
        assert status == 401 and "did not come from Bailey's page" in answer["error"]
        assert bailey.call("GET", "/api/settings", token="a-guess")[0] == 401
        status, answer = bailey.call("GET", "/api/status")
        assert status == 200 and answer["keys"]["kind"] == "file"
    finally:
        bailey.stop()


def test_a_key_saved_in_settings_survives_a_restart_without_being_written_in_the_clear(tmp_path: Path) -> None:
    home = tmp_path / "home"
    first = Running(home)
    try:
        assert first.call("POST", "/api/keys/passphrase", {"passphrase": PASSPHRASE})[0] == 200
        assert first.call("PUT", "/api/settings", {"provider": "anthropic"})[0] == 200
        status, answer = first.call("PUT", "/api/keys/anthropic", {"key": KEY})
        assert status == 200 and answer["providers"][0]["key"]["saved"] is True
    finally:
        first.stop()

    files = [path for path in home.rglob("*") if path.is_file()]
    assert {path.name for path in files} >= {"bailey.db", FILE_NAME}
    for path in files:
        raw = path.read_bytes()
        for form in (KEY.encode(), base64.b64encode(KEY.encode()), b"0123456789abcdefghij", PASSPHRASE.encode()):
            assert form not in raw, (path.name, form)

    second = Running(home)
    try:
        assert second.token != first.token
        assert second.call("GET", "/api/settings", token=first.token)[0] == 401, "an earlier session's token is spent"
        status, answer = second.call("GET", "/api/settings")
        assert status == 200 and answer["provider"] == "anthropic"
        assert answer["keys"]["locked"] is True
        status, answer = second.call("POST", "/api/keys/unlock", {"passphrase": PASSPHRASE})
        assert status == 200 and answer["providers"][0]["key"] == {"saved": True, "hint": "…WXYZ"}
    finally:
        second.stop()
    reopened = EncryptedFileKeys(home / FILE_NAME)
    reopened.unlock(PASSPHRASE)
    assert reopened.get("provider/anthropic") == KEY


def test_bailey_passphrase_opens_the_file_at_start(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    keys = EncryptedFileKeys(home / FILE_NAME)
    keys.create(PASSPHRASE)
    keys.set("provider/openai", "sk-proj-0123456789abcdef")
    bailey = Running(home, extra_env={"BAILEY_PASSPHRASE": PASSPHRASE})
    try:
        status, answer = bailey.call("GET", "/api/settings")
        assert answer["keys"]["locked"] is False
        assert answer["providers"][1]["key"] == {"saved": True, "hint": "…cdef"}
    finally:
        bailey.stop()


@pytest.mark.skipif(os.name != "posix", reason="Windows ends a process without a signal it can answer")
@pytest.mark.parametrize("stop", ["SIGTERM", "SIGINT"])
def test_a_stop_signal_closes_the_store_and_exits_cleanly(tmp_path: Path, stop: str) -> None:
    home = tmp_path / "home"
    bailey = Running(home)
    assert bailey.call("PUT", "/api/settings", {"provider": "openai"})[0] == 200
    bailey.process.send_signal(getattr(signal, stop))
    code, printed = bailey.stop()
    assert code == 0
    assert "Bailey has stopped." in printed
    assert "Traceback" not in printed
    assert not (home / "bailey.db-wal").exists(), "the store was closed, its log folded back in"


def test_a_wrong_bailey_passphrase_stops_it_with_a_sentence(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    EncryptedFileKeys(home / FILE_NAME).create(PASSPHRASE)
    completed = run_to_exit(home, {"BAILEY_PASSPHRASE": "not the passphrase"})
    assert completed.returncode == 1
    assert f"BAILEY_PASSPHRASE does not open {home / FILE_NAME}" in completed.stdout
    assert "Traceback" not in completed.stdout + completed.stderr


def test_a_data_folder_it_cannot_make_is_said_so(tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder", encoding="utf-8")
    completed = run_to_exit(blocker / "home")
    assert completed.returncode == 1
    assert "Bailey cannot create its data folder" in completed.stdout
    assert "BAILEY_HOME" in completed.stdout


def test_a_store_from_a_newer_bailey_stops_it_with_a_sentence(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    connection = sqlite3.connect(home / "bailey.db")
    connection.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
    connection.close()
    completed = run_to_exit(home)
    assert completed.returncode == 1
    assert "was written by a newer Bailey" in completed.stdout


def test_a_port_already_taken_is_said_so(tmp_path: Path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        completed = run_to_exit(tmp_path / "home", None, "--port", str(port))
    assert completed.returncode == 1
    assert f"Bailey cannot listen on 127.0.0.1:{port}" in completed.stdout


class _Server:
    def __init__(self, started: bool) -> None:
        self.started = started
        self.should_exit = not started


def test_the_browser_opens_on_the_launch_address_once_the_server_is_up(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys

    opened: list[tuple[str, int]] = []
    monkeypatch.setattr("webbrowser.open", lambda url, new=0: opened.append((url, new)) or True)
    _open_when_ready(_Server(started=True), "http://127.0.0.1:1/#token=t", sys.stdout)  # type: ignore[arg-type]
    assert opened == [("http://127.0.0.1:1/#token=t", 2)]

    monkeypatch.setattr("webbrowser.open", lambda url, new=0: False)
    _open_when_ready(_Server(started=True), "http://127.0.0.1:1/#token=t", sys.stdout)  # type: ignore[arg-type]
    assert "No browser could be opened here" in capsys.readouterr().out

    opened.clear()
    monkeypatch.setattr("webbrowser.open", lambda url, new=0: opened.append((url, new)) or True)
    _open_when_ready(_Server(started=False), "http://127.0.0.1:1/#token=t", sys.stdout)  # type: ignore[arg-type]
    assert opened == [], "a server that stopped before it started opens nothing"


def test_the_address_it_prints_names_only_this_computer(tmp_path: Path) -> None:
    bailey = Running(tmp_path / "home")
    try:
        printed = "".join(bailey.lines)
        addresses = re.findall(r"https?://[^/\s]+", printed)
        assert addresses and all(address.startswith("http://127.0.0.1:") for address in addresses)
    finally:
        bailey.stop()


def test_the_server_starts_without_loading_the_agent_core() -> None:
    """PydanticAI and its SDKs take about two seconds to import. The server loads them when
    a message needs them, and warms them in the background once it is up, so a start of
    the app never waits for them."""

    import subprocess
    import sys

    from running import SRC

    probe = "import sys, bailey.server; print(sorted(m for m in ('pydantic_ai', 'anthropic', 'openai') if m in sys.modules))"
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        env={**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.stdout.strip() == "[]", completed.stderr
