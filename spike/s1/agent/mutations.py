"""Breaks the agent core on purpose, one way at a time, and confirms `check.py` goes red each time.

    python mutations.py

A check that has never failed proves nothing. Each mutation below is applied to a copy of this
directory, `check.py` runs against the copy, and the mutation passes only if the checks it names
fail. It also sends each emulated provider a request with a tool call left unanswered, which the
real services refuse, and confirms the emulator refuses it too.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))

import providers  # noqa: E402

HERE = Path(__file__).parent
APPROVAL_CHECKS = ["approve_inline", "deny_inline", "approve_after_restart", "deny_after_restart"]

#: name: (file, original, replacement, the checks that must fail)
MUTATIONS: dict[str, tuple[str, str, str, list[str]]] = {
    "write tools are not wrapped for approval": (
        "core.py",
        "toolsets=[tool_server(drafts_file).approval_required(is_write_tool)],",
        "toolsets=[tool_server(drafts_file)],",
        APPROVAL_CHECKS,
    ),
    "the server gets the app's whole environment": (
        "core.py",
        "env={NAMED_VARIABLE: str(drafts_file)},",
        'env={**__import__("os").environ, NAMED_VARIABLE: str(drafts_file)},',
        ["environment"],
    ),
    "answers arrive in one piece": (
        "providers.py",
        "def _pieces(text: str, size: int = 6)",
        "def _pieces(text: str, size: int = 10**6)",
        ["streaming"],
    ),
    "the model never sees the tool's result": (
        "providers.py",
        'turn = Turn("create_draft answered: " + conversation.results[-1][1])',
        'turn = Turn("Done.")',
        APPROVAL_CHECKS,
    ),
}

UNANSWERED = {
    "/anthropic/v1/messages": {
        "model": "m",
        "max_tokens": 10,
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "create_draft", "input": {}}]},
            {"role": "user", "content": "and?"},
        ],
    },
    "/openai/v1/responses": {
        "model": "m",
        "input": [
            {"role": "user", "content": "hi"},
            {"type": "function_call", "call_id": "c1", "name": "create_draft", "arguments": "{}"},
        ],
    },
    "/local/v1/chat/completions": {
        "model": "m",
        "messages": [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "create_draft", "arguments": "{}"}}
                ],
            },
            {"role": "user", "content": "and?"},
        ],
    },
}


def failing_checks(directory: Path) -> list[str]:
    run = subprocess.run(
        [sys.executable, "check.py"], cwd=directory, capture_output=True, text=True, timeout=600, check=False
    )
    report = json.loads(run.stdout)
    return sorted(
        {name for checks in report["providers"].values() for name, result in checks.items() if not result["ok"]}
    )


def main() -> int:
    results: dict[str, dict[str, object]] = {}
    for name, (filename, original, replacement, expected) in MUTATIONS.items():
        with tempfile.TemporaryDirectory() as scratch:
            copy = Path(scratch) / "agent"
            shutil.copytree(HERE, copy, ignore=shutil.ignore_patterns("__pycache__"))
            target = copy / filename
            source = target.read_text(encoding="utf-8")
            if source.count(original) != 1:
                raise SystemExit(f"{name}: the code to mutate is not in {filename} exactly once")
            target.write_text(source.replace(original, replacement), encoding="utf-8")
            failed = failing_checks(copy)
        results[name] = {"ok": set(expected) <= set(failed), "expected_red": expected, "red": failed}
    base = providers.serve_in_background()
    refusals = {path: httpx.post(base + path, json=body).status_code for path, body in UNANSWERED.items()}
    results["an unanswered tool call is refused"] = {
        "ok": all(code == 400 for code in refusals.values()),
        "status": refusals,
    }
    print(json.dumps(results, indent=2))
    return 0 if all(result["ok"] for result in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
