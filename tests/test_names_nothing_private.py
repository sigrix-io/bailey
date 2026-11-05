"""Bailey's repository names nothing of the Sigrix platform's private code.

Bailey is public, and the platform's own repository is not. A docstring citing one of its
issues, a link into it, or the path of one of its modules tells a reader nothing they can open,
and maps a private codebase for anyone who reads this one. So the stories here go by their own
names (S1 to S11), the platform's code by what it does ("Sigrix's own listing scan"), and the
recipe for the one fixture its builder writes is kept with that builder. Sigrix's public
repositories (``sigrix-io/sigrix-runtime`` and the rest, each named with a hyphen) and its
public site (``sigrix.io``) are named freely.

Every text file in the repository is read, and the canary plants each shape in a tree of its
own, beside the public names that must pass.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: The private repository's name, put together here so that this file names it nowhere itself.
PRIVATE_REPOSITORY = "/".join(("sigrix-io", "sigrix"))
#: The private repository by name, and its modules' paths, in a path's spelling or an import's.
PRIVATE = re.compile(re.escape(PRIVATE_REPOSITORY) + r"(?![\w-])|\bsrc/services/|\bsrc\.services\.")
#: What a checkout holds that is not the repository's: environments, caches, builds, recordings.
SKIPPED = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
SKIPPED_PATHS = {"lessons/out", "dist", "build"}


def private_references(root: Path, *, skip: frozenset[Path] = frozenset()) -> list[str]:
    """Each line under ``root`` that names the private repository or its code, as ``path:line``."""

    found = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if (
            not path.is_file()
            or path in skip
            or SKIPPED.intersection(relative.parts)
            or any(relative.as_posix().startswith(prefix + "/") for prefix in SKIPPED_PATHS)
        ):
            continue
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if PRIVATE.search(line):
                found.append(f"{relative.as_posix()}:{number}")
    return found


def test_the_repository_names_nothing_of_the_platforms_private_code() -> None:
    read = [path for path in ROOT.rglob("*.py") if not SKIPPED.intersection(path.relative_to(ROOT).parts)]
    assert len(read) > 50, "the sweep reaches the package, the tests and the lessons"

    assert private_references(ROOT, skip=frozenset({Path(__file__).resolve()})) == []


def test_each_shape_is_found_and_the_public_names_are_not(tmp_path: Path) -> None:
    # Made-up issues and modules, so this file names nothing private either; the sweep above
    # passes over it, as it plants each shape.
    repo = PRIVATE_REPOSITORY
    planted = {
        "issue.md": f"Story S10 ({repo}#7).",
        "link.md": f"See [the issue](https://github.com/{repo}/issues/1).",
        "path.py": "# The platform's own shapes (src/services/made_up_module.py).",
        "import.md": "from src.services.made_up_module import made_up_function",
        "public.md": (
            "sigrix-io/sigrix-runtime, sigrix-io/bailey and https://github.com/sigrix-io/sigrix-mcp are public,\n"
            "and so is https://sigrix.io/learn/tag/bailey; src/bailey/services.py is Bailey's own.\n"
        ),
        "lessons/out/first/lesson.md": f"{repo}#1, in a recording's folder, which git ignores",
    }
    for name, text in planted.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / "video.mp4").write_bytes(b"\xff\xfe " + f"{repo}#2".encode() + b" \x00")

    assert private_references(tmp_path) == ["import.md:1", "issue.md:1", "link.md:1", "path.py:1"]
