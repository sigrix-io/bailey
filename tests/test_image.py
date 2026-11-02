"""The sigrix/bailey image: what its Dockerfile promises, held to what the app, the docs and the workflows say.

The image itself is built and opened in CI, where ``ci.yml``'s ``image`` job runs
``scripts/check_launch.py`` against it on both architectures, and published by ``image.yml``.
These read the files instead. A port, a volume or a variable written in two places drifts in
whichever one nobody is looking at, so each fact several files must agree on is pinned once
here, against the code that reads it.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shlex
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

from bailey.keys import FORCE_FILE_VARIABLE
from bailey.server import CONTAINER_VARIABLE

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
WORKFLOWS = ROOT / ".github" / "workflows"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_launch", ROOT / "scripts" / "check_launch.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECK = _script()


def _instructions() -> list[tuple[str, str]]:
    """The Dockerfile's instructions, continuation lines joined, heredoc bodies left out."""

    found: list[tuple[str, str]] = []
    lines = iter(DOCKERFILE.splitlines())
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        while line.endswith("\\"):
            line = line[:-1] + " " + next(lines).strip()
        keyword, _, rest = line.strip().partition(" ")
        found.append((keyword.upper(), rest.strip()))
        if "<<'PY'" in rest:
            for body in lines:
                if body == "PY":
                    break
    return found


def _last_stage() -> list[tuple[str, str]]:
    instructions = _instructions()
    start = max(index for index, (keyword, _) in enumerate(instructions) if keyword == "FROM")
    return instructions[start:]


def _environment() -> dict[str, str]:
    env: dict[str, str] = {}
    for keyword, rest in _last_stage():
        if keyword == "ENV":
            for pair in shlex.split(rest):
                name, _, value = pair.partition("=")
                env[name] = value
    return env


def _one(keyword: str) -> str:
    (value,) = [rest for found, rest in _last_stage() if found == keyword]
    return value


def test_the_parser_reads_this_dockerfile() -> None:
    """Guards the guards: a parser reading nothing would pass every assertion below."""

    keywords = [keyword for keyword, _ in _instructions()]
    assert keywords.count("FROM") == 2 and {"ENV", "USER", "ENTRYPOINT", "CMD", "VOLUME", "EXPOSE"} <= set(keywords)
    assert "import shutil" not in json.dumps(_instructions()), "a heredoc's Python is not read as instructions"


def test_it_runs_bailey_under_tini_as_a_user_of_its_own_on_the_port_the_check_publishes() -> None:
    assert json.loads(_one("ENTRYPOINT")) == ["tini", "--", "bailey"]
    assert json.loads(_one("CMD")) == ["--port", str(CHECK.IMAGE_PORT), "--no-browser"]
    assert _one("EXPOSE") == str(CHECK.IMAGE_PORT)
    assert _one("USER") not in ("root", "0"), "the app and every tool server it starts would run as root"


def test_it_tells_bailey_it_is_in_a_container_and_where_its_data_keys_and_model_go() -> None:
    env = _environment()
    assert env[CONTAINER_VARIABLE] == "1"
    assert env[FORCE_FILE_VARIABLE] == "file", "a container has no keychain; the encrypted file it is"
    assert env["BAILEY_HOME"] == _one("VOLUME") == "/data"
    model = env["BAILEY_SEARCH_MODEL"]
    assert not model.startswith("/data"), "a new volume would hide the model the image carries"
    assert env["HF_HUB_OFFLINE"] == "1", "the model is in the image; nothing fetches it at run time"


def test_each_base_image_is_pinned_by_digest_and_dependabot_moves_the_pins() -> None:
    bases = [rest for keyword, rest in _instructions() if keyword == "FROM"]
    for base in bases:
        assert re.fullmatch(r"[a-z0-9./-]+:[\w.-]+@sha256:[0-9a-f]{64}( AS \w+)?", base), base
    dependabot = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    assert {"package-ecosystem": "docker", "directory": "/"}.items() <= next(
        update for update in dependabot["updates"] if update["package-ecosystem"] == "docker"
    ).items()


def test_the_readme_runs_the_image_as_the_check_does() -> None:
    port = CHECK.IMAGE_PORT
    command = f"docker run -p 127.0.0.1:{port}:{port} -v bailey:{_one('VOLUME')} sigrix/bailey"
    assert command in (ROOT / "README.md").read_text(encoding="utf-8")


def _workflow(name: str) -> dict[str, Any]:
    loaded = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_only_the_image_workflow_holds_a_registry_credential_and_only_in_the_jobs_that_push() -> None:
    source = (WORKFLOWS / "image.yml").read_text(encoding="utf-8")
    assert "id-token" not in source, "the image workflow needs no OIDC; release.yml alone has it"
    named = set(re.findall(r"secrets\.(\w+)", source))
    assert named == {"REGISTRY_USERNAME", "REGISTRY_TOKEN"}, named
    for name, job in _workflow("image.yml")["jobs"].items():
        if "secrets." in json.dumps(job):
            assert job.get("environment") == "docker-hub", f"{name} reads a secret outside the docker-hub environment"
    for other in sorted(WORKFLOWS.glob("*.yml")):
        if other.name != "image.yml":
            assert "REGISTRY_TOKEN" not in other.read_text(encoding="utf-8"), other.name


def test_every_image_it_publishes_is_opened_before_and_after_the_push() -> None:
    jobs = _workflow("image.yml")["jobs"]
    built = [step.get("run", "") for step in jobs["image"]["steps"]]
    opened = next(index for index, run in enumerate(built) if "check_launch.py --image" in run)
    pushed = next(index for index, run in enumerate(built) if "docker push" in run)
    assert opened < pushed, "an image is opened before it is pushed"
    assert any("check_launch.py --image" in step.get("run", "") for step in jobs["verify"]["steps"])
    assert "docker pull" in json.dumps(jobs["verify"]["steps"]), "and opened again as pulled by name"
    assert {entry["arch"] for entry in jobs["image"]["strategy"]["matrix"]["include"]} == {"amd64", "arm64"}


def test_ci_builds_and_opens_the_image_and_its_gate_waits_for_it() -> None:
    jobs = _workflow("ci.yml")["jobs"]
    assert "image" in jobs["ci-passed"]["needs"]
    assert any("check_launch.py --image" in step.get("run", "") for step in jobs["image"]["steps"])
    assert set(jobs["image"]["strategy"]["matrix"]["runner"]) == {"ubuntu-latest", "ubuntu-24.04-arm"}


def test_the_release_opens_the_app_it_published_to_pypi() -> None:
    steps = _workflow("release.yml")["jobs"]["verify"]["steps"]
    assert any("check_launch.py" in step.get("run", "") and "--command" in step.get("run", "") for step in steps)
