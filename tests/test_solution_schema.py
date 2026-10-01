"""The solution format's schema, and the case table every implementation of it runs.

`schemas/0.1/assistant.schema.json` is the writer's contract for `assistant.json`,
and Bailey is where it is published because Bailey is what reads it. A builder
that writes the document vendors the schema, and a pair mirrored across two
repositories drifts in whichever direction nobody is looking. So the cases live
beside the schema, in `assistant.cases.json`, and a vendoring repository runs the
same table with its own validator. Each invalid case is a valid one with exactly
one change, which is what makes "it failed" mean "it failed for that change".

Read as files rather than through the package: the schema is published from this
repository, and the app reads it once installing a solution is built.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
VERSION = "0.1"
SCHEMA = json.loads((SCHEMAS / VERSION / "assistant.schema.json").read_text(encoding="utf-8"))
CASES = json.loads((SCHEMAS / VERSION / "assistant.cases.json").read_text(encoding="utf-8"))
VALID: dict[str, Any] = {case["name"]: case["document"] for case in CASES["valid"]}
VALIDATOR = Draft202012Validator(SCHEMA)


def _tokens(pointer: str) -> list[str]:
    """RFC 6901: split a JSON Pointer, and unescape ``~1`` before ``~0``."""
    assert pointer.startswith("/"), f"not a JSON Pointer: {pointer!r}"
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]


def _apply(document: Any, change: dict[str, Any]) -> Any:
    """Apply one RFC 6902 add, replace or remove to a copy of *document*.

    Stricter than the RFC on purpose: an ``add`` must create a member rather than
    overwrite one, and a ``replace`` or ``remove`` must find one, so a case whose
    path is misspelt fails here instead of passing because its base was invalid.
    """
    changed = copy.deepcopy(document)
    *parents, last = _tokens(change["path"])
    target = changed
    for token in parents:
        target = target[int(token)] if isinstance(target, list) else target[token]
    op = change["op"]
    if isinstance(target, list):
        index = int(last)
        if op == "add":
            target.insert(index, change["value"])
        elif op == "replace":
            target[index] = change["value"]
        elif op == "remove":
            del target[index]
        else:
            raise AssertionError(f"unknown op {op!r}")
        return changed
    if op == "add":
        assert last not in target, f"add would overwrite {change['path']}"
        target[last] = change["value"]
    elif op == "replace":
        assert last in target, f"nothing to replace at {change['path']}"
        target[last] = change["value"]
    elif op == "remove":
        assert last in target, f"nothing to remove at {change['path']}"
        del target[last]
    else:
        raise AssertionError(f"unknown op {op!r}")
    return changed


def _errors(document: Any) -> list[str]:
    return [
        f"{'/'.join(map(str, error.absolute_path)) or '(root)'}: {error.message}"
        for error in VALIDATOR.iter_errors(document)
    ]


def test_the_schema_is_a_draft_2020_12_schema() -> None:
    Draft202012Validator.check_schema(SCHEMA)


def test_its_identifier_names_the_directory_it_is_published_from() -> None:
    """The ``$id`` is permanent once served, and the directory is the format version."""
    assert SCHEMA["$id"] == f"https://sigrix.io/schemas/bailey/{VERSION}/assistant.schema.json"
    assert SCHEMA["properties"]["format_version"]["const"] == VERSION
    assert CASES["schema"] == SCHEMA["$id"], "the case table names another schema"


@pytest.mark.parametrize("name", list(VALID))
def test_a_valid_case_validates(name: str) -> None:
    assert _errors(VALID[name]) == []


@pytest.mark.parametrize("case", CASES["invalid"], ids=[case["name"] for case in CASES["invalid"]])
def test_an_invalid_case_fails(case: dict[str, Any]) -> None:
    assert case["base"] in VALID, f"{case['name']!r} is built on {case['base']!r}, which is not a valid case"
    document = _apply(VALID[case["base"]], case["change"])
    assert document != VALID[case["base"]], "the change changed nothing"
    assert _errors(document), f"{case['name']!r} validates, so the schema allows what the case says it refuses"


def test_case_names_are_unique() -> None:
    names = [case["name"] for case in CASES["valid"] + CASES["invalid"]]
    assert len(names) == len(set(names))


def _members(schema: dict[str, Any]) -> set[str]:
    """Every member the schema defines, at the root and in each object of ``$defs``."""
    members = set(schema["properties"])
    for definition in schema["$defs"].values():
        members |= set(definition.get("properties", {}))
    return members


def _used(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {name for item in value.values() for name in _used(item)}
    if isinstance(value, list):
        return {name for item in value for name in _used(item)}
    return set()


def test_every_member_appears_in_a_valid_case() -> None:
    """A member no valid document carries is one nothing shows a writer how to write."""
    used = {name for document in VALID.values() for name in _used(document)}
    assert _members(SCHEMA) - used == set()


def _where_each_definition_sits() -> dict[str, str]:
    """``$defs`` object name -> the pattern of the paths its instances sit at.

    Read off the root's own properties: a list of them sits at ``/<member>/<index>``
    and a map of them at ``/<member>/<key>``.
    """
    places: dict[str, str] = {}
    for member, schema in SCHEMA["properties"].items():
        holder = schema.get("items") or schema.get("additionalProperties")
        if isinstance(holder, dict) and "$ref" in holder:
            places[holder["$ref"].rsplit("/", 1)[1]] = f"/{member}/[^/]+"
    return places


def test_every_required_member_has_a_case_without_it() -> None:
    """Derived from the schema, so a member made required later needs its case too."""
    removed = {case["change"]["path"] for case in CASES["invalid"] if case["change"]["op"] == "remove"}
    wanted = {f"/{member}" for member in SCHEMA["required"]}
    for definition, place in _where_each_definition_sits().items():
        wanted |= {f"{place}/{member}" for member in SCHEMA["$defs"][definition].get("required", [])}
    missing = {pattern for pattern in wanted if not any(re.fullmatch(pattern, path) for path in removed)}
    assert missing == set()


def test_every_object_the_documents_hold_is_located() -> None:
    """The canary for the test above: a definition it cannot place it cannot check."""
    objects = {name for name, definition in SCHEMA["$defs"].items() if definition.get("type") == "object"}
    assert set(_where_each_definition_sits()) == objects


def test_the_helper_refuses_a_change_that_finds_nothing() -> None:
    """The guard on the guard: a misspelt path must not read as a passing case."""
    base = VALID["the least a writer may emit"]
    with pytest.raises(AssertionError):
        _apply(base, {"op": "remove", "path": "/titel"})
    with pytest.raises(AssertionError):
        _apply(base, {"op": "add", "path": "/title", "value": "x"})
