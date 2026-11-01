"""An app's environment made ready without a download, for tests and for driving the app by hand.

Bailey looks for an app's environment in its data folder, under a name made from the
app's requirements (:class:`bailey.apps.VirtualEnvironments`). This makes a real
virtual environment there, marked ready, whose Python reaches this one's packages (the
pinned ``sigrix-runtime``, PyYAML, python-dotenv) and ``tests/stand_ins``, where the
stand-in CrewAI is, through a ``.pth`` file. Bailey then finds it, and runs the app in
it, exactly as it would one it built from PyPI.

    python tests/app_environments.py <data folder> <app folder>
"""

from __future__ import annotations

import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bailey.apps import VirtualEnvironments, requirements_of  # noqa: E402

STAND_INS = Path(__file__).resolve().parent / "stand_ins"


def ready_environment(data_folder: Path, app: Path, *extra_paths: Path, startup: str = "") -> Path:
    """The environment Bailey will run ``app`` in, made from this Python; its interpreter's path.

    ``startup`` names a module on ``extra_paths`` that every process this environment's Python
    starts imports first, through the same ``.pth`` file (a line there that begins with
    ``import`` runs): the no-telemetry test records the runner and its crew that way.
    """

    requirements = requirements_of(app)
    environments = VirtualEnvironments(data_folder / "environments")
    python = environments.python(requirements)
    folder = python.parents[1]
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(folder)], check=True)  # noqa: S603
    purelib = subprocess.run(  # noqa: S603
        [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    reached = [STAND_INS, *extra_paths, Path(sysconfig.get_paths()["purelib"])]
    lines = [f"{path}\n" for path in reached] + ([f"import {startup}\n"] if startup else [])
    (Path(purelib) / "bailey_test_paths.pth").write_text("".join(lines), encoding="utf-8")
    (folder / "requirements.txt").write_bytes(requirements)
    (folder / ".ready").write_text("made by tests/app_environments.py\n", encoding="utf-8")
    return python


if __name__ == "__main__":
    print(ready_environment(Path(sys.argv[1]), Path(sys.argv[2])))
