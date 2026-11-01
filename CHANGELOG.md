# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the versioning is
described in `VERSIONING.md`.

## [Unreleased]

### Added

- Tools (story S7 of 0.1). Each MCP server a solution's `mcp.json` names starts as its own
  process over stdio, built on FastMCP's base `StdioTransport` rather than its `uvx` and
  `npx` helpers, which hand a child the app's whole environment. A server receives the MCP
  SDK's short default list (`HOME`, `PATH` and the like), each variable its `mcp.json`
  names, filled from the keychain and never from Bailey's own environment or the package,
  and the proxy and certificate variables Bailey has, so a `uvx` or `npx` server can
  download itself behind a corporate proxy. A server starts at the first message that
  needs it and keeps running between messages; it stops when its assistant or its values
  change, when the assistant is deleted, and when Bailey stops. A remote server is listed
  and not connected in this version.
- Approval before a write. A read a server's declaration lists runs without asking, and
  the chat shows each call as it happens. A write, a tool the declaration leaves out, and
  every tool of a server with no declaration ask first: once, always for this assistant,
  or no. "No" reaches the model as the tool's result. "Always" is kept per assistant,
  server and tool, and Settings lists each with a way to ask again. A run that asks ends
  there, and the question waits in the conversation, so it survives a reload and a
  restart; the answer resumes the run. A run that fails after a tool ran keeps that
  tool's result, so a write that happened is never asked about again.
- Each assistant's Tools panel lists its servers, their tools with the writes marked, and
  their variables, each saved or not, with a field to give it. `SIGRIX_TOKEN`, the runner
  token of a server Sigrix delivers (`uvx sigrix-launcher run <seller>/<listing-id>`), is
  kept once for every such server. The import and the panel say plainly that Bailey does
  not sandbox a server beyond its environment.
- The solution format: an import reads `mcp.json` and `assistant.json`'s `tools`, keeping
  each variable's name and never its value, and an export writes them back, with an
  empty value for each variable. The store's third migration keeps the "always" grants.
- The contract tests for tools, on the provider emulator and a stdio mailbox server: the
  read runs and the write asks, on all three protocols; a yes runs it and a no reaches
  the model; a paused run is read back from the store and resumed by a new server
  process; a read and a write in one reply are stored and resumed in a shape each
  provider takes. A server sees only its named variables, with a decoy secret beside
  them, and the no-telemetry test now runs a chat through a tool server too.
- PydanticAI gains its `mcp` extra, with `fastmcp-slim` 4.1.0 and `mcp` 2.3.0 pinned
  exactly, the versions the spike measured. They load after the server is up.

- Chat and the agent core (story S6 of 0.1). An assistant answers on Anthropic (the
  Messages API), OpenAI (the Responses API) or any OpenAI-compatible server, local models
  included (Chat Completions), with the person's own key, and the answer streams into
  the page as text. The core is PydanticAI 2.56.0 with `anthropic` 1.13.0 and `openai`
  3.31.0, all pinned exactly at the versions S1's spike measured; the base URL is always
  passed, so a shell's `ANTHROPIC_BASE_URL` or `OPENAI_BASE_URL` never redirects a key,
  and PydanticAI's banner is off. The server loads the core only to answer, and warms it
  in the background once it is up, so starting the app waits for none of it.
- The context: the assistant's instructions and voice, then each skill's name and
  one-line description, and nothing more of any skill. A skill's body and its small
  `references/` files reach the model only when it calls `load_skill` for that skill.
  On Anthropic the instructions are cached as a stable prefix
  (`anthropic_cache_instructions`).
- Each answer ends with a usage line: tokens in and out, and an estimated cost in US
  dollars, labelled as an estimate, from the price data PydanticAI ships with. Nothing
  fetches newer prices; a model the data does not know, or a local one, shows tokens
  alone.
- Assistants. A first start gets the default one, *Everyday helper*, with two skills,
  *summarise* and *draft-email*; a new one starts from a template (*Writing editor* is
  the other), never an empty canvas. An assistant is edited as a form, chosen from the
  chat, exported as a zip in the solution layout (`plugin.json`, `assistant.json`,
  `skills/<name>/SKILL.md`), and imported from one. The templates are written in that
  layout too, so one reader serves all three; an import is bounded in size, entries and
  unpack ratio, and refuses a link or a path outside its folder, each with a sentence.
- Conversations, stored in a format of Bailey's own, `bailey-message/1`, and converted to
  and from PydanticAI's messages only at the edge (S1's ruling), so no stored history
  depends on one library's schema. A run that fails leaves a notice in the conversation,
  shown and never sent to a model.
- Settings: the model, per provider. Anthropic starts on `claude-sonnet-5-5` and OpenAI
  on `gpt-5-mini`; an OpenAI-compatible server takes the name it lists.
- The contract tests S1's ruling asked for, on the spike's provider emulator, now in
  `tests/emulated_providers.py`: an answer streams on all three protocols, an assistant
  with two skills answers from the right one, and the other skill's body is in no request
  at all. Every PydanticAI bump passes them before it lands. The no-telemetry test now
  runs a whole chat, and still fails on any connection but the provider's.

- The app's frame (story S5 of 0.1). `bailey` with no command starts a server on 127.0.0.1
  and opens the browser on an address that carries a token for this session; `--port`
  picks the port and `--no-browser` prints the address instead. Everything but the page
  and its three static files needs that token, so a request from another web page, or one
  from an earlier session, is refused, and a Host that is not a loopback name is refused
  everywhere. The page is plain JavaScript with no build step and loads nothing from
  another origin, under a CSP that allows nothing inline; it writes what it is given as
  text, never as markup, and a test sweeps its scripts for every way markup gets in.
  Ctrl+C and a terminate signal both stop it cleanly.
- Settings: the AI provider (Anthropic, OpenAI, or an OpenAI-compatible server and its
  address) and its key. A key goes to the system keychain (macOS Keychain, Windows
  Credential Manager, or the Secret Service on Linux), and where there is none, to
  `keys.enc`, a file encrypted under a passphrase the user chooses: scrypt makes the key
  and AES-256-GCM encrypts under it, so the file cannot be read or changed unnoticed
  without the passphrase. Bailey asks for it each time it starts, or reads
  `BAILEY_PASSPHRASE`; `BAILEY_KEYS=file` uses the file even beside a keychain, and a
  forgotten passphrase means starting over. A key is never written to the local store,
  a log or a response; the page sees whether one is saved and its last four characters.
- The local store: `bailey.db`, SQLite in the platform's folder for application data
  (`BAILEY_HOME` moves it), with a schema that only moves forward. A store written by a
  newer Bailey is refused rather than read.
- `bailey doctor` checks the data folder (required, and asked about without creating it)
  and the keychain (optional: without one, keys go to the encrypted file).
- A test runs the app under Python's audit hooks and fails on any connection it makes to
  anything but this computer, with a canary that proves the hooks see one.
- The package now installs its dependencies: Starlette and uvicorn for the server,
  keyring for the keychain, and cryptography for the key file. `bailey doctor` and
  `bailey --version` load none of the server.

- `docs/spikes/0001-agent-core-and-local-index.md`: the measurements behind the agent core
  and the knowledge index planned for 0.1, what they recommend, and the owner's ruling on
  both (2026-10-16). Nothing in the package changes.
- The solution format, published: `schemas/0.1/assistant.schema.json` is the schema for
  `assistant.json`, the document that says how a solution's parts fit together, with its
  `$id` at `https://sigrix.io/schemas/bailey/0.1/assistant.schema.json`.
  `schemas/README.md` gives the rules a schema cannot express and what a reader does with
  a version it does not know. `schemas/0.1/assistant.cases.json` is the table of valid and
  invalid documents that the tests run, and that a repository vendoring the schema runs
  too. The tests need `jsonschema`, a development dependency; the package still installs
  nothing beyond the standard library.
- The package is typed: it ships `py.typed`, so a type checker reads its
  annotations, and CI checks them with `mypy --strict`. To pass, the doctor's
  Python check defaults to `sys.version_info[:3]`, the three numbers it reads;
  what it reports does not change.
- Each release is installed back from PyPI by name after it is published, and
  the run fails if that version never arrives, reports another version, lacks
  `py.typed`, or its `bailey` command does not answer with it. The build job
  runs the same checks against the wheel before the upload.
- `CODE_OF_CONDUCT.md`, issue forms and a pull request template, the set the
  other open repositories carry. Blank issues are off: a report is a defect or
  a change, and the links beside the forms send a security report to
  security@sigrix.io and a conduct concern to conduct@sigrix.io. Nothing in
  the package changes.

### Changed

- The README opens with badges for the PyPI release, the Python versions, CI
  and the licence, and a new *Where it fits* section names the projects a
  solution's apps meet: Postern, which they speak, and Gatehouse, which the
  app planned for 0.1 draws them with. The README is the PyPI page too, so the
  next release carries it there. Nothing `bailey doctor` reports changes.
- The README carries the project's logo above its title, and readers in
  GitHub's dark mode are shown a version drawn for it. The README is the PyPI
  page too, so the next release carries the logo there. Nothing in the package
  changes.
- Dependabot opens one pull request per ecosystem instead of one per
  dependency. The branch ruleset only merges a pull request that is up to date
  with `main`, so each separate update merged put every other one behind.
  Nothing in the package changes.
- Every GitHub Action the workflows run is pinned to a commit, with its release
  named beside it, and Dependabot keeps those pins and the dev tools current.
  CI ends in one `ci-passed` job for the branch ruleset to require. Nothing in
  the package changes.

### Fixed

- The Assistants view could lose an assistant just imported: a list asked for before the
  import, answering after it, drew the list without it. A list answer older than the
  latest change is now dropped.

## [0.0.1] — 2026-09-29

First release: the name, and a way to check a machine before the assistant
arrives.

### Added

- `bailey doctor`: checks Python 3.11 or newer, SQLite with full-text search
  (FTS5) and a loopback address, which the assistant requires; and SQLite
  extension loading, `uv` and Node.js, which some solutions need. Each check
  says what it found, what it is for and how to fix it. Exits 1 when a
  required check fails; `--json` prints the answers for a script.
- `bailey --version`.
