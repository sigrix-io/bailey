"""An assistant as a solution: the Agent Plugins layout Bailey reads and writes.

A solution is a folder, or a zip of one::

    plugin.json                      $schema, name, and extensions["org.sigrix"].assistant
    assistant.json                   format 0.1 (schemas/0.1/assistant.schema.json)
    skills/<instructions>/SKILL.md   the assistant's own instructions, in context from the first message
    skills/<name>/SKILL.md           each other skill, loaded when the conversation needs it
    skills/<name>/references/<file>  a skill's small text files, loaded with it
    persona.md                       the voice, when the writer keeps it apart from the instructions

Bailey's templates are written this way, an assistant exports this way, and an import
reads it, so one reader serves all three. It is the wider reader ``schemas/README.md``
describes: it ignores a member it does not know and refuses a major version it does not
know. What installing a stranger's package needs on top of reading it (the credential
scan, the unverified mark, the install screen) is story S10's.
"""

from __future__ import annotations

import io
import json
import re
import stat
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Any

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
ASSISTANT_FILE = "assistant.json"
FORMAT_VERSION = "0.1"
KNOWN_MAJOR = "0"
PERSONA_FILE = "persona.md"

PLUGIN_NAME = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
SKILL_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_FILE_PATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*(?:/[A-Za-z0-9_][A-Za-z0-9._-]*)*$")
_RESERVED_WORDS = ("anthropic", "claude")

#: Bounds on what is read, so a package cannot make Bailey hold more than it should.
MAX_PACKAGE_BYTES = 5 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024
MAX_ENTRIES = 300
MAX_COMPRESSION_RATIO = 100
MAX_REFERENCE_BYTES = 100 * 1024

#: The fixed time written into every zip entry, so one assistant always exports to the same bytes.
_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


class SolutionRefused(ValueError):
    """A package Bailey will not read, with the sentence that says why."""


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    #: Each reference file's name under ``references/`` and its text, in name order.
    references: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "body": self.body,
            "references": [list(item) for item in self.references],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Skill:
        return cls(
            name=str(data["name"]),
            description=str(data.get("description") or ""),
            body=str(data.get("body") or ""),
            references=tuple((str(name), str(text)) for name, text in data.get("references") or ()),
        )


@dataclass(frozen=True)
class Assistant:
    name: str
    title: str
    description: str
    #: The skill holding the assistant's own instructions; its body is always in context.
    instructions: Skill
    persona: str = ""
    starters: tuple[str, ...] = ()
    skills: tuple[Skill, ...] = field(default_factory=tuple)

    def skill(self, name: str) -> Skill | None:
        return next((skill for skill in self.skills if skill.name == name), None)

    def as_dict(self) -> dict[str, Any]:
        """Bailey's own record of an assistant, as the local store keeps it."""

        return {
            "format": "bailey-assistant/1",
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "instructions": self.instructions.as_dict(),
            "persona": self.persona,
            "starters": list(self.starters),
            "skills": [skill.as_dict() for skill in self.skills],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Assistant:
        if data.get("format") != "bailey-assistant/1":
            raise SolutionRefused("This assistant was saved by a newer Bailey, in a format this one does not read.")
        return cls(
            name=str(data["name"]),
            title=str(data["title"]),
            description=str(data.get("description") or ""),
            instructions=Skill.from_dict(data["instructions"]),
            persona=str(data.get("persona") or ""),
            starters=tuple(str(starter) for starter in data.get("starters") or ()),
            skills=tuple(Skill.from_dict(skill) for skill in data.get("skills") or ()),
        )


# ---------------------------------------------------------------------------
# SKILL.md
# ---------------------------------------------------------------------------


def _scalar(value: str, where: str) -> str:
    value = value.strip()
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise SolutionRefused(f"{where}: a quoted value Bailey cannot read.") from error
        if not isinstance(parsed, str):
            raise SolutionRefused(f"{where}: a quoted value Bailey cannot read.")
        return parsed
    if value.startswith("'"):
        if not value.endswith("'") or len(value) < 2:
            raise SolutionRefused(f"{where}: a quoted value Bailey cannot read.")
        return value[1:-1].replace("''", "'")
    return value


def parse_skill(text: str, where: str) -> tuple[dict[str, str], str]:
    """A SKILL.md's frontmatter, its top-level ``key: value`` lines, and its body.

    Agent Skills frontmatter is YAML. Bailey reads the part a skill's identity is in:
    plain and quoted values, and ``|`` or ``>`` block values, which long descriptions
    use. A nested value it does not need (``metadata:``) is skipped; anything else it
    cannot read is refused rather than guessed at.
    """

    text = text.lstrip("﻿").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise SolutionRefused(f"{where} has no frontmatter: it should open with a line of three dashes.")
    end = text.find("\n---", 3)
    if end == -1:
        raise SolutionRefused(f"{where}'s frontmatter is never closed by a line of three dashes.")
    # The body starts after the closing line and any blank lines under it.
    head, body = text[4:end], text[end + 4 :].lstrip("\n")
    lines = head.split("\n")
    values: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":
            continue  # part of a nested value that was skipped
        key, colon, rest = line.partition(":")
        if not colon or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
            raise SolutionRefused(f"{where}'s frontmatter has a line Bailey cannot read: {line[:60]!r}.")
        rest = rest.strip()
        if rest[:1] in ("|", ">") and re.fullmatch(r"[|>][+-]?", rest):
            block: list[str] = []
            while index < len(lines) and (not lines[index].strip() or lines[index][0] in " \t"):
                block.append(lines[index].strip())
                index += 1
            joined = "\n".join(block) if rest[0] == "|" else " ".join(part for part in block if part)
            values[key] = joined.strip()
        elif rest:
            values[key] = _scalar(rest, f"{where}, {key}")
        # An empty value opens a nested one, whose indented lines the loop skips.
    return values, body


def render_skill(skill: Skill) -> str:
    # A JSON string is a valid YAML double-quoted scalar, so any description round-trips.
    return f"---\nname: {skill.name}\ndescription: {json.dumps(skill.description)}\n---\n\n{skill.body.strip()}\n"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _text(files: Mapping[str, bytes], path: str) -> str:
    try:
        return files[path].decode("utf-8")
    except KeyError as error:
        raise SolutionRefused(f"The package names {path}, but does not carry it.") from error
    except UnicodeDecodeError as error:
        raise SolutionRefused(f"{path} is not UTF-8 text.") from error


def check_skill_name(name: object, where: str) -> str:
    if not isinstance(name, str) or not SKILL_NAME.fullmatch(name):
        raise SolutionRefused(
            f"{where}: a skill's name is lowercase letters, digits and hyphens, up to 64, "
            "neither starting nor ending with a hyphen."
        )
    if any(word in name for word in _RESERVED_WORDS):
        raise SolutionRefused(f"{where}: a skill's name may not contain anthropic or claude, which clients reserve.")
    return name


def _read_skill(files: Mapping[str, bytes], path: object, where: str) -> Skill:
    if not isinstance(path, str):
        raise SolutionRefused(f"{where}: a skill's path should be text.")
    match = re.fullmatch(r"skills/([^/]+)/SKILL\.md", path)
    if match is None:
        raise SolutionRefused(f"{where}: a skill lives at skills/<name>/SKILL.md, and {path} does not.")
    folder = check_skill_name(match.group(1), where)
    meta, body = parse_skill(_text(files, path), path)
    if meta.get("name") != folder:
        raise SolutionRefused(
            f"{path} names itself {meta.get('name')!r}; Agent Skills wants the folder's name, {folder}."
        )
    description = meta.get("description", "").strip()
    if not description:
        raise SolutionRefused(f"{path} has no description, which tells a model when to load it.")
    prefix = f"skills/{folder}/references/"
    references = []
    for name in sorted(files):
        if name.startswith(prefix):
            data = files[name]
            if len(data) > MAX_REFERENCE_BYTES:
                raise SolutionRefused(
                    f"{name} is {len(data) // 1024} KB, and a reference file loads whole with its skill, so Bailey "
                    f"takes them up to {MAX_REFERENCE_BYTES // 1024} KB."
                )
            references.append((name.removeprefix(prefix), _text(files, name)))
    return Skill(name=folder, description=description, body=body.strip(), references=tuple(references))


def read_solution(files: Mapping[str, bytes]) -> Assistant:
    """The assistant a solution's files describe, or :class:`SolutionRefused` saying why not."""

    document_path = ASSISTANT_FILE
    if "plugin.json" in files:
        try:
            plugin = json.loads(_text(files, "plugin.json"))
        except json.JSONDecodeError as error:
            raise SolutionRefused("plugin.json is not JSON.") from error
        sigrix = (plugin.get("extensions") or {}).get("org.sigrix") if isinstance(plugin, dict) else None
        named = sigrix.get("assistant") if isinstance(sigrix, dict) else None
        if isinstance(named, str) and named:
            document_path = named
    try:
        document = json.loads(_text(files, document_path))
    except json.JSONDecodeError as error:
        raise SolutionRefused(f"{document_path} is not JSON.") from error
    if not isinstance(document, dict):
        raise SolutionRefused(f"{document_path} should hold one JSON object.")
    version = document.get("format_version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+", version):
        raise SolutionRefused(f"{document_path} has no format_version Bailey can read.")
    if version.split(".")[0] != KNOWN_MAJOR:
        raise SolutionRefused(
            f"This solution is written in format {version}, which this Bailey does not read; a newer Bailey may."
        )
    name = document.get("name")
    if not isinstance(name, str) or len(name) > 64 or not PLUGIN_NAME.fullmatch(name):
        raise SolutionRefused(
            f"{document_path}'s name is not a plugin name: lowercase letters, digits, dots and hyphens."
        )
    title = document.get("title")
    if not isinstance(title, str) or not title.strip():
        raise SolutionRefused(f"{document_path} has no title.")
    description = document.get("description") or ""
    if not isinstance(description, str):
        raise SolutionRefused(f"{document_path}'s description should be text.")
    instructions = _read_skill(files, document.get("instructions"), f"{document_path}, instructions")
    persona = ""
    persona_path = document.get("persona")
    if persona_path is not None:
        if not isinstance(persona_path, str) or not _FILE_PATH.fullmatch(persona_path):
            raise SolutionRefused(f"{document_path}'s persona is not a file inside the package.")
        persona = _text(files, persona_path).strip()
    starters = document.get("starters") or []
    if not isinstance(starters, list) or not all(isinstance(item, str) and item.strip() for item in starters):
        raise SolutionRefused(f"{document_path}'s starters should be a list of messages.")
    skills: list[Skill] = []
    for index, entry in enumerate(document.get("skills") or []):
        where = f"{document_path}, skill {index + 1}"
        if not isinstance(entry, dict):
            raise SolutionRefused(f"{where} should be an object with a name and a path.")
        skill = _read_skill(files, entry.get("path"), where)
        if entry.get("name") != skill.name:
            raise SolutionRefused(f"{where} is named {entry.get('name')!r} but its path says {skill.name}.")
        if skill.name == instructions.name or any(other.name == skill.name for other in skills):
            raise SolutionRefused(f"{where}: {skill.name} is listed twice.")
        skills.append(skill)
    return Assistant(
        name=name,
        title=title.strip(),
        description=description.strip(),
        instructions=instructions,
        persona=persona,
        starters=tuple(item.strip() for item in starters),
        skills=tuple(skills),
    )


def _safe_member(name: str) -> str | None:
    """The path an entry may be read under, or ``None`` for one Bailey refuses."""

    if "\\" in name or "\0" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def unzip_solution(data: bytes) -> dict[str, bytes]:
    """A zip's files by path, within bounds; one folder wrapping everything is taken off."""

    if len(data) > MAX_PACKAGE_BYTES:
        raise SolutionRefused(f"The package is over {MAX_PACKAGE_BYTES // (1024 * 1024)} MB.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise SolutionRefused("That file is not a zip.") from error
    # Each entry is judged by the name the archive carries, ``orig_filename``. Python's
    # ``filename`` is rewritten by platform: on Windows a backslash becomes "/", and
    # everywhere a NUL ends the name. Judged by that copy, one package would be refused
    # on Linux and macOS and taken on Windows. Sigrix's import refuses both characters too.
    entries = [info for info in archive.infolist() if not info.orig_filename.endswith("/")]
    if len(entries) > MAX_ENTRIES:
        raise SolutionRefused(f"The package holds more than {MAX_ENTRIES} files.")
    total = 0
    files: dict[str, bytes] = {}
    for info in entries:
        name = info.orig_filename
        if stat.S_ISLNK(info.external_attr >> 16):
            raise SolutionRefused(f"{name} is a link, which a package may not carry.")
        path = _safe_member(name)
        if path is None:
            raise SolutionRefused(f"{name} points outside the package.")
        if info.file_size > MAX_FILE_BYTES:
            raise SolutionRefused(f"{path} is larger than {MAX_FILE_BYTES // 1024} KB.")
        if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
            raise SolutionRefused(f"{path} unpacks to far more than it occupies, as an archive bomb does.")
        total += info.file_size
        if total > MAX_PACKAGE_BYTES:
            raise SolutionRefused(f"The package unpacks to more than {MAX_PACKAGE_BYTES // (1024 * 1024)} MB.")
        with archive.open(info) as member:
            content = member.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise SolutionRefused(f"{path} is larger than {MAX_FILE_BYTES // 1024} KB.")
        files[path] = content
    tops = {path.split("/", 1)[0] for path in files}
    if len(tops) == 1 and all("/" in path for path in files):
        prefix = next(iter(tops)) + "/"
        files = {path.removeprefix(prefix): content for path, content in files.items()}
    return files


# ---------------------------------------------------------------------------
# The form
# ---------------------------------------------------------------------------

#: The limits the form's fields are held to. Over one, a field is refused with a
#: sentence, never cut short.
FORM_LIMITS = {
    "title": 120,
    "description": 500,
    "instructions": 20_000,
    "persona": 5_000,
    "starters": 10,
    "starter": 300,
    "skills": 30,
    "skill_description": 1_024,
    "skill_body": 50_000,
}


def _field(form: Mapping[str, Any], key: str, label: str, limit: int, *, required: bool) -> str:
    value = form.get(key, "")
    if not isinstance(value, str):
        raise SolutionRefused(f"{label} should be text.")
    value = value.strip()
    if required and not value:
        raise SolutionRefused(f"{label} cannot be empty.")
    if len(value) > limit:
        raise SolutionRefused(f"{label} is {len(value):,} characters; it can be {limit:,} at most.")
    return value


def assistant_from_form(form: Mapping[str, Any], previous: Assistant) -> Assistant:
    """The assistant the edit form describes, built on the one it edits.

    The instructions skill keeps its name and description; a skill keeps its reference
    files while it keeps its name. Every field is checked, and one over its limit is
    refused rather than cut.
    """

    title = _field(form, "title", "The title", FORM_LIMITS["title"], required=True)
    description = _field(form, "description", "The description", FORM_LIMITS["description"], required=False)
    instructions = _field(form, "instructions", "The instructions", FORM_LIMITS["instructions"], required=True)
    persona = _field(form, "persona", "The voice", FORM_LIMITS["persona"], required=False)
    starters_in = form.get("starters", [])
    if not isinstance(starters_in, list) or not all(isinstance(item, str) for item in starters_in):
        raise SolutionRefused("The starters should be a list of messages.")
    starters = [item.strip() for item in starters_in if item.strip()]
    if len(starters) > FORM_LIMITS["starters"]:
        raise SolutionRefused(f"An assistant offers {FORM_LIMITS['starters']} starters at most.")
    for starter in starters:
        if len(starter) > FORM_LIMITS["starter"]:
            raise SolutionRefused(f"A starter can be {FORM_LIMITS['starter']} characters at most.")
    skills_in = form.get("skills", [])
    if not isinstance(skills_in, list) or not all(isinstance(item, dict) for item in skills_in):
        raise SolutionRefused("The skills should be a list.")
    if len(skills_in) > FORM_LIMITS["skills"]:
        raise SolutionRefused(f"An assistant carries {FORM_LIMITS['skills']} skills at most.")
    skills: list[Skill] = []
    for index, entry in enumerate(skills_in, start=1):
        name = check_skill_name(str(entry.get("name", "")).strip(), f"Skill {index}")
        if name == previous.instructions.name or any(skill.name == name for skill in skills):
            raise SolutionRefused(f"Two skills are named {name}; each needs a name of its own.")
        skill_description = _field(
            entry, "description", f"The {name} skill's description", FORM_LIMITS["skill_description"], required=True
        )
        body = _field(entry, "body", f"The {name} skill's instructions", FORM_LIMITS["skill_body"], required=True)
        kept = previous.skill(name)
        skills.append(Skill(name, skill_description, body, kept.references if kept else ()))
    return Assistant(
        name=previous.name if title == previous.title else plugin_name(title),
        title=title,
        description=description,
        instructions=Skill(
            previous.instructions.name,
            previous.instructions.description,
            instructions,
            previous.instructions.references,
        ),
        persona=persona,
        starters=tuple(starters),
        skills=tuple(skills),
    )


def form_of(assistant: Assistant) -> dict[str, Any]:
    """What the edit form shows of an assistant: everything a person edits, and the reference files by name."""

    return {
        "title": assistant.title,
        "description": assistant.description,
        "instructions": assistant.instructions.body,
        "persona": assistant.persona,
        "starters": list(assistant.starters),
        "skills": [
            {
                "name": skill.name,
                "description": skill.description,
                "body": skill.body,
                "references": [name for name, _ in skill.references],
            }
            for skill in assistant.skills
        ],
    }


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def plugin_name(title: str) -> str:
    """A plugin name for a title: lowercase words joined by hyphens."""

    words = re.findall(r"[a-z0-9]+", title.lower())
    name = "-".join(words)[:64].strip("-")
    return name or "assistant"


def solution_files(assistant: Assistant) -> dict[str, bytes]:
    """The assistant in the solution layout, each file's bytes by its path."""

    files: dict[str, bytes] = {}

    def put_skill(skill: Skill) -> str:
        path = f"skills/{skill.name}/SKILL.md"
        files[path] = render_skill(skill).encode("utf-8")
        for name, text in skill.references:
            files[f"skills/{skill.name}/references/{name}"] = text.encode("utf-8")
        return path

    document: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "name": assistant.name,
        "title": assistant.title,
    }
    if assistant.description:
        document["description"] = assistant.description
    document["instructions"] = put_skill(assistant.instructions)
    if assistant.persona:
        files[PERSONA_FILE] = (assistant.persona.strip() + "\n").encode("utf-8")
        document["persona"] = PERSONA_FILE
    if assistant.starters:
        document["starters"] = list(assistant.starters)
    if assistant.skills:
        document["skills"] = [{"name": skill.name, "path": put_skill(skill)} for skill in assistant.skills]
    plugin: dict[str, Any] = {"$schema": PLUGIN_SCHEMA, "name": assistant.name}
    if assistant.description:
        plugin["description"] = assistant.description
    plugin["extensions"] = {"org.sigrix": {"assistant": ASSISTANT_FILE}}
    files["plugin.json"] = (json.dumps(plugin, indent=2) + "\n").encode("utf-8")
    files[ASSISTANT_FILE] = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return files


def zip_solution(assistant: Assistant) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(solution_files(assistant).items()):
            info = zipfile.ZipInfo(f"{assistant.name}/{path}", date_time=_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, content)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

#: The template a new user's first assistant is made from: the proposal's day one.
DEFAULT_TEMPLATE = "everyday"


def _read_tree(root: Traversable, prefix: str = "") -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for entry in root.iterdir():
        path = f"{prefix}{entry.name}"
        if entry.is_dir():
            files.update(_read_tree(entry, path + "/"))
        elif entry.is_file():
            files[path] = entry.read_bytes()
    return files


def template_ids() -> list[str]:
    root = resources.files("bailey").joinpath("templates")
    return sorted(entry.name for entry in root.iterdir() if entry.is_dir() and not entry.name.startswith(("_", ".")))


def template(template_id: str) -> Assistant:
    if template_id not in template_ids():
        raise SolutionRefused("Bailey has no template by that name.")
    return read_solution(_read_tree(resources.files("bailey").joinpath("templates", template_id)))
