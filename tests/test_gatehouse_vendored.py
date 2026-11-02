"""The vendored Gatehouse is a published release, byte for byte, and the script that brings one in refuses anything else.

``src/bailey/web/gatehouse/`` is a copy of ``@sigrix-io/gatehouse`` from npm
(``scripts/vendor_gatehouse.py``). ``RELEASE.json`` names the release and holds each
file's sha256, so an edit made here instead of upstream fails until the next release
replaces it.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
VENDORED = ROOT / "src" / "bailey" / "web" / "gatehouse"
RELEASE = json.loads((VENDORED / "RELEASE.json").read_text(encoding="utf-8"))

sys.path.insert(0, str(ROOT / "scripts"))
import vendor_gatehouse  # noqa: E402

sys.path.pop(0)


def test_the_record_names_one_published_release() -> None:
    assert RELEASE["package"] == "@sigrix-io/gatehouse"
    assert re.fullmatch(r"\d+\.\d+\.\d+", RELEASE["version"])
    assert re.fullmatch(r"[0-9a-f]{40}", RELEASE["commit"])
    assert RELEASE["integrity"].startswith("sha512-")
    assert RELEASE["tarball"] == (
        f"https://registry.npmjs.org/@sigrix-io/gatehouse/-/gatehouse-{RELEASE['version']}.tgz"
    )


def test_the_folder_holds_the_records_files_and_nothing_else() -> None:
    assert sorted(path.name for path in VENDORED.iterdir()) == sorted([*RELEASE["files"], "RELEASE.json", "README.md"])
    assert list(RELEASE["files"]) == [*vendor_gatehouse.SHIPPED, *vendor_gatehouse.LICENSED]


def test_each_file_is_the_releases_own() -> None:
    for name, digest in RELEASE["files"].items():
        assert hashlib.sha256((VENDORED / name).read_bytes()).hexdigest() == digest, (
            f"{name} differs from Gatehouse {RELEASE['version']}: change it upstream and vendor the release"
        )


def _tarball(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _registry(monkeypatch: pytest.MonkeyPatch, tarball: bytes, integrity: str | None = None) -> None:
    record = {
        "gitHead": "a" * 40,
        "dist": {
            "tarball": "https://registry.npmjs.org/@sigrix-io/gatehouse/-/gatehouse-9.9.9.tgz",
            "integrity": integrity or "sha512-" + base64.b64encode(hashlib.sha512(tarball).digest()).decode(),
        },
    }

    def fetch(url: str) -> bytes:
        if url.endswith("%2fgatehouse/9.9.9"):
            return json.dumps(record).encode()
        if url == record["dist"]["tarball"]:
            return tarball
        raise AssertionError(f"fetched {url}")

    monkeypatch.setattr(vendor_gatehouse, "_fetch", fetch)


def _complete() -> dict[str, bytes]:
    return {
        **{f"package/src/{name}": f"/* {name} */".encode() for name in vendor_gatehouse.SHIPPED},
        **{f"package/{name}": name.encode() for name in vendor_gatehouse.LICENSED},
    }


def test_the_script_writes_the_release_and_its_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _registry(monkeypatch, _tarball(_complete()))
    release: dict[str, Any] = vendor_gatehouse.vendor("9.9.9", tmp_path)
    assert release["files"]["run_renderer.js"] == hashlib.sha256(b"/* run_renderer.js */").hexdigest()
    assert json.loads((tmp_path / "RELEASE.json").read_text(encoding="utf-8")) == release


def test_a_tarball_the_registry_does_not_name_writes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _registry(monkeypatch, _tarball(_complete()), integrity="sha512-" + base64.b64encode(b"x" * 64).decode())
    with pytest.raises(SystemExit, match="not the tarball the registry's record names"):
        vendor_gatehouse.vendor("9.9.9", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_a_release_missing_a_file_writes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    files = _complete()
    del files["package/src/run_renderer.js"]
    _registry(monkeypatch, _tarball(files))
    with pytest.raises(SystemExit, match="run_renderer.js is not a file"):
        vendor_gatehouse.vendor("9.9.9", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_only_an_exact_version_is_vendored(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        vendor_gatehouse.main(["^0.4"])
    assert "give an exact version" in capsys.readouterr().err
