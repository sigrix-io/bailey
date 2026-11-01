"""Every thread imports the agent core through one door (``bailey.core``).

The app imports PydanticAI and the MCP libraries in the background once it is up, and a
message that comes sooner imports them itself. Two threads importing one package for the
first time can deadlock in Python's import system, and CI met that as a 500 on a first
message: one thread held ``pydantic_ai.messages`` and waited for ``pydantic_ai``, whose
``__init__`` the other was running. The first test forces that interleaving instead of
waiting for it; the second holds every lazy import of the core to the loader.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import textwrap
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "bailey"

#: The libraries that take seconds to import, and so are imported inside functions.
CORE = ("pydantic_ai", "fastmcp", "mcp", "bailey.agent")

# A fresh interpreter, because the race is about a first import. An import hook holds the
# warm-up inside pydantic_ai's import lock for half a second, and a first message's
# to_history starts in that window, as it did in CI's slow runs.
RACE = textwrap.dedent(
    """
    import json, sys, threading, time, traceback

    import bailey.server  # the app as it starts serving, with no agent core loaded
    from bailey import core
    from bailey.conversations import to_history

    assert "pydantic_ai" not in sys.modules
    holding = threading.Event()
    failures = []


    class HoldInsidePydanticAI:
        def find_spec(self, name, path=None, target=None):
            if name == "pydantic_ai" and threading.current_thread().name == "warm-up":
                holding.set()
                time.sleep(0.5)
            return None


    sys.meta_path.insert(0, HoldInsidePydanticAI())


    def warm_up():
        try:
            core.load()
        except BaseException:
            failures.append("warm-up: " + traceback.format_exc())


    def first_message():
        holding.wait(30)
        try:
            to_history([])
        except BaseException:
            failures.append("message: " + traceback.format_exc())


    threads = [threading.Thread(target=warm_up, name="warm-up"), threading.Thread(target=first_message)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)
    print(json.dumps({"held": holding.is_set(), "failures": failures, "loaded": "bailey.agent" in sys.modules}))
    """
)


def test_a_first_message_during_the_background_import_waits_for_it_rather_than_deadlocking() -> None:
    finished = subprocess.run(
        [sys.executable, "-c", RACE], capture_output=True, text=True, timeout=300, cwd=SOURCE.parents[1]
    )
    assert finished.returncode == 0, finished.stderr
    report = json.loads(finished.stdout.strip().splitlines()[-1])
    assert report["held"], "the canary: the warm-up was held inside pydantic_ai's import"
    assert report["failures"] == [], "\n".join(report["failures"])
    assert report["loaded"] is True


def _lazy_imports() -> dict[str, list[tuple[str, bool]]]:
    """Each function importing the core, with each such import and whether the function called the loader before it."""

    found: dict[str, list[tuple[str, bool]]] = {}
    for path in sorted(SOURCE.glob("*.py")):
        if path.name in ("core.py", "agent.py"):
            continue  # the loader itself, and the core, which imports its own libraries at the top
        for function in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            loaded_at = [
                node.lineno
                for node in ast.walk(function)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("load", "load_core")
            ]
            for node in ast.walk(function):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    if name in CORE or name.startswith(tuple(f"{library}." for library in CORE)):
                        found.setdefault(f"{path.name}:{function.name}", []).append(
                            (name, any(line < node.lineno for line in loaded_at))
                        )
    return found


def test_every_lazy_import_of_the_core_goes_through_the_loader() -> None:
    found = _lazy_imports()
    assert {"conversations.py:to_records", "conversations.py:to_history", "tools.py:_start"} <= set(found), (
        f"the canary: the sweep finds the lazy imports it is meant to hold, and found {sorted(found)}"
    )
    unguarded = [
        f"{where} imports {name}" for where, imports in found.items() for name, guarded in imports if not guarded
    ]
    assert unguarded == [], f"call bailey.core.load first, so a first import keeps to one thread: {unguarded}"
    warm_up = next(
        node
        for node in ast.walk(ast.parse((SOURCE / "server.py").read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef) and node.name == "_warm_up"
    )
    assert any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "load_core"
        for node in ast.walk(warm_up)
    ), "the background import comes through the same door"
