"""Installing a solution from a file: what arrives, what is checked first, and the install screen.

Story S10 (design §6 and §7 step 8).

**What arrives.** A zip, read as an import always read one
(:func:`bailey.solutions.unzip_solution`), or a folder, which the page sends as its files and
their paths (:func:`files_from_folder`). Safari unzips a download by default, so a folder is
what many people have. A folder meets a zip's limits and path rules.

A download from Sigrix has been a solution since 2026-10-01: it carries
``plugin.json`` and ``assistant.json`` beside the files a download always carried. One from
before carries ``SKILL.md``, ``README.md``, ``AGENTS.md``, the MCP configs and ``setup.sh``, and
:func:`convert_download` writes the solution files it lacks, so one reader reads both.

**What is checked before anything is written:**

- the package's limits and paths, by the readers above;
- no credential value anywhere (:func:`check_credentials`). Postern §4.1.3 makes a package
  carrying one nonconformant, and §7 says a client should refuse it. Where the format names a
  key (an MCP configuration's variables and headers, an app's ``.env`` and its credentials),
  a value is refused. In every text file, the credential shapes Sigrix's own scan knows are;
- a skill that carries scripts, which installs only from the store unless the person turns that
  off (design decision 3; :func:`skill_files`). Bailey 0.1 runs no skill's scripts, so a skill
  let in that way installs without them.

**The install screen** (:func:`install_screen`) is what the page shows before Install, from what
the package says: what the solution does, each key and what reads it, each knowledge slot, the
tools with the write tools marked, the apps, and what is left out. Everything installed from a
file is marked unverified, since nobody has checked it. What Install writes is what was checked
and shown: :class:`PendingInstalls` holds it between the two.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from bailey.apps import APPS_NOTICE, ENVIRONMENT_DISK_MB, ENVIRONMENT_DOWNLOAD_MB, PROVIDER_KEYS, provider_note
from bailey.solutions import (
    ASSISTANT_FILE,
    MAX_ENTRIES,
    MAX_FILE_BYTES,
    MAX_PACKAGE_BYTES,
    ORIGIN_FILE,
    PLUGIN_SCHEMA,
    SHARED_CLIENT_PLACEHOLDER,
    Assistant,
    Skill,
    SolutionRefused,
    carries_no_value,
    check_skill_name,
    parse_skill,
    read_apps,
    read_solution,
    render_skill,
    safe_member,
    value_refused,
    without_wrapper,
)
from bailey.tools import SANDBOX_NOTICE

#: The setting that lets a skill carrying scripts install from a file, in the words the
#: Settings page and the refusal both use.
SCRIPTS_SETTING = "skills_with_scripts"
SCRIPTS_SETTING_LABEL = "Install skills that carry scripts from a file"

#: Said of everything installed from a file, until the store can say otherwise (design §4).
UNVERIFIED_NOTE = (
    "Unverified: this solution comes from a file, not from the Sigrix store, so nobody has checked what it does. "
    "Install solutions from people you trust."
)
CONVERTED_NOTE = (
    "Made from an assistant download from before downloads carried assistant.json: its instructions and tools "
    "are here, and every tool asks before it runs, since the download declared none."
)
#: Said of a key an app reads that is the person's provider key, which Bailey gives it.
PROVIDER_KEY_NOTE = "Bailey gives the app your AI provider's key from Settings."
#: Said of any other key an app reads, which Bailey 0.1 cannot give it yet.
APP_KEY_NOTE = "Bailey cannot give an app this key yet, so the app will refuse to run until a later version can."
#: Said of the variable a Sigrix download fills with its shared sign-in client.
SHARED_CLIENT_NOTE = (
    "In other AI apps, the download's setup fills this in with Sigrix's shared sign-in client, which Bailey "
    "cannot use yet. Give it the path to a client file of your own, or leave it empty."
)

#: How long a checked package waits for Install, and how many wait at once.
PENDING_SECONDS = 30 * 60
MAX_PENDING = 5

#: What the page sends a folder as: each file's bytes, base64-encoded, beside its path.
MAX_FOLDER_REQUEST = MAX_PACKAGE_BYTES * 4 // 3 + MAX_ENTRIES * 1024

# ---------------------------------------------------------------------------
# What arrives
# ---------------------------------------------------------------------------


def files_from_folder(entries: object) -> dict[str, bytes]:
    """A folder's files by path, as the page sends them: ``[{"path": ..., "data": <base64>}, ...]``.

    Held to a zip's limits and path rules. Every path starts with the folder the person
    picked, which is taken off, and then any one folder wrapping the rest, as a zip's is.
    """

    if not isinstance(entries, list):
        raise SolutionRefused("The folder arrived without its files.")
    if len(entries) > MAX_ENTRIES:
        raise SolutionRefused(f"The folder holds more than {MAX_ENTRIES} files.")
    total = 0
    files: dict[str, bytes] = {}
    for entry in entries:
        name = entry.get("path") if isinstance(entry, dict) else None
        encoded = entry.get("data") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not isinstance(encoded, str):
            raise SolutionRefused("Each of the folder's files should arrive with its path and its contents.")
        path = safe_member(name)
        if path is None:
            raise SolutionRefused(f"{name[:200]} points outside the folder.")
        if path in files:
            raise SolutionRefused(f"The folder carries two files named {path}.")
        if len(encoded) > MAX_FILE_BYTES * 4 // 3 + 4:
            raise SolutionRefused(f"{path} is larger than {MAX_FILE_BYTES // 1024} KB.")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise SolutionRefused(f"{path} arrived garbled; choose the folder again.") from error
        if len(content) > MAX_FILE_BYTES:
            raise SolutionRefused(f"{path} is larger than {MAX_FILE_BYTES // 1024} KB.")
        total += len(content)
        if total > MAX_PACKAGE_BYTES:
            raise SolutionRefused(f"The folder holds more than {MAX_PACKAGE_BYTES // (1024 * 1024)} MB.")
        files[path] = content
    if not files:
        raise SolutionRefused("The folder is empty.")
    return without_wrapper(without_wrapper(files))


#: The MCP configurations a download from before 2026-10-01 carried, in the order one is taken.
_DOWNLOAD_CONFIGS = ("mcp.json", ".mcp.json", ".cursor/mcp.json")


def convert_download(files: Mapping[str, bytes]) -> tuple[dict[str, bytes], bool]:
    """A solution's files: the package's own, or written from a download made before downloads were solutions.

    Such a download is a root ``SKILL.md`` (the assistant's prompt as one skill), a
    ``README.md`` titled with the assistant's name, and the MCP configs, with no
    ``assistant.json``. The skill becomes the instructions and the first config the servers,
    with no declaration, so every tool asks before it runs. Its name is the skill's own, or,
    in an older download, which named it with its title, one made from that title.
    """

    # A plugin.json naming its assistant document makes a solution, even a broken one, which
    # the reader refuses with a sentence; a plain plugin, with no such member, converts.
    if _named_assistant(files) or ASSISTANT_FILE in files or "SKILL.md" not in files:
        return dict(files), False
    meta, body = parse_skill(_utf8(files, "SKILL.md"), "SKILL.md")
    given = meta.get("name", "").strip()
    named = _skill_name_or_empty(given)
    title = _readme_title(files) or (given if given and not named else "") or (named or "Assistant").replace("-", " ")
    title = title[:1].upper() + title[1:]
    name = named or skill_name_for(title)
    description = meta.get("description", "").strip() or title
    instructions = f"skills/{name}/SKILL.md"
    document: dict[str, Any] = {
        "format_version": "0.1",
        "name": name,
        "title": title,
        "description": description,
        "instructions": instructions,
        "skills": [],
    }
    solution = dict(files)
    solution[instructions] = render_skill(Skill(name, description, body)).encode("utf-8")
    config = next((path for path in _DOWNLOAD_CONFIGS if path in files), None)
    if config is not None:
        solution["mcp.json"] = files[config]
        document["mcp"] = "mcp.json"
    solution[ASSISTANT_FILE] = json.dumps(document, indent=2).encode("utf-8")
    plugin = {
        "$schema": PLUGIN_SCHEMA,
        "name": name,
        "description": description,
        "extensions": {"org.sigrix": {"assistant": ASSISTANT_FILE}},
    }
    solution["plugin.json"] = json.dumps(plugin, indent=2).encode("utf-8")
    return solution, True


def skill_name_for(title: str) -> str:
    """An Agent Skills name for a title: its words, lowercase and hyphenated, less the reserved ones."""

    words = [
        word for word in re.findall(r"[a-z0-9]+", title.lower()) if "anthropic" not in word and "claude" not in word
    ]
    name = "-".join(words)[:64].strip("-")
    return name or "assistant"


def _skill_name_or_empty(name: str) -> str:
    try:
        return check_skill_name(name, "SKILL.md")
    except SolutionRefused:
        return ""


def _readme_title(files: Mapping[str, bytes]) -> str:
    text = _decoded(files.get("README.md", b"")) or ""
    for line in text.splitlines()[:20]:
        match = re.fullmatch(r"#\s+(.+?)\s*#*\s*", line)
        if match:
            return match.group(1)[:120]
    return ""


def _utf8(files: Mapping[str, bytes], path: str) -> str:
    text = _decoded(files[path])
    if text is None:
        raise SolutionRefused(f"{path} is not UTF-8 text.")
    return text


def _decoded(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


# ---------------------------------------------------------------------------
# No credential value anywhere
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CredentialShape:
    """A shape of text that is a provider's credential or is nothing."""

    kind: str
    pattern: re.Pattern[str]


#: The credential shapes Sigrix's own listing scan looks for, less its JSON Web Token, and with
#: a private key held to its body as well as its banner. The platform's scan shows a moderator what it finds, and the moderator weighs
#: it; here a find refuses the package with nobody to weigh it, so only the shapes that are a
#: long-lived key or nothing are kept. A sample token and a key's banner are common in
#: documentation, and a token expires. Case-sensitive, as there: folding case makes prose match.
CREDENTIAL_SHAPES: tuple[CredentialShape, ...] = (
    CredentialShape("an AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # `sk-ant-` is a prefix of `sk-`, so the lookahead keeps an Anthropic key named as one.
    CredentialShape("an OpenAI API key", re.compile(r"\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{20,}")),
    CredentialShape("an Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    CredentialShape("a GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}")),
    CredentialShape("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    CredentialShape("a Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}")),
    CredentialShape("a Stripe live secret key", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{24,}")),
    CredentialShape("a SendGrid API key", re.compile(r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b")),
    CredentialShape(
        "a private key",
        re.compile(
            r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----\r?\n(?:[A-Za-z-]+: [^\n]*\r?\n)*\r?\n?"
            r"(?:[A-Za-z0-9+/=]{40,}\r?\n){2,}"
        ),
    ),
)

#: What marks a match as documentation rather than a key, checked against the matched text
#: alone, as the platform checks it: a real key does not spell one of these.
PLACEHOLDER_HINTS = (
    "example",
    "placeholder",
    "your-key",
    "yourkey",
    "your_key",
    "changeme",
    "change-me",
    "replace-me",
    "replaceme",
    "redacted",
    "xxxxxxxx",
    "notarealkey",
)

_ENV_FILE = re.compile(r"(?:^|/)\.env(?:\.[A-Za-z0-9_-]+)?$")
_EXAMPLE_ENV_FILE = re.compile(r"(?:^|/)\.env\.(?:example|sample|template|dist)$")
#: The words in a variable's name that make it a credential, which a ``.env`` may not fill.
_CREDENTIAL_WORDS = frozenset(
    {
        "KEY",
        "APIKEY",
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "PASSWD",
        "PASS",
        "PASSPHRASE",
        "CREDENTIAL",
        "CREDENTIALS",
        "AUTH",
        "PRIVATE",
        "COOKIE",
        "SESSION",
        "DSN",
        "PAT",
        "BEARER",
    }
)
#: A header a remote server is reached with that carries a credential, which a package may
#: fill only with a reference to a variable.
_CREDENTIAL_HEADER = re.compile(r"auth|token|key|secret|cookie|session|password", re.IGNORECASE)
_USERINFO_PASSWORD = re.compile(r"://[^/\s:@]+:[^/\s@]+@")


def check_credentials(files: Mapping[str, bytes]) -> None:
    """Refuse a package that carries a credential value, naming the file and never the value."""

    for path in sorted(files):
        text = _decoded(files[path])
        if text is None:
            continue
        name = path.rsplit("/", 1)[-1]
        if name.endswith("mcp.json"):
            _check_mcp_config(path, text)
        if _ENV_FILE.search(path) and not _EXAMPLE_ENV_FILE.search(path):
            _check_env_file(path, text)
        if name == "postern.json":
            _check_credential_entries(path, text)
        for shape in CREDENTIAL_SHAPES:
            for match in shape.pattern.finditer(text):
                if not any(hint in match.group(0).lower() for hint in PLACEHOLDER_HINTS):
                    raise SolutionRefused(
                        f"{path} carries what looks like {shape.kind}. A package names each key and never carries "
                        "one (Postern §4.1.3), so Bailey installs nothing from it."
                    )


def _check_mcp_config(path: str, text: str) -> None:
    try:
        config = json.loads(text)
    except ValueError:
        return  # one Bailey reads is refused by its reader; the others only the shapes are read in
    if not isinstance(config, dict):
        return
    for key in ("mcpServers", "servers"):
        servers = config.get(key)
        if not isinstance(servers, dict):
            continue
        for server, entry in servers.items():
            if not isinstance(entry, dict):
                continue
            where = f"{path}, server {str(server)[:64]!r}"
            env = entry.get("env")
            if isinstance(env, dict):
                for variable, value in env.items():
                    if not carries_no_value(value):
                        raise SolutionRefused(value_refused(where, str(variable)[:128]))
            headers = entry.get("headers")
            if isinstance(headers, dict):
                for header, value in headers.items():
                    if (
                        _CREDENTIAL_HEADER.search(str(header))
                        and isinstance(value, str)
                        and value.strip()
                        and "${" not in value
                    ):
                        raise SolutionRefused(value_refused(where, f"its {str(header)[:64]} header"))


def _check_env_file(path: str, text: str) -> None:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        stripped = stripped.removeprefix("export ").strip()
        variable, equals, value = stripped.partition("=")
        if not equals:
            continue
        variable, value = variable.strip(), value.strip()
        if value[:1] in ("'", '"'):
            value = value[1:].split(value[0], 1)[0]
        else:
            value = value.split(" #", 1)[0].strip()
        if not value:
            continue
        words = set(re.split(r"[^A-Z0-9]+", variable.upper()))
        is_credential = bool(words & _CREDENTIAL_WORDS) or any(
            word.endswith(("KEY", "TOKEN", "SECRET")) for word in words
        )
        if is_credential or _USERINFO_PASSWORD.search(value):
            raise SolutionRefused(value_refused(path, variable[:128]))


def _check_credential_entries(path: str, text: str) -> None:
    try:
        document = json.loads(text)
    except ValueError:
        return
    credentials = document.get("credentials") if isinstance(document, dict) else None
    for credential in credentials if isinstance(credentials, list) else ():
        if isinstance(credential, dict) and set(credential) - {"env", "purpose", "signup_url"}:
            variable = str(credential.get("env") or "a credential")[:128]
            raise SolutionRefused(
                f"{path} declares {variable} with more beside it than its purpose and where to get one, and a "
                "value has no place there (Postern §4.1.3), so Bailey installs nothing from it."
            )


# ---------------------------------------------------------------------------
# Skills that carry scripts
# ---------------------------------------------------------------------------

#: What makes a file in a skill's folder a script: Agent Skills keeps them in ``scripts/``,
#: and a program is known by its suffix or by the ``#!`` line it starts with.
_SCRIPT_SUFFIXES = frozenset(
    {
        "py",
        "pyw",
        "sh",
        "bash",
        "zsh",
        "fish",
        "js",
        "mjs",
        "cjs",
        "ts",
        "rb",
        "pl",
        "php",
        "lua",
        "ps1",
        "psm1",
        "bat",
        "cmd",
        "vbs",
        "command",
        "applescript",
        "scpt",
        "exe",
        "dll",
        "so",
        "dylib",
        "jar",
        "bin",
    }
)


def skill_files(made: Assistant, files: Mapping[str, bytes]) -> tuple[dict[str, tuple[str, ...]], tuple[str, ...]]:
    """For each skill the assistant installs, the scripts its folder carries; and the folder's other files.

    Bailey reads a skill's ``SKILL.md`` and its ``references/``. Anything else in the folder
    is left out: a script, which design decision 3 lets in only from the store, or another file.
    """

    scripts: dict[str, list[str]] = {}
    other: list[str] = []
    for skill in (made.instructions, *made.skills):
        prefix = f"skills/{skill.name}/"
        for path in sorted(files):
            rest = path.removeprefix(prefix)
            if rest == path or rest == "SKILL.md" or rest.startswith("references/"):
                continue
            leaf = rest.rsplit("/", 1)[-1]
            suffix = leaf.rsplit(".", 1)[-1].lower() if "." in leaf else ""
            if rest.startswith("scripts/") or suffix in _SCRIPT_SUFFIXES or files[path].startswith(b"#!"):
                scripts.setdefault(skill.name, []).append(path)
            else:
                other.append(path)
    return {name: tuple(paths) for name, paths in scripts.items()}, tuple(other)


def scripts_refused(scripts: Mapping[str, tuple[str, ...]]) -> str:
    skill, paths = next(iter(scripts.items()))
    more = sum(len(found) for found in scripts.values()) - 1
    named = paths[0] + (f" and {more} more" if more else "")
    return (
        f"The {skill} skill carries scripts ({named}). A skill with scripts installs only from the Sigrix store, "
        f'unless you turn on "{SCRIPTS_SETTING_LABEL}" in Settings. Bailey runs no skill\'s scripts, so it '
        "would install the skill without them."
    )


# ---------------------------------------------------------------------------
# The install screen
# ---------------------------------------------------------------------------


def install_screen(
    made: Assistant,
    files: Mapping[str, bytes],
    *,
    converted: bool,
    left_out: tuple[str, ...],
    provider: str,
) -> dict[str, Any]:
    """What the page shows before Install: everything the person needs, from what the package says."""

    document = _json(files, _assistant_path(files))
    keys: list[dict[str, Any]] = []
    shared = _shared_client_variables(files, document)
    for server in made.servers:
        for variable in server.env:
            keys.append(
                {
                    "name": variable,
                    "reads_it": f"the {server.name} tool server",
                    "server": server.name,
                    "purpose": "",
                    "signup_url": "",
                    "note": SHARED_CLIENT_NOTE if (server.name, variable) in shared else "",
                }
            )
    apps = []
    for app in made.apps:
        describe = _json(app.files, "postern.json")
        for credential in describe.get("credentials") or []:
            if not isinstance(credential, dict) or not isinstance(credential.get("env"), str):
                continue
            signup = str(credential.get("signup_url") or "")
            keys.append(
                {
                    "name": credential["env"],
                    "reads_it": f"the {app.title} app",
                    "app": app.name,
                    "purpose": str(credential.get("purpose") or ""),
                    "signup_url": signup if signup.startswith(("https://", "http://")) else "",
                    "note": PROVIDER_KEY_NOTE if credential["env"] in PROVIDER_KEYS.values() else APP_KEY_NOTE,
                }
            )
        apps.append(
            {
                "name": app.name,
                "title": app.title,
                "summary": app.summary,
                "download_mb": ENVIRONMENT_DOWNLOAD_MB,
                "disk_mb": ENVIRONMENT_DISK_MB,
                "provider_note": provider_note(describe, provider),
            }
        )
    tools = []
    for server in made.servers:
        tools.append(
            {
                "name": server.name,
                "remote": server.remote,
                "runs": server.url if server.remote else " ".join([server.command, *server.args]),
                "tools": None
                if server.tools is None
                else [{"name": tool, "asks": server.asks_before(tool)} for tool in server.tools],
            }
        )
    notices = [UNVERIFIED_NOTE]
    if converted:
        notices.append(CONVERTED_NOTE)
    if any(not server.remote for server in made.servers):
        notices.append(SANDBOX_NOTICE)
    if made.apps:
        notices.append(APPS_NOTICE)
    sold = document.get("skills_sold_separately")
    return {
        "name": made.name,
        "title": made.title,
        "description": made.description,
        "unverified": made.unverified,
        "converted": converted,
        "skills": [{"name": skill.name, "description": skill.description} for skill in made.skills],
        "skills_sold_separately": [str(name) for name in sold] if isinstance(sold, list) else [],
        "keys": keys,
        "knowledge": [
            {
                "name": slot.name,
                "description": slot.description,
                "accepts": list(slot.accepts),
                "required": slot.required,
                "sample": made.sample(slot.name) is not None,
            }
            for slot in made.knowledge
        ],
        "tools": tools,
        "apps": apps,
        "notices": notices,
        "left_out": list(left_out),
    }


def _named_assistant(files: Mapping[str, bytes]) -> str:
    """The assistant document ``plugin.json`` names, or ``""`` where it names none."""

    extensions = _json(files, "plugin.json").get("extensions")
    sigrix = extensions.get("org.sigrix") if isinstance(extensions, dict) else None
    named = sigrix.get("assistant") if isinstance(sigrix, dict) else None
    return named if isinstance(named, str) and named else ""


def _assistant_path(files: Mapping[str, bytes]) -> str:
    return _named_assistant(files) or ASSISTANT_FILE


def _json(files: Mapping[str, bytes], path: str) -> dict[str, Any]:
    try:
        value = json.loads(files[path].decode("utf-8"))
    except (KeyError, UnicodeDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _shared_client_variables(files: Mapping[str, bytes], document: Mapping[str, Any]) -> set[tuple[str, str]]:
    """Each server's variables the download fills with Sigrix's shared sign-in client."""

    path = document.get("mcp")
    servers = _json(files, path).get("mcpServers") if isinstance(path, str) else None
    found: set[tuple[str, str]] = set()
    for server, entry in servers.items() if isinstance(servers, dict) else ():
        env = entry.get("env") if isinstance(entry, dict) else None
        for variable, value in env.items() if isinstance(env, dict) else ():
            if isinstance(value, str) and SHARED_CLIENT_PLACEHOLDER.fullmatch(value):
                found.add((str(server), str(variable)))
    return found


# ---------------------------------------------------------------------------
# Checked, shown, and installed
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Checked:
    """A package that passed every check: the assistant it installs, what it leaves out, and its screen."""

    made: Assistant
    left_out: tuple[str, ...]
    app_left_out: tuple[str, ...]
    screen: dict[str, Any]


def check_package(files: Mapping[str, bytes], *, scripts_allowed: bool, provider: str) -> Checked:
    """Every check a package meets before anything is written, and its install screen; or a refusal saying why."""

    check_credentials(files)
    solution, converted = convert_download(files)
    if ASSISTANT_FILE not in solution and not _named_assistant(solution):
        raise SolutionRefused(
            "This is not a solution Bailey can install: it carries no assistant.json, and no SKILL.md to make one from."
        )
    made = replace(read_solution(solution), origin=ORIGIN_FILE)
    _, app_left_out = read_apps(solution)
    scripts, other = skill_files(made, solution)
    if scripts and not scripts_allowed:
        raise SolutionRefused(scripts_refused(scripts))
    left_out = (*app_left_out, *other, *(path for paths in scripts.values() for path in paths))
    screen = install_screen(made, solution, converted=converted, left_out=left_out, provider=provider)
    return Checked(made=made, left_out=left_out, app_left_out=tuple(app_left_out), screen=screen)


@dataclass(frozen=True)
class _Held:
    checked: Checked
    until: float


class PendingInstalls:
    """Packages checked and shown, each waiting for the person's Install for a while.

    Install writes what was held, never a package sent again, so what is written is what was
    checked and shown, whatever happened to the file in between.
    """

    def __init__(self, *, keep_for: float = PENDING_SECONDS, most: int = MAX_PENDING) -> None:
        self.keep_for = keep_for
        self.most = most
        self._held: dict[str, _Held] = {}
        self._lock = threading.Lock()

    def hold(self, checked: Checked) -> str:
        install_id = secrets.token_urlsafe(16)
        with self._lock:
            now = time.monotonic()
            for stale in [key for key, held in self._held.items() if held.until <= now]:
                del self._held[stale]
            while len(self._held) >= self.most:
                del self._held[min(self._held, key=lambda key: self._held[key].until)]
            self._held[install_id] = _Held(checked, now + self.keep_for)
        return install_id

    def take(self, install_id: str) -> Checked | None:
        with self._lock:
            held = self._held.pop(install_id, None)
        if held is None or held.until <= time.monotonic():
            return None
        return held.checked

    def drop(self, install_id: str) -> None:
        with self._lock:
            self._held.pop(install_id, None)
