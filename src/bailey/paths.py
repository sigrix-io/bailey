"""Where Bailey keeps its files: one folder per user, in the platform's place for application data.

- **Windows:** ``%LOCALAPPDATA%\\Bailey``.
- **macOS:** ``~/Library/Application Support/Bailey``.
- **Linux and the rest:** ``$XDG_DATA_HOME/bailey``, or ``~/.local/share/bailey``.

``BAILEY_HOME`` overrides all three, for a container's volume or a second, separate
setup. The folder holds the local store (``bailey.db``) and, on a machine with no
keychain, the encrypted key file (``keys.enc``); keys never go anywhere else.

Every function takes what it reads as an argument, so a test can describe any machine.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

#: The variable that moves the data folder.
HOME_VARIABLE = "BAILEY_HOME"


def data_folder(
    *,
    environ: Mapping[str, str] = os.environ,
    platform: str = sys.platform,
    home: Path | None = None,
) -> Path:
    """The folder Bailey keeps its files in. Nothing is created here; :func:`ensure_data_folder` does that."""

    override = environ.get(HOME_VARIABLE, "").strip()
    if override:
        return Path(override).expanduser()
    user_home = home if home is not None else Path.home()
    if platform.startswith("win"):
        local = environ.get("LOCALAPPDATA", "").strip()
        return (Path(local) if local else user_home / "AppData" / "Local") / "Bailey"
    if platform == "darwin":
        return user_home / "Library" / "Application Support" / "Bailey"
    xdg = environ.get("XDG_DATA_HOME", "").strip()
    # The XDG specification says a relative path here is invalid and to be ignored. It is
    # a POSIX path, absolute when it starts with "/", whatever system reads this.
    base = Path(xdg) if xdg.startswith("/") else user_home / ".local" / "share"
    return base / "bailey"


def ensure_data_folder(folder: Path) -> Path:
    """Create the folder, readable by this user alone where the system has such permissions."""

    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        folder.chmod(0o700)
    return folder


def nearest_existing(path: Path) -> Path:
    """The path itself if it exists, or the closest ancestor that does: where creating it would start."""

    current = path
    while not current.exists() and current.parent != current:
        current = current.parent
    return current
