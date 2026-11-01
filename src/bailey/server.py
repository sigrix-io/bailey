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
import signal
import socket
import sqlite3
import sys
import threading
import time
import uuid
import webbrowser
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
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
from bailey.conversations import notice_record, shown, to_history, to_records, user_record
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
    read_solution,
    template,
    template_ids,
    unzip_solution,
    zip_solution,
)
from bailey.store import STORE_FILE, NewerStore, Store

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
STATIC_FILES: Mapping[str, str] = {
    "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "icon.svg": "image/svg+xml",
}

#: The only paths answered without the token: the page and its static files.
PUBLIC_PATHS = frozenset({"/", *(f"/static/{name}" for name in STATIC_FILES)})

#: No request Bailey's page sends is near this, but a message, which may carry a document.
MAX_BODY = 64 * 1024

#: A message may carry a pasted document to summarise.
MAX_MESSAGE_BODY = 1024 * 1024
MAX_MESSAGE_CHARACTERS = 200_000

#: A conversation is named after its first message, cut at a word to about this length.
TITLE_LENGTH = 60

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
        except (SettingRefused, SolutionRefused) as refusal:
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


def _file_keys(state: AppState) -> EncryptedFileKeys:
    if not isinstance(state.keys, EncryptedFileKeys):
        raise Refusal(409, f"Your keys are kept in {state.keys.where}, which has no passphrase of Bailey's.")
    return state.keys


def _read_web(name: str) -> bytes:
    return resources.files("bailey").joinpath("web", name).read_bytes()


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
        made = read_solution(unzip_solution(data))

        def save() -> dict[str, Any]:
            _ensure_an_assistant(state)
            assistant_id = uuid.uuid4().hex
            state.store.put_assistant(assistant_id, made.as_dict())
            return {"id": assistant_id, **_assistants_view(state)}

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
        made = await run_in_threadpool(_assistant, state, request.path_params["assistant_id"])
        return Response(
            zip_solution(made),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{made.name}.zip"'},
        )

    @_answers
    async def delete_assistant(request: Request) -> Response:
        assistant_id = request.path_params["assistant_id"]

        def delete() -> dict[str, Any]:
            _assistant(state, assistant_id)
            state.store.delete_assistant(assistant_id)
            return _assistants_view(state)

        return JSONResponse(await run_in_threadpool(delete))

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
            return {
                "id": conversation_id,
                "assistant": found["assistant_id"],
                "title": found["title"],
                "running": conversation_id in state.running,
                "messages": shown(state.store.messages(conversation_id)),
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

        def prepare() -> tuple[Connection, Assistant, list[Any]]:
            found = _conversation(conversation_id)
            made = _assistant(state, found["assistant_id"])
            connection = _connection(state)
            if conversation_id in state.running:
                raise Refusal(409, "An answer is still coming in this conversation. Wait for it to finish.")
            stored = state.store.messages(conversation_id)
            history = to_history(stored)
            state.store.append_messages(conversation_id, [user_record(text)])
            if not stored:
                state.store.rename_conversation(conversation_id, _title_for(text))
            state.running.add(conversation_id)
            return connection, made, history

        connection, made, history = await run_in_threadpool(prepare)

        async def stream() -> AsyncIterator[bytes]:
            # Imported here: the agent core takes seconds to load, which a start of the app
            # should not wait for. serve() loads it in the background once the server is up.
            from bailey.agent import answer

            try:
                async for event in answer(connection, made, history, text):
                    if event["type"] == "done":
                        records = _without_the_prompt(
                            to_records(event["messages"], provider=connection.provider, model=connection.model), text
                        )
                        if records:
                            records[-1]["usage"] = event["usage"]
                            records[-1]["estimate"] = event["estimate"]
                        await run_in_threadpool(state.store.append_messages, conversation_id, records)
                        yield _event(
                            {
                                "type": "done",
                                "usage": event["usage"],
                                "estimate": event["estimate"],
                                "model": connection.model,
                            }
                        )
                    elif event["type"] == "error":
                        await run_in_threadpool(
                            state.store.append_messages, conversation_id, [notice_record(event["message"])]
                        )
                        yield _event(event)
                    else:
                        yield _event(event)
            finally:
                state.running.discard(conversation_id)

        return StreamingResponse(stream(), media_type="text/event-stream")

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/static/{name}", asset),
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
            Route("/api/conversations", list_conversations, methods=["GET"]),
            Route("/api/conversations", create_conversation, methods=["POST"]),
            Route("/api/conversations/{conversation_id}", read_conversation, methods=["GET"]),
            Route("/api/conversations/{conversation_id}", delete_conversation, methods=["DELETE"]),
            Route("/api/conversations/{conversation_id}/messages", send_message, methods=["POST"]),
        ]
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
    """Load the agent core in the background, so the first message does not wait seconds for it."""

    # A failure here surfaces, with its sentence, when a message needs the core.
    with contextlib.suppress(Exception):
        import bailey.agent  # noqa: F401


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
            build_app(state), log_level="warning", access_log=False, lifespan="off", server_header=False
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
