"""Apps: a solution's crew, run by the real Postern runner and reached through Bailey's proxy (story S9).

Story S9's *done when* (sigrix-io/sigrix#6567): a solution with one crew app shows its
form, runs it on the user's key, and shows its steps as they happen. The fixture
solution, ``tests/fixtures/weekly-digest``, carries one crew app. Everything here is
real but CrewAI and the app's environment: the runner is ``sigrix_runtime.postern``,
started by Bailey's app host, and a run goes through the runtime's execution layer and
loader to ``tests/stand_ins/crewai``, which sends each task to the provider emulator
with the key in its environment. The environment is this test's own Python, with the
stand-in on its path, in place of one built from PyPI.
"""

from __future__ import annotations

import io
import json
import os
import socket
import sys
import threading
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import emulated_providers as emulator
import httpx
import pytest
import uvicorn
from app_environments import ready_environment
from fakes import FakeKeychain, UnreadableKeychain
from running import Running
from starlette.testclient import TestClient

from bailey import NATIVE_TELEMETRY_OFF
from bailey.apps import QUIET, RUNTIME_REQUIREMENT, app_folder, requirements_of, runner_environment
from bailey.keys import FILE_NAME, EncryptedFileKeys, KeychainKeys
from bailey.server import AppState, build_app
from bailey.settings import Connection
from bailey.solutions import read_solution, zip_solution
from bailey.store import Store

TOKEN = "s" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}
KEY = "sk-user-key-for-the-crew-1234"
FIXTURE = Path(__file__).parent / "fixtures" / "weekly-digest"
STAND_INS = Path(__file__).parent / "stand_ins"
NOTES = "New this week: The Lighthouse Keeper by Mara Quinn. Author reading on Friday at six."


class HereEnvironments:
    """An app's environment as this test's own Python, with the stand-in CrewAI on its path."""

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.prepared: list[bytes] = []
        self.extra: dict[str, str] = {}

    def state(self, requirements: bytes) -> tuple[str, str]:
        if self.prepared and not self.ready:
            return "preparing", ""
        return ("ready", "") if self.ready else ("missing", "")

    def python(self, requirements: bytes) -> Path:
        return Path(sys.executable)

    def prepare(self, requirements: bytes) -> None:
        self.prepared.append(requirements)

    def variables(self) -> dict[str, str]:
        return {"PYTHONPATH": str(STAND_INS), **self.extra}


@pytest.fixture(scope="module")
def provider_base() -> str:
    return emulator.serve_in_background()


@pytest.fixture(autouse=True)
def _fresh_requests() -> Iterator[None]:
    emulator.REQUESTS.clear()
    yield
    emulator.REQUESTS.clear()


@pytest.fixture
def environments() -> HereEnvironments:
    return HereEnvironments()


@pytest.fixture
def state(tmp_path: Path, provider_base: str, environments: HereEnvironments) -> Iterator[AppState]:
    store = Store(tmp_path / "bailey.db")
    yield AppState(
        token=TOKEN,
        store=store,
        keys=KeychainKeys(FakeKeychain()),  # type: ignore[arg-type]
        data_folder=tmp_path,
        base_urls={"openai": provider_base + "/openai/v1", "anthropic": provider_base + "/anthropic"},
        environments=environments,
    )
    store.close()


@pytest.fixture
def client(state: AppState) -> Iterator[TestClient]:
    with TestClient(build_app(state), base_url="http://127.0.0.1:8123") as made:
        made.app_state = state  # type: ignore[attr-defined]
        yield made


def _fixture_files(**extra: bytes) -> dict[str, bytes]:
    files = {path.relative_to(FIXTURE).as_posix(): path.read_bytes() for path in FIXTURE.rglob("*") if path.is_file()}
    return {**files, **extra}


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _ready(
    client: TestClient, provider: str = "openai", files: dict[str, bytes] | None = None, address: str = ""
) -> dict[str, Any]:
    settings: dict[str, Any] = {"provider": provider}
    if provider == "openai-compatible":
        settings |= {"address": address, "model": "qwen3:8b"}
    assert client.put("/api/settings", headers=AUTH, json=settings).status_code == 200
    if provider in ("openai", "anthropic"):
        assert client.put(f"/api/keys/{provider}", headers=AUTH, json={"key": KEY}).status_code == 200
    imported = client.post("/api/assistants/import", headers=AUTH, content=_zip(files or _fixture_files()))
    assert imported.status_code == 200, imported.text
    return dict(imported.json())


def _app(assistant_id: str) -> str:
    return f"/api/assistants/{assistant_id}/apps/digest"


def _events(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    name = ""
    for line in text.splitlines():
        if line.startswith("event: "):
            name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            events.append((name, json.loads(line.removeprefix("data: "))))
    return events


# ---------------------------------------------------------------------------
# Importing a solution with an app
# ---------------------------------------------------------------------------


def test_an_imported_solution_keeps_its_app_and_says_what_an_app_is(client: TestClient, state: AppState) -> None:
    # A .env giving a key a value is refused (tests/test_install.py); one with a listing's id
    # and a blank key is only left out.
    env = b"POSTERN_AGENT_ID=sigrix/weekly-digest\nOPENAI_API_KEY=\n"
    imported = _ready(client, files=_fixture_files(**{"apps/digest/.env": env}))
    assert "a program its seller wrote" in imported["notice"]
    assert "Left out of its apps: apps/digest/.env" in imported["notice"]
    folder = app_folder(state.data_folder, imported["id"], "digest")
    assert (folder / "config" / "tasks.yaml").read_bytes() == (FIXTURE / "apps/digest/config/tasks.yaml").read_bytes()
    assert not (folder / ".env").exists(), "a seller's .env never reaches the app's folder"
    panel = client.get(f"/api/assistants/{imported['id']}/apps", headers=AUTH).json()
    (app,) = panel["apps"]
    assert (app["name"], app["title"], app["environment"]) == ("digest", "Digest", "ready")
    assert app["summary"] == "Turns the week's notes into the shop's newsletter."
    assert app["ui"]["run_label"] == "Write the digest", "the page lays the form out from the seller's ui.json"
    assert (panel["download_mb"], panel["disk_mb"]) == (250, 900) and app["provider_note"] == ""


def test_the_export_writes_the_app_back_and_imports_again(client: TestClient, state: AppState) -> None:
    imported = _ready(client)
    client.post(f"{_app(imported['id'])}/postern/v0/run", headers=AUTH, json={"inputs": {"prompt": NOTES}})
    folder = app_folder(state.data_folder, imported["id"], "digest")
    assert (folder / "workspace").is_dir(), "the canary: a run made its workspace"
    # What a crew and its runner leave behind: an output file, and the trace of a failed run.
    (folder / "workspace" / "digest.md").write_text("A run's output.", encoding="utf-8")
    (folder / ".last_error").write_text("Traceback", encoding="utf-8")
    exported = client.get(f"/api/assistants/{imported['id']}/export", headers=AUTH)
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        # The export wraps the solution in a folder of its name, which an import takes off.
        names = {name.removeprefix("weekly-digest/") for name in archive.namelist()}
        postern = archive.read("weekly-digest/apps/digest/postern.json")
    assert postern == (FIXTURE / "apps/digest/postern.json").read_bytes()
    assert {f"apps/digest/{path}" for path in ("ui.json", "requirements.txt", "config/agents.yaml")} <= names
    assert not any("workspace/" in name or name.endswith(".last_error") for name in names), (
        "what a run wrote stays out of the package"
    )
    again = client.post("/api/assistants/import", headers=AUTH, content=exported.content)
    assert again.status_code == 200 and [app.name for app in read_solution(_fixture_files()).apps] == ["digest"]


def test_deleting_the_assistant_stops_its_runner_and_removes_its_app(client: TestClient, state: AppState) -> None:
    imported = _ready(client)
    assert client.get(f"{_app(imported['id'])}/postern/v0/status", headers=AUTH).status_code == 200
    assert state.app_host is not None and state.app_host.running(imported["id"], "digest")
    assert client.delete(f"/api/assistants/{imported['id']}", headers=AUTH).status_code == 200
    assert not state.app_host.running(imported["id"], "digest")
    assert not app_folder(state.data_folder, imported["id"], "digest").exists()


# ---------------------------------------------------------------------------
# Done when: the form, a run on the person's key, its steps as they happen
# ---------------------------------------------------------------------------


def test_the_form_comes_from_the_apps_describe_through_bailey(client: TestClient) -> None:
    imported = _ready(client)
    describe = client.get(f"{_app(imported['id'])}/postern/v0/describe", headers=AUTH)
    assert describe.status_code == 200
    assert describe.json()["agent"]["name"] == "Digest"
    assert [item["key"] for item in describe.json()["inputs"]] == ["prompt"], "the passages input is Bailey's to fill"
    status = client.get(f"{_app(imported['id'])}/postern/v0/status", headers=AUTH).json()
    assert status["state"] == "ready" and status["entitlement"]["state"] == "not_required"
    assert status["update"]["state"] == "not_required", "no update check, so no call to a distributor"
    assert client.get(f"{_app(imported['id'])}/postern/v0/describe").status_code == 401, (
        "the session's token, as anywhere"
    )
    host = client.app_state.app_host  # type: ignore[attr-defined]
    runner = host._running[(imported["id"], "digest")]
    assert httpx.get(f"{runner.base}/postern/v0/describe").status_code == 401, "the runner answers only Bailey"


#: A machine that cannot look up its own address: ``socket.getfqdn`` raises, in every Python
#: started with this folder first on its path.
NO_REVERSE_LOOKUP = """
import socket


def getfqdn(name=""):
    raise OSError("this machine cannot look up " + repr(name))


socket.getfqdn = getfqdn
"""


def test_a_runner_starts_without_looking_up_its_own_address(
    client: TestClient, environments: HereEnvironments, tmp_path: Path
) -> None:
    """The standard library's HTTP server names itself after a reverse lookup of 127.0.0.1,
    which took more than 30 seconds at every start on GitHub's macOS runners."""

    resolver = tmp_path / "resolver"
    resolver.mkdir()
    (resolver / "sitecustomize.py").write_text(NO_REVERSE_LOOKUP, encoding="utf-8")
    environments.extra = {"PYTHONPATH": os.pathsep.join([str(resolver), str(STAND_INS)])}
    imported = _ready(client)
    describe = client.get(f"{_app(imported['id'])}/postern/v0/describe", headers=AUTH)
    assert describe.status_code == 200, describe.text
    run = client.post(f"{_app(imported['id'])}/postern/v0/run", headers=AUTH, json={"inputs": {"prompt": NOTES}})
    assert run.status_code == 200, run.text
    assert run.json()["output"]["value"].startswith("Newsletter Writer did:")


@pytest.mark.parametrize("provider", ["openai", "openai-compatible"])
def test_a_run_streams_its_steps_and_runs_on_the_persons_key(
    client: TestClient, provider: str, provider_base: str
) -> None:
    imported = _ready(client, provider=provider, address=provider_base + "/local/v1")
    streamed = client.post(
        f"{_app(imported['id'])}/postern/v0/stream", headers=AUTH, json={"inputs": {"prompt": NOTES}}
    )
    assert streamed.status_code == 200, streamed.text
    events = _events(streamed.text)
    assert [name for name, _ in events] == ["start", "step", "step", "done"]
    assert [payload["name"] for name, payload in events if name == "step"] == ["pick_highlights", "write_newsletter"]
    output = events[-1][1]["output"]["value"]
    assert output.startswith("Newsletter Writer did:"), output
    sent = [request for request in emulator.REQUESTS if request["path"].endswith("/chat/completions")]
    assert len(sent) == 2, "one request per task"
    expected = KEY if provider == "openai" else "not-needed"
    assert {request["key"] for request in sent} == {expected}, "every task ran on the person's own key"
    assert NOTES in json.dumps(sent[0]["body"]), "the brief reached the first task"


def test_a_run_answers_whole_through_run_too(client: TestClient) -> None:
    imported = _ready(client)
    answered = client.post(f"{_app(imported['id'])}/postern/v0/run", headers=AUTH, json={"inputs": {"prompt": NOTES}})
    assert answered.status_code == 200
    assert answered.json()["output"]["value"].startswith("Newsletter Writer did:")
    assert [step["name"] for step in answered.json()["usage"]["steps"]] == ["pick_highlights", "write_newsletter"]


def test_the_passages_the_documents_hold_reach_the_app(client: TestClient) -> None:
    imported = _ready(client)
    policy = b"# Shop policies\n\n## Events\n\nAuthor readings are free; booking is not needed."
    added = client.post(f"/api/assistants/{imported['id']}/knowledge?name=policies.md", headers=AUTH, content=policy)
    assert added.status_code == 200, added.text
    client.post(f"{_app(imported['id'])}/postern/v0/run", headers=AUTH, json={"inputs": {"prompt": NOTES}})
    first = json.dumps(emulator.REQUESTS[0]["body"])
    assert "Author readings are free" in first and "[P" in first, "the passages arrive numbered, as in chat"


def test_another_providers_key_is_named_first_and_refused_by_the_runner(client: TestClient) -> None:
    imported = _ready(client, provider="anthropic")
    (app,) = client.get(f"/api/assistants/{imported['id']}/apps", headers=AUTH).json()["apps"]
    assert app["provider_note"].startswith("This app runs on OpenAI's key (OPENAI_API_KEY)")
    refused = client.post(f"{_app(imported['id'])}/postern/v0/run", headers=AUTH, json={"inputs": {"prompt": NOTES}})
    assert refused.status_code == 424 and refused.json()["error"]["code"] == "missing_credential"
    assert emulator.REQUESTS == [], "nothing was sent anywhere"


def test_an_app_without_its_environment_says_so_and_is_prepared_on_request(
    client: TestClient, environments: HereEnvironments
) -> None:
    environments.ready = False
    imported = _ready(client)
    describe = client.get(f"{_app(imported['id'])}/postern/v0/describe", headers=AUTH)
    assert describe.status_code == 503 and "not installed yet" in describe.json()["error"]["message"]
    panel = client.post(f"{_app(imported['id'])}/environment", headers=AUTH).json()
    assert panel["apps"][0]["environment"] == "preparing"
    (asked,) = environments.prepared
    assert asked.startswith((FIXTURE / "apps/digest/requirements.txt").read_bytes().rstrip())
    assert asked.decode().strip().endswith(RUNTIME_REQUIREMENT), "the pinned runner joins the app's own requirements"


def _asked_with(state: AppState, keys: EncryptedFileKeys | KeychainKeys, method: str, verb: str, address: str) -> Any:
    """One of the app's verbs, asked once it is installed on a local server, with ``keys`` in place.

    What the app raises comes back as the answer a browser would get, not as an exception.
    """

    with TestClient(build_app(state), base_url="http://127.0.0.1:8123", raise_server_exceptions=False) as client:
        imported = _ready(client, provider="openai-compatible", address=address)
        state.keys = keys
        body = {"inputs": {"prompt": NOTES}} if method == "POST" else None
        return client.request(method, f"{_app(imported['id'])}/postern/v0/{verb}", headers=AUTH, json=body)


@pytest.mark.parametrize(("method", "verb"), [("GET", "describe"), ("GET", "status"), ("POST", "run")])
def test_an_app_opened_while_the_key_file_is_locked_says_to_unlock_it(
    state: AppState, provider_base: str, tmp_path: Path, method: str, verb: str
) -> None:
    """As every other route says it, in Postern's envelope. It answered a bare 500, which the page
    could only call "not JSON", though a local server needs no key."""

    EncryptedFileKeys(tmp_path / FILE_NAME).create("correct horse battery")
    locked = EncryptedFileKeys(tmp_path / FILE_NAME)
    answered = _asked_with(state, locked, method, verb, provider_base + "/local/v1")
    assert answered.status_code == 423, answered.text
    assert answered.json() == {
        "error": {"code": "unavailable", "message": "Unlock your keys with their passphrase first."}
    }
    assert emulator.REQUESTS == [], "nothing was sent anywhere"


def test_an_app_opened_while_the_keychain_refuses_says_so(state: AppState, provider_base: str) -> None:
    refusing = KeychainKeys(UnreadableKeychain())  # type: ignore[arg-type]
    answered = _asked_with(state, refusing, "GET", "describe", provider_base + "/local/v1")
    assert answered.status_code == 503, answered.text
    assert answered.json()["error"]["message"] == "The keychain refused: The user cancelled the keychain prompt."


# ---------------------------------------------------------------------------
# What the runner may see
# ---------------------------------------------------------------------------


def test_the_runner_sees_the_persons_key_and_none_of_baileys_own_settings() -> None:
    environ = {
        "PATH": "/usr/bin",
        "HOME": "/home/someone",
        "HTTPS_PROXY": "http://proxy.example:3128",
        "SIGRIX_TOKEN": "sgx-account-token",
        "POSTERN_AGENT_ID": "acme/listing",
        "ANTHROPIC_API_KEY": "sk-ant-in-baileys-own-environment",
        "BAILEY_PASSPHRASE": "decoy-secret",
    }
    connection = Connection(provider="openai", model="gpt-5-mini", api_key=KEY, base_url="https://api.openai.com/v1")
    env = runner_environment(connection, inbound_token="runner-token", base_urls={}, environ=environ)
    assert env["OPENAI_API_KEY"] == KEY and env["MODEL"] == env["OPENAI_MODEL_NAME"] == "gpt-5-mini"
    assert env["POSTERN_INBOUND_TOKEN"] == "runner-token" and env["PYTHONSAFEPATH"] == "1"
    assert env["HTTPS_PROXY"] == "http://proxy.example:3128" and env["PATH"] == "/usr/bin"
    for absent in ("SIGRIX_TOKEN", "POSTERN_AGENT_ID", "ANTHROPIC_API_KEY", "BAILEY_PASSPHRASE"):
        assert absent not in env, f"{absent} reached the runner"
    assert QUIET.items() <= env.items()
    assert NATIVE_TELEMETRY_OFF.items() <= env.items(), "a crew's onnxruntime sends no telemetry either"


def test_crewais_telemetry_switches_are_the_runtimes_own() -> None:
    from sigrix_runtime.quiet import QUIET_ENV_DEFAULTS

    assert QUIET == QUIET_ENV_DEFAULTS, "sigrix_runtime.quiet moved; Bailey exports what it sets"


def test_an_apps_environment_is_its_requirements_and_the_pinned_runner(tmp_path: Path) -> None:
    folder = tmp_path / "app"
    folder.mkdir()
    assert requirements_of(folder) == f"\n{RUNTIME_REQUIREMENT}\n".encode()
    (folder / "requirements.txt").write_text("crewai==1.15.33\n\n", encoding="utf-8")
    assert requirements_of(folder) == f"crewai==1.15.33\n{RUNTIME_REQUIREMENT}\n".encode()
    assert RUNTIME_REQUIREMENT in (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")


def test_the_fixture_is_a_solution_bailey_reads() -> None:
    made = read_solution(_fixture_files())
    (app,) = made.apps
    assert app.files["config/agents.yaml"] and zip_solution(made)


# ---------------------------------------------------------------------------
# Building an app's environment
# ---------------------------------------------------------------------------


def _build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uv: str | None, fails: bool = False
) -> tuple[Any, list[list[str]]]:
    import shutil
    import subprocess

    from bailey import apps

    ran: list[list[str]] = []

    def run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        ran.append(command)
        if "venv" in command:
            if Path(command[-1]).exists():  # as uv refuses
                return subprocess.CompletedProcess(command, 2, "", f"A directory already exists at: {command[-1]}")
            python = environments.python(REQUIREMENTS)
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
        if fails and "install" in command:
            return subprocess.CompletedProcess(
                command, 1, "", "ERROR: No matching distribution found for crewai==9.9.9"
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(shutil, "which", lambda name: uv if name == "uv" else None)
    monkeypatch.setattr(subprocess, "run", run)
    environments = apps.VirtualEnvironments(tmp_path / "environments")
    assert environments.state(REQUIREMENTS) == ("missing", "")
    environments.prepare(REQUIREMENTS)
    for thread in list(environments._building.values()):
        thread.join(30)
    return environments, ran


REQUIREMENTS = f"crewai==1.15.33\n{RUNTIME_REQUIREMENT}\n".encode()


def test_an_environment_is_built_with_uv_when_bailey_finds_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    environments, ran = _build(tmp_path, monkeypatch, uv="/opt/uv")
    assert environments.state(REQUIREMENTS) == ("ready", "")
    listed = environments.python(REQUIREMENTS).parents[1] / "requirements.txt"
    assert listed.read_bytes() == REQUIREMENTS
    assert ran == [
        ["/opt/uv", "venv", "--python", sys.executable, str(listed.parent)],
        ["/opt/uv", "pip", "install", "--python", str(environments.python(REQUIREMENTS)), "-r", str(listed)],
    ]


def test_without_uv_an_environment_is_built_with_venv_and_pip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    environments, ran = _build(tmp_path, monkeypatch, uv=None)
    assert environments.state(REQUIREMENTS) == ("ready", "")
    assert ran[0] == [sys.executable, "-m", "venv", str(environments.python(REQUIREMENTS).parents[1])]
    assert ran[1][:4] == [str(environments.python(REQUIREMENTS)), "-m", "pip", "install"]


def test_an_environment_that_fails_to_build_says_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    environments, _ = _build(tmp_path, monkeypatch, uv="/opt/uv", fails=True)
    state, why = environments.state(REQUIREMENTS)
    assert state == "failed"
    assert why == "Installing the app's libraries failed: ERROR: No matching distribution found for crewai==9.9.9"


def test_a_new_key_restarts_the_runner_on_it(client: TestClient) -> None:
    imported = _ready(client)
    client.post(f"{_app(imported['id'])}/postern/v0/run", headers=AUTH, json={"inputs": {"prompt": NOTES}})
    assert client.put("/api/keys/openai", headers=AUTH, json={"key": "sk-the-new-key-5678"}).status_code == 200
    emulator.REQUESTS.clear()
    client.post(f"{_app(imported['id'])}/postern/v0/run", headers=AUTH, json={"inputs": {"prompt": NOTES}})
    assert {request["key"] for request in emulator.REQUESTS} == {"sk-the-new-key-5678"}


def test_bailey_stopping_stops_its_apps_runners(state: AppState) -> None:
    with TestClient(build_app(state), base_url="http://127.0.0.1:8123") as client:
        imported = _ready(client)
        assert client.get(f"{_app(imported['id'])}/postern/v0/status", headers=AUTH).status_code == 200
        assert state.app_host is not None
        (runner,) = state.app_host._running.values()
    assert runner.process.poll() is not None, "the runner ended with Bailey"


def test_each_step_reaches_the_page_as_its_task_finishes(state: AppState, environments: HereEnvironments) -> None:
    """The done-when's "as they happen": served for real, since a test client reads a response whole."""

    environments.extra = {"STAND_IN_TASK_SECONDS": "0.8"}
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(build_app(state), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=AUTH, timeout=60) as http:
            assert http.put("/api/settings", json={"provider": "openai"}).status_code == 200
            assert http.put("/api/keys/openai", json={"key": KEY}).status_code == 200
            imported = http.post("/api/assistants/import", content=_zip(_fixture_files())).json()
            seen: list[tuple[str, float]] = []
            stream = f"{_app(imported['id'])}/postern/v0/stream"
            with http.stream("POST", stream, json={"inputs": {"prompt": NOTES}}) as response:
                for line in response.iter_lines():
                    if line.startswith("event: "):
                        seen.append((line.removeprefix("event: "), time.monotonic()))
    finally:
        server.should_exit = True
        thread.join(30)
    assert [name for name, _ in seen] == ["start", "step", "step", "done"]
    first_step = next(at for name, at in seen if name == "step")
    assert seen[-1][1] - first_step >= 0.5, "the first step arrived as its task finished, not with the end of the run"


# ---------------------------------------------------------------------------
# Bailey as a person starts it
# ---------------------------------------------------------------------------


def test_an_app_opens_and_runs_when_bailey_home_names_a_relative_folder(tmp_path: Path, provider_base: str) -> None:
    """``BAILEY_HOME=./some-folder bailey``. The runner starts in the app's own folder, and a data
    folder kept as written named the app's Python and its bundle from there, where neither is."""

    home = tmp_path / "some-folder"
    bailey = Running("./some-folder", cwd=tmp_path, extra_env={"BAILEY_PASSPHRASE": "correct horse battery"})
    try:
        settings = {"provider": "openai-compatible", "address": provider_base + "/local/v1", "model": "qwen3:8b"}
        assert bailey.call("PUT", "/api/settings", settings)[0] == 200
        status, imported = bailey.call("POST", "/api/assistants/import", _zip(_fixture_files()))
        assert status == 200, imported
        ready_environment(home, FIXTURE / "apps" / "digest")
        status, describe = bailey.call("GET", f"{_app(imported['id'])}/postern/v0/describe")
        assert status == 200, describe
        status, answered = bailey.call("POST", f"{_app(imported['id'])}/postern/v0/run", {"inputs": {"prompt": NOTES}})
        assert status == 200, answered
        assert answered["output"]["value"].startswith("Newsletter Writer did:")
        # Named in full from the folder Bailey started in, as Settings names the key file.
        status, answer = bailey.call("GET", "/api/status")
        assert Path(answer["data_folder"]) == home
        assert answer["keys"]["where"] == f"an encrypted file, {Path(answer['data_folder']) / FILE_NAME}"
    finally:
        bailey.stop()
