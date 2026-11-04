"""The data folder: the platform's place for an application's files, or ``BAILEY_HOME``."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from bailey.paths import data_folder, ensure_data_folder, nearest_existing

HOME = Path("/home/someone")


@pytest.mark.parametrize(
    ("platform", "environ", "expected"),
    [
        (
            "win32",
            {"LOCALAPPDATA": "C:\\Users\\someone\\AppData\\Local"},
            Path("C:\\Users\\someone\\AppData\\Local") / "Bailey",
        ),
        ("win32", {}, HOME / "AppData" / "Local" / "Bailey"),
        ("darwin", {}, HOME / "Library" / "Application Support" / "Bailey"),
        ("linux", {"XDG_DATA_HOME": "/data/someone"}, Path("/data/someone") / "bailey"),
        ("linux", {"XDG_DATA_HOME": "relative/ignored"}, HOME / ".local" / "share" / "bailey"),
        ("linux", {}, HOME / ".local" / "share" / "bailey"),
        ("freebsd14", {}, HOME / ".local" / "share" / "bailey"),
    ],
)
def test_each_platform_keeps_the_files_in_its_own_place(platform: str, environ: dict[str, str], expected: Path) -> None:
    assert data_folder(environ=environ, platform=platform, home=HOME) == expected


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_bailey_home_moves_the_folder_on_every_platform(platform: str, tmp_path: Path) -> None:
    environ = {"BAILEY_HOME": str(tmp_path / "elsewhere"), "LOCALAPPDATA": "C:\\x", "XDG_DATA_HOME": "/x"}
    assert data_folder(environ=environ, platform=platform, home=HOME) == tmp_path / "elsewhere"


def test_a_blank_bailey_home_is_no_override() -> None:
    assert data_folder(environ={"BAILEY_HOME": "  "}, platform="linux", home=HOME) == HOME / ".local/share/bailey"


@pytest.mark.parametrize("named", ["some-folder", "./some-folder"])
def test_a_relative_bailey_home_is_named_in_full_from_the_folder_bailey_starts_in(
    named: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An app's runner starts in the app's own folder, where a relative path would lead nowhere.
    monkeypatch.chdir(tmp_path)
    assert data_folder(environ={"BAILEY_HOME": named}, platform="linux", home=HOME) == tmp_path / "some-folder"


def test_a_bailey_home_in_the_users_home_is_expanded_before_it_is_named_in_full() -> None:
    expanded = data_folder(environ={"BAILEY_HOME": "~/bailey-two"}, platform="linux", home=HOME)
    assert expanded == Path.home() / "bailey-two"


def test_the_folder_is_made_with_its_parents(tmp_path: Path) -> None:
    folder = tmp_path / "a" / "b" / "bailey"
    assert ensure_data_folder(folder) == folder
    assert folder.is_dir()
    ensure_data_folder(folder)  # a second start finds it there


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_the_folder_is_this_users_alone(tmp_path: Path) -> None:
    folder = tmp_path / "bailey"
    folder.mkdir(mode=0o755)
    ensure_data_folder(folder)
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_where_creating_would_start(tmp_path: Path) -> None:
    assert nearest_existing(tmp_path) == tmp_path
    assert nearest_existing(tmp_path / "not" / "yet") == tmp_path
