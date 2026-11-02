"""``scripts/check_launch.py``, the release's proof that the app opens, run against the checkout's own app.

CI runs it against the image and the release against the PyPI install; this keeps the script
itself honest between releases, in its ``--command`` mode, which needs no Docker. A check
that has never failed proves nothing, so one case asks for a version the app does not report.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

from bailey import __version__

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the script stops the app with a signal, as a terminal does")


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_launch", ROOT / "scripts" / "check_launch.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _command() -> list[str]:
    return ["--command", sys.executable, "-m", "bailey", "--no-browser", "--port", "0"]


def test_the_checkouts_app_passes_every_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    assert _script().main(["--expect-version", __version__, *_command()]) == 0
    printed = capsys.readouterr().out
    for check in (
        "the page answers at the address it printed",
        f"a request with the printed token is answered, by Bailey {__version__}",
        "a request without the token is refused",
        "a request naming another Host is refused",
        "a stop ends it cleanly",
    ):
        assert f"PASS  {check}" in printed


def test_an_app_reporting_another_version_fails_the_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    assert _script().main(["--expect-version", "9.9.9", *_command()]) == 1
    assert f"FAIL  It reports version '{__version__}', not '9.9.9'." in capsys.readouterr().err


def test_a_command_that_never_opens_the_app_fails_the_check(capsys: pytest.CaptureFixture[str]) -> None:
    assert _script().main(["--command", sys.executable, "-c", "print('not Bailey')"]) == 1
    assert "It stopped before printing" in capsys.readouterr().err


#: An app that prints Bailey's launch line and answers like it, except for what ``LAX`` names:
#: ``token`` answers a request without the token, ``host`` one naming another Host, ``page``
#: serves no page, and ``stop`` dies at a stop without saying it stopped.
FAKE_APP = r"""
import http.server, json, os, sys
lax = os.environ["LAX"]
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        host_ok = self.headers.get("Host", "").startswith("127.0.0.1:") or lax == "host"
        token_ok = self.headers.get("Authorization") == "Bearer abc" or lax == "token"
        if not host_ok:
            status, body = 403, b"no"
        elif self.path == "/api/status":
            status, body = (200, json.dumps({"version": "0.1.0"}).encode()) if token_ok else (401, b"{}")
        elif lax == "page":
            status, body = 404, b"none"
        else:
            status, body = 200, b"<title>Bailey</title>"
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
print(f"Open it at http://127.0.0.1:{server.server_port}/#token=abc", flush=True)
try:
    server.serve_forever()
except KeyboardInterrupt:
    if lax == "stop":
        sys.exit(3)
    print("Bailey has stopped.", flush=True)
"""


@pytest.mark.parametrize(
    ("lax", "sentence"),
    [
        ("token", "A request without the token was answered 200, not refused with 401."),
        ("host", "A request naming another Host was answered 200, not refused with 403."),
        ("page", "The page did not answer: 404."),
        ("stop", "It did not stop cleanly: exit 3."),
    ],
)
def test_an_app_that_answers_what_it_should_refuse_fails_the_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], lax: str, sentence: str
) -> None:
    monkeypatch.setenv("LAX", lax)
    assert _script().main(["--command", sys.executable, "-c", FAKE_APP]) == 1
    assert f"FAIL  {sentence}" in capsys.readouterr().err


def test_the_fake_app_passes_when_it_refuses_both(monkeypatch: pytest.MonkeyPatch) -> None:
    """The canary for the two cases above: the stand-in, refusing both, passes every check."""

    monkeypatch.setenv("LAX", "neither")
    assert _script().main(["--expect-version", "0.1.0", "--command", sys.executable, "-c", FAKE_APP]) == 0
