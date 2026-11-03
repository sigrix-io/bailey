"""Start Bailey for a recording: a data folder of its own, and a scripted model instead of a provider.

    python lessons/scripted_bailey.py <replies.json> [port]

The model is Bailey's own test emulator (``tests/emulated_providers.py``), which speaks each
provider's wire protocol, so the app runs exactly as it does for a person: only the answers
are scripted, so every recording of a lesson comes out the same, with no key and no cost.

``replies.json`` lists the answers a lesson scripts, each for the message that contains its
``when``. A reply loads ``skill`` first, when it names one; then makes its ``calls`` in
order, each a ``{"tool": ..., "args": {...}}`` the app runs or asks the person about, one per
turn as a model makes them; then answers with ``reply``, a list of ``[text, phrase]`` pairs,
citing after a text the passage that holds its phrase, as Bailey asks a model to (``[P3]``),
once per passage. Any other message gets the emulator's own answer.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "tests"), str(REPO / "src")]

import emulated_providers as emulator  # noqa: E402

from bailey import server  # noqa: E402

#: Slower than the tests' stream, so a viewer sees the answer arrive.
CHUNK_DELAY = 0.045


def _passages(system: str) -> list[tuple[str, str, str]]:
    """The numbered passages of the documents the request carries whole, as the emulator reads them."""

    if "## Your documents" not in system:
        return []
    return emulator._sent_whole(system.split("## Your documents", 1)[1])


def answer(conversation: emulator.Conversation, replies: Sequence[Mapping[str, Any]]) -> emulator.Turn | None:
    """The scripted model's next turn, or ``None`` when no reply is scripted for the latest message."""

    asked = conversation.user[-1] if conversation.user else ""
    reply = next((reply for reply in replies if reply["when"].lower() in asked.lower()), None)
    if reply is None:
        return None
    done = dict(conversation.turn_results)
    if reply.get("skill") and "load_skill" in conversation.tools and "load_skill" not in done:
        return emulator.Turn("", [emulator.Call(emulator._new_id("call"), "load_skill", {"name": reply["skill"]})])
    for call in reply.get("calls", ()):
        if call["tool"] not in done:
            if call["tool"] not in conversation.tools:
                # As a provider refuses a call to a tool it was not offered: the lesson no longer fits the app.
                raise emulator.Violation(f"the script calls {call['tool']!r}; offered: {conversation.tools}")
            return emulator.Turn("", [emulator.Call(emulator._new_id("call"), call["tool"], dict(call["args"]))])
    passages = _passages(conversation.system)
    text = ""
    for part, phrase in reply["reply"]:
        text += part
        number = next((number for number, _, body in passages if phrase and phrase in body), None)
        if number is not None and f"[P{number}]" not in text:
            text += f" [P{number}]"
    return emulator.Turn(text)


def main() -> int:
    replies = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    emulators_turn = emulator.next_turn

    def scripted_turn(conversation: emulator.Conversation) -> emulator.Turn:
        return answer(conversation, replies) or emulators_turn(conversation)

    emulator.CHUNK_DELAY = CHUNK_DELAY
    emulator.next_turn = scripted_turn
    base = emulator.serve_in_background()
    app_state = server.AppState

    def scripted_state(*args: Any, **kwargs: Any) -> server.AppState:
        # Each provider's address is the emulator's, as the tests point them (AppState.base_urls).
        kwargs.setdefault("base_urls", {"anthropic": base + "/anthropic", "openai": base + "/openai/v1"})
        return app_state(*args, **kwargs)

    server.AppState = scripted_state
    return server.serve(port=int(sys.argv[2]) if len(sys.argv) > 2 else 0, open_browser=False)


if __name__ == "__main__":
    sys.exit(main())
