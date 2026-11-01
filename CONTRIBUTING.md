# Contributing

Thanks for looking. Bailey's first release is a single command, `bailey doctor`;
the assistant itself is being built toward 0.1, and its frame (the local server,
the page, settings and keys, and chat with assistants and their skills) is on
`main`.

## What fits now

- A check the doctor gets wrong on a real machine: a Python build, an
  operating system or a tool installer it misreads. Say what it printed and
  what was actually there.
- A clearer sentence when a check fails. Say what is missing and what to do;
  never guess at a cause the check did not see.
- The app's frame on a system or browser where it misbehaves. Say which, and
  what you saw.

## What does not

- A check that reaches the network, writes a file or reads a secret. The
  doctor reads this machine and nothing else.
- Anything that makes the app reach a host other than this computer and the
  AI provider the user chose. There is no telemetry, and a test fails on any
  other connection.
- A request the app answers without the session's token, beyond the page and
  its static files. A new route is refused until it is named as public, which
  it should not be.
- Markup built from a string in the page's scripts. The page writes text with
  `textContent`; a test fails on `innerHTML` and its relatives.
- A conversation stored in a library's own message schema. Bailey stores its
  own format, `bailey-message/1` (`src/bailey/conversations.py`), and converts
  at the edge, so a dependency's next version moves no one's history.
- A skill's body in every request. The model sees a skill's name and
  description, and its body arrives when `load_skill` asks for it; the contract
  tests fail when a body is sent unasked.

## Working on it

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest
```

`bailey` from the checkout starts the app; `BAILEY_HOME=/some/folder bailey`
keeps a second setup apart from your own, and `BAILEY_KEYS=file` keeps its keys
out of your keychain. No test writes to a real keychain except
`tests/test_real_keychain.py`, which runs only with `BAILEY_TEST_REAL_KEYCHAIN=1`.

Chat is tested on `tests/emulated_providers.py`, which speaks each provider's
own wire protocol and refuses what the real services refuse, so no test needs a
key. A PydanticAI or SDK version bump lands only once
`tests/test_agent_contract.py` passes on it.

Each doctor check takes what it probes as an argument, so a test can hand it a
machine that lacks something. A new check comes with a test on both sides.

## Pull requests

- One change per pull request, with a test that fails without it.
- `CHANGELOG.md` gets a line under *Unreleased*.
