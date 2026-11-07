"""CI and the release run ``scripts/check_wheels.py``, and the release checks the published package on a Mac too.

The check is only worth its list while something runs it. These read the workflows themselves:

- CI's ``wheels`` job installs uv at the version the image pins, then runs the check, and the gate a
  ruleset requires (``ci-passed``) waits for it;
- the release's build job runs the same check before it uploads anything, since a version on PyPI can
  never be reused, and a dependency can drop a computer between a merge and a tag;
- the release's post-publish job runs on a Mac with an Apple chip as well as on Linux, and waits for the
  index to serve the new version for twice as long as PyPI lets the index be cached.

This checks the repository rather than the package, so the sdist, which carries no workflows and no
Dockerfile, leaves it out (``pyproject.toml``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]

CHECK = "python scripts/check_wheels.py"
#: How long PyPI lets a project's index page be cached: ``Cache-Control: max-age=600`` on
#: ``/simple/<name>/``, as PyPI served it on 2026-11-07.
INDEX_CACHE_SECONDS = 600
#: uv at the image's pin, read from the Dockerfile so that there is one spelling of it.
UV_AT_THE_IMAGES_PIN = "python -m pip install \"uv==$(sed -n 's/^ARG UV_VERSION=//p' Dockerfile)\""


def _jobs(workflow: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8"))["jobs"]


def _runs(steps: list[dict[str, Any]]) -> list[str]:
    return [str(step.get("run", "")).strip() for step in steps]


def test_ci_runs_the_check_with_the_images_uv_and_its_gate_waits_for_it() -> None:
    jobs = _jobs("ci.yml")
    runs = _runs(jobs["wheels"]["steps"])

    assert runs.index(UV_AT_THE_IMAGES_PIN) < runs.index(CHECK)
    assert "wheels" in jobs["ci-passed"]["needs"]


def test_the_dockerfile_pins_the_one_uv_the_jobs_install() -> None:
    pins = re.findall(r"^ARG UV_VERSION=(\S*)$", (ROOT / "Dockerfile").read_text(encoding="utf-8"), flags=re.MULTILINE)

    assert len(pins) == 1 and re.fullmatch(r"\d+\.\d+\.\d+", pins[0]), pins


def test_the_release_runs_the_check_before_it_uploads_anything() -> None:
    steps = _jobs("release.yml")["build"]["steps"]
    runs = _runs(steps)
    upload = next(
        index for index, step in enumerate(steps) if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )

    assert runs.index(UV_AT_THE_IMAGES_PIN) < runs.index(CHECK) < upload


def test_the_release_checks_the_published_package_on_a_mac_as_well_as_linux() -> None:
    verify = _jobs("release.yml")["verify"]

    assert verify["runs-on"] == "${{ matrix.runner }}"
    assert {"ubuntu-latest", "macos-latest"} <= set(verify["strategy"]["matrix"]["runner"])
    assert verify["strategy"]["fail-fast"] is False


def test_the_release_waits_out_the_index_for_twice_its_cache() -> None:
    """On 0.1.3's release the Linux runner was handed the index page from before the upload for all
    of the action's default ten minutes (40 attempts, 15 seconds apart), and the run failed with the
    version published. Both numbers are spelled in the workflow, so none of the action's defaults is
    assumed here."""

    steps = _jobs("release.yml")["verify"]["steps"]
    given = next(step for step in steps if step.get("id") == "published")["with"]

    assert int(given["attempts"]) * int(given["interval"]) >= 2 * INDEX_CACHE_SECONDS, given
