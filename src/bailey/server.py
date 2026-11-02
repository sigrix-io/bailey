"""The app server: Bailey's page and its API, on this computer's own address, for one person.

``uvx bailey`` runs :func:`serve`: it opens the data folder, the local store and the keys,
listens on 127.0.0.1, and opens the browser on the launch address.

Two rules hold for every request, in one middleware (:class:`Guard`), so no route can be
added without them:

- **The Host is a loopback name** (``127.0.0.1``, ``localhost``, ``[::1]``). A page that
  points its own domain at 127.0.0.1 still sends its domain as the Host, and is refused:
  that is DNS rebinding.
- **Everything but the page carries this session's token**, as ``Authorization: Bearer
  <token>``. Any web page can send a request to a loopback port, but none can read this
  page's token, or add that header without the server's leave, which no CORS answer
  gives. So a request without it is refused, and a route added later is refused too until
  it is named in :data:`PUBLIC_PATHS`, where only the page and its static files are.

The page and its static files carry no secret: they are the files the wheel ships. The
token reaches the page in the launch address's fragment, which a browser never sends to
a server or in a Referer; the page keeps it and takes it out of the address bar. Every
response carries a CSP that allows nothing from another origin and no inline script or
style, and the page writes what it is given as text, never as markup.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import shlex
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from importlib import resources
from pathlib import Path
from types import FrameType
from typing import Any, TextIO

import uvicorn
from keyring.errors import KeyringError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from bailey import __version__
from bailey.apps import (
    APPS_NOTICE,
    ENVIRONMENT_DISK_MB,
    ENVIRONMENT_DOWNLOAD_MB,
    PASSAGES_INPUT,
    AppFailed,
    AppHost,
    Environments,
    VirtualEnvironments,
    app_folder,
    describe_of,
    passages_input,
    provider_note,
    read_app_files,
    remove_apps,
    requirements_of,
    write_apps,
)
from bailey.conversations import notice_record, pending, shown, shown_text, to_history, to_records, user_record
from bailey.core import load as load_core
from bailey.keys import (
    FILE_NAME,
    EncryptedFileKeys,
    KeychainKeys,
    KeysLocked,
    PassphraseRefused,
    UnreadableKeyFile,
    WrongPassphrase,
    open_keys,
)
from bailey.knowledge import (
    ACCEPTED_TYPES,
    LOCAL_WHOLE_LIMIT,
    MAX_DOCUMENT_BYTES,
    MINILM,
    WHOLE_LIMIT,
    WHOLE_PROVIDERS,
    DocumentRefused,
    Documents,
    Embedder,
    LocalEmbedder,
    add_document,
    cited,
    extension_of,
    mode,
    passage_text,
    search,
    whole_text,
)
from bailey.knowledge import (
    index as index_collection,
)
from bailey.paths import data_folder, ensure_data_folder
from bailey.settings import (
    PROVIDERS,
    Connection,
    SettingRefused,
    clean_address,
    clean_key,
    clean_model,
    key_hint,
    key_name,
    provider,
)
from bailey.solutions import (
    DEFAULT_TEMPLATE,
    MAX_PACKAGE_BYTES,
    Assistant,
    SolutionRefused,
    assistant_from_form,
    form_of,
    read_apps,
    read_solution,
    template,
    template_ids,
    unzip_solution,
    zip_solution,
)
from bailey.store import STORE_FILE, NewerStore, Store
from bailey.tools import (
    SANDBOX_NOTICE,
    SIGRIX_TOKEN,
    ToolHost,
    ToolServerFailed,
    environment_for,
    missing_variables,
    variable_key,
)

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

#: Set on every response, whatever answered it.
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", CSP.encode("ascii")),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"cache-control", b"no-store"),
)
_SECURITY_HEADER_NAMES = frozenset(name for name, _ in SECURITY_HEADERS)

LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})

#: The static files the page loads, each with its type. Nothing else is served from disk.
#: Gatehouse, which draws an app's run page, is a vendored release (``web/gatehouse/``).
STATIC_FILES: Mapping[str, str] = {
    "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "icon.svg": "image/svg+xml",
    "gatehouse/postern_client.js": "text/javascript; charset=utf-8",
    "gatehouse/run_view_model.js": "text/javascript; charset=utf-8",
    "gatehouse/refusal_copy.js": "text/javascript; charset=utf-8",
    "gatehouse/run_renderer.js": "text/javascript; charset=utf-8",
    "gatehouse/gatehouse.css": "text/css; charset=utf-8",
}

#: The only paths answered without the token: the page and its static files.
PUBLIC_PATHS = frozenset({"/", *(f"/static/{name}" for name in STATIC_FILES)})

#: An app's four Postern verbs, each with its method.
POSTERN_VERBS = {"describe": "GET", "status": "GET", "run": "POST", "stream": "POST"}
#: A run's body, as a runner takes it (Postern §2.2).
MAX_RUN_BODY = 1024 * 1024
#: The most of an assistant's documents an app's ``passages`` input carries, in characters.
MAX_PASSAGES_CHARACTERS = 400_000

#: No request Bailey's page sends is near this, but a message, which may carry a document.
MAX_BODY = 64 * 1024

#: A message may carry a pasted document to summarise.
MAX_MESSAGE_BODY = 1024 * 1024
MAX_MESSAGE_CHARACTERS = 200_000

#: A conversation is named after its first message, cut at a word to about this length.
TITLE_LENGTH = 60

#: The longest value Bailey keeps for a tool server's variable.
MAX_VARIABLE_VALUE = 8192

#: The answers a person gives a tool call that asks first.
ANSWERS = ("once", "always", "no")

NOT_FROM_THE_PAGE = (
    "This request did not come from Bailey's page. Open Bailey at the address it printed when it started."
)


@dataclass
class AppState:
    token: str
    store: Store
    keys: KeychainKeys | EncryptedFileKeys
    data_folder: Path
    #: Where each provider's API is, when it is not the provider's own: the tests point
    #: them at an emulator. Empty in the app, where each provider's address is its own.
    base_urls: Mapping[str, str] = field(default_factory=dict)
    #: Conversations with an answer still coming, which take no second message until it ends.
    running: set[str] = field(default_factory=set)
    #: Each assistant's MCP servers, started at the first message that needs them and kept running.
    tools: ToolHost = field(default_factory=ToolHost)
    #: The search model; made from the data folder on first use, and handed in by the tests.
    embedder: Embedder | None = None
    #: Where each app's Python comes from (bailey.apps); made from the data folder on
    #: first use, and handed in by the tests.
    environments: Environments | None = None
    #: Each open app's runner, started when its page opens.
    app_host: AppHost | None = None


class Refusal(Exception):
    """A request answered with an error and the sentence that says why."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _host_name(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[: end + 1] if end != -1 else host
    return host.rsplit(":", 1)[0]


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


class Guard:
    """The two rules every request meets, and the headers every response carries."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            # Nothing in Bailey speaks WebSocket yet, so nothing is let through.
            await send({"type": "websocket.close", "code": 1008})
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                kept = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if bytes(name).lower() not in _SECURITY_HEADER_NAMES
                ]
                message = {**message, "headers": kept + list(SECURITY_HEADERS)}
            await send(message)

        headers = Headers(scope=scope)
        if _host_name(headers.get("host", "")) not in LOOPBACK_NAMES:
            refusal = _error(403, "Bailey answers only on this computer's own address.")
            await refusal(scope, receive, send_with_headers)
            return
        if scope["path"] not in PUBLIC_PATHS and not secrets.compare_digest(
            headers.get("authorization", "").encode(), self._expected
        ):
            await _error(401, NOT_FROM_THE_PAGE)(scope, receive, send_with_headers)
            return
        await self.app(scope, receive, send_with_headers)


async def _read_body(request: Request, limit: int) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise Refusal(413, "That request is larger than Bailey accepts.")
    return bytes(body)


async def _json_body(request: Request, limit: int = MAX_BODY) -> dict[str, Any]:
    body = await _read_body(request, limit)
    try:
        value = json.loads(bytes(body) or b"{}")
    except ValueError as error:
        raise Refusal(400, "Send the request as JSON.") from error
    if not isinstance(value, dict):
        raise Refusal(400, "Send a JSON object.")
    return value


def _answers(handler: Callable[[Request], Awaitable[Response]]) -> Callable[[Request], Awaitable[Response]]:
    """Turn what a handler refuses into a JSON answer with a sentence, never a traceback."""

    async def endpoint(request: Request) -> Response:
        try:
            return await handler(request)
        except Refusal as refusal:
            return _error(refusal.status, refusal.message)
        except (SettingRefused, SolutionRefused, DocumentRefused) as refusal:
            return _error(400, str(refusal))
        except KeysLocked:
            return _error(423, "Unlock your keys with their passphrase first.")
        except KeyringError as error:
            return _error(503, f"The keychain refused: {error}")

    return endpoint


def _keys_status(keys: KeychainKeys | EncryptedFileKeys) -> dict[str, Any]:
    status: dict[str, Any] = {"kind": keys.kind, "where": keys.where, "locked": keys.locked}
    if isinstance(keys, EncryptedFileKeys):
        status["has_passphrase"] = keys.has_passphrase
    return status


def _settings_view(state: AppState) -> dict[str, Any]:
    """The settings as the page draws them. A key appears as whether it is saved and a hint, never itself."""

    chosen = state.store.get_setting("provider")
    providers = []
    for entry in PROVIDERS:
        described = entry.as_dict()
        if state.keys.locked:
            described["key"] = None
        else:
            value = state.keys.get(key_name(entry.id))
            described["key"] = {"saved": value is not None, "hint": key_hint(value) if value else ""}
        providers.append(described)
    models = state.store.get_setting("models") or {}
    return {
        "provider": chosen if any(entry.id == chosen for entry in PROVIDERS) else None,
        "address": state.store.get_setting("address") or "",
        "models": {entry.id: str(models.get(entry.id) or entry.default_model) for entry in PROVIDERS},
        "providers": providers,
        "keys": _keys_status(state.keys),
    }


def _connection(state: AppState) -> Connection:
    """How to reach the model the person chose, or a refusal naming the setting still missing."""

    chosen = state.store.get_setting("provider")
    if not chosen:
        raise Refusal(409, "Choose an AI provider in Settings first.")
    setup = provider(chosen)
    model = str((state.store.get_setting("models") or {}).get(setup.id) or setup.default_model)
    if not model:
        raise Refusal(409, "Give the model's name in Settings first.")
    base_url = state.base_urls.get(setup.id) or setup.base_url or str(state.store.get_setting("address") or "")
    if not base_url:
        raise Refusal(409, "Give your server's address in Settings first.")
    key = state.keys.get(key_name(setup.id)) or ""
    if setup.key_required and not key:
        raise Refusal(409, f"Save your {setup.name} key in Settings first.")
    return Connection(provider=setup.id, model=model, api_key=key, base_url=base_url)


def _ensure_an_assistant(state: AppState) -> None:
    """A first start, or one after the last assistant was deleted, gets the default one: never an empty canvas."""

    if not state.store.list_assistants():
        assistant_id = uuid.uuid4().hex
        state.store.put_assistant(assistant_id, template(DEFAULT_TEMPLATE).as_dict())
        state.store.set_setting("assistant", assistant_id)


def _active_assistant_id(state: AppState) -> str:
    _ensure_an_assistant(state)
    chosen = state.store.get_setting("assistant")
    known = [assistant_id for assistant_id, _ in state.store.list_assistants()]
    if chosen in known:
        return str(chosen)
    state.store.set_setting("assistant", known[0])
    return known[0]


def _assistant(state: AppState, assistant_id: str) -> Assistant:
    record = state.store.get_assistant(assistant_id)
    if record is None:
        raise Refusal(404, "Bailey has no assistant by that id.")
    return Assistant.from_dict(record)


def _assistants_view(state: AppState) -> dict[str, Any]:
    active = _active_assistant_id(state)
    assistants = []
    for assistant_id, record in state.store.list_assistants():
        assistants.append(
            {
                "id": assistant_id,
                "title": record["title"],
                "description": record.get("description") or "",
                "skills": [skill["name"] for skill in record.get("skills") or []],
                "starters": list(record.get("starters") or []),
                "servers": [server["name"] for server in record.get("servers") or []],
                "apps": [app["title"] for app in record.get("apps") or []],
            }
        )
    templates = []
    for template_id in template_ids():
        made = template(template_id)
        templates.append({"id": template_id, "title": made.title, "description": made.description})
    return {"active": active, "assistants": assistants, "templates": templates}


def _title_for(text: str) -> str:
    line = " ".join(text.split())
    if len(line) <= TITLE_LENGTH:
        return line or "New conversation"
    cut = line[:TITLE_LENGTH].rsplit(" ", 1)[0] or line[:TITLE_LENGTH]
    return cut + "…"


def _without_the_prompt(records: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    """The run's records less the person's message, which was stored before the run began."""

    if records and records[0]["role"] == "user" and records[0]["parts"] == [{"kind": "text", "text": text}]:
        return records[1:]
    return records


def _event(data: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def _listed(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _tool_environments(state: AppState, assistant_id: str, made: Assistant) -> dict[str, dict[str, str]]:
    """What each of the assistant's local servers receives, or a refusal naming a variable still missing."""

    environments: dict[str, dict[str, str]] = {}
    for server in made.servers:
        if server.remote:
            continue
        values = {
            name: value
            for name in server.env
            if (value := state.keys.get(variable_key(assistant_id, server.name, name)))
        }
        missing = missing_variables(server, values)
        if missing:
            them = "it" if len(missing) == 1 else "them"
            raise Refusal(
                409,
                f"The {server.name} tool server needs {_listed(missing)}. Give {them} in this assistant's Tools, "
                "under Assistants.",
            )
        environments[server.name] = environment_for(server, values, os.environ)
    return environments


def _embedder(state: AppState) -> Embedder:
    """The search model: in the data folder once fetched, or in the folder ``BAILEY_SEARCH_MODEL`` names."""

    if state.embedder is None:
        named = os.environ.get("BAILEY_SEARCH_MODEL")
        state.embedder = LocalEmbedder(state.data_folder / "models", folder=Path(named) if named else None)
    return state.embedder


def _sent_whole_limit(provider: str) -> int:
    return WHOLE_LIMIT if provider in WHOLE_PROVIDERS else LOCAL_WHOLE_LIMIT


def _collection(state: AppState, assistant_id: str, provider: str) -> tuple[list[dict[str, Any]], int, str]:
    """The assistant's documents, the tokens of those sent whole, and how they reach the model.

    The seller's reference files are kept for search alone: whole, they would be sent a
    second time, beside their skill.
    """

    documents = state.store.documents(assistant_id)
    tokens = sum(int(document["tokens"]) for document in documents if document["kind"] != "reference")
    return documents, tokens, mode(tokens, provider) if tokens else "none"


def _download_first(made: Assistant, provider: str) -> str:
    why = "too long to send whole" if provider in WHOLE_PROVIDERS else "searched, as on any local server"
    return (
        f"{made.title}'s documents are {why}, so Bailey searches them with a model it downloads once from Hugging "
        f"Face: {MINILM.label}, about {MINILM.download_mb} MB. Download it under Assistants, in {made.title}'s "
        "Documents."
    )


def _environments(state: AppState) -> Environments:
    if state.environments is None:
        state.environments = VirtualEnvironments(state.data_folder / "environments")
    return state.environments


def _app_host(state: AppState) -> AppHost:
    if state.app_host is None:
        state.app_host = AppHost(_environments(state))
    return state.app_host


def _apps_view(state: AppState, assistant_id: str, made: Assistant) -> dict[str, Any]:
    """The assistant's Apps panel: each app, whether its environment is built, and whether the provider fits it."""

    provider = str(state.store.get_setting("provider") or "")
    environments = _environments(state)
    host = _app_host(state)
    apps = []
    for app in made.apps:
        folder = app_folder(state.data_folder, assistant_id, app.name)
        environment, why = environments.state(requirements_of(folder))
        apps.append(
            {
                "name": app.name,
                "title": app.title,
                "summary": app.summary,
                "ui": app.ui,
                "environment": environment,
                "why": why,
                "provider_note": provider_note(describe_of(folder), provider),
                "running": host.running(assistant_id, app.name),
            }
        )
    return {
        "id": assistant_id,
        "apps": apps,
        "download_mb": ENVIRONMENT_DOWNLOAD_MB,
        "disk_mb": ENVIRONMENT_DISK_MB,
        "notice": APPS_NOTICE if made.apps else "",
    }


def _app_passages(state: AppState, assistant_id: str, made: Assistant, provider: str, prompt: str, limit: int) -> str:
    """The passages the assistant's documents hold for an app's run, numbered as in chat, whole passages only."""

    documents = _documents_for(state, assistant_id, made, provider)
    if documents is None:
        return ""
    if documents.mode == "whole":
        found = [passage for passage in state.store.passages(assistant_id) if passage["kind"] != "reference"]
    else:
        found = documents.search(prompt) if documents.search is not None and prompt.strip() else []
    pieces: list[str] = []
    used = 0
    for passage in found:
        text = passage_text(passage)
        if used + len(text) + 2 > limit:
            break
        pieces.append(text)
        used += len(text) + 2
    return "\n\n".join(pieces)


def _documents_for(state: AppState, assistant_id: str, made: Assistant, provider: str) -> Documents | None:
    """How the assistant's documents reach the model in this run, or a refusal saying what to fetch first."""

    _, _, way = _collection(state, assistant_id, provider)
    if way == "whole":
        passages = [passage for passage in state.store.passages(assistant_id) if passage["kind"] != "reference"]
        return Documents("whole", text=whole_text(passages), grounded_only=made.grounded_only)
    if way == "search":
        embedder = _embedder(state)
        if not embedder.ready():
            raise Refusal(409, _download_first(made, provider))
        index_collection(state.store, assistant_id, embedder)
        return Documents(
            "search",
            search=lambda query: search(state.store, assistant_id, query, embedder),
            grounded_only=made.grounded_only,
        )
    return None


def _citations(state: AppState, assistant_id: str, records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The passages an answer cites, ``[P12]``, each as its document, heading, page and text.

    A number that is not one of this assistant's passages is left out.
    """

    ids = cited("\n".join(shown_text(record) for record in records if record["role"] == "assistant"))
    found = {passage["id"]: passage for passage in state.store.passages(assistant_id, ids)}
    return [
        {
            "id": passage_id,
            "document": found[passage_id]["document"],
            "heading": found[passage_id]["heading"],
            "page": found[passage_id]["page"],
            "text": found[passage_id]["body"],
        }
        for passage_id in ids
        if passage_id in found
    ]


def _knowledge_view(state: AppState, assistant_id: str, made: Assistant) -> dict[str, Any]:
    """The assistant's Documents panel: its slots, its other documents, and how they reach the model."""

    provider = str(state.store.get_setting("provider") or "")
    documents, tokens, way = _collection(state, assistant_id, provider)
    by_slot = {document["slot"]: document for document in documents if document["slot"]}
    return {
        "id": assistant_id,
        "slots": [
            {**slot.as_dict(), "sample": made.sample(slot.name) is not None, "document": by_slot.get(slot.name)}
            for slot in made.knowledge
        ],
        "documents": [document for document in documents if not document["slot"]],
        "tokens": tokens,
        "limit": _sent_whole_limit(provider),
        "mode": way,
        "accepts": sorted(ACCEPTED_TYPES),
        "grounded_only": made.grounded_only,
        "model": {
            "label": MINILM.label,
            "size_mb": MINILM.download_mb,
            "source": "Hugging Face",
            "ready": _embedder(state).ready() if way == "search" or state.embedder is not None else False,
        },
    }


def _granted(state: AppState, assistant_id: str) -> set[tuple[str, str]]:
    return {(grant["server"], grant["tool"]) for grant in state.store.grants(assistant_id)}


def _records(
    messages: Sequence[Any],
    connection: Connection,
    servers: Mapping[str, str],
    asked: Collection[str],
    text: str | None,
) -> list[dict[str, Any]]:
    records = to_records(messages, provider=connection.provider, model=connection.model, servers=servers, asked=asked)
    return _without_the_prompt(records, text) if text is not None else records


def _with_usage(
    records: list[dict[str, Any]], usage: dict[str, Any], estimate: Any, citations: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """The run's usage, and the passages its answer cites, on its last answer, where the page reads them."""

    for record in reversed(records):
        if record["role"] == "assistant":
            record["usage"] = usage
            record["estimate"] = estimate
            if citations:
                record["citations"] = citations
            break
    return records


def command_line(words: Sequence[str], system: str | None = None) -> str:
    """A command as the person's own shell would write it: cmd's quoting on Windows, a POSIX shell's elsewhere.

    ``shlex`` quotes for a POSIX shell alone, and on Windows it wraps every path with a
    backslash in single quotes, which no Windows shell reads as quoting.
    """

    if (system or os.name) == "nt":
        return subprocess.list2cmdline(list(words))
    return shlex.join(words)


def _tools_view(state: AppState, assistant_id: str, made: Assistant) -> dict[str, Any]:
    """The assistant's Tools panel: each server, its tools with the writes marked, and its variables."""

    locked = state.keys.locked
    servers = []
    for server in made.servers:
        variables = [
            {
                "name": name,
                "saved": not locked and bool(state.keys.get(variable_key(assistant_id, server.name, name))),
                "shared": name == SIGRIX_TOKEN,
            }
            for name in server.env
        ]
        servers.append(
            {
                "name": server.name,
                "remote": server.remote,
                "launch": server.url if server.remote else command_line([server.command, *server.args]),
                "declared": server.tools is not None,
                "tools": [{"name": tool, "writes": tool in server.write_tools} for tool in server.tools or ()],
                "variables": variables,
            }
        )
    return {
        "id": assistant_id,
        "notice": SANDBOX_NOTICE,
        "locked": locked,
        "servers": servers,
        "grants": state.store.grants(assistant_id),
    }


def _grants_view(state: AppState) -> dict[str, Any]:
    titles = {assistant_id: str(record.get("title") or "") for assistant_id, record in state.store.list_assistants()}
    return {
        "grants": [
            {**grant, "assistant_title": titles.get(grant["assistant_id"], "")} for grant in state.store.grants()
        ]
    }


def _file_keys(state: AppState) -> EncryptedFileKeys:
    if not isinstance(state.keys, EncryptedFileKeys):
        raise Refusal(409, f"Your keys are kept in {state.keys.where}, which has no passphrase of Bailey's.")
    return state.keys


def _postern_error(status: int, code: str, message: str) -> JSONResponse:
    """An answer in Postern's error envelope, which Gatehouse shows as the runner's own."""

    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _read_web(name: str) -> bytes:
    return resources.files("bailey").joinpath("web", *name.split("/")).read_bytes()


def build_app(state: AppState) -> ASGIApp:
    page = _read_web("index.html")
    static = {name: _read_web(name) for name in STATIC_FILES}

    async def index(request: Request) -> Response:
        return Response(page, media_type="text/html; charset=utf-8")

    async def asset(request: Request) -> Response:
        name = request.path_params["name"]
        if name not in static:  # unreachable past the guard, which answers only PUBLIC_PATHS without the token
            return Response(status_code=404)
        return Response(static[name], media_type=STATIC_FILES[name])

    @_answers
    async def read_status(request: Request) -> Response:
        return JSONResponse(
            {"version": __version__, "data_folder": str(state.data_folder), "keys": _keys_status(state.keys)}
        )

    @_answers
    async def read_settings(request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(_settings_view, state))

    @_answers
    async def write_settings(request: Request) -> Response:
        body = await _json_body(request)
        chosen = provider(body.get("provider"))
        address = clean_address(body.get("address")) if chosen.needs_address else None
        # Saved as given, one setting at a time; a chat names the setting still missing.
        model = clean_model(body["model"]) if body.get("model") not in (None, "") else None

        def save() -> dict[str, Any]:
            state.store.set_setting("provider", chosen.id)
            if address is not None:
                state.store.set_setting("address", address)
            if model is not None:
                models = dict(state.store.get_setting("models") or {})
                models[chosen.id] = model
                state.store.set_setting("models", models)
            return _settings_view(state)

        return JSONResponse(await run_in_threadpool(save))

    def _named_provider(request: Request) -> str:
        try:
            return provider(request.path_params["provider"]).id
        except SettingRefused as refusal:
            raise Refusal(404, "Bailey knows no provider by that name.") from refusal

    @_answers
    async def save_key(request: Request) -> Response:
        provider_id = _named_provider(request)
        key = clean_key((await _json_body(request)).get("key"))

        def save() -> dict[str, Any]:
            state.keys.set(key_name(provider_id), key)
            return _settings_view(state)

        return JSONResponse(await run_in_threadpool(save))

    @_answers
    async def remove_key(request: Request) -> Response:
        provider_id = _named_provider(request)

        def remove() -> dict[str, Any]:
            state.keys.delete(key_name(provider_id))
            return _settings_view(state)

        return JSONResponse(await run_in_threadpool(remove))

    @_answers
    async def unlock_keys(request: Request) -> Response:
        keys = _file_keys(state)
        passphrase = (await _json_body(request)).get("passphrase")
        if not isinstance(passphrase, str) or not passphrase:
            raise Refusal(400, "Type the passphrase first.")
        if not keys.has_passphrase:
            raise Refusal(409, "No passphrase has been chosen yet. Choose one first.")
        try:
            await run_in_threadpool(keys.unlock, passphrase)
        except WrongPassphrase as error:
            raise Refusal(400, "That passphrase does not open your key file.") from error
        except UnreadableKeyFile as error:
            raise Refusal(409, str(error)) from error
        return JSONResponse(await run_in_threadpool(_settings_view, state))

    @_answers
    async def choose_passphrase(request: Request) -> Response:
        keys = _file_keys(state)
        passphrase = (await _json_body(request)).get("passphrase")
        if not isinstance(passphrase, str):
            raise Refusal(400, "Type a passphrase first.")
        if keys.has_passphrase:
            raise Refusal(409, "A passphrase is already set. Unlock with it, or start over.")
        try:
            await run_in_threadpool(keys.create, passphrase)
        except PassphraseRefused as error:
            raise Refusal(400, str(error)) from error
        return JSONResponse(await run_in_threadpool(_settings_view, state))

    @_answers
    async def reset_keys(request: Request) -> Response:
        keys = _file_keys(state)
        await run_in_threadpool(keys.reset)
        return JSONResponse(await run_in_threadpool(_settings_view, state))

    # -- assistants ------------------------------------------------------------

    @_answers
    async def list_assistants(request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(_assistants_view, state))

    @_answers
    async def create_assistant(request: Request) -> Response:
        template_id = (await _json_body(request)).get("template")
        if not isinstance(template_id, str) or template_id not in template_ids():
            raise Refusal(400, "Choose one of the templates listed.")

        def create() -> dict[str, Any]:
            _ensure_an_assistant(state)  # the default is there whatever was asked for first
            assistant_id = uuid.uuid4().hex
            state.store.put_assistant(assistant_id, template(template_id).as_dict())
            return {"id": assistant_id, **_assistants_view(state)}

        return JSONResponse(await run_in_threadpool(create))

    @_answers
    async def import_assistant(request: Request) -> Response:
        data = await _read_body(request, MAX_PACKAGE_BYTES)
        files = unzip_solution(data)
        made = read_solution(files)
        _, left_out = read_apps(files)

        def save() -> dict[str, Any]:
            _ensure_an_assistant(state)
            assistant_id = uuid.uuid4().hex
            state.store.put_assistant(assistant_id, made.as_dict())
            # A solution with tool servers runs programs on this computer, and the person
            # is told so as it arrives (design §4). So does a solution with apps.
            notices = [SANDBOX_NOTICE] if any(not server.remote for server in made.servers) else []
            if made.apps:
                write_apps(state.data_folder, assistant_id, made.apps)
                notices.append(APPS_NOTICE)
            if left_out:
                notices.append(
                    f"Left out of its apps: {', '.join(left_out)}. A package's .env would carry its seller's keys "
                    "or a listing's id, and Bailey gives an app your provider's key itself."
                )
            notice = " ".join(notices)
            # The seller's reference files are kept for search too (design §3.7), so a large
            # one is found without its skill loaded. One Bailey cannot read stays with its skill.
            for skill in (made.instructions, *made.skills):
                for name, text in skill.references:
                    with contextlib.suppress(DocumentRefused):
                        add_document(
                            state.store,
                            assistant_id,
                            f"{skill.name}/references/{name}",
                            text.encode("utf-8"),
                            kind="reference",
                        )
            return {
                "id": assistant_id,
                "notice": notice,
                "asks_for_documents": bool(made.knowledge),
                **_assistants_view(state),
            }

        return JSONResponse(await run_in_threadpool(save))

    @_answers
    async def read_assistant(request: Request) -> Response:
        assistant_id = request.path_params["assistant_id"]
        made = await run_in_threadpool(_assistant, state, assistant_id)
        return JSONResponse({"id": assistant_id, **form_of(made)})

    @_answers
    async def write_assistant(request: Request) -> Response:
        assistant_id = request.path_params["assistant_id"]
        form = await _json_body(request, MAX_MESSAGE_BODY)

        def save() -> dict[str, Any]:
            edited = assistant_from_form(form, _assistant(state, assistant_id))
            state.store.put_assistant(assistant_id, edited.as_dict())
            return {"id": assistant_id, **form_of(edited)}

        return JSONResponse(await run_in_threadpool(save))

    @_answers
    async def use_assistant(request: Request) -> Response:
        assistant_id = request.path_params["assistant_id"]

        def use() -> dict[str, Any]:
            _assistant(state, assistant_id)
            state.store.set_setting("assistant", assistant_id)
            return _assistants_view(state)

        return JSONResponse(await run_in_threadpool(use))

    @_answers
    async def export_assistant(request: Request) -> Response:
        assistant_id = request.path_params["assistant_id"]

        def gather() -> Assistant:
            made = _assistant(state, assistant_id)
            apps = tuple(
                replace(app, files=read_app_files(state.data_folder, assistant_id, app.name)) for app in made.apps
            )
            return replace(made, apps=apps)

        made = await run_in_threadpool(gather)
        return Response(
            zip_solution(made),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{made.name}.zip"'},
        )

    @_answers
    async def delete_assistant(request: Request) -> Response:
        assistant_id = request.path_params["assistant_id"]

        def delete() -> dict[str, Any]:
            made = _assistant(state, assistant_id)
            state.store.delete_assistant(assistant_id)
            # Its tool servers' values go with it. The Sigrix token is every Sigrix server's,
            # and stays; locked keys keep theirs, unreadable to anyone without the passphrase.
            for server in made.servers:
                for name in server.env:
                    if name != SIGRIX_TOKEN:
                        with contextlib.suppress(KeysLocked):
                            state.keys.delete(variable_key(assistant_id, server.name, name))
            return _assistants_view(state)

        view = await run_in_threadpool(delete)
        await state.tools.stop(assistant_id)
        await _app_host(state).stop(assistant_id)
        await run_in_threadpool(remove_apps, state.data_folder, assistant_id)
        return JSONResponse(view)

    # -- tools -------------------------------------------------------------------

    @_answers
    async def read_tools(request: Request) -> Response:
        assistant_id = request.path_params["assistant_id"]

        def read() -> dict[str, Any]:
            return _tools_view(state, assistant_id, _assistant(state, assistant_id))

        return JSONResponse(await run_in_threadpool(read))

    def _named_variable(request: Request) -> tuple[str, Assistant, str, str]:
        assistant_id = request.path_params["assistant_id"]
        made = _assistant(state, assistant_id)
        server = made.server(request.path_params["server"])
        if server is None or server.remote:
            raise Refusal(404, "This assistant has no tool server by that name.")
        name = request.path_params["variable"]
        if name not in server.env:
            raise Refusal(404, f"The {server.name} tool server names no variable {name}.")
        return assistant_id, made, server.name, name

    @_answers
    async def save_variable(request: Request) -> Response:
        value = (await _json_body(request)).get("value")
        if not isinstance(value, str) or not value.strip():
            raise Refusal(400, "Give the variable a value.")
        if len(value) > MAX_VARIABLE_VALUE:
            raise Refusal(400, f"That value is longer than the {MAX_VARIABLE_VALUE:,} characters Bailey keeps.")

        def save() -> dict[str, Any]:
            assistant_id, made, server, name = _named_variable(request)
            state.keys.set(variable_key(assistant_id, server, name), value.strip())
            return _tools_view(state, assistant_id, made)

        return JSONResponse(await run_in_threadpool(save))

    @_answers
    async def remove_variable(request: Request) -> Response:
        def remove() -> dict[str, Any]:
            assistant_id, made, server, name = _named_variable(request)
            state.keys.delete(variable_key(assistant_id, server, name))
            return _tools_view(state, assistant_id, made)

        return JSONResponse(await run_in_threadpool(remove))

    @_answers
    async def list_grants(request: Request) -> Response:
        return JSONResponse(await run_in_threadpool(_grants_view, state))

    @_answers
    async def revoke_grant(request: Request) -> Response:
        body = await _json_body(request)
        named = [body.get(key) for key in ("assistant", "server", "tool")]
        if not all(isinstance(value, str) and value for value in named):
            raise Refusal(400, "Name the assistant, the server and the tool to ask about again.")

        def revoke() -> dict[str, Any]:
            state.store.revoke(str(named[0]), str(named[1]), str(named[2]))
            return _grants_view(state)

        return JSONResponse(await run_in_threadpool(revoke))

    # -- documents ---------------------------------------------------------------

    def _indexed(assistant_id: str) -> None:
        # A document is embedded as it arrives once the model is here, so a message later
        # waits for none of it; without the model it waits until the collection needs it.
        if state.embedder is not None or os.environ.get("BAILEY_SEARCH_MODEL"):
            embedder = _embedder(state)
            if embedder.ready():
                index_collection(state.store, assistant_id, embedder)

    @_answers
    async def read_knowledge(request: Request) -> Response:
        assistant_id = request.path_params["assistant_id"]

        def read() -> dict[str, Any]:
            return _knowledge_view(state, assistant_id, _assistant(state, assistant_id))

        return JSONResponse(await run_in_threadpool(read))

    @_answers
    async def add_knowledge(request: Request) -> Response:
        assistant_id = request.path_params["assistant_id"]
        name = request.query_params.get("name", "").strip()
        slot_name = request.query_params.get("slot", "").strip()
        if not name:
            raise Refusal(400, "Name the document's file.")
        data = await _read_body(request, MAX_DOCUMENT_BYTES)

        def add() -> dict[str, Any]:
            made = _assistant(state, assistant_id)
            if slot_name:
                slot = made.slot(slot_name)
                if slot is None:
                    raise Refusal(404, f"{made.title} asks for no document named {slot_name}.")
                if extension_of(name) not in slot.accepts:
                    raise Refusal(400, f"{slot.description} Bailey takes it as {', '.join(slot.accepts)}.")
            add_document(state.store, assistant_id, name, data, slot=slot_name, kind="slot" if slot_name else "own")
            _indexed(assistant_id)
            return _knowledge_view(state, assistant_id, made)

        return JSONResponse(await run_in_threadpool(add))

    @_answers
    async def use_sample(request: Request) -> Response:
        assistant_id = request.path_params["assistant_id"]
        slot_name = request.path_params["slot"]

        def use() -> dict[str, Any]:
            made = _assistant(state, assistant_id)
            sample = made.sample(slot_name)
            if sample is None:
                raise Refusal(404, f"{made.title} has no sample for {slot_name}.")
            add_document(
                state.store,
                assistant_id,
                f"{sample.slot}.{sample.extension}",
                sample.text.encode("utf-8"),
                slot=sample.slot,
                kind="sample",
            )
            _indexed(assistant_id)
            return _knowledge_view(state, assistant_id, made)

        return JSONResponse(await run_in_threadpool(use))

    @_answers
    async def remove_knowledge(request: Request) -> Response:
        assistant_id = request.path_params["assistant_id"]
        document_id = request.path_params["document_id"]

        def remove() -> dict[str, Any]:
            made = _assistant(state, assistant_id)
            if not state.store.delete_document(assistant_id, document_id):
                raise Refusal(404, "This assistant has no document by that id.")
            return _knowledge_view(state, assistant_id, made)

        return JSONResponse(await run_in_threadpool(remove))

    @_answers
    async def fetch_model(request: Request) -> Response:
        """Fetch the search model from Hugging Face, once: the page asks the person before it calls this."""

        embedder = _embedder(state)

        def fetch() -> dict[str, Any]:
            if not embedder.ready():
                try:
                    embedder.download()  # type: ignore[attr-defined]
                except Exception as error:  # the network, a proxy, a full disk: each a sentence
                    raise Refusal(
                        502,
                        f"Bailey could not download {MINILM.label} from Hugging Face: {type(error).__name__}. "
                        "Check this computer's connection to huggingface.co, or put the model in a folder and point "
                        "BAILEY_SEARCH_MODEL at it.",
                    ) from error
            return {"ready": embedder.ready(), "label": MINILM.label}

        return JSONResponse(await run_in_threadpool(fetch))

    # -- apps ----------------------------------------------------------------------

    @_answers
    async def read_apps_panel(request: Request) -> Response:
        assistant_id = request.path_params["assistant_id"]

        def read() -> dict[str, Any]:
            return _apps_view(state, assistant_id, _assistant(state, assistant_id))

        return JSONResponse(await run_in_threadpool(read))

    @_answers
    async def prepare_app(request: Request) -> Response:
        """Build an app's environment: the page asks the person, naming the download, before it calls this."""

        assistant_id = request.path_params["assistant_id"]
        name = request.path_params["name"]

        def prepare() -> dict[str, Any]:
            made = _assistant(state, assistant_id)
            if made.app(name) is None:
                raise Refusal(404, f"{made.title} has no app named {name}.")
            _environments(state).prepare(requirements_of(app_folder(state.data_folder, assistant_id, name)))
            return _apps_view(state, assistant_id, made)

        return JSONResponse(await run_in_threadpool(prepare))

    async def app_verb(request: Request) -> Response:
        """One of an app's four Postern verbs, forwarded to its runner with the runner's own token.

        Answered in Postern's error envelope, which Gatehouse reads, rather than Bailey's.
        """

        import httpx

        assistant_id = request.path_params["assistant_id"]
        name = request.path_params["name"]
        verb = request.path_params["verb"]
        if POSTERN_VERBS.get(verb) != request.method:
            return _postern_error(404, "bad_request", f"Postern has no {request.method} {verb}.")
        try:
            made = await run_in_threadpool(_assistant, state, assistant_id)
            if made.app(name) is None:
                raise Refusal(404, f"{made.title} has no app named {name}.")
            connection = await run_in_threadpool(_connection, state)
            folder = app_folder(state.data_folder, assistant_id, name)
            runner = await _app_host(state).runner(assistant_id, name, folder, connection, state.base_urls)
        except Refusal as refused:
            return _postern_error(refused.status, "unavailable", refused.message)
        except AppFailed as failed:
            return _postern_error(503, "unavailable", str(failed))
        url = f"{runner.base}/postern/v0/{verb}"
        headers = {"Authorization": f"Bearer {runner.token}"}
        unreached = "The app's runner did not answer. Close the app and open it again."
        if request.method == "GET":
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    upstream = await client.get(url, headers=headers)
            except httpx.HTTPError:
                return _postern_error(503, "unavailable", unreached)
            if verb == "describe" and upstream.status_code == 200:
                # The passages input is Bailey's to fill, so the form never shows it.
                document = upstream.json()
                document["inputs"] = [
                    item
                    for item in document.get("inputs") or []
                    if not (isinstance(item, dict) and item.get("key") == PASSAGES_INPUT)
                ]
                return JSONResponse(document)
            return Response(upstream.content, status_code=upstream.status_code, media_type="application/json")
        try:
            body = json.loads(await _read_body(request, MAX_RUN_BODY) or b"{}")
        except (Refusal, ValueError):
            return _postern_error(400, "bad_request", "A run's body is a JSON object with its inputs, up to 1 MB.")
        inputs = body.get("inputs") if isinstance(body, dict) else None
        if not isinstance(inputs, dict):
            return _postern_error(400, "bad_request", 'A run\'s body names its inputs: {"inputs": {...}}.')
        declared = passages_input(describe_of(folder))
        if declared is not None:
            limit = int((declared.get("validation") or {}).get("max_length") or MAX_PASSAGES_CHARACTERS)
            try:
                inputs[PASSAGES_INPUT] = await run_in_threadpool(
                    _app_passages,
                    state,
                    assistant_id,
                    made,
                    connection.provider,
                    str(inputs.get("prompt") or ""),
                    min(limit, MAX_PASSAGES_CHARACTERS),
                )
            except Refusal as refused:
                return _postern_error(refused.status, "unavailable", refused.message)
        headers["Content-Type"] = "application/json"
        if request.headers.get("idempotency-key"):
            headers["Idempotency-Key"] = request.headers["idempotency-key"]
        client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10))
        try:
            upstream = await client.send(
                client.build_request("POST", url, json={"inputs": inputs}, headers=headers), stream=True
            )
        except httpx.HTTPError:
            await client.aclose()
            return _postern_error(503, "unavailable", unreached)
        if verb == "run" or upstream.status_code != 200:
            content = await upstream.aread()
            await upstream.aclose()
            await client.aclose()
            kind = upstream.headers.get("content-type", "application/json")
            return Response(content, status_code=upstream.status_code, media_type=kind)

        async def relay() -> AsyncIterator[bytes]:
            # A page that closes stops reading; closing the runner's stream then cancels the run.
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(relay(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    # -- conversations -----------------------------------------------------------

    @_answers
    async def list_conversations(request: Request) -> Response:
        def listed() -> dict[str, Any]:
            assistant_id = request.query_params.get("assistant") or _active_assistant_id(state)
            _assistant(state, assistant_id)
            return {"assistant": assistant_id, "conversations": state.store.list_conversations(assistant_id)}

        return JSONResponse(await run_in_threadpool(listed))

    @_answers
    async def create_conversation(request: Request) -> Response:
        wanted = (await _json_body(request)).get("assistant")

        def create() -> dict[str, Any]:
            assistant_id = wanted if isinstance(wanted, str) and wanted else _active_assistant_id(state)
            _assistant(state, assistant_id)
            conversation_id = uuid.uuid4().hex
            state.store.create_conversation(conversation_id, assistant_id, "New conversation")
            return {"id": conversation_id, "assistant": assistant_id, "title": "New conversation", "messages": []}

        return JSONResponse(await run_in_threadpool(create))

    def _conversation(conversation_id: str) -> dict[str, str]:
        found = state.store.get_conversation(conversation_id)
        if found is None:
            raise Refusal(404, "Bailey has no conversation by that id.")
        return found

    @_answers
    async def read_conversation(request: Request) -> Response:
        conversation_id = request.path_params["conversation_id"]

        def read() -> dict[str, Any]:
            found = _conversation(conversation_id)
            records = state.store.messages(conversation_id)
            return {
                "id": conversation_id,
                "assistant": found["assistant_id"],
                "title": found["title"],
                "running": conversation_id in state.running,
                "messages": shown(records),
                "pending": pending(records),
            }

        return JSONResponse(await run_in_threadpool(read))

    @_answers
    async def delete_conversation(request: Request) -> Response:
        conversation_id = request.path_params["conversation_id"]

        def delete() -> dict[str, Any]:
            found = _conversation(conversation_id)
            state.store.delete_conversation(conversation_id)
            return {
                "assistant": found["assistant_id"],
                "conversations": state.store.list_conversations(found["assistant_id"]),
            }

        return JSONResponse(await run_in_threadpool(delete))

    def _stream(
        conversation_id: str,
        assistant_id: str,
        made: Assistant,
        connection: Connection,
        history: list[Any],
        environments: dict[str, dict[str, str]],
        *,
        text: str | None = None,
        decisions: dict[str, bool] | None = None,
        documents: Documents | None = None,
    ) -> StreamingResponse:
        """One run of the agent as server-sent events, its records stored as it ends.

        A message starts it with ``text``; an answer to the questions a run stopped on
        resumes it with ``decisions``. Either way it starts the assistant's tool servers
        when they are not running, and a failed run keeps the records of its last whole
        step, so a tool that ran is never asked about again.
        """

        granted = _granted(state, assistant_id)

        async def stream() -> AsyncIterator[bytes]:
            # Imported here: the agent core takes seconds to load, which a start of the app
            # should not wait for. serve() loads it in the background once the server is up,
            # and the request's own preparation has loaded it by now (to_history).
            load_core()
            from bailey.agent import answer

            try:
                toolsets: dict[str, Any] = {}
                if environments:
                    if not state.tools.running(assistant_id):
                        yield _event({"type": "status", "text": "Starting this assistant's tools…"})
                    try:
                        toolsets = await state.tools.toolsets(assistant_id, made.servers, environments)
                    except ToolServerFailed as failure:
                        await run_in_threadpool(
                            state.store.append_messages, conversation_id, [notice_record(str(failure))]
                        )
                        yield _event({"type": "error", "message": str(failure)})
                        return
                async for event in answer(
                    connection,
                    made,
                    history,
                    text,
                    toolsets=toolsets,
                    granted=granted,
                    decisions=decisions,
                    documents=documents,
                ):
                    if event["type"] == "done":
                        records = _records(event["messages"], connection, event["servers"], event["asked"], text)
                        citations = await run_in_threadpool(_citations, state, assistant_id, records)
                        records = _with_usage(records, event["usage"], event["estimate"], citations)
                        await run_in_threadpool(state.store.append_messages, conversation_id, records)
                        yield _event(
                            {
                                "type": "done",
                                "usage": event["usage"],
                                "estimate": event["estimate"],
                                "model": connection.model,
                                "paused": bool(event["asked"]),
                                "citations": citations,
                            }
                        )
                    elif event["type"] == "error":
                        records = _records(event["messages"], connection, event["servers"], (), text)
                        records.append(notice_record(event["message"]))
                        await run_in_threadpool(state.store.append_messages, conversation_id, records)
                        if environments:
                            # A server may have stopped answering; the next message starts it again.
                            await state.tools.stop(assistant_id)
                        yield _event({"type": "error", "message": event["message"]})
                    else:
                        yield _event(event)
            finally:
                state.running.discard(conversation_id)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @_answers
    async def send_message(request: Request) -> Response:
        conversation_id = request.path_params["conversation_id"]
        text = (await _json_body(request, MAX_MESSAGE_BODY)).get("text")
        if not isinstance(text, str) or not text.strip():
            raise Refusal(400, "Write a message first.")
        if len(text) > MAX_MESSAGE_CHARACTERS:
            raise Refusal(
                400,
                f"That message is {len(text):,} characters; Bailey sends {MAX_MESSAGE_CHARACTERS:,} at most in one.",
            )
        text = text.strip()

        def prepare() -> tuple[str, Assistant, Connection, list[Any], dict[str, dict[str, str]], Documents | None]:
            found = _conversation(conversation_id)
            assistant_id = found["assistant_id"]
            made = _assistant(state, assistant_id)
            connection = _connection(state)
            if conversation_id in state.running:
                raise Refusal(409, "An answer is still coming in this conversation. Wait for it to finish.")
            stored = state.store.messages(conversation_id)
            if pending(stored):
                raise Refusal(409, "Answer the question above first: allow the tool call, or decline it.")
            environments = _tool_environments(state, assistant_id, made)
            documents = _documents_for(state, assistant_id, made, connection.provider)
            history = to_history(stored)
            state.store.append_messages(conversation_id, [user_record(text)])
            if not stored:
                state.store.rename_conversation(conversation_id, _title_for(text))
            state.running.add(conversation_id)
            return assistant_id, made, connection, history, environments, documents

        assistant_id, made, connection, history, environments, documents = await run_in_threadpool(prepare)
        return _stream(
            conversation_id, assistant_id, made, connection, history, environments, text=text, documents=documents
        )

    @_answers
    async def answer_questions(request: Request) -> Response:
        """The person's answer to each tool call a run stopped on: once, always for this assistant, or no."""

        conversation_id = request.path_params["conversation_id"]
        answers = (await _json_body(request)).get("answers")
        if not isinstance(answers, dict) or not all(
            isinstance(call_id, str) and given in ANSWERS for call_id, given in answers.items()
        ):
            raise Refusal(400, "Answer each question with once, always or no.")

        def prepare() -> tuple[
            str, Assistant, Connection, list[Any], dict[str, dict[str, str]], dict[str, bool], Documents | None
        ]:
            found = _conversation(conversation_id)
            assistant_id = found["assistant_id"]
            made = _assistant(state, assistant_id)
            connection = _connection(state)
            if conversation_id in state.running:
                raise Refusal(409, "An answer is still coming in this conversation. Wait for it to finish.")
            stored = state.store.messages(conversation_id)
            waiting = pending(stored)
            if not waiting:
                raise Refusal(409, "Nothing in this conversation is waiting for an answer.")
            if set(answers) != {call["id"] for call in waiting}:
                raise Refusal(400, "Answer every question this conversation is waiting on, and only those.")
            environments = _tool_environments(state, assistant_id, made)
            documents = _documents_for(state, assistant_id, made, connection.provider)
            for call in waiting:
                if answers[call["id"]] == "always" and call["server"]:
                    state.store.grant(assistant_id, call["server"], call["tool"])
            state.running.add(conversation_id)
            decisions = {call_id: given != "no" for call_id, given in answers.items()}
            return assistant_id, made, connection, to_history(stored), environments, decisions, documents

        assistant_id, made, connection, history, environments, decisions, documents = await run_in_threadpool(prepare)
        return _stream(
            conversation_id,
            assistant_id,
            made,
            connection,
            history,
            environments,
            decisions=decisions,
            documents=documents,
        )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        try:
            yield
        finally:
            # Bailey's tool servers and app runners stop with it, rather than run on.
            await state.tools.close()
            if state.app_host is not None:
                await state.app_host.close()

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/", index),
            Route("/static/{name:path}", asset),
            Route("/api/status", read_status),
            Route("/api/settings", read_settings, methods=["GET"]),
            Route("/api/settings", write_settings, methods=["PUT"]),
            Route("/api/keys/unlock", unlock_keys, methods=["POST"]),
            Route("/api/keys/passphrase", choose_passphrase, methods=["POST"]),
            Route("/api/keys/reset", reset_keys, methods=["POST"]),
            Route("/api/keys/{provider}", save_key, methods=["PUT"]),
            Route("/api/keys/{provider}", remove_key, methods=["DELETE"]),
            Route("/api/assistants", list_assistants, methods=["GET"]),
            Route("/api/assistants", create_assistant, methods=["POST"]),
            Route("/api/assistants/import", import_assistant, methods=["POST"]),
            Route("/api/assistants/{assistant_id}", read_assistant, methods=["GET"]),
            Route("/api/assistants/{assistant_id}", write_assistant, methods=["PUT"]),
            Route("/api/assistants/{assistant_id}", delete_assistant, methods=["DELETE"]),
            Route("/api/assistants/{assistant_id}/use", use_assistant, methods=["POST"]),
            Route("/api/assistants/{assistant_id}/export", export_assistant, methods=["GET"]),
            Route("/api/assistants/{assistant_id}/tools", read_tools, methods=["GET"]),
            Route("/api/assistants/{assistant_id}/tools/{server}/variables/{variable}", save_variable, methods=["PUT"]),
            Route(
                "/api/assistants/{assistant_id}/tools/{server}/variables/{variable}",
                remove_variable,
                methods=["DELETE"],
            ),
            Route("/api/assistants/{assistant_id}/knowledge", read_knowledge, methods=["GET"]),
            Route("/api/assistants/{assistant_id}/knowledge", add_knowledge, methods=["POST"]),
            Route("/api/assistants/{assistant_id}/knowledge/samples/{slot}", use_sample, methods=["POST"]),
            Route("/api/assistants/{assistant_id}/knowledge/{document_id}", remove_knowledge, methods=["DELETE"]),
            Route("/api/knowledge/model", fetch_model, methods=["POST"]),
            Route("/api/assistants/{assistant_id}/apps", read_apps_panel),
            Route("/api/assistants/{assistant_id}/apps/{name}/environment", prepare_app, methods=["POST"]),
            Route("/api/assistants/{assistant_id}/apps/{name}/postern/v0/{verb}", app_verb, methods=["GET", "POST"]),
            Route("/api/grants", list_grants, methods=["GET"]),
            Route("/api/grants/revoke", revoke_grant, methods=["POST"]),
            Route("/api/conversations", list_conversations, methods=["GET"]),
            Route("/api/conversations", create_conversation, methods=["POST"]),
            Route("/api/conversations/{conversation_id}", read_conversation, methods=["GET"]),
            Route("/api/conversations/{conversation_id}", delete_conversation, methods=["DELETE"]),
            Route("/api/conversations/{conversation_id}/messages", send_message, methods=["POST"]),
            Route("/api/conversations/{conversation_id}/answers", answer_questions, methods=["POST"]),
        ],
    )
    return Guard(app, state.token)


def bind_loopback(port: int) -> socket.socket:
    """A socket listening on 127.0.0.1 alone; port 0 takes any free one."""

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name != "nt":  # on Windows the same option would let another program take the port
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        # Listening before the server starts queues an early request rather than refusing it.
        listener.listen(128)
    except OSError:
        listener.close()
        raise
    return listener


class _Stopped(BaseException):
    """A terminate signal, turned into an exception so the store is closed on the way out."""


def _stop_on_terminate(signum: int, frame: FrameType | None) -> None:
    raise _Stopped


def _warm_up() -> None:
    """Load the agent core and the tool host's libraries in the background, so the first
    message does not wait seconds for them (about two, and one more for the MCP ones)."""

    # A failure here surfaces, with its sentence, when a message needs the core. A message
    # that comes first imports through the same door rather than beside it (bailey.core).
    with contextlib.suppress(Exception):
        load_core()


def _open_when_ready(server: uvicorn.Server, launch: str, out: TextIO) -> None:
    while not server.started and not server.should_exit:
        time.sleep(0.05)
    if server.started and not webbrowser.open(launch, new=2):
        out.write("No browser could be opened here. Open the address above in one.\n")
        out.flush()


def serve(
    *,
    port: int = 0,
    open_browser: bool = True,
    environ: Mapping[str, str] = os.environ,
    out: TextIO = sys.stdout,
) -> int:
    """Start Bailey and serve until stopped. Returns the exit status."""

    folder = data_folder(environ=environ)
    try:
        ensure_data_folder(folder)
    except OSError as error:
        out.write(
            f"Bailey cannot create its data folder, {folder}: {error.strerror or error}.\n"
            "Point BAILEY_HOME at a folder this user can write in.\n"
        )
        return 1
    try:
        store = Store(folder / STORE_FILE)
    except NewerStore as error:
        out.write(f"{error}\n")
        return 1
    except sqlite3.Error as error:
        out.write(f"Bailey cannot open its store, {folder / STORE_FILE}: {error}.\n")
        return 1
    try:
        try:
            keys = open_keys(folder, environ=environ)
        except WrongPassphrase:
            out.write(
                f"BAILEY_PASSPHRASE does not open {folder / FILE_NAME}. Give the passphrase it was made with, "
                "or delete the file to start over; the keys in it go with it.\n"
            )
            return 1
        except (UnreadableKeyFile, PassphraseRefused) as error:
            out.write(f"{error}\n")
            return 1
        try:
            listener = bind_loopback(port)
        except OSError as error:
            out.write(f"Bailey cannot listen on 127.0.0.1:{port}: {error.strerror or error}.\n")
            return 1
        token = secrets.token_urlsafe(32)
        address = f"http://127.0.0.1:{listener.getsockname()[1]}/"
        launch = f"{address}#token={token}"
        state = AppState(token=token, store=store, keys=keys, data_folder=folder)
        config = uvicorn.Config(
            build_app(state), log_level="warning", access_log=False, lifespan="on", server_header=False
        )
        server = uvicorn.Server(config)
        out.write(
            f"Bailey {__version__} is running on this computer at {address}\n"
            f"Open it at {launch}\n"
            "That address carries this session's token, which changes each time Bailey starts.\n"
            "Press Ctrl+C to stop.\n"
        )
        out.flush()
        if open_browser:
            threading.Thread(target=_open_when_ready, args=(server, launch, out), daemon=True).start()
        threading.Thread(target=_warm_up, daemon=True).start()
        # uvicorn shuts down gracefully on Ctrl+C or a terminate signal, then raises the
        # signal again: Ctrl+C as KeyboardInterrupt, and a terminate signal through the
        # handler in place before it started, which by default ends the process at once
        # and skips closing the store. This handler makes both end the same way.
        previous = None
        if threading.current_thread() is threading.main_thread() and hasattr(signal, "SIGTERM"):
            previous = signal.signal(signal.SIGTERM, _stop_on_terminate)
        try:
            server.run(sockets=[listener])
        except (KeyboardInterrupt, _Stopped):
            pass
        finally:
            listener.close()
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)
        out.write("Bailey has stopped.\n")
        out.flush()
        return 0
    finally:
        store.close()
