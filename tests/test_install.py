"""Installing a solution from a file (story S10): what arrives, the checks before anything is written, the screen.

Story S10's *done when*: a pilot solution and a download from Sigrix
both install from a file, and a package carrying a credential value or a path outside its
folder is refused with a sentence saying why.

- **A download from Sigrix** is ``tests/fixtures/sigrix-download/order-desk.zip``, the bytes
  Sigrix's own builder writes (its README says how). It installs as it is, and in the shape a
  download had before 2026-10-01, which this file makes by taking those additions out.
- **The pilot**, Customer email replies, is read from the copy of its package that
  ``BAILEY_TEST_PILOT`` names, since copying it into these tests is its owner's call; without one, that test is skipped. A made-up shop shaped like
  it (``tests/fixtures/shop-replies``) installs the same two ways on every run.
"""

from __future__ import annotations

import base64
import io
import json
import os
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeKeychain
from starlette.testclient import TestClient

from bailey.install import (
    CONVERTED_NOTE,
    CREDENTIAL_SHAPES,
    SCRIPTS_SETTING_LABEL,
    UNVERIFIED_NOTE,
    PendingInstalls,
    check_credentials,
    check_package,
    convert_download,
    skill_name_for,
)
from bailey.keys import KeychainKeys
from bailey.server import AppState, build_app
from bailey.solutions import (
    DEFAULT_TEMPLATE,
    MAX_ENTRIES,
    MAX_FILE_BYTES,
    SolutionRefused,
    read_solution,
    unzip_solution,
)
from bailey.store import Store
from bailey.tools import variable_key

TOKEN = "s" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}
FIXTURES = Path(__file__).parent / "fixtures"
ORDER_DESK = FIXTURES / "sigrix-download" / "order-desk.zip"
#: What a download gained when it became a solution, which one from before lacks.
SOLUTION_ADDITIONS = ("plugin.json", "assistant.json", "mcp.json", "skills/", "samples/")


@pytest.fixture
def state(tmp_path: Path) -> Iterator[AppState]:
    store = Store(tmp_path / "bailey.db")
    yield AppState(token=TOKEN, store=store, keys=KeychainKeys(FakeKeychain()), data_folder=tmp_path)  # type: ignore[arg-type]
    store.close()


@pytest.fixture
def client(state: AppState) -> Iterator[TestClient]:
    with TestClient(build_app(state), base_url="http://127.0.0.1:8123") as made:
        yield made


def _tree(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _folder(files: dict[str, bytes], picked: str = "Downloads/order-desk") -> dict[str, Any]:
    """A folder as the page sends one: each file under the folder the person picked, as a browser names it."""

    top = picked.rsplit("/", 1)[-1]
    return {
        "files": [{"path": f"{top}/{name}", "data": base64.b64encode(data).decode()} for name, data in files.items()]
    }


def _order_desk() -> dict[str, bytes]:
    return unzip_solution(ORDER_DESK.read_bytes())


def _before_solutions(files: dict[str, bytes], *, titled: bool = False) -> dict[str, bytes]:
    """The download as it was before it became a solution, on 2026-10-01.

    ``titled`` names its SKILL.md with the assistant's title, as older downloads did.
    """

    old = {name: data for name, data in files.items() if not name.startswith(SOLUTION_ADDITIONS)}
    if titled:
        old["SKILL.md"] = old["SKILL.md"].replace(b'name: "order-desk"', b"name: Order desk")
    return old


def _check(client: TestClient, files: dict[str, bytes], *, how: str = "zip") -> Any:
    if how == "zip":
        return client.post("/api/installs", headers={**AUTH, "Content-Type": "application/zip"}, content=_zip(files))
    return client.post("/api/installs", headers=AUTH, json=_folder(files))


def _install(client: TestClient, files: dict[str, bytes], *, how: str = "zip") -> tuple[dict[str, Any], dict[str, Any]]:
    checked = _check(client, files, how=how)
    assert checked.status_code == 200, checked.text
    screen = checked.json()
    installed = client.post(f"/api/installs/{screen['id']}", headers=AUTH)
    assert installed.status_code == 200, installed.text
    return screen, installed.json()


def _nothing_written(state: AppState) -> None:
    assert state.store.list_assistants() == [], "no assistant was written"
    assert not (state.data_folder / "apps").exists(), "no app's files were written"


# ---------------------------------------------------------------------------
# Done when: a pilot solution and a download from Sigrix both install from a file
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not os.environ.get("BAILEY_TEST_PILOT"), reason="names no sigrix checkout's pilot package")
@pytest.mark.parametrize("how", ["zip", "folder"])
def test_the_pilot_installs_from_a_zip_and_from_a_folder(client: TestClient, state: AppState, how: str) -> None:
    pilot = _tree(Path(os.environ["BAILEY_TEST_PILOT"]))
    screen, installed = _install(client, pilot, how=how)
    assert screen["title"] == "Customer email replies" and screen["unverified"] is True
    assert [slot["name"] for slot in screen["knowledge"]] == ["policies", "faq", "product_catalog"]
    assert all(slot["sample"] for slot in screen["knowledge"]), "each slot's made-up example comes with it"
    assert [skill["name"] for skill in screen["skills"]] == ["support-reply", "policy-grounding"]
    assert screen["keys"] == [] and screen["tools"] == [] and screen["left_out"] == []
    assert installed["asks_for_documents"] is True
    (listed,) = [entry for entry in installed["assistants"] if entry["id"] == installed["id"]]
    assert listed["unverified"] is True


@pytest.mark.parametrize("how", ["zip", "folder"])
def test_a_made_up_shop_shaped_like_the_pilot_installs_from_a_zip_and_from_a_folder(
    client: TestClient, state: AppState, how: str
) -> None:
    screen, installed = _install(client, _tree(FIXTURES / "shop-replies"), how=how)
    assert screen["title"] == "Shop replies" and screen["unverified"] is True and screen["converted"] is False
    assert [(slot["name"], slot["required"], slot["sample"]) for slot in screen["knowledge"]] == [
        ("policies", True, True),
        ("faq", False, True),
        ("catalog", False, True),
    ]
    stored = state.store.get_assistant(installed["id"])
    assert stored is not None and stored["origin"] == "file" and stored["title"] == "Shop replies"


def test_a_download_from_sigrix_installs_as_it_is(client: TestClient, state: AppState) -> None:
    screen, installed = _install(client, _order_desk())
    assert (screen["title"], screen["unverified"], screen["converted"]) == ("Order desk", True, False)
    assert screen["notices"][0] == UNVERIFIED_NOTE
    assert screen["keys"] == [
        {
            "name": "SHOP_API_KEY",
            "reads_it": "the orders tool server",
            "server": "orders",
            "purpose": "",
            "signup_url": "",
            "note": "",
        }
    ], "an MCP server's key is a name in the format: who reads it, and nothing invented beside it"
    (server,) = screen["tools"]
    assert server["runs"] == "uvx shop-orders-mcp==1.4.2"
    assert server["tools"] == [{"name": "find_order", "asks": False}, {"name": "refund_order", "asks": True}]
    assert [(slot["name"], slot["required"], slot["sample"]) for slot in screen["knowledge"]] == [
        ("policies", True, True)
    ]
    assert screen["skills"][0]["name"] == "refund-rules" and screen["left_out"] == []
    made = read_solution(_order_desk())
    stored = state.store.get_assistant(installed["id"])
    assert stored is not None and stored["skills"][0]["references"] == [list(made.skills[0].references[0])], (
        "the seller's reference file installs with its skill"
    )


@pytest.mark.parametrize("titled", [False, True], ids=["named", "named-with-its-title"])
def test_a_download_from_before_downloads_were_solutions_is_converted_and_installs(
    client: TestClient, state: AppState, titled: bool
) -> None:
    old = _before_solutions(_order_desk(), titled=titled)
    assert "assistant.json" not in old and "SKILL.md" in old and ".mcp.json" in old, "the canary: the old shape"
    screen, installed = _install(client, old)
    assert (screen["name"], screen["title"], screen["converted"]) == ("order-desk", "Order desk", True)
    assert CONVERTED_NOTE in screen["notices"]
    (server,) = screen["tools"]
    assert server["name"] == "orders" and server["tools"] is None, "a download declared no tools, so each asks"
    assert [key["name"] for key in screen["keys"]] == ["SHOP_API_KEY"]
    stored = state.store.get_assistant(installed["id"])
    assert stored is not None
    root_body = old["SKILL.md"].decode().split("---", 2)[2].strip()
    assert stored["instructions"]["body"] == root_body, "the download's prompt is the assistant's instructions"


def test_a_folder_and_a_zip_of_one_package_install_the_same_assistant(client: TestClient, state: AppState) -> None:
    _, from_zip = _install(client, _order_desk(), how="zip")
    _, from_folder = _install(client, _order_desk(), how="folder")
    assert state.store.get_assistant(from_zip["id"]) == state.store.get_assistant(from_folder["id"])


def test_a_folder_holding_the_solutions_own_folder_is_read_from_inside_it(client: TestClient) -> None:
    files = {f"order-desk/{name}": data for name, data in _order_desk().items()}
    checked = client.post("/api/installs", headers=AUTH, json=_folder(files, picked="Downloads"))
    assert checked.status_code == 200, checked.text
    assert checked.json()["title"] == "Order desk"


# ---------------------------------------------------------------------------
# Done when: a credential value is refused with a sentence, and nothing is written
# ---------------------------------------------------------------------------

#: Values planted in a package; none may reach a refusal's sentence.
SECRET = "sk-proj-aB3dE5gH7jK9mN1pQ3sT5vX7z"
GITHUB = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
AWS = "AKIA" + "Q2W3E4R5T6Y7U8I9"
SLACK = "xoxb-" + "1234567890-0987654321-AbCdEfGhIjKl"
PEM = "-----BEGIN RSA PRIVATE KEY-----\n" + ("MIIEowIBAAKCAQEA1x2y3z4A5B6C7D8E9F0G1H2I3J4K5L6M7N8O9P0Q1R2S3T4\n" * 3)


def _mcp(env: dict[str, Any], *, key: str = "mcpServers", **entry: Any) -> bytes:
    return json.dumps(
        {key: {"orders": {"command": "uvx", "args": ["shop-orders-mcp==1.4.2"], "env": env, **entry}}}
    ).encode()


CREDENTIAL_CASES = [
    pytest.param(
        {"mcp.json": _mcp({"SHOP_API_KEY": SECRET})},
        "mcp.json, server 'orders' gives SHOP_API_KEY a value",
        id="mcp-json",
    ),
    pytest.param(
        {".mcp.json": _mcp({"SHOP_API_KEY": "live-value"})},
        ".mcp.json, server 'orders' gives SHOP_API_KEY a value",
        id="claude-code-config",
    ),
    pytest.param(
        {".vscode/mcp.json": _mcp({"SHOP_API_KEY": "live-value"}, key="servers", type="stdio")},
        ".vscode/mcp.json, server 'orders' gives SHOP_API_KEY a value",
        id="vs-code-config",
    ),
    pytest.param(
        {
            ".cursor/mcp.json": json.dumps(
                {
                    "mcpServers": {
                        "orders": {
                            "url": "https://orders.example/mcp",
                            "headers": {"Authorization": "Bearer live-value"},
                        }
                    }
                }
            ).encode()
        },
        "gives its Authorization header a value",
        id="remote-header",
    ),
    pytest.param(
        {"skills/order-desk/SKILL.md": None, "SKILL.md": None},
        "carries what looks like an OpenAI API key",
        id="instructions-text",
    ),
    pytest.param(
        {"skills/refund-rules/references/refund-limits.md": f"Token: {GITHUB}\n".encode()},
        "refund-limits.md carries what looks like a GitHub token",
        id="reference",
    ),
    pytest.param(
        {"README.md": f"Use {AWS} to sign in.\n".encode()},
        "README.md carries what looks like an AWS access key id",
        id="readme",
    ),
    pytest.param(
        {"setup.sh": f"export SLACK={SLACK}\n".encode()},
        "setup.sh carries what looks like a Slack token",
        id="setup-script",
    ),
    pytest.param(
        {"samples/policies.md": PEM.encode()},
        "samples/policies.md carries what looks like a private key",
        id="private-key",
    ),
]


@pytest.mark.parametrize(("changed", "sentence"), CREDENTIAL_CASES)
def test_a_package_carrying_a_credential_value_is_refused_with_a_sentence_and_writes_nothing(
    client: TestClient, state: AppState, changed: dict[str, bytes | None], sentence: str
) -> None:
    files = _order_desk()
    for name, data in changed.items():
        files[name] = data if data is not None else files[name] + f"\nThe shop's key is {SECRET}.\n".encode()
    refused = _check(client, files)
    assert refused.status_code == 400, refused.text
    assert sentence in refused.json()["error"] and "so Bailey installs nothing from it" in refused.json()["error"]
    for planted in (SECRET, GITHUB, AWS, SLACK, "live-value", "MIIEowIBAAKCAQEA"):
        assert planted not in refused.text, "a refusal names the file and the kind of key, never the key"
    _nothing_written(state)


def test_an_apps_env_giving_a_key_a_value_is_refused_and_one_without_is_left_out(
    client: TestClient, state: AppState
) -> None:
    files = _tree(FIXTURES / "weekly-digest")
    assert _check(client, files).status_code == 200, "the canary: the solution passes as it is"
    keyed = {**files, "apps/digest/.env": b"OPENAI_API_KEY=seller-key\nMODEL=gpt-5\n"}
    refused = _check(client, keyed)
    assert refused.status_code == 400
    assert "apps/digest/.env gives OPENAI_API_KEY a value" in refused.json()["error"]
    assert "seller-key" not in refused.text
    database = {**files, "apps/digest/.env": b"DIGEST_STORE=postgres://shop:hunter2@db.example/digest\n"}
    assert "apps/digest/.env gives DIGEST_STORE a value" in _check(client, database).json()["error"]
    settings = {**files, "apps/digest/.env": b"POSTERN_AGENT_ID=sigrix/weekly-digest\nOPENAI_API_KEY=\nMODEL=gpt-5\n"}
    checked = _check(client, settings)
    assert checked.status_code == 200 and checked.json()["left_out"] == ["apps/digest/.env"]
    assert state.store.list_assistants() == [], "checking writes nothing"


def test_an_apps_credential_entry_with_anything_beside_its_name_purpose_and_link_is_refused(
    client: TestClient, state: AppState
) -> None:
    files = _tree(FIXTURES / "weekly-digest")
    describe = json.loads(files["apps/digest/postern.json"])
    describe["credentials"][0]["value"] = "seller-key"
    refused = _check(client, {**files, "apps/digest/postern.json": json.dumps(describe).encode()})
    assert refused.status_code == 400
    assert "apps/digest/postern.json declares OPENAI_API_KEY with more beside it" in refused.json()["error"]
    assert "seller-key" not in refused.text
    _nothing_written(state)


def test_the_one_step_import_refuses_what_the_install_screen_refuses(client: TestClient, state: AppState) -> None:
    files = {**_order_desk(), "README.md": f"Use {AWS} to sign in.\n".encode()}
    refused = client.post("/api/assistants/import", headers=AUTH, content=_zip(files))
    assert refused.status_code == 400 and "README.md carries what looks like an AWS access key id" in refused.text
    _nothing_written(state)


#: Each shape, and the text near it that is not one: documentation, placeholders, prose.
SHAPE_CASES = [
    (f"key = {SECRET}", "an OpenAI API key"),
    ("key = sk-ant-api03-" + "a" * 30, "an Anthropic API key"),
    (GITHUB, "a GitHub token"),
    (AWS, "an AWS access key id"),
    ("AIza" + "B" * 35, "a Google API key"),
    (SLACK, "a Slack token"),
    ("sk_live_" + "c" * 26, "a Stripe live secret key"),
    ("SG." + "d" * 22 + "." + "e" * 43, "a SendGrid API key"),
    (PEM, "a private key"),
    # What a skill or a README says about keys without carrying one.
    ("AKIAIOSFODNN7EXAMPLE, AWS's own documented example", None),
    ("export OPENAI_API_KEY=sk-your-key-here-replace-me-please", None),
    ("-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----", None),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U", None),
    ("a task-management-and-risk-assessment-framework", None),
    ("ask about sk-ant- prefixes", None),
]


@pytest.mark.parametrize(("text", "kind"), SHAPE_CASES)
def test_each_credential_shape_is_a_key_or_nothing(text: str, kind: str | None) -> None:
    if kind is None:
        check_credentials({"README.md": text.encode()})
        return
    with pytest.raises(SolutionRefused, match=f"README.md carries what looks like {kind}\\."):
        check_credentials({"README.md": text.encode()})


def test_every_credential_shape_has_a_case() -> None:
    kinds = {kind for _, kind in SHAPE_CASES if kind}
    assert kinds == {shape.kind for shape in CREDENTIAL_SHAPES}


def test_a_variable_left_blank_referenced_or_filled_by_the_shared_client_passes_every_config() -> None:
    env = {"A": "", "B": "${B}", "C": "${input:c}", "D": "${env:D}", "E": "__SIGRIX_SHARED_OAUTH__:E"}
    files = {
        ".mcp.json": _mcp(env),
        ".vscode/mcp.json": _mcp(env, key="servers"),
        "remote.mcp.json": json.dumps(
            {
                "mcpServers": {
                    "r": {
                        "url": "https://r.example",
                        "headers": {"Authorization": "Bearer ${R_TOKEN}", "Accept": "application/json"},
                    }
                }
            }
        ).encode(),
        ".env.example": b"OPENAI_API_KEY=sk-your-key-here\n",
    }
    check_credentials(files)


# ---------------------------------------------------------------------------
# Done when: a path outside its folder is refused with a sentence
# ---------------------------------------------------------------------------


def _raw_zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(zipfile.ZipInfo(name), data)
    return buffer.getvalue()


@pytest.mark.parametrize("name", ["../plugin.json", "skills/../../escape.md", "/etc/cron.d/bailey", "C:/Windows/x"])
def test_a_zip_naming_a_path_outside_its_folder_is_refused(client: TestClient, state: AppState, name: str) -> None:
    entries = [*sorted(_order_desk().items()), (name, b"x")]
    refused = client.post(
        "/api/installs", headers={**AUTH, "Content-Type": "application/zip"}, content=_raw_zip(entries)
    )
    assert refused.status_code == 400 and refused.json()["error"] == f"{name} points outside the package."
    _nothing_written(state)


@pytest.mark.parametrize("name", ["../plugin.json", "order-desk/../../escape.md", "/etc/passwd", "a\\b"])
def test_a_folder_naming_a_path_outside_itself_is_refused(client: TestClient, state: AppState, name: str) -> None:
    body = _folder(_order_desk())
    body["files"].append({"path": name, "data": base64.b64encode(b"x").decode()})
    refused = client.post("/api/installs", headers=AUTH, json=body)
    assert refused.status_code == 400 and refused.json()["error"] == f"{name} points outside the folder."
    _nothing_written(state)


@pytest.mark.filterwarnings("ignore:Duplicate name")
def test_a_zip_carrying_two_files_under_one_name_is_refused(client: TestClient) -> None:
    entries = [*sorted(_order_desk().items()), ("README.md", b"a second README")]
    refused = client.post(
        "/api/installs", headers={**AUTH, "Content-Type": "application/zip"}, content=_raw_zip(entries)
    )
    assert refused.json()["error"] == "The package carries two files named README.md."


@pytest.mark.parametrize(
    ("entries", "sentence"),
    [
        ([{"path": f"f/{index}.md", "data": ""} for index in range(MAX_ENTRIES + 1)], f"more than {MAX_ENTRIES} files"),
        ([{"path": "f/big.md", "data": base64.b64encode(b"x" * (MAX_FILE_BYTES + 1)).decode()}], "larger than 512 KB"),
        ([{"path": "f/a.md", "data": "not base64!"}], "arrived garbled"),
        ([{"path": "f/a.md"}], "with its path and its contents"),
        ([], "The folder is empty."),
    ],
)
def test_a_folder_is_held_to_a_packages_limits(client: TestClient, entries: list[Any], sentence: str) -> None:
    refused = client.post("/api/installs", headers=AUTH, json={"files": entries})
    assert refused.status_code == 400 and sentence in refused.json()["error"]


def test_a_package_with_no_assistant_json_and_no_skill_md_is_not_a_solution(client: TestClient) -> None:
    refused = _check(client, {"README.md": b"# Notes\n"})
    assert refused.json()["error"] == (
        "This is not a solution Bailey can install: it carries no assistant.json, and no SKILL.md to make one from."
    )


# ---------------------------------------------------------------------------
# A skill carrying scripts installs only from the store, unless the person says otherwise
# ---------------------------------------------------------------------------


def _with_scripts() -> dict[str, bytes]:
    return {
        **_order_desk(),
        # One script for each way a file is one: its folder, its suffix, its first line.
        "skills/refund-rules/scripts/limits.awk": b"{ print $1 }\n",
        "skills/refund-rules/helpers/limits.py": b"print('SCRIPT-RAN-7f3a')\n",
        "skills/refund-rules/check": b"#!/bin/sh\necho SHEBANG-RAN-9c1d\n",
        "skills/refund-rules/assets/letter.txt": b"Dear customer,\n",
    }


def test_a_skill_carrying_scripts_is_refused_naming_the_setting_that_allows_it(
    client: TestClient, state: AppState
) -> None:
    refused = _check(client, _with_scripts())
    assert refused.status_code == 400
    assert refused.json()["error"] == (
        "The refund-rules skill carries scripts (skills/refund-rules/check and 2 more). A skill with scripts installs "
        f'only from the Sigrix store, unless you turn on "{SCRIPTS_SETTING_LABEL}" in Settings. Bailey runs no '
        "skill's scripts, so it would install the skill without them."
    )
    _nothing_written(state)
    settings = client.get("/api/settings", headers=AUTH).json()
    assert settings["installing"] == {"skills_with_scripts": False, "label": SCRIPTS_SETTING_LABEL}


def test_with_the_setting_on_the_skill_installs_without_its_scripts(client: TestClient, state: AppState) -> None:
    turned = client.put("/api/settings", headers=AUTH, json={"skills_with_scripts": True})
    assert turned.status_code == 200 and turned.json()["installing"]["skills_with_scripts"] is True
    assert turned.json()["provider"] is None, "turning it on chose no provider"
    screen, installed = _install(client, _with_scripts())
    assert screen["left_out"] == [
        "skills/refund-rules/assets/letter.txt",
        "skills/refund-rules/check",
        "skills/refund-rules/helpers/limits.py",
        "skills/refund-rules/scripts/limits.awk",
    ]
    assert "Left out of its skills: skills/refund-rules/assets/letter.txt" in installed["notice"]
    stored = state.store.get_assistant(installed["id"])
    assert stored is not None
    assert "SCRIPT-RAN-7f3a" not in json.dumps(stored) and "SHEBANG-RAN-9c1d" not in json.dumps(stored)


def test_a_skill_with_other_files_and_no_script_installs_and_says_what_it_left_out(client: TestClient) -> None:
    files = {**_order_desk(), "skills/refund-rules/assets/letter.txt": b"Dear customer,\n"}
    checked = _check(client, files)
    assert checked.status_code == 200 and checked.json()["left_out"] == ["skills/refund-rules/assets/letter.txt"]


def test_the_settings_refuse_a_scripts_setting_that_is_not_true_or_false(client: TestClient) -> None:
    refused = client.put("/api/settings", headers=AUTH, json={"skills_with_scripts": "yes"})
    assert refused.status_code == 400 and refused.json()["error"] == "skills_with_scripts is true or false."


# ---------------------------------------------------------------------------
# What Install writes is what was checked and shown
# ---------------------------------------------------------------------------


def test_checking_writes_nothing_and_install_writes_what_was_held_once(client: TestClient, state: AppState) -> None:
    checked = _check(client, _order_desk())
    assert checked.status_code == 200
    _nothing_written(state)
    install_id = checked.json()["id"]
    assert client.post(f"/api/installs/{install_id}", headers=AUTH).status_code == 200
    again = client.post(f"/api/installs/{install_id}", headers=AUTH)
    assert (
        again.status_code == 404
        and again.json()["error"] == "That install is no longer waiting. Choose the solution again."
    )
    installed = [record for _, record in state.store.list_assistants() if record.get("origin") == "file"]
    assert len(installed) == 1, "the default assistant is made beside it, and the package is written once"


def test_cancel_drops_what_was_held(client: TestClient, state: AppState) -> None:
    install_id = _check(client, _order_desk()).json()["id"]
    assert client.delete(f"/api/installs/{install_id}", headers=AUTH).json() == {"cancelled": True}
    assert client.post(f"/api/installs/{install_id}", headers=AUTH).status_code == 404
    _nothing_written(state)


def test_a_held_install_expires_and_the_oldest_gives_way() -> None:
    checked = check_package(_order_desk(), scripts_allowed=False, provider="")
    expired = PendingInstalls(keep_for=0)
    assert expired.take(expired.hold(checked)) is None
    held = PendingInstalls(most=2)
    first, second, third = (held.hold(checked) for _ in range(3))
    assert held.take(first) is None and held.take(second) is checked and held.take(third) is checked


def test_keys_entered_on_the_screen_are_kept_where_a_tools_panel_keeps_them(
    client: TestClient, state: AppState
) -> None:
    _, installed = _install(client, _order_desk())
    saved = client.put(
        f"/api/assistants/{installed['id']}/tools/orders/variables/SHOP_API_KEY", headers=AUTH, json={"value": "my-key"}
    )
    assert saved.status_code == 200
    assert state.keys.get(variable_key(installed["id"], "orders", "SHOP_API_KEY")) == "my-key"


# ---------------------------------------------------------------------------
# Unverified
# ---------------------------------------------------------------------------


def test_what_comes_from_a_file_is_unverified_and_stays_so_and_a_template_is_not(client: TestClient) -> None:
    _, installed = _install(client, _order_desk())
    made = client.post("/api/assistants", headers=AUTH, json={"template": DEFAULT_TEMPLATE}).json()
    listed = {
        entry["id"]: entry["unverified"] for entry in client.get("/api/assistants", headers=AUTH).json()["assistants"]
    }
    assert listed[installed["id"]] is True and listed[made["id"]] is False
    form = client.get(f"/api/assistants/{installed['id']}", headers=AUTH).json()
    form["title"] = "Order desk, edited"
    assert client.put(f"/api/assistants/{installed['id']}", headers=AUTH, json=form).status_code == 200
    listed = {
        entry["id"]: entry["unverified"] for entry in client.get("/api/assistants", headers=AUTH).json()["assistants"]
    }
    assert listed[installed["id"]] is True, "an edit does not make a stranger's package checked"


# ---------------------------------------------------------------------------
# Reading what arrives
# ---------------------------------------------------------------------------


def test_a_skill_name_comes_from_a_title_without_the_reserved_words() -> None:
    assert skill_name_for("Order desk") == "order-desk"
    assert skill_name_for("Claude's helpful Anthropic desk") == "s-helpful-desk"
    assert skill_name_for("!!!") == "assistant"


def test_a_solution_is_never_converted_and_a_plain_plugin_is() -> None:
    files = _order_desk()
    assert convert_download(files) == (files, False)
    plain = {**_before_solutions(files), "plugin.json": json.dumps({"name": "order-desk"}).encode()}
    converted, was = convert_download(plain)
    assert was is True and json.loads(converted["assistant.json"])["instructions"] == "skills/order-desk/SKILL.md"


def test_a_converted_download_is_titled_by_its_readme() -> None:
    skill = b'---\nname: shop-desk\ndescription: "Answers order questions."\n---\n\nAnswer from the order system.\n'
    readme = b"# Thistlecombe order desk\n\nAnswers order questions.\n"
    converted, was = convert_download({"SKILL.md": skill, "README.md": readme})
    document = json.loads(converted["assistant.json"])
    assert was is True and (document["name"], document["title"]) == ("shop-desk", "Thistlecombe order desk")
