# Contributing

Thanks for looking. Bailey's first release is a single command, `bailey doctor`;
the assistant itself is being built toward 0.1, and its frame (the local server,
the page, settings and keys, chat with assistants and their skills, their
tools and their documents) is on `main`.

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
- A tool server started any way but `bailey.tools`. FastMCP's `uvx` and `npx`
  transports hand the child Bailey's whole environment; the tool host builds
  each server on the base `StdioTransport` with only its named variables, and
  `tests/test_tools.py` fails when a decoy secret reaches one.
- A tool that changes something and runs without asking. A write, and any tool a
  solution does not declare, waits for the person unless they allowed it always
  for that assistant (`tests/test_tool_contract.py`).
- A document, or anything about one, leaving the computer other than the
  passages a request carries to the AI provider. Conversion, splitting,
  indexing and search all run locally (`src/bailey/knowledge.py`).
- An app's runner started any way but `bailey.apps`, or handed `SIGRIX_TOKEN` or
  `POSTERN_AGENT_ID`. With neither, the runner checks no licence and looks for no
  update, and the no-telemetry test records the runner and its crew to hold it so.
- CrewAI, or anything an app needs, installed with Bailey. An app runs in an
  environment of its own, built from its own requirements once the person says yes.
- An edit to `src/bailey/web/gatehouse/`. It is a release of
  `@sigrix-io/gatehouse`; change Gatehouse upstream and run
  `python scripts/vendor_gatehouse.py <version>`.
- A download nobody asked for. The search model is fetched only from
  `POST /api/knowledge/model`, which the page calls after it has named the model,
  its size and where it comes from; everywhere else it loads with nothing
  fetched.

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
key, and tools on `tests/tool_servers/mailbox.py`, a real stdio MCP server. A
PydanticAI, SDK, FastMCP or MCP version bump lands only once
`tests/test_agent_contract.py` and `tests/test_tool_contract.py` pass on it.

Documents are tested on the fixture solution `tests/fixtures/shop-replies`, with
`fakes.FakeEmbedder` standing in for the search model, so no test downloads it.
One test runs the real model: set `BAILEY_SEARCH_MODEL` to a folder holding it,
or `BAILEY_TEST_REAL_EMBEDDINGS=1` to let the test fetch it; CI does the second
on one job. A MarkItDown, fastembed or onnxruntime bump lands only once
`tests/test_knowledge_contract.py` and that test pass on it.

Apps are tested on the fixture solution `tests/fixtures/weekly-digest` with the
real runner, `sigrix-runtime`, and `tests/stand_ins/crewai` in place of CrewAI.
`tests/app_environments.py` makes an app's environment ready without a download,
for the tests and for trying an app by hand.

Each doctor check takes what it probes as an argument, so a test can hand it a
machine that lacks something. A new check comes with a test on both sides.

## Pull requests

- One change per pull request, with a test that fails without it.
- `CHANGELOG.md` gets a line under *Unreleased*.
