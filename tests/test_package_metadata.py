"""The version the package reports, and the annotations it declares, are the ones it ships.

The release's verify job installs the published version from PyPI and checks that
`bailey.__version__` names it and that `py.typed` came with it. By then the version
is spent, so both are checked here first. `pyproject.toml` and `__init__.py` each
carry the number; this is what keeps them from drifting apart before a tag does.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import bailey


def test_the_reported_version_is_the_distributions() -> None:
    assert bailey.__version__ == importlib.metadata.version("bailey"), (
        "__init__.py and pyproject.toml name different versions; move both."
    )


def test_the_package_declares_its_annotations() -> None:
    assert (Path(bailey.__file__).parent / "py.typed").is_file(), (
        "py.typed is missing, so type checkers will ignore these annotations."
    )
