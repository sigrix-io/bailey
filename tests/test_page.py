"""The page's own files: text never markup, nothing from another origin, nothing inline, and nothing it names missing.

Gatehouse's rule, held the same way: the page writes what it is given with
``textContent``, never as markup, because what a server or a model returns is data. The
CSP already refuses inline script and anything from another origin; these catch the
attempt in the source, before a browser refuses it on someone's machine. Each sweep has a
canary, since a sweep that finds nothing and a file with nothing to find read alike.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

import bailey
from bailey.server import STATIC_FILES

WEB = Path(bailey.__file__).parent / "web"
SCRIPTS = sorted(WEB.glob("*.js"))
STYLES = sorted(WEB.glob("*.css"))

#: Every way a script can turn a string into markup or code.
_MARKUP_WRITERS = re.compile(
    r"\b(?:innerHTML|outerHTML|insertAdjacentHTML|createContextualFragment|DOMParser|srcdoc)\b"
    r"|\bdocument\s*\.\s*write(?:ln)?\b"
    r"|\beval\s*\("
    r"|\bnew\s+Function\b"
    r"|\bsetAttribute\s*\(\s*[\"'](?:on\w+|style|srcdoc)[\"']"
    r"|\bsetHTMLUnsafe\b"
)

#: A URL naming a host, which a page that loads nothing from elsewhere has no use for.
_URL_WITH_A_HOST = re.compile(r"\b(?:https?|wss?)://[A-Za-z0-9\[]", re.IGNORECASE)

#: Ways a script can send something without a request the server answers.
_OTHER_CHANNELS = re.compile(r"\b(?:sendBeacon|WebSocket|EventSource|XMLHttpRequest)\b")


def markup_writers(source: str) -> list[str]:
    return [match.group(0) for match in _MARKUP_WRITERS.finditer(source)]


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.inline_script = False
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        self._in_script = tag == "script"

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script and data.strip():
            self.inline_script = True


def _page() -> _Tags:
    parsed = _Tags()
    parsed.feed((WEB / "index.html").read_text(encoding="utf-8"))
    return parsed


def test_there_are_scripts_and_styles_to_sweep() -> None:
    assert SCRIPTS and STYLES, "the canary: the sweeps below read the page's own files"


@pytest.mark.parametrize(
    "planted",
    [
        "element.innerHTML = answer;",
        "element.outerHTML = answer;",
        "list.insertAdjacentHTML('beforeend', answer);",
        "document.write(answer);",
        "new DOMParser().parseFromString(answer, 'text/html');",
        "range.createContextualFragment(answer);",
        "frame.srcdoc = answer;",
        "eval(answer);",
        "new Function(answer)();",
        "element.setAttribute('onclick', answer);",
        'element.setAttribute("style", answer);',
        "element.setHTMLUnsafe(answer);",
    ],
)
def test_the_markup_sweep_catches_each_way_in(planted: str) -> None:
    assert markup_writers(planted), planted


def test_the_markup_sweep_leaves_text_alone() -> None:
    assert markup_writers("element.textContent = answer; element.append(child); list.replaceChildren();") == []


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_no_script_writes_markup(script: Path) -> None:
    assert markup_writers(script.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_no_script_names_another_origin_or_another_channel(script: Path) -> None:
    source = script.read_text(encoding="utf-8")
    assert _URL_WITH_A_HOST.findall(source) == []
    assert _OTHER_CHANNELS.findall(source) == []


def test_the_origin_sweeps_catch_a_planted_address_and_beacon() -> None:
    assert _URL_WITH_A_HOST.findall('fetch("https://telemetry.example/collect")')
    assert _OTHER_CHANNELS.findall("navigator.sendBeacon(path, data)")


def test_the_page_loads_only_its_own_static_files_and_nothing_inline() -> None:
    page = _page()
    loads = [
        attrs.get("src") or attrs.get("href")
        for tag, attrs in page.tags
        if tag in ("script", "img") or (tag == "link" and attrs.get("rel") in ("stylesheet", "icon"))
    ]
    assert loads, "the canary: the page loads its script and sheet"
    assert all(isinstance(load, str) and load.startswith("/static/") for load in loads), loads
    assert {load.removeprefix("/static/") for load in loads if load} <= set(STATIC_FILES)
    assert not page.inline_script
    for tag, attrs in page.tags:
        assert tag != "style", "a <style> block, which the CSP refuses"
        assert "style" not in attrs, f"a style attribute on <{tag}>, which the CSP refuses"
        assert not [name for name in attrs if name.startswith("on")], f"an inline handler on <{tag}>"


@pytest.mark.parametrize("sheet", STYLES, ids=lambda path: path.name)
def test_no_sheet_loads_anything(sheet: Path) -> None:
    source = sheet.read_text(encoding="utf-8")
    assert "url(" not in source and "@import" not in source


def test_every_file_served_exists_and_every_file_there_is_served() -> None:
    present = {path.name for path in WEB.iterdir() if path.is_file()} - {"index.html"}
    # Gatehouse's scripts and stylesheet; its licence, notice and record sit beside them unserved.
    release = json.loads((WEB / "gatehouse" / "RELEASE.json").read_text(encoding="utf-8"))
    vendored = {f"gatehouse/{name}" for name in release["files"] if name.endswith((".js", ".css"))}
    assert present | vendored == set(STATIC_FILES)
    assert all((WEB / name).is_file() for name in STATIC_FILES)
