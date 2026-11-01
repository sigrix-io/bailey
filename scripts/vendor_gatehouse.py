"""Vendor a released Gatehouse into ``src/bailey/web/gatehouse/``.

Gatehouse draws an app's run page: its form from the runner's ``describe``, the
steps as they happen, and the result. It is edited in ``sigrix-io/gatehouse`` and
published to npm as ``@sigrix-io/gatehouse``, and Bailey serves a copy of a release
rather than a build of its own, as the Sigrix platform does.

The script fetches one release from the registry and refuses a tarball whose
sha512 is not the one the registry's own record names. It writes the files a run
page loads, the licence and notice they ship under, and ``RELEASE.json``, which
``tests/test_gatehouse_vendored.py`` holds every one of those files to::

    python scripts/vendor_gatehouse.py 0.4.0

The copy is never edited here: a change would be undone by the next release, and
the test refuses it until then.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

PACKAGE = "@sigrix-io/gatehouse"
REGISTRY = "https://registry.npmjs.org"
TARGET = Path(__file__).resolve().parents[1] / "src" / "bailey" / "web" / "gatehouse"

#: What an app's run page loads, in load order, then the stylesheet. The host blocks
#: and the playbook block draw pages Bailey does not have, so they are left out.
SHIPPED = ("postern_client.js", "run_view_model.js", "refusal_copy.js", "run_renderer.js", "gatehouse.css")
#: What Apache-2.0 asks to travel with a copy.
LICENSED = ("LICENSE", "NOTICE")

_EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def _fetch(url: str) -> bytes:
    if not url.startswith(f"{REGISTRY}/"):
        raise SystemExit(f"{url} is not on {REGISTRY}; nothing written")
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - the npm registry, https only
        data: bytes = response.read()
    return data


def verified_tarball(record: dict[str, Any]) -> bytes:
    """The tarball the registry's record names, or an exit: nothing is written from a mismatch."""

    dist = record["dist"]
    tarball = _fetch(dist["tarball"])
    algorithm, _, expected = str(dist["integrity"]).partition("-")
    actual = base64.b64encode(hashlib.sha512(tarball).digest()).decode()
    if algorithm != "sha512" or actual != expected:
        raise SystemExit(f"{dist['tarball']} is not the tarball the registry's record names; nothing written")
    return tarball


def _member(archive: tarfile.TarFile, inner: str, version: str) -> bytes:
    try:
        handle = archive.extractfile(inner)
    except KeyError:
        handle = None
    if handle is None:
        raise SystemExit(f"{inner} is not a file in {PACKAGE} {version}; nothing written")
    return handle.read()


def vendor(version: str, target: Path = TARGET) -> dict[str, Any]:
    record = json.loads(_fetch(f"{REGISTRY}/{PACKAGE.replace('/', '%2f')}/{version}"))
    tarball = verified_tarball(record)
    members: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as archive:
        # Named members only, each to a fixed name: nothing in the archive chooses a path here.
        for name in SHIPPED + LICENSED:
            members[name] = _member(archive, f"package/src/{name}" if name in SHIPPED else f"package/{name}", version)
    target.mkdir(parents=True, exist_ok=True)
    for name, data in members.items():
        (target / name).write_bytes(data)
    release = {
        "package": PACKAGE,
        "version": version,
        "commit": record.get("gitHead"),
        "tarball": record["dist"]["tarball"],
        "integrity": record["dist"]["integrity"],
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in members.items()},
    }
    (target / "RELEASE.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8", newline="\n")
    return release


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vendor a released Gatehouse into src/bailey/web/gatehouse/.")
    parser.add_argument("version", help="a published version, such as 0.4.0")
    args = parser.parse_args(argv)
    if not _EXACT_VERSION.match(args.version):
        parser.error("give an exact version, such as 0.4.0")
    release = vendor(args.version)
    print(f"vendored {PACKAGE} {release['version']} ({release['integrity']}) into {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
