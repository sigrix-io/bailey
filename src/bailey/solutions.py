"""An assistant as a solution: the Agent Plugins layout Bailey reads and writes.

A solution is a folder, or a zip of one::

    plugin.json                      $schema, name, and extensions["org.sigrix"].assistant
    assistant.json                   format 0.1 (schemas/0.1/assistant.schema.json)
    skills/<instructions>/SKILL.md   the assistant's own instructions, in context from the first message
    skills/<name>/SKILL.md           each other skill, loaded when the conversation needs it
    skills/<name>/references/<file>  a skill's small text files, loaded with it
    persona.md                       the voice, when the writer keeps it apart from the instructions
    mcp.json                         its MCP servers: how each starts, and the names of its variables
    samples/<slot>.<ext>             a made-up example of each document the solution asks for
    apps/<name>/                     an app: a Postern bundle (a crew), run by its own runner

An app is found by its folder, as a sample is: format 0.1 has no member for apps, and
refuses one it does not know. Its files stay out of Bailey's record of the assistant (a
crew carries its runner's code); the app host keeps them in the data folder, where the
runner runs (:mod:`bailey.apps`).

``assistant.json``'s ``knowledge`` names the documents the solution asks the person for,
its knowledge slots, which they give on their own machine; a slot's sample stands in
until they do. Its ``tools`` says, for each server, which tools the writer declared
and which of those write. Bailey asks before a write, and treats as a write any tool a
server lists that its declaration does not, and every tool of a server with none.

Bailey's templates are written this way, an assistant exports this way, and an import
reads it, so one reader serves all three. It is the wider reader ``schemas/README.md``
describes: it ignores a member it does not know and refuses a major version it does not
know. What installing a stranger's package needs on top of reading it (a folder, a
download from before downloads were solutions, the credential check, the unverified mark,
the install screen) is :mod:`bailey.install`'s.
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
MCP_FILE = "mcp.json"

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
MAX_SERVERS = 20
MAX_ARGS = 100
MAX_ARG_CHARACTERS = 4096
MAX_VARIABLES = 50
MAX_SLOTS = 20
#: A sample is text (Sigrix takes md, txt, csv or json), and the store bounds it at 256 KiB.
MAX_SAMPLE_BYTES = 256 * 1024
SAMPLES_FOLDER = "samples"

#: A server's name in ``mcp.json``, and a variable's name in its ``env``.
SERVER_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
#: The placeholder a Sigrix download writes for the variable its shared sign-in client fills,
#: which the download's ``setup.sh`` replaces with that client's file (Sigrix's
#: ``assistant_spec._SHARED_OAUTH_SENTINEL``). A name standing in for a path, not a value.
SHARED_CLIENT_PLACEHOLDER = re.compile(r"^__SIGRIX_SHARED_OAUTH__:[A-Za-z_][A-Za-z0-9_]{0,127}$")
#: A reference to a variable, as Claude Code's configuration writes one (``${NAME}``), or to an
#: environment variable or a prompted input, as VS Code's does (``${env:NAME}``, ``${input:id}``).
_VARIABLE_REFERENCE = re.compile(r"^\$\{(?:env:|input:)?[A-Za-z_][A-Za-z0-9_.-]{0,127}\}$")
#: A knowledge slot's name, and an extension it accepts (schemas/0.1, knowledge_slot).
SLOT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
EXTENSION = re.compile(r"^[a-z0-9]{1,16}$")

#: An app's folder name, and how many a solution may carry.
APP_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
APPS_FOLDER = "apps"
MAX_APPS = 10
#: The seller's ``ui.json``, which Gatehouse lays an app's page out from.
MAX_UI_BYTES = 64 * 1024
#: What an app's folder holds that a package never brings in: a ``.env``, which holds a
#: seller's keys or a listing's id, and what a runner writes as it runs.
_LEFT_OUT = re.compile(r"^(?:\.env|\.last_error|\.postern[^/]*|workspace/.*|(?:.*/)?__pycache__/.*|.*\.pyc)$")

#: The fixed time written into every zip entry, so one assistant always exports to the same bytes.
_ZIP_TIME = (2026, 1, 1, 0, 0, 0)

#: :attr:`Assistant.origin` for an assistant installed from a file.
ORIGIN_FILE = "file"


def carries_no_value(value: object) -> bool:
    """Whether what an MCP configuration gives a variable is no credential: nothing, a reference, or the placeholder.

    A package names each key and leaves its value to the person (Postern §4.1.3): the
    download writes ``""`` for each, and a value anywhere makes the package nonconformant.
    """

    if value is None or value == "":
        return True
    return isinstance(value, str) and bool(
        _VARIABLE_REFERENCE.fullmatch(value) or SHARED_CLIENT_PLACEHOLDER.fullmatch(value)
    )


def value_refused(where: str, variable: str) -> str:
    """The sentence for a package that gives a variable a value, which it never quotes."""

    return (
        f"{where} gives {variable} a value. A package names each key and leaves its value to you "
        "(Postern §4.1.3), so Bailey installs nothing from it."
    )


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
class Server:
    """One MCP server a solution names: how it starts, and what its tools do.

    ``env`` holds the names of the variables it reads, never their values: a package
    carries names only (design §2.1, rule 1), and each value comes from the person, by
    way of the keychain. ``tools`` is the writer's declaration, or ``None`` where
    ``assistant.json`` declares nothing for this server.
    """

    name: str
    command: str = ""
    args: tuple[str, ...] = ()
    env: tuple[str, ...] = ()
    #: A remote server's address and transport. Bailey 0.1 lists one and does not connect to it.
    url: str = ""
    transport: str = ""
    tools: tuple[str, ...] | None = None
    write_tools: tuple[str, ...] = ()

    @property
    def remote(self) -> bool:
        return bool(self.url)

    def asks_before(self, tool: str) -> bool:
        """Whether calling ``tool`` waits for the person: a declared write, or a tool nobody declared.

        Postern calls a write declaration advisory to the client and load-bearing for the
        person, and the store does not verify one yet (sigrix-io/sigrix#5053), so a server
        listing a tool its declaration leaves out gets no benefit of the doubt.
        """

        if self.tools is None or tool not in self.tools:
            return True
        return tool in self.write_tools

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "args": list(self.args),
            "env": list(self.env),
            "url": self.url,
            "transport": self.transport,
            "tools": None if self.tools is None else list(self.tools),
            "write_tools": list(self.write_tools),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Server:
        tools = data.get("tools")
        return cls(
            name=str(data["name"]),
            command=str(data.get("command") or ""),
            args=tuple(str(arg) for arg in data.get("args") or ()),
            env=tuple(str(name) for name in data.get("env") or ()),
            url=str(data.get("url") or ""),
            transport=str(data.get("transport") or ""),
            tools=None if tools is None else tuple(str(tool) for tool in tools),
            write_tools=tuple(str(tool) for tool in data.get("write_tools") or ()),
        )


@dataclass(frozen=True)
class KnowledgeSlot:
    """A document the solution asks the person for: its name, what to give, the file types it takes."""

    name: str
    description: str
    accepts: tuple[str, ...]
    required: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "accepts": list(self.accepts),
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> KnowledgeSlot:
        return cls(
            name=str(data["name"]),
            description=str(data.get("description") or ""),
            accepts=tuple(str(item) for item in data.get("accepts") or ()),
            required=bool(data.get("required")),
        )


@dataclass(frozen=True)
class Example:
    """An input the assistant is meant to handle, and the answer of the kind it should give, when its writer gave one.

    ``bailey check`` runs each one (:mod:`bailey.check`); the answer is a model of the
    reply, not its text.
    """

    input: str
    output: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"input": self.input, **({"output": self.output} if self.output else {})}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Example:
        return cls(input=str(data["input"]), output=str(data.get("output") or ""))


@dataclass(frozen=True)
class Sample:
    """A slot's sample: a made-up document of the kind the slot asks for, carried as text."""

    slot: str
    extension: str
    text: str

    @property
    def path(self) -> str:
        return f"{SAMPLES_FOLDER}/{self.slot}.{self.extension}"

    def as_dict(self) -> dict[str, Any]:
        return {"slot": self.slot, "extension": self.extension, "text": self.text}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Sample:
        return cls(slot=str(data["slot"]), extension=str(data["extension"]), text=str(data.get("text") or ""))


@dataclass(frozen=True)
class App:
    """One of a solution's apps: a crew in ``apps/<name>/``, a Postern bundle its runner serves.

    ``files`` are the bundle's, by path inside its folder. They travel with an import and
    an export and are kept in the data folder in between, never in the assistant's record.
    """

    name: str
    title: str
    summary: str = ""
    #: The seller's ``ui.json``: how Gatehouse lays out the app's form and result.
    ui: Mapping[str, Any] | None = field(default=None, compare=False, hash=False)
    files: Mapping[str, bytes] = field(default_factory=dict, compare=False, hash=False, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "title": self.title, "summary": self.summary, "ui": self.ui}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> App:
        ui = data.get("ui")
        return cls(
            name=str(data["name"]),
            title=str(data.get("title") or data["name"]),
            summary=str(data.get("summary") or ""),
            ui=ui if isinstance(ui, dict) else None,
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
    servers: tuple[Server, ...] = field(default_factory=tuple)
    knowledge: tuple[KnowledgeSlot, ...] = field(default_factory=tuple)
    samples: tuple[Sample, ...] = field(default_factory=tuple)
    apps: tuple[App, ...] = field(default_factory=tuple)
    examples: tuple[Example, ...] = field(default_factory=tuple)
    #: Bailey's own setting, not the solution's: answer only from the documents, and say
    #: so rather than answer from what the model knows when they do not cover a question.
    grounded_only: bool = False
    #: Bailey's own record of where this copy came from, not the solution's: ``"file"`` for
    #: one installed from a file, which nobody has checked and the page marks unverified
    #: (design §4); ``""`` for Bailey's templates and what the person made from them.
    origin: str = ""

    @property
    def unverified(self) -> bool:
        return self.origin == ORIGIN_FILE

    def skill(self, name: str) -> Skill | None:
        return next((skill for skill in self.skills if skill.name == name), None)

    def server(self, name: str) -> Server | None:
        return next((server for server in self.servers if server.name == name), None)

    def slot(self, name: str) -> KnowledgeSlot | None:
        return next((slot for slot in self.knowledge if slot.name == name), None)

    def sample(self, slot: str) -> Sample | None:
        return next((sample for sample in self.samples if sample.slot == slot), None)

    def app(self, name: str) -> App | None:
        return next((app for app in self.apps if app.name == name), None)

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
            "servers": [server.as_dict() for server in self.servers],
            "knowledge": [slot.as_dict() for slot in self.knowledge],
            "samples": [sample.as_dict() for sample in self.samples],
            "apps": [app.as_dict() for app in self.apps],
            "examples": [example.as_dict() for example in self.examples],
            "grounded_only": self.grounded_only,
            "origin": self.origin,
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
            servers=tuple(Server.from_dict(server) for server in data.get("servers") or ()),
            knowledge=tuple(KnowledgeSlot.from_dict(slot) for slot in data.get("knowledge") or ()),
            samples=tuple(Sample.from_dict(sample) for sample in data.get("samples") or ()),
            apps=tuple(App.from_dict(app) for app in data.get("apps") or ()),
            examples=tuple(Example.from_dict(example) for example in data.get("examples") or ()),
            grounded_only=bool(data.get("grounded_only")),
            origin=str(data.get("origin") or ""),
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
            # A value may go on over indented lines, as a writer wraps a long description;
            # YAML folds them into one line, quoted or not.
            more, index = _continued(lines, index)
            values[key] = _scalar(_folded(rest, more), f"{where}, {key}")
        # An empty value opens a nested one, whose indented lines the loop skips.
    return values, body


def _continued(lines: list[str], index: int) -> tuple[list[str], int]:
    """The indented lines carrying on the value of the key before them, and the index after them.

    A blank line followed by another indented one is a line break within the value.
    """

    taken: list[str] = []
    while index < len(lines):
        line = lines[index]
        if line[:1] in (" ", "\t") and line.strip():
            taken.append(line.strip())
        elif not line.strip() and index + 1 < len(lines) and lines[index + 1][:1] in (" ", "\t"):
            taken.append("")
        else:
            break
        index += 1
    return taken, index


def _folded(first: str, more: list[str]) -> str:
    """A flow scalar's lines as YAML reads them: each break a space, each blank line a break."""

    folded = first.rstrip()
    for part in more:
        if not part:
            folded += "\n"
        elif folded.endswith("\n"):
            folded += part
        else:
            folded += " " + part
    return folded


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


def _tool_names(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and 0 < len(item) <= 128 for item in value):
        raise SolutionRefused(f"{where} should be a list of tool names.")
    return tuple(dict.fromkeys(value))


def _read_declaration(value: object, where: str) -> tuple[tuple[str, ...] | None, tuple[str, ...]]:
    if value is None:
        return None, ()
    if not isinstance(value, dict):
        raise SolutionRefused(f"{where} should be an object with tools and write_tools.")
    tools = _tool_names(value.get("tools") or [], f"{where}, tools")
    writes = _tool_names(value.get("write_tools") or [], f"{where}, write_tools")
    # A write left out of ``tools`` is undeclared, and an undeclared tool asks anyway, so
    # dropping it from ``write_tools`` changes nothing that asks and keeps an export valid.
    return tools, tuple(tool for tool in writes if tool in tools)


def _read_server(name: str, entry: Mapping[str, Any], declaration: object, where: str, declared_at: str) -> Server:
    tools, write_tools = _read_declaration(declaration, f"{declared_at}, tools, {name}")
    command = entry.get("command")
    url = entry.get("url")
    if command is None and isinstance(url, str) and url.strip():
        if not url.strip().startswith(("https://", "http://")):
            raise SolutionRefused(f"{where}: a remote server's url is an http or https address.")
        transport = entry.get("type")
        return Server(
            name,
            url=url.strip(),
            transport=transport if isinstance(transport, str) else "",
            tools=tools,
            write_tools=write_tools,
        )
    if not isinstance(command, str) or not command.strip():
        raise SolutionRefused(f"{where} has no command to start it and no url to reach it.")
    args = entry.get("args") or []
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise SolutionRefused(f"{where}: args should be a list of text.")
    if len(command) > MAX_ARG_CHARACTERS or len(args) > MAX_ARGS or any(len(arg) > MAX_ARG_CHARACTERS for arg in args):
        raise SolutionRefused(f"{where} starts with a command longer than Bailey runs.")
    env = entry.get("env") or {}
    if not isinstance(env, dict):
        raise SolutionRefused(f"{where}: env should name each variable the server reads, which the person fills in.")
    if len(env) > MAX_VARIABLES:
        raise SolutionRefused(f"{where} names {len(env)} variables; Bailey fills {MAX_VARIABLES} at most.")
    for variable, value in env.items():
        if not VARIABLE_NAME.fullmatch(variable):
            raise SolutionRefused(f"{where}: {variable[:60]!r} is not a variable's name.")
        # A value is refused, not dropped: a package carrying one is nonconformant, and
        # Postern §7 says a client should refuse it rather than install the rest.
        if not carries_no_value(value):
            raise SolutionRefused(value_refused(where, variable))
    # Only the names are kept: the person gives each value, and the keychain keeps it.
    return Server(name, command=command.strip(), args=tuple(args), env=tuple(env), tools=tools, write_tools=write_tools)


def _read_servers(files: Mapping[str, bytes], document: Mapping[str, Any], document_path: str) -> tuple[Server, ...]:
    """The servers the MCP configuration names, each with the declaration ``tools`` gives it."""

    config_path = document.get("mcp")
    if config_path is None:
        return ()
    if not isinstance(config_path, str) or not _FILE_PATH.fullmatch(config_path):
        raise SolutionRefused(f"{document_path}'s mcp is not a file inside the package.")
    try:
        config = json.loads(_text(files, config_path))
    except json.JSONDecodeError as error:
        raise SolutionRefused(f"{config_path} is not JSON.") from error
    entries = config.get("mcpServers") if isinstance(config, dict) else None
    if not isinstance(entries, dict):
        raise SolutionRefused(f"{config_path} has no mcpServers object, where an MCP configuration lists its servers.")
    if len(entries) > MAX_SERVERS:
        raise SolutionRefused(f"{config_path} names {len(entries)} servers; Bailey starts {MAX_SERVERS} at most.")
    declared = document.get("tools") or {}
    if not isinstance(declared, dict):
        raise SolutionRefused(f"{document_path}'s tools should map each server to the tools it declares.")
    servers: list[Server] = []
    for name, entry in entries.items():
        where = f"{config_path}, server {name[:64]!r}"
        if not SERVER_NAME.fullmatch(name):
            raise SolutionRefused(
                f"{where}: a server's name is letters, digits, dots, hyphens and underscores, up to 64."
            )
        if not isinstance(entry, dict):
            raise SolutionRefused(f"{where} should be an object.")
        servers.append(_read_server(name, entry, declared.get(name), where, document_path))
    return tuple(servers)


def _read_knowledge(
    files: Mapping[str, bytes], document: Mapping[str, Any], document_path: str
) -> tuple[tuple[KnowledgeSlot, ...], tuple[Sample, ...]]:
    """The knowledge slots ``assistant.json`` declares, and each slot's text sample the package carries."""

    declared = document.get("knowledge")
    if declared is None:
        return (), ()
    if not isinstance(declared, list):
        raise SolutionRefused(f"{document_path}'s knowledge should be a list of the documents it asks for.")
    if len(declared) > MAX_SLOTS:
        raise SolutionRefused(f"{document_path} asks for {len(declared)} documents; Bailey takes {MAX_SLOTS} at most.")
    slots: list[KnowledgeSlot] = []
    samples: list[Sample] = []
    for index, entry in enumerate(declared, start=1):
        where = f"{document_path}, knowledge {index}"
        if not isinstance(entry, dict):
            raise SolutionRefused(f"{where} should be an object with a name, a description and the types it accepts.")
        name = entry.get("name")
        if not isinstance(name, str) or not SLOT_NAME.fullmatch(name):
            raise SolutionRefused(
                f"{where}: a document's name is lowercase letters, digits and underscores, starting with a letter."
            )
        if any(slot.name == name for slot in slots):
            raise SolutionRefused(f"{where}: {name} is asked for twice.")
        description = entry.get("description")
        if not isinstance(description, str) or not description.strip():
            raise SolutionRefused(f"{where}: {name} has no description, which tells the person what to give.")
        accepts = entry.get("accepts")
        if (
            not isinstance(accepts, list)
            or not accepts
            or not all(isinstance(item, str) and EXTENSION.fullmatch(item) for item in accepts)
        ):
            raise SolutionRefused(f"{where}: {name}'s accepts should list file extensions, such as pdf or csv.")
        required = entry.get("required", False)
        if not isinstance(required, bool):
            raise SolutionRefused(f"{where}: {name}'s required should be true or false.")
        slot = KnowledgeSlot(name, description.strip(), tuple(dict.fromkeys(accepts)), required)
        slots.append(slot)
        sample = _read_sample(files, slot)
        if sample is not None:
            samples.append(sample)
    return tuple(slots), tuple(samples)


def _read_sample(files: Mapping[str, bytes], slot: KnowledgeSlot) -> Sample | None:
    """The slot's sample, ``samples/<slot>.<ext>`` in one of the types it accepts, if the package has a text one."""

    for extension in slot.accepts:
        path = f"{SAMPLES_FOLDER}/{slot.name}.{extension}"
        if path not in files:
            continue
        data = files[path]
        if len(data) > MAX_SAMPLE_BYTES:
            raise SolutionRefused(
                f"{path} is {len(data) // 1024} KB; a sample can be {MAX_SAMPLE_BYTES // 1024} KB at most."
            )
        try:
            return Sample(slot.name, extension, data.decode("utf-8"))
        except UnicodeDecodeError:
            continue  # a sample Bailey cannot read as text is left for the person's own document
    return None


def _read_app(name: str, files: Mapping[str, bytes]) -> App:
    """One app's folder, its paths relative to it."""

    where = f"{APPS_FOLDER}/{name}"
    if "config/agents.yaml" not in files or "config/tasks.yaml" not in files:
        raise SolutionRefused(f"{where} holds no crew: an app needs config/agents.yaml and config/tasks.yaml.")
    title, summary = name, ""
    if "postern.json" in files:
        try:
            describe = json.loads(files["postern.json"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SolutionRefused(f"{where}/postern.json is not JSON.") from error
        agent = describe.get("agent") if isinstance(describe, dict) else None
        if not isinstance(agent, dict):
            raise SolutionRefused(f"{where}/postern.json has no agent, which a describe document names.")
        if isinstance(agent.get("name"), str) and agent["name"].strip():
            title = agent["name"].strip()[:120]
        found = agent.get("summary") or agent.get("description")
        if isinstance(found, str):
            summary = found.strip()[:500]
    ui = None
    if "ui.json" in files:
        if len(files["ui.json"]) > MAX_UI_BYTES:
            raise SolutionRefused(f"{where}/ui.json is over {MAX_UI_BYTES // 1024} KB.")
        try:
            ui = json.loads(files["ui.json"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SolutionRefused(f"{where}/ui.json is not JSON.") from error
        if not isinstance(ui, dict):
            raise SolutionRefused(f"{where}/ui.json should hold one JSON object.")
    return App(name=name, title=title, summary=summary, ui=ui, files=dict(files))


def read_apps(files: Mapping[str, bytes]) -> tuple[tuple[App, ...], tuple[str, ...]]:
    """The solution's apps, and the paths left out of them (a ``.env``, a runner's own files)."""

    grouped: dict[str, dict[str, bytes]] = {}
    left_out: list[str] = []
    for path, data in sorted(files.items()):
        if not path.startswith(f"{APPS_FOLDER}/"):
            continue
        name, _, inner = path.removeprefix(f"{APPS_FOLDER}/").partition("/")
        if not inner:
            raise SolutionRefused(f"{path} is not inside an app's folder, apps/<name>/.")
        if not APP_NAME.fullmatch(name):
            raise SolutionRefused(f"{APPS_FOLDER}/{name} is not an app's name: lowercase letters, digits and hyphens.")
        if _LEFT_OUT.fullmatch(inner):
            left_out.append(path)
            continue
        grouped.setdefault(name, {})[inner] = data
    if len(grouped) > MAX_APPS:
        raise SolutionRefused(f"The solution carries {len(grouped)} apps; Bailey takes {MAX_APPS} at most.")
    return tuple(_read_app(name, inner) for name, inner in grouped.items()), tuple(left_out)


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
    knowledge, samples = _read_knowledge(files, document, document_path)
    apps, _ = read_apps(files)
    examples = _read_examples(document, document_path)
    return Assistant(
        name=name,
        title=title.strip(),
        description=description.strip(),
        instructions=instructions,
        persona=persona,
        starters=tuple(item.strip() for item in starters),
        skills=tuple(skills),
        servers=_read_servers(files, document, document_path),
        knowledge=knowledge,
        samples=samples,
        apps=apps,
        examples=examples,
    )


def _read_examples(document: Mapping[str, Any], document_path: str) -> tuple[Example, ...]:
    """The inputs the solution is meant to handle, each with the answer its writer gave, if any."""

    entries = document.get("examples") or []
    if not isinstance(entries, list):
        raise SolutionRefused(f"{document_path}'s examples should be a list, each with an input.")
    examples: list[Example] = []
    for index, entry in enumerate(entries, start=1):
        where = f"{document_path}, example {index}"
        given = entry.get("input") if isinstance(entry, dict) else None
        # As the schema has it (minLength 1), so a document it accepts is one Bailey reads.
        if not isinstance(given, str) or not given:
            raise SolutionRefused(f"{where} has no input: what the person sends.")
        output = entry.get("output") or ""
        if not isinstance(output, str):
            raise SolutionRefused(f"{where}'s output should be text.")
        examples.append(Example(input=given, output=output))
    return tuple(examples)


def safe_member(name: str) -> str | None:
    """The path an entry may be read under, or ``None`` for one Bailey refuses.

    Refused rather than repaired, as ``sigrix-runtime``'s pull refuses one: a package
    naming ``../`` or ``/etc/`` is not one with a typo in it.
    """

    if "\\" in name or "\0" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def without_wrapper(files: Mapping[str, bytes]) -> dict[str, bytes]:
    """The files with the one folder that wraps them all taken off, as a zip of a folder has."""

    tops = {path.split("/", 1)[0] for path in files}
    if len(tops) == 1 and all("/" in path for path in files):
        prefix = next(iter(tops)) + "/"
        return {path.removeprefix(prefix): content for path, content in files.items()}
    return dict(files)


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
        path = safe_member(name)
        if path is None:
            raise SolutionRefused(f"{name} points outside the package.")
        if path in files:
            # Two entries under one name are read differently by different unzippers, so
            # what one tool checked need not be what another installs.
            raise SolutionRefused(f"The package carries two files named {path}.")
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
    return without_wrapper(files)


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
    files while it keeps its name; the servers are not the form's, and stay as they were.
    Every field is checked, and one over its limit is refused rather than cut.
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
        servers=previous.servers,
        knowledge=previous.knowledge,
        samples=previous.samples,
        apps=previous.apps,
        examples=previous.examples,
        grounded_only=_flag(form, "grounded_only", previous.grounded_only),
        origin=previous.origin,
    )


def _flag(form: Mapping[str, Any], key: str, default: bool) -> bool:
    value = form.get(key, default)
    if not isinstance(value, bool):
        raise SolutionRefused(f"{key} should be true or false.")
    return value


def form_of(assistant: Assistant) -> dict[str, Any]:
    """What the edit form shows of an assistant: everything a person edits, and the reference files by name."""

    return {
        "title": assistant.title,
        "description": assistant.description,
        "instructions": assistant.instructions.body,
        "persona": assistant.persona,
        "starters": list(assistant.starters),
        "grounded_only": assistant.grounded_only,
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
    if assistant.examples:
        document["examples"] = [example.as_dict() for example in assistant.examples]
    if assistant.knowledge:
        document["knowledge"] = [
            {key: value for key, value in slot.as_dict().items() if key != "required" or value}
            for slot in assistant.knowledge
        ]
        for sample in assistant.samples:
            files[sample.path] = sample.text.encode("utf-8")
    # An app is written as its folder, from the files the caller loaded into it.
    for app in assistant.apps:
        for path, data in sorted(app.files.items()):
            files[f"{APPS_FOLDER}/{app.name}/{path}"] = data
    if assistant.servers:
        entries: dict[str, Any] = {}
        declarations: dict[str, Any] = {}
        for server in assistant.servers:
            entry: dict[str, Any]
            if server.remote:
                entry = {"type": server.transport, "url": server.url} if server.transport else {"url": server.url}
            else:
                entry = {"command": server.command, "args": list(server.args)}
                if server.env:
                    # Names only, each with an empty value for the person to fill, as Sigrix writes them.
                    entry["env"] = dict.fromkeys(server.env, "")
            entries[server.name] = entry
            if server.tools is not None:
                declarations[server.name] = {"tools": list(server.tools), "write_tools": list(server.write_tools)}
        files[MCP_FILE] = (json.dumps({"mcpServers": entries}, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        document["mcp"] = MCP_FILE
        if declarations:
            document["tools"] = declarations
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
