# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the versioning is
described in `VERSIONING.md`.

## [Unreleased]

## [0.1.3] — 2026-11-07

A fix release. Ctrl+C or a terminate signal that came at the wrong moment could be lost, and
leave Bailey running; one that came the moment it said it was running ended it without
closing its store. Every stop once it says it is running now shuts it down cleanly.

### Added

- `scripts/stop_soak.py` starts Bailey and stops it at once, a few hundred times, and fails on a
  stop that does not end cleanly within 30 seconds. A third of the stops come at the ready line,
  a third in the 50 milliseconds after it, and a third in the three seconds after it, while the
  agent core can still be loading. `stop-soak.yml` runs it by hand on a macOS or a Linux runner,
  and on a pull request that changes it.

### Changed

- A test whose Bailey does not stop in time, or never prints its address, now fails with the
  stack of every thread in it, which `faulthandler` writes when the test sends it SIGABRT, and
  ends it, so no Bailey is left running after the test. The soak also prints each thread's
  native stack, with `sample`, where the computer has it.

### Fixed

- A stop no longer leaves Bailey running. Its own handler for the terminate signal, and Python's
  for Ctrl+C until uvicorn's took over, raised an exception wherever the main thread was. One
  that landed inside a weakref callback was reported and dropped there, so the stop was lost.
  When that callback was importlib's own, its lock stayed held, and the background load of the
  agent core waited on it for ever while Bailey went on serving. One handler for both signals
  now asks the server to shut down, as uvicorn's own does. It is in place before Bailey says it
  is running, so a stop that comes the moment it does closes the store too, rather than the
  signal ending the process where it stands.

## [0.1.2] — 2026-11-07

A release that changes nothing in the app. The package now says which computers `uvx`, `pipx`
and `pip` install it on, in its README and its classifiers, and sends every other computer to
the Docker image, and every release checks those computers before it uploads anything.

### Added

- The four lessons are on YouTube, and each page names its video, so the next pack and import
  link the lesson's page on sigrix.io to it.
- `scripts/check_wheels.py` checks what `uvx`, `pipx` and `pip` find on each computer the
  README names. It resolves each one as an installer does, newest versions and source downloads
  allowed, then with wheels only, held to the same versions. It fails when a computer the README
  covers would install nothing or build something from source, and when one it sends to Docker
  starts to install. CI runs it, the release runs it before uploading anything, and the release's
  post-publish check now runs on a Mac as well as on Linux.

### Changed

- The package says which computers it installs on. Its classifiers name macOS, Windows and Linux
  rather than "OS Independent". The README lists the computers `uvx`, `pipx` and `pip` cover:
  Linux with glibc 2.28 or newer on an Intel, AMD or ARM chip, Windows on an Intel or AMD chip,
  and a Mac with an Apple chip on macOS 14 or newer. Every other computer uses the Docker image.
  Nothing changes for anyone running 0.1.0 or 0.1.1. Those releases pin onnxruntime as this one
  does, and it has no build for an Intel Mac or a Mac on macOS 13 or older, so neither release
  ever ran there. On such a Mac an installer installed 0.0.1, the doctor-only first release,
  without an error. Once 0.0.1 is yanked, the installer refuses instead, naming onnxruntime.

- Nothing here names the Sigrix platform's private code any more. The docstrings, comments and
  docs that cited its issues, linked into its repository or named its modules say what they meant
  instead, and the recipe for the download fixture is kept with the platform's own code.
  `tests/test_names_nothing_private.py` keeps it so.
- The lessons are recorded with [Pageant](https://github.com/sigrix-io/pageant), the recorder that
  started in `lessons/` and is a package of its own now: `pageant record lessons/first_solution.py`
  records a lesson, and `pageant pack` and `pageant youtube-text` on `lessons/pages.py` publish
  them. `lessons/` keeps the four lessons, their pages, the scripted model (Pageant's start hook
  for Bailey), the stand-in tool server and the solutions. Recorded again with it, every lesson's
  page and cover are byte for byte the published ones, and its captions and chapters say the same
  things in the same order, within the second or so a recording moves from one run to the next.
- The lessons' tests hold every page, and the learning path, to the lesson format's JSON Schemas
  as Pageant ships them (`pageant.schema`), rather than to a copy of the import's rules kept in
  `tests/test_lesson_pages.py`, which could drift from them without failing. The schemas are
  stricter than the copy: a page has at most eight tags, each lowercase words joined by hyphens,
  at most 32 characters and listed once; its body is at most 16,384 characters; its YouTube link
  is a watch address or a `youtu.be` one; and the path, which the copy did not cover, lists one to
  twelve lessons, under a title of at most 120 characters, an audience of at most 60 and a
  description of at most 280. The four pages and their path already fit. `lessons/requirements.txt`
  pins Pageant at a commit that ships the schemas, where `pageant record` refuses a page the import
  would refuse, and `pageant pack` a page or the path, each with every reason.

## [0.1.1] — 2026-11-05

A fix release. Bailey sends no telemetry again, onnxruntime's included. An app opens from a
relative `BAILEY_HOME`, asks for a locked key file's passphrase rather than failing, and runs on
a server that needs no key before any passphrase is chosen.

### Added

- `lessons/` records video lessons from the app itself: Bailey on a scripted model, driven in
  Chromium with a cursor, highlights and captions, narrated by a text-to-speech model that runs
  on the recording machine over music generated there, and written out as a 1080p MP4 with its
  poster, captions and chapters. The first is lesson 1, your first solution. It is a
  maintainer's tool, in neither the wheel nor the sdist (`lessons/README.md`).
- Lesson 2, tools that ask before they act: a solution's tool server and the key it needs, a
  tool that reads and runs, a write that asks first, allowing once and always, and taking an
  "always" back in Settings. Its solution is `lessons/solutions/harbour-mail-desk`, with a
  stand-in server, and a lesson's scripted model now makes tool calls.
- Lesson 3, apps that run a crew: a solution's app on the install screen, installing its
  libraries once, and a run of its crew from the form it draws. Its solution is
  `lessons/solutions/harbour-weekly-digest`; the recording makes the app's environment a
  stand-in, as the tests do, rather than downloading 250 MB.
- Lesson 4, check a solution: `bailey doctor`, and `bailey check` on lesson 1's solution, whose
  seven cases pass, and on a draft that lost its quote-sources skill, whose examples fail with
  their reasons. The recorder films a terminal for it: each command typed, then really run.
- A recorded lesson is a bundle Sigrix's Learn console imports in one step: each lesson records
  into a folder of its own, its media under fixed names beside its page on sigrix.io
  (`lesson.json`, from `lessons/pages.py`), and `python lessons/pack.py` zips the recorded lessons
  with their learning path. Nothing of a lesson's page is typed by hand any more.
- The README links the four video lessons on Sigrix Learn. `python lessons/youtube.py` writes each
  recorded lesson's YouTube title, description and tags from its page, with its chapters and a
  link back to its page, held to what YouTube refuses. A lesson's page can name its YouTube video
  (`youtube_url`), and each page now says what in its lesson is scripted or a stand-in.

### Changed

- The tool contract tests, which every PydanticAI bump must pass, now store the call a tool
  server's error sends back for another try, reload it, and send the next message on it, on each
  kind of provider. PydanticAI 2.63 passed them while Bailey stored such a call as a successful
  result. Nothing in the package changes.

### Fixed

- Bailey sends no telemetry again, onnxruntime's included. onnxruntime 1.31, which document search
  and MarkItDown's file detection run on, tries to reach Microsoft's telemetry collector from
  native code once it has been loaded for about ten seconds, so Bailey 0.1.0 did, once it had
  converted or searched a document. Bailey now sets `ORT_DISABLE_TELEMETRY=1` for itself and for
  each app's runner before onnxruntime loads. The no-telemetry test, whose audit hook cannot see
  native code, also gives the app a proxy that records every request, and checks the switch.
- A retry is stored as a retry again. PydanticAI 2.63 split its retry part in two: a call the
  model must make again comes back as that call's result, marked `retried`, which Bailey stored
  as a successful result, and a retry that answers no call, as after a reply with nothing in it,
  comes back as a part of its own, which Bailey dropped. Both are stored as `retry` parts, in the
  shape they had before, and go back to the model as those two parts, never as the
  `RetryPromptPart` that 2.63 deprecates and v3 removes. A conversation stored before the split
  still loads.
- An app opens when `BAILEY_HOME` names a relative folder, as `BAILEY_HOME=./some-folder bailey`
  does. Bailey kept the folder as written, so the app's Python and its bundle were named relative
  to where Bailey started, and its runner, which starts in the app's own folder, looked for both
  from there: "The app's runner could not start: No such file or directory." The folder is now
  named in full where `BAILEY_HOME` is read, so every path made from it is too. Settings names
  the key file in full, and `bailey doctor` the data folder.
- A provider that needs no key, such as a local server, chats and runs apps with no passphrase
  chosen. Where keys go to the encrypted file, as in the `sigrix/bailey` image, Bailey asked for
  the passphrase first, though nothing was kept under one: with no file there is no key, so
  nothing to unlock. A key file that exists still asks for its passphrase.
- While an answer streams, a tool call the model is to make again, as after an error from its
  tool server, says it was given another try. PydanticAI 2.63 reports such a call with a word of
  its own, `retried`, which the page did not know, so the call read as still running.
- Opening an app while the encrypted key file is locked, or before its passphrase is chosen, says
  "Unlock your keys with their passphrase first.", as chat does. The route that forwards an app's
  requests to its runner let the error through as a bare 500, which the page could only call "not
  JSON", whatever the provider; a keychain that refuses is said so there too.
- A view opened from the menu while the page is still starting stays open. The menu shows before
  the settings have loaded, and the page then opened Chat whatever had been chosen meanwhile;
  clicking Assistants as the menu appeared landed on Chat every time.
- A conversation in which the model once replied with only its thinking stops telling it, on every
  later message, that its previous response failed. Bailey does not store such a reply, but it kept
  PydanticAI's request for another try and sent that on beside the person's message, before the
  model had said anything. The request is now left out of what the model is sent, in conversations
  stored before this too.

## [0.1.0] — 2026-11-02

The app, without the store: a solution installed from a file does its job on this computer,
on the person's own AI provider and key. `uvx bailey`, or `docker run sigrix/bailey`.

### Added

- The image `sigrix/bailey` (story S12 of 0.1):
  `docker run -p 127.0.0.1:8123:8123 -v bailey:/data sigrix/bailey`. It carries:
  - Node.js 24 with npm and npx, and uv and uvx, which a solution's tool servers start with;
  - the search model, so nothing is fetched on its first run.

  tini is PID 1, Bailey runs as a user of its own, its data is in the `/data` volume, and its
  keys are in the encrypted file. `image.yml` builds it on amd64 and arm64 from the wheel PyPI
  serves for a release, once the release succeeds. Every image is opened as a person opens it
  before it is pushed, and again once pulled by name.
- In a container (`BAILEY_CONTAINER=1`, which the image sets), Bailey listens on the
  container's own address, which a published port reaches, and opens no browser. A request
  still needs a loopback Host and the session's token.
- `scripts/check_launch.py` opens the app as a person does and checks the page, the token,
  both refusals and a clean stop; in the image, also its tools, the search model with the
  network off, and `bailey doctor`. CI runs it against the image on every pull request, on
  both architectures, and the release against the PyPI install.
- `bailey check <solution>` (story S11 of 0.1) runs a solution headless, with no browser. It runs
  the solution's examples, plus a grounding pair for each knowledge slot that has a sample, in a
  Bailey of its own that keeps nothing. It drives the app's own routes in-process, so the
  solution goes through the same install checks, agent core, skills, documents and tool host as
  it does in the app. Each case passes or fails, and a failing case says why:
  - an example's answer is held to what the example's own reply shows: passages quoted word for
    word, citations, or saying the documents do not cover the question;
  - a question that a slot's sample answers must cite or quote that sample;
  - a question about a made-up name must be said not to be covered.

  A tool that changes something is refused, never run, and the case says so. The check runs on
  the provider, model and key in Bailey's Settings, which it reads without opening the store as
  the app does, so nothing is migrated or written. `--provider`, `--model` and `--address` set
  them instead, with the key in `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`. A key in
  `OPENAI_API_KEY` never goes to an OpenAI-compatible server. A tool server's variables come from
  the environment by name.

  It prints each case as it is judged, then the tokens the run took and an estimate of what they
  cost. `--json` prints the whole report, with the provider and model. It exits 0 when every case
  passes, 1 when one fails, and 2 when it cannot run, a provider that refuses the key or cannot
  be reached included.
- A solution's `examples` are read, kept with the assistant and written back on export. Bailey
  refuses an example as the schema does: one with no input, or an output that is not text.
- Installing a solution from a file (story S10 of 0.1). The Assistants view takes a ZIP
  or a folder, which is what Safari makes of a download by default, and a folder meets a
  ZIP's limits and path rules. Before anything is written, Bailey checks the package and
  shows its install screen:
  - what the solution does;
  - each key, with what reads it; an app's key also has its purpose and where to get one,
    from its `postern.json`;
  - each knowledge slot;
  - the tools, with the write tools marked;
  - the apps;
  - what it leaves out.

  A server's keys can be entered right there, and go to the keychain. Install writes what
  was checked and shown, which Bailey holds for half an hour; Cancel writes nothing.
- A download from Sigrix installs. Today's is already a solution. One from before
  downloads carried `assistant.json` is converted on import: its `SKILL.md` becomes the
  instructions and its `.mcp.json` the servers, and every tool asks before it runs.
- No credential value anywhere in a package (Postern §4.1.3 and §7). A package is refused
  when:
  - an MCP configuration gives a variable a value, or a remote server's credential header
    a literal;
  - an app's `.env` gives a key a value;
  - an app's credential entry carries more than `env`, `purpose` and `signup_url`;
  - any text file carries one of the provider key shapes Sigrix's own scan knows.

  The refusal names the file and the kind of key, and quotes none of it. A variable left
  blank, a reference to one, and the placeholder a Sigrix download writes for its shared
  sign-in client are names, not values.
- A skill carrying scripts installs only from the Sigrix store, unless the person turns on
  "Install skills that carry scripts from a file" in Settings (design decision 3). With it
  on, the skill installs without its scripts, which Bailey 0.1 never runs.
- Everything installed from a file is marked unverified, on the install screen and in the
  assistant list, and stays so through edits. Templates are Bailey's own and are not
  marked.
- A ZIP carrying two files under one name is refused, since different unzippers read
  different copies.
- Apps (story S9 of 0.1). A solution's app is a crew in `apps/<name>/`, a Postern bundle:
  `postern.json`, `ui.json` when the seller composed one, `config/agents.yaml` and
  `config/tasks.yaml`, and `requirements.txt`. Format 0.1 has no member for apps and
  refuses one it does not know, so Bailey finds an app by its folder, as it finds a
  sample. An import keeps each app's files in the data folder, never a `.env` or what a
  runner wrote: a `.env` giving a key a value is refused (story S10), and one holding a
  listing's id or settings is left out. The import says that an app is its seller's
  program running on this computer. An export writes the apps back, and deleting the
  assistant removes them.
- CrewAI is installed per app, never with Bailey. `sigrix-runtime[crewai]` resolves to
  148 packages and pins `openai` 2.x and `mcp` 1.x against Bailey's own 3.31.0 and 2.3.0.
  Each app runs in a virtual environment of its own in the data folder, built from its
  `requirements.txt` and `sigrix-runtime==0.1.0`, with `uv` when Bailey finds it and with
  venv and pip otherwise. Apps with the same requirements share one. Building it
  downloads about 250 MB from PyPI and takes about 900 MB on disk, which the Apps panel
  says, and runs only when the person presses the button.
- The app host starts an app's runner when its page opens: the runtime's own `main`, as
  `python -m sigrix_runtime.postern --bundle <folder> --host 127.0.0.1 --port 0 --no-pull`
  runs it, less the standard library's reverse lookup of the address it binds, a name
  nothing reads that held every start back by more than 30 seconds on GitHub's macOS
  runners. `PYTHONSAFEPATH` makes the pinned runtime serve rather than the copy a Sigrix
  bundle carries, and the port is read from the runner's `POSTERN_PORT=` line. The runner stops
  with its app's assistant and with Bailey. Its environment is built as the tool host
  builds a server's: the system's few defaults, the proxy and certificate settings, the
  person's provider key and model, CrewAI's telemetry switched off, and a token of
  Bailey's that it demands of every request. It never receives `SIGRIX_TOKEN` or
  `POSTERN_AGENT_ID`, so it checks no licence and looks for no update.
- Gatehouse draws an app: `@sigrix-io/gatehouse` 0.4.0, vendored from npm by
  `scripts/vendor_gatehouse.py` as the Sigrix platform vendors it. The script takes an
  exact version, checks the registry's sha512, and writes each file's sha256 into
  `RELEASE.json`, which a test holds every vendored file to. The page's `PosternClient`
  points at Bailey, which forwards the four verbs to the runner, streams `stream`
  through, and cancels the run when the page closes, so the page's CSP stays
  `connect-src 'self'`. The form comes from the app's `describe`, laid out by its
  `ui.json`, and each step shows as it finishes. Gatehouse reads Bailey's colours, in
  both themes.
- An app whose `describe` declares a text input keyed `passages` receives the passages
  the assistant's documents hold for its run's `prompt`, sent whole or searched as chat
  decides and numbered as in chat (design §3.7, until runners can call tools). The form
  never shows that input. An app that runs on another provider's key than the person's
  says so in the Apps panel, and its runner refuses the run with `missing_credential`.
- The contract tests for apps run the real runner, `sigrix-runtime` 0.1.0, with a
  stand-in for CrewAI (`tests/stand_ins/crewai`) that sends each task to the provider
  emulator with the key in its environment. The fixture solution
  `tests/fixtures/weekly-digest` carries one crew app. The no-telemetry test now runs an
  app, and records the runner and its crew as well as Bailey. One test builds an app's
  environment from PyPI and runs the real CrewAI (`BAILEY_TEST_REAL_CREWAI=1`), which CI
  does on one job; its first run here found that `uv` makes a venv only in a folder that
  does not exist yet.
- `httpx` is named as a dependency, for the proxy (the provider SDKs already bring it).
  The tests add `sigrix-runtime`, PyYAML and python-dotenv, never Bailey itself.

- Knowledge (story S8 of 0.1). Each assistant keeps a collection of documents on this
  computer. Markdown, text, CSV and JSON go in as they are; PDF, Word and HTML are
  converted by MarkItDown's converter for that kind, called directly rather than through
  its guess at a file's kind, so a damaged PDF is refused instead of being kept as noise.
  A document is split into passages of about 180 words that overlap by 30, each under its
  heading and, in a PDF, its page, and a table keeps its header row in every passage. The
  store's fourth migration keeps the documents, their passages and a full-text index over
  them (FTS5). Nothing of a document leaves the computer but the passages a request
  carries.
- Two ways to the model, as S1's ruling asked (Decision 6), with the limit set by the cost
  of a message (`spike/s8/whole_or_searched.py`). A collection of up to 10,000 tokens goes
  into the instructions whole, every passage numbered, on Anthropic and OpenAI, whose prompt
  caches make that cheaper than searching while the cache is warm: 0.76 and 0.85 of a
  searched message. When the cache has gone cold, after a few idle minutes, it costs 2.2
  and 1.8 times one. A larger collection, or one over 2,000 tokens on a local server, whose
  context window Bailey cannot know, is searched: the model calls
  `search_knowledge` with a query of its own, keywords and vectors each rank the passages,
  reciprocal rank fusion merges the two rankings, and the five best come back numbered. A
  passage that only the vectors found is dropped below a cosine similarity of 0.2.
- Citations. The instructions ask the model to cite each passage it relies on as `[P12]`,
  and to say plainly when the documents do not cover a question. Bailey reads the numbers
  out of the answer and stores each cited passage's document, heading and text on the
  answer's record. The chat shows the sources under the answer, each opening to its
  passage, and they survive a reload. Each search the model makes is shown as it runs. An
  assistant set to answer only from its documents stops there, instead of going on to
  answer from what the model knows. That setting is the person's, kept with the assistant
  in Bailey; the solution format does not carry it, so an export leaves it out.
- The search model is all-MiniLM-L6-v2, run through fastembed and onnxruntime, and it is
  downloaded only when the person asks for it. A collection that needs searching names
  the model, its size (about 90 MB) and Hugging Face, and the Documents panel downloads it
  at a button. Until then it loads with nothing fetched, and a chat that needs it says
  what to download instead of answering without the documents. Hugging Face's telemetry is
  off. `BAILEY_SEARCH_MODEL` names a folder that holds the model, for a computer that
  cannot reach Hugging Face.
- Each assistant's Documents panel lists what its solution asks for, each slot with the
  file types it takes, holding the person's own file or the solution's sample once they
  choose it. It also lists their other documents and says how large the collection is and
  which way it reaches the model.
- The solution format: `assistant.json`'s `knowledge` names the documents a solution asks
  for (a name, a sentence, the file types and whether it is required), and
  `samples/<name>.<type>` holds a text sample for each, which an import keeps and an export
  writes back. Importing a solution that asks for documents opens its Documents panel. A
  skill's `references/` files are indexed for search but never sent whole, because their
  skill already carries them.
- The contract tests for knowledge run on the provider emulator with the fixture solution
  `tests/fixtures/shop-replies`. On all three protocols, sent whole and searched, a question
  the documents cover is answered with a citation of the passage that covers it, and one
  they do not cover is answered by saying so. The tests embed with a stand-in, and one test
  runs the real model, which CI downloads on one job. The no-telemetry test now runs a
  chat over documents.
- `markitdown[pdf,docx]` 0.1.8, `fastembed` 0.9.0, `onnxruntime` 1.31.0 and `numpy` 2.5.4,
  pinned exactly. numpy 2.5 needs Python 3.12, so Python 3.11 takes numpy 2.4.6. They load
  when a document is added or searched, not when the app starts.

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
  each variable's name and refusing a package that gives one a value (story S10), and an
  export writes them back, with an empty value for each variable. The store's third
  migration keeps the "always" grants.
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

- `bailey doctor` no longer checks whether SQLite can load an extension, so `--json`
  prints one entry fewer. The vectors are stored as BLOBs and compared in numpy, so
  nothing in Bailey loads one (S1's ruling, Decision 6). FTS5 stays required.
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

- A first message sent while the agent core was still loading in the background could
  fail with a server error. Reading the conversation imported PydanticAI's messages beside
  the background import, and two threads importing one package through different modules
  can deadlock in Python's import system. Every import of the core now goes through
  `bailey.core.load`, in one order, so a second thread waits for the first.
- A skill's frontmatter value wrapped over indented lines, as an editor wraps a long
  description, now reads whole. A plain value was cut to its first line, and a quoted one
  refused the import. A blank line inside the value is a line break, as YAML reads it.
- The reference files of the skill that holds an assistant's instructions never reached
  the model: no `load_skill` call loads that skill, and its body alone went into the
  instructions. Its `references/` files now go in with it.
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
