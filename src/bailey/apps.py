"""Apps: a solution's crews, each served by its own Postern runner on loopback (design §3.3).

An app is a Postern bundle in the solution's ``apps/<name>/`` (:class:`bailey.solutions.App`).
Bailey keeps its files in the data folder, and runs it the way the bundle's own README
says a buyer does, with one difference: its own environment.

- **CrewAI is installed per app, never with Bailey.** ``sigrix-runtime[crewai]``
  resolves to 148 packages and pins ``openai`` 2.x and ``mcp`` 1.x, against Bailey's own
  3.x and 2.x. So each app runs in a virtual environment of its own in the data folder,
  built from its ``requirements.txt`` and :data:`RUNTIME_REQUIREMENT`. Apps whose
  requirements are the same share one. Building it downloads about
  :data:`ENVIRONMENT_DOWNLOAD_MB` MB from PyPI, which the page announces and the person
  says yes to first, as with the search model.
- **The runner** is the runtime's own ``main`` (:data:`RUNNER_START`), started from the
  app's folder on a loopback port of its own (:class:`AppHost`). ``PYTHONSAFEPATH`` keeps the pinned
  runtime serving rather than the copy a Sigrix bundle carries. Its environment is
  built here as the tool host builds a server's: the system's few defaults, the proxy and
  certificate settings, the person's provider key and model, CrewAI's telemetry and
  onnxruntime's switched off, and a token of Bailey's that the runner demands of every
  request. It never receives ``SIGRIX_TOKEN`` or ``POSTERN_AGENT_ID``: without them the
  runner checks no licence and looks for no update, so it reaches nothing but the provider.
- **The page reaches it through Bailey** (``bailey.server``), which forwards the four
  verbs with that token, so the page's CSP stays ``connect-src 'self'``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from bailey import NATIVE_TELEMETRY_OFF
from bailey.settings import Connection
from bailey.solutions import App
from bailey.tools import NETWORK_VARIABLES

#: The runner every app's environment gets, pinned as the Sigrix platform pins it.
RUNTIME_REQUIREMENT = "sigrix-runtime==0.1.0"
#: What an environment downloads, in MB: CrewAI's wheels, as the platform measures them.
ENVIRONMENT_DOWNLOAD_MB = 250
#: What it takes on disk once installed, in MB: measured for the fixture's crew
#: (crewai 1.15.33 and sigrix-runtime 0.1.0 on Python 3.13), 902 MB.
ENVIRONMENT_DISK_MB = 900
#: How long a runner may take to start serving, in seconds.
START_TIMEOUT = 60.0

#: How a runner starts: the runtime's own ``main``, as ``python -m sigrix_runtime.postern``
#: runs it, less one step. The standard library's HTTP server names itself after a reverse
#: lookup of the address it has bound, a name nothing in the runner reads, and a resolver
#: that cannot answer for 127.0.0.1 at once holds the runner's port back until it gives up:
#: on GitHub's macOS runners, for more than 30 seconds at every start.
RUNNER_START = (
    "import http.server, socketserver, sys\n"
    "def server_bind(self):\n"
    "    socketserver.TCPServer.server_bind(self)\n"
    "    self.server_name, self.server_port = self.server_address[:2]\n"
    "http.server.HTTPServer.server_bind = server_bind\n"
    "from sigrix_runtime.postern.__main__ import main\n"
    "sys.exit(main())\n"
)

#: CrewAI's telemetry and tracing switches, as ``sigrix_runtime.quiet`` sets them. Bailey
#: exports them too, so a bundle cannot turn them back on from a file of its own; the
#: tests hold the two lists in step.
QUIET = {
    "CREWAI_TRACING_ENABLED": "false",
    "CREWAI_TESTING": "true",
    "CREWAI_DISABLE_TELEMETRY": "true",
    "OTEL_SDK_DISABLED": "true",
    "ANONYMIZED_TELEMETRY": "false",
}

#: The system's own variables a program needs to run, as the MCP SDK passes a server.
SYSTEM_VARIABLES = (
    ("APPDATA", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "PATH", "PATHEXT", "PROCESSOR_ARCHITECTURE")
    + ("SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "USERNAME", "USERPROFILE")
    if sys.platform == "win32"
    else ("HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER", "LANG", "LC_ALL", "TMPDIR")
)

#: The variables a runner configures itself with, which Bailey never hands it.
RUNNER_SETTINGS = ("SIGRIX_TOKEN", "POSTERN_AGENT_ID", "POSTERN_DISTRIBUTOR")


#: Said when a solution with apps arrives, as the tool host says it of tool servers.
APPS_NOTICE = (
    "Each of this solution's apps is a program its seller wrote, which Bailey runs on this computer with your AI "
    "provider's key, in an environment of its own. Bailey does not otherwise limit what an app can do, so install "
    "solutions from people you trust."
)

#: The input an app declares to receive the passages the assistant's documents hold for its
#: run (design §3.7): Bailey fills it in, and the form never shows it.
PASSAGES_INPUT = "passages"

#: Each provider's key, as an app's ``describe`` names the one it runs on.
PROVIDER_KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "openai-compatible": "OPENAI_API_KEY"}
_KEY_PROVIDERS = {"ANTHROPIC_API_KEY": "Anthropic", "OPENAI_API_KEY": "OpenAI"}
_PROVIDER_NAMES = {"anthropic": "Anthropic", "openai": "OpenAI", "openai-compatible": "a local server"}


class AppFailed(Exception):
    """An app that cannot be prepared or started, with the sentence that says why."""


# ---------------------------------------------------------------------------
# Where an app's files live
# ---------------------------------------------------------------------------


def apps_folder(data_folder: Path, assistant_id: str) -> Path:
    return data_folder / "apps" / assistant_id


def app_folder(data_folder: Path, assistant_id: str, name: str) -> Path:
    return apps_folder(data_folder, assistant_id) / name


def write_apps(data_folder: Path, assistant_id: str, apps: tuple[App, ...]) -> None:
    """The apps' files, in their folders; whatever an earlier import left there is replaced."""

    root = apps_folder(data_folder, assistant_id)
    shutil.rmtree(root, ignore_errors=True)
    for app in apps:
        folder = root / app.name
        for path, data in app.files.items():
            target = folder.joinpath(*path.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def read_app_files(data_folder: Path, assistant_id: str, name: str) -> dict[str, bytes]:
    """An app's files as the import wrote them, for an export; what its runs wrote is left out."""

    folder = app_folder(data_folder, assistant_id, name)
    if not folder.is_dir():
        return {}
    files: dict[str, bytes] = {}
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder).as_posix()
        if path.is_file() and not relative.startswith(("workspace/", ".")) and "__pycache__" not in relative:
            files[relative] = path.read_bytes()
    return files


def remove_apps(data_folder: Path, assistant_id: str) -> None:
    shutil.rmtree(apps_folder(data_folder, assistant_id), ignore_errors=True)


def requirements_of(folder: Path) -> bytes:
    """What the app's environment installs: its own ``requirements.txt``, and the runner."""

    own = (folder / "requirements.txt").read_bytes() if (folder / "requirements.txt").is_file() else b""
    return own.rstrip() + f"\n{RUNTIME_REQUIREMENT}\n".encode()


def describe_of(folder: Path) -> dict[str, Any]:
    """The app's own ``describe`` document (``postern.json``), or an empty one when it carries none."""

    try:
        document = json.loads((folder / "postern.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def passages_input(describe: Mapping[str, Any]) -> dict[str, Any] | None:
    """The text input keyed ``passages``, when the app declares one."""

    for declared in describe.get("inputs") or []:
        if isinstance(declared, dict) and declared.get("key") == PASSAGES_INPUT and declared.get("type") == "text":
            return declared
    return None


def provider_note(describe: Mapping[str, Any], provider: str) -> str:
    """Why the person's provider cannot run this app, or nothing when it can.

    An app's ``describe`` names the key it runs on; Bailey gives it the person's own, under
    their provider's name, and no other.
    """

    mine = PROVIDER_KEYS.get(provider)
    for credential in describe.get("credentials") or []:
        name = credential.get("env") if isinstance(credential, dict) else None
        if name in _KEY_PROVIDERS and name != mine:
            other = _KEY_PROVIDERS[str(name)]
            choices = f"{other} or a local server" if name == "OPENAI_API_KEY" else other
            return (
                f"This app runs on {other}'s key ({name}), and your provider is "
                f"{_PROVIDER_NAMES.get(provider, 'not chosen yet')}. Choose {choices} in Settings to run it."
            )
    return ""


# ---------------------------------------------------------------------------
# Environments
# ---------------------------------------------------------------------------


class Environments(Protocol):
    """Where an app's Python comes from: an environment with its requirements installed."""

    def state(self, requirements: bytes) -> tuple[str, str]: ...

    def python(self, requirements: bytes) -> Path: ...

    def prepare(self, requirements: bytes) -> None: ...

    def variables(self) -> dict[str, str]: ...


class VirtualEnvironments:
    """One virtual environment per distinct set of requirements, under ``root``.

    It is built in the background once the person says yes, with ``uv`` when Bailey finds
    it and with ``venv`` and pip otherwise; ``.ready`` marks one that finished.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._building: dict[str, threading.Thread] = {}
        self._failed: dict[str, str] = {}
        self._lock = threading.Lock()

    def _folder(self, requirements: bytes) -> Path:
        return self.root / hashlib.sha256(requirements).hexdigest()[:16]

    def python(self, requirements: bytes) -> Path:
        folder = self._folder(requirements)
        return folder / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    def state(self, requirements: bytes) -> tuple[str, str]:
        """``missing``, ``preparing``, ``ready`` or ``failed``, and a sentence for a failure."""

        key = self._folder(requirements).name
        with self._lock:
            if key in self._building and self._building[key].is_alive():
                return "preparing", ""
            if key in self._failed:
                return "failed", self._failed[key]
        if (self._folder(requirements) / ".ready").is_file() and self.python(requirements).is_file():
            return "ready", ""
        return "missing", ""

    def prepare(self, requirements: bytes) -> None:
        """Start building the environment, unless it is built or being built."""

        key = self._folder(requirements).name
        with self._lock:
            if key in self._building and self._building[key].is_alive():
                return
            self._failed.pop(key, None)
            thread = threading.Thread(target=self._build, args=(requirements,), daemon=True)
            self._building[key] = thread
        thread.start()

    def _build(self, requirements: bytes) -> None:
        folder = self._folder(requirements)
        try:
            shutil.rmtree(folder, ignore_errors=True)
            self.root.mkdir(parents=True, exist_ok=True)
            listed = folder / "requirements.txt"
            python = self.python(requirements)
            uv = shutil.which("uv")
            if uv:
                create = [uv, "venv", "--python", sys.executable, str(folder)]
                install = [uv, "pip", "install", "--python", str(python), "-r", str(listed)]
            else:
                create = [sys.executable, "-m", "venv", str(folder)]
                install = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(listed)]
            # The environment first, then its list: uv makes a venv only where nothing is yet.
            _install_step(create)
            listed.write_bytes(requirements)
            _install_step(install)
            (folder / ".ready").write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), encoding="utf-8")
        except (AppFailed, OSError, subprocess.SubprocessError) as error:
            with self._lock:
                self._failed[folder.name] = str(error) if isinstance(error, AppFailed) else type(error).__name__

    def variables(self) -> dict[str, str]:
        return {}


def _install_step(command: list[str]) -> None:
    done = subprocess.run(command, capture_output=True, text=True, timeout=1800)  # noqa: S603 - fixed argv
    if done.returncode != 0:
        last = (done.stderr or done.stdout).strip().splitlines()[-3:]
        raise AppFailed(f"Installing the app's libraries failed: {' '.join(last) or 'no reason given'}")


# ---------------------------------------------------------------------------
# The runner's environment
# ---------------------------------------------------------------------------


def model_variables(connection: Connection, base_urls: Mapping[str, str]) -> dict[str, str]:
    """The person's provider, key and model, as CrewAI reads them."""

    variables: dict[str, str] = {}
    if connection.provider == "anthropic":
        variables |= {"ANTHROPIC_API_KEY": connection.api_key, "MODEL": f"anthropic/{connection.model}"}
        if base_urls.get("anthropic"):
            variables["ANTHROPIC_BASE_URL"] = base_urls["anthropic"]
    elif connection.provider == "openai":
        variables |= {"OPENAI_API_KEY": connection.api_key, "MODEL": connection.model}
        if base_urls.get("openai"):
            variables["OPENAI_BASE_URL"] = variables["OPENAI_API_BASE"] = base_urls["openai"]
    else:
        # A local server takes any key; CrewAI's OpenAI client wants one set.
        address = connection.base_url or ""
        variables |= {
            "OPENAI_API_KEY": connection.api_key or "not-needed",
            "OPENAI_BASE_URL": address,
            "OPENAI_API_BASE": address,
            "MODEL": f"openai/{connection.model}",
        }
    variables["OPENAI_MODEL_NAME"] = variables["MODEL"]
    return variables


def runner_environment(
    connection: Connection,
    *,
    inbound_token: str,
    base_urls: Mapping[str, str],
    environ: Mapping[str, str],
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Everything the runner, and the crew it runs, may see."""

    env = {name: environ[name] for name in (*SYSTEM_VARIABLES, *NETWORK_VARIABLES) if environ.get(name)}
    env |= model_variables(connection, base_urls)
    env |= QUIET
    # A crew's libraries may load onnxruntime, whose telemetry no audit hook sees (bailey.NATIVE_TELEMETRY_OFF).
    env |= NATIVE_TELEMETRY_OFF
    env |= {
        # The pinned runtime serves, never the copy in the bundle's folder.
        "PYTHONSAFEPATH": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "POSTERN_INBOUND_TOKEN": inbound_token,
        "POSTERN_PULL": "0",
    }
    env |= dict(extra or {})
    for name in RUNNER_SETTINGS:
        env.pop(name, None)
    return env


# ---------------------------------------------------------------------------
# The host
# ---------------------------------------------------------------------------


@dataclass
class Runner:
    """One app's runner: where it answers, the token it demands, and its process."""

    port: int
    token: str
    signature: str
    process: subprocess.Popen[str] = field(repr=False)
    errors: list[str] = field(default_factory=list, repr=False)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def alive(self) -> bool:
        return self.process.poll() is None


def _signature(folder: Path, python: Path, connection: Connection, base_urls: Mapping[str, str]) -> str:
    seen = repr(
        (
            str(folder),
            str(python),
            connection.provider,
            connection.model,
            connection.base_url,
            sorted(base_urls.items()),
        )
    )
    return hashlib.sha256((seen + connection.api_key).encode()).hexdigest()


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


class AppHost:
    """Each open app's runner, started when its page opens and stopped with it, its assistant and Bailey."""

    def __init__(self, environments: Environments, *, start_timeout: float = START_TIMEOUT) -> None:
        self.environments = environments
        self.start_timeout = start_timeout
        self._running: dict[tuple[str, str], Runner] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def running(self, assistant_id: str, name: str) -> bool:
        runner = self._running.get((assistant_id, name))
        return runner is not None and runner.alive()

    async def runner(
        self, assistant_id: str, name: str, folder: Path, connection: Connection, base_urls: Mapping[str, str]
    ) -> Runner:
        """The app's runner, started now if it is not running, or if the person's key or model changed."""

        key = (assistant_id, name)
        async with self._locks.setdefault(key, asyncio.Lock()):
            requirements = requirements_of(folder)
            state, why = self.environments.state(requirements)
            if state != "ready":
                raise AppFailed(why or "This app's libraries are not installed yet.")
            python = self.environments.python(requirements)
            signature = _signature(folder, python, connection, base_urls)
            running = self._running.get(key)
            if running is not None and running.alive() and running.signature == signature:
                return running
            if running is not None:
                await asyncio.to_thread(_stop, running.process)
                self._running.pop(key, None)
            started = await asyncio.to_thread(self._start, folder, python, connection, base_urls, signature)
            self._running[key] = started
            return started

    def _start(
        self, folder: Path, python: Path, connection: Connection, base_urls: Mapping[str, str], signature: str
    ) -> Runner:
        token = secrets.token_urlsafe(32)
        env = runner_environment(
            connection,
            inbound_token=token,
            base_urls=base_urls,
            environ=os.environ,
            extra=self.environments.variables(),
        )
        command = [str(python), "-c", RUNNER_START, "--bundle", str(folder)]
        command += ["--host", "127.0.0.1", "--port", "0", "--no-pull", "--quiet"]
        try:
            process = subprocess.Popen(  # noqa: S603 - the app's own interpreter, a fixed argv
                command,
                cwd=str(folder),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as error:
            raise AppFailed(f"The app's runner could not start: {error.strerror or type(error).__name__}.") from error
        errors: list[str] = []
        if process.stderr is None or process.stdout is None:  # pragma: no cover - both are pipes above
            _stop(process)
            raise AppFailed("The app's runner could not start: its output could not be read.")
        threading.Thread(target=_keep_tail, args=(process.stderr, errors), daemon=True).start()
        port = _read_port(process, self.start_timeout)
        if port is None:
            _stop(process)
            said = " ".join(errors[-3:]).strip()
            raise AppFailed(f"The app's runner did not start{': ' + said if said else ''}.")
        return Runner(port=port, token=token, signature=signature, process=process, errors=errors)

    async def stop(self, assistant_id: str, name: str | None = None) -> None:
        """Stop an app's runner, or every one of the assistant's."""

        for key in [key for key in self._running if key[0] == assistant_id and (name is None or key[1] == name)]:
            runner = self._running.pop(key)
            await asyncio.to_thread(_stop, runner.process)

    async def close(self) -> None:
        for key in list(self._running):
            runner = self._running.pop(key)
            await asyncio.to_thread(_stop, runner.process)


def _keep_tail(stream: object, lines: list[str]) -> None:
    for line in stream:  # type: ignore[attr-defined]
        lines.append(str(line).strip())
        del lines[:-20]


def _read_port(process: subprocess.Popen[str], timeout: float) -> int | None:
    """The port from the runner's first line, ``POSTERN_PORT=<n>`` (Postern §2), within ``timeout``."""

    found: list[int] = []
    announced = threading.Event()

    def read() -> None:
        # Read to the end, past the port: a pipe nobody drains would stop the runner.
        for line in process.stdout or ():
            if not found and line.startswith("POSTERN_PORT="):
                try:
                    found.append(int(line.split("=", 1)[1]))
                except ValueError:
                    pass
                announced.set()
        announced.set()

    threading.Thread(target=read, daemon=True).start()
    announced.wait(timeout)
    return found[0] if found else None
