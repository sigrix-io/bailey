# Spike 0001: the agent core and the local index

**Status:** measured on 2026-10-14; waiting for the ruling on decision 1, the agent core, and
decision 6, knowledge kept on the user's machine.
Refs [sigrix-io/sigrix#6558](https://github.com/sigrix-io/sigrix/issues/6558).
The code that produced every number here is under `spike/s1/` at commit
[`d6537f0`](https://github.com/sigrix-io/bailey/tree/d6537f078c4647c44d98fb41b9b94e8fe91ff4a7/spike/s1), with the JSON each
script printed in `spike/s1/results/`.

## The short version

- **Agent core: adopt PydanticAI.** Every check passed against all three kinds of provider:
  a stdio MCP server that sees only its named variables, an answer streamed into a browser page,
  and a write tool that waits for the page's answer, "no" included, even across a restart. Each
  check was also seen failing when the code was broken on purpose. The fallback, a thin loop over
  the `anthropic` and `openai` SDKs, is not needed. One gap remains: these runs used emulated
  providers, and `check.py --live` has not yet been run against the real ones.
- **Knowledge: MarkItDown, fastembed and SQLite, without sqlite-vec.** Keyword and vector
  search merged ranked the right passage first for 9 of 10 questions, where keywords alone
  managed 7 and vectors alone 8. The test set is about 250 pages of invented shop documents,
  which index in about a minute on 4 CPUs. Vectors stored in SQLite and searched with numpy returned exactly what sqlite-vec
  returned, 10 to 30 times faster, with no extension to load. LanceDB found no more and installs
  411 MB.
- **Where SQLite cannot load extensions:** nothing to degrade. With the vectors searched in
  numpy, the pipeline loads no extension at all, so every Python that passes `bailey doctor`
  (SQLite with FTS5) gets the same hybrid search.

## What ran, and where

A Linux container with 4 CPUs and Python 3.13. Two things it could not reach decided how the
spike was run:

- **No model provider keys.** Anthropic's Messages API, OpenAI's Responses API and Chat
  Completions (what Ollama, LM Studio, llama.cpp and vLLM speak) are emulated on their own wire
  protocols (`agent/providers.py`). PydanticAI talks to them through the official `anthropic` and
  `openai` SDKs, the same code that talks to the real services. The emulator refuses what the real
  services refuse: a tool call left without its result, a result for a call never made, a tool the
  request did not offer. This proves the plumbing, not how well any model uses tools.
  `check.py --live` runs the same checks against the real providers.
- **No access to Hugging Face**, where fastembed downloads its models. The model measured,
  all-MiniLM-L6-v2 in ONNX, was fetched from a public mirror (Chroma's copy on S3) and loaded into
  fastembed from disk. fastembed's own first-run download was not timed; its size is the same
  order (fastembed lists the model at 0.09 GB).

The documents are invented. The pilot solution's real documents do not exist yet, so
`index/corpus.py` writes a made-up outdoor shop's: a refund policy (Word), a shipping page (HTML),
a warranty (PDF, 7 pages), an FAQ (Markdown), a product catalogue (CSV of 290 rows, and a PDF of
97 pages) and 290 product pages (HTML). That makes 296 files and 126,138 words, about
250 pages of 500 words, split into 2,496 passages. Each fact a question asks about is written
once among close neighbours: eight Ember headlamps, but one Ember 400 runtime; twelve Kestrel
jackets and six Kestrel trousers, but one Kestrel 2.5L jacket rating.

## Decision 1: the agent core

`agent/check.py` runs six checks on each provider (Anthropic over the Messages API, OpenAI over
the Responses API, and an OpenAI-compatible server over Chat Completions), and
`agent/browser_check.py` drives the page in Chromium.

| Check | Anthropic | OpenAI | OpenAI-compatible |
| --- | --- | --- | --- |
| The tool server sees only its named variable and the MCP SDK's default list; a decoy secret in the app's environment does not reach it | pass | pass | pass |
| The answer streams: 18 text events, the first after about 1 s of a run under 2 s | pass | pass | pass |
| A write tool pauses a streaming run; "allow" runs it once | pass | pass | pass |
| "No": the tool does not run, and the model receives the denial as the tool's result | pass | pass | pass |
| The paused run is written to JSON (about 3.4 KB), read back by a new agent with a new tool server, and resumed with "allow" and with "no" | pass | pass | pass |
| In Chromium: the answer grows on the page in 21 steps, and markup in it stays text | pass | pass | pass |
| In Chromium: "No", "Allow once", and "Always for this assistant" (the next call does not ask) | pass | pass | pass |

Around those:

- **The server's environment.** It saw `HOME`, `LC_CTYPE` (added by Python's locale coercion),
  `PATH`, `SHELL`, `TERM` and the one variable its config names. That is the MCP SDK's default
  list plus the named variables, and it holds only for FastMCP's base `StdioTransport`.
- **The denial, as each provider received it.** Anthropic got a `tool_result` block for the call,
  OpenAI a `function_call_output`, and the local server a `tool` message, each carrying the
  denial's text. No request was refused by the emulator.
- **Approval waits inside the run.** PydanticAI's `HandleDeferredToolCalls` lets the run wait for
  the page's answer and then carry on streaming. A run can also end at the approval with
  `DeferredToolRequests` as its output, and resume from its saved messages. That second path is
  what keeps a pending approval across a restart.
- **The page.** It sends a per-session token on every request; a request without it, or with a
  `Host` that is not a loopback name, is refused (401). Its CSP allows no inline script, and the
  console logged no errors.
- **Each check was seen failing** (`agent/mutations.py`). Unwrapping the write tool turned the four
  approval checks red. So did a model that never read the tool's result. Handing the server the
  app's whole environment turned the environment check red, and an answer arriving in one piece
  the streaming check. The emulator also refused an unanswered tool call on all three protocols
  (400).
- **Nothing called out.** Python's audit hooks recorded every connection the process made across
  all 18 runs: loopback only. No telemetry, no price-list fetch, no update check.
- **What it costs.** PydanticAI with its Anthropic, OpenAI and MCP extras, plus Starlette and
  uvicorn, installs 134 MB; the thin loop's SDKs alone, 61 MB. Starting the tool server and
  listing its tools takes about 1.1 s, and the first text of a run waits for it, so Bailey
  should keep a solution's servers running between messages rather than start them per run.

Found on the way, each for the part of 0.1 it names:

1. **FastMCP's `UvxStdioTransport` and `NpxStdioTransport` hand the child the app's whole
   environment, API keys included,** as soon as one variable is named (`UvStdioTransport` does
   the same). Only the base `StdioTransport`, which `load_mcp_toolsets` also uses, passes the
   default list plus the named variables. Bailey builds `StdioTransport` itself, with `uvx` or
   `npx` as the command (the tool host).
2. **The default list carries no proxy or CA settings** (`HTTPS_PROXY`, `SSL_CERT_FILE`), so a
   `uvx` or `npx` tool that has to download itself through a corporate proxy will not find the
   proxy. That follows from the list rather than from a run here. Which non-secret variables every
   tool receives is a decision for the tool host.
3. **`load_mcp_toolsets` fills `${VAR}` from the app's own environment.** Bailey fills a tool's
   variables from the keychain instead, so it should not use it for keys.
4. **PydanticAI prints a first-run banner on stderr** advertising its hosted observability;
   `pydantic_ai.BANNER_ENABLED = False` turns it off.
5. **The SDKs read `ANTHROPIC_BASE_URL` and `OPENAI_BASE_URL`** when no base URL is passed, so
   Bailey always passes one. A user's shell may set those for some other tool.
6. **`openai:` means the Responses API in PydanticAI 2.x.** A local server needs
   `OpenAIChatModel`, which speaks Chat Completions.
7. **The API moves.** In 2.56 a tool result event carries `.part` and a run's `usage` is a
   property. Both cost a run here, and the version should be pinned exactly.

## Decision 6: knowledge on the machine

`index/pipeline.py` converts, splits, embeds and indexes the documents, then searches four ways for
each of the ten questions in `index/questions.json`. Keywords use FTS5 and bm25. Vectors are
searched through sqlite-vec, and again through numpy with no extension loaded. Hybrid merges
keywords and vectors by reciprocal rank fusion. A question counts as answered at rank *n* when
the *n*th passage contains its answer, read as a citation would show it.

| Step | Measured |
| --- | --- |
| Convert 296 files with MarkItDown | 5.1 s: 291 HTML pages in 2.5 s, two PDFs of 104 pages in 2.0 s, the Word file in 0.2 s, the CSV and the Markdown in hundredths of a second |
| Split into passages of at most 180 words, 30 of them overlapping | 2,496 passages in 0.05 s |
| The embedding model | 83 MB to download (a 90 MB ONNX file); 0.5 to 1.3 s to load |
| Embed every passage | 49 s, about 50 passages a second on 4 CPUs: the step that takes the time |
| Write the SQLite index: passages, FTS5, vectors | 0.1 s, a 10.6 MB file |
| Answer a question | about 10 ms to embed it, then 1.1 ms for keywords, 0.3 ms for vectors in numpy (3.2 ms through sqlite-vec), 1.4 ms for the hybrid |

A few hundred pages index in about a minute, and nearly all of that minute is embedding.

**Retrieval on the ten questions:** at rank 1, in the top 5, and the mean reciprocal rank to 10.

| | Keywords | Vectors (sqlite-vec and numpy alike) | Hybrid |
| --- | --- | --- | --- |
| Each passage starts with its title and heading | 7, 9, 0.75 | 8, 9, 0.82 | **9, 9, 0.90** |
| The passage's text alone | 7, 8, 0.73 | 7, 8, 0.75 | 8, 8, 0.80 |
| LanceDB, with the same passages and vectors | 5, 8, 0.62 | 8, 9, 0.82 | 8, 8, 0.81 |

What that says:

- **Hybrid beats either alone.** Keywords find exact names and numbers (the Ember 400, $6.95);
  vectors find meaning ("import tax" against "duties and taxes"); fusion keeps both.
- **Starting each passage with its document's title and heading** puts one more question at
  rank 1, at no cost at query time. A specifications table that never names its product is
  otherwise anonymous.
- **The one miss is the customer's own wording.** "I wore my new rain jacket on one walk and I
  don't love the colour. Can I still send it back?" matches product pages on "rain jacket" and
  "colour". The search a model would write, "return policy for clothing that has been worn", puts
  the policy first under all three methods. So search stays a tool the model calls in its own
  words, as planned, and never takes a customer's message as the query.
- **"Not covered" is visible.** The best match for the ten answerable questions scored 0.55 to
  0.84 in cosine similarity, and for the two the documents do not answer (electric bikes, Bitcoin)
  0.25 to 0.33. With this model, a floor near 0.45 lets the search tool say it found nothing. The
  floor is set per model when the knowledge store is built.
- **PDFs keep their pages but lose their headings.** MarkItDown marks each PDF page with a form
  feed, so a citation can name the page; the headings do not survive, so a PDF is split by page.

**sqlite-vec, numpy or LanceDB.** sqlite-vec and numpy returned the same top 10 for every
question, because both search exactly. At the sizes a business's documents reach, numpy was the
faster of the two. Measured on random 384-dimension vectors:

| Passages | sqlite-vec, a query | numpy, a query | Reading the vectors into numpy, once | numpy's memory |
| --- | --- | --- | --- | --- |
| 10,000 | 8.9 ms | 0.3 ms | 0.13 s | 15 MB |
| 50,000 | 39 ms | 3.0 ms | 1.3 s | 77 MB |
| 100,000 | 86 ms | 5.2 ms | 2.3 s | 154 MB |

LanceDB found no more than the hybrid above, and its keyword search, with its default tokenizer,
was the weakest of the three. It took 14 to 17 ms a query and installs 411 MB, of which pyarrow
is 162 MB. MarkItDown, fastembed and sqlite-vec together install 228 MB, of which onnxruntime and
numpy are 118 MB.

## Recommendation

**Decision 1, the agent core: adopt PydanticAI**, pinned exactly (2.56.0 here), with
`fastmcp-slim` and `mcp`. The write tools a solution declares are wrapped with
`approval_required`. A run waits for the answer inside itself (`HandleDeferredToolCalls`), and a
pending approval is saved as the run's messages so it survives a restart. Tool servers are built
as `StdioTransport` with only their named variables, and kept running between messages. The banner
is off. Before 0.1 ships, run `agent/check.py --live` once with real keys and one local server:
the emulator proves the plumbing, not a model.

**Decision 6, knowledge: MarkItDown, fastembed and SQLite, merged by reciprocal rank fusion.**
The vectors are stored as BLOBs in the passages table and searched in numpy, beside FTS5. Each
passage starts with its title and heading. Leave sqlite-vec and LanceDB out of 0.1. That removes
the pre-1.0 dependency and the extension-loading question, and `bailey doctor`'s extension check
with them; FTS5 stays required. Look again at a store with an approximate index only if a
collection passes about 100,000 passages, more than a small business's documents.

**Still to measure or decide**

- **The live runs:** Anthropic and OpenAI keys (a few cents of usage), and Ollama or LM Studio on
  a laptop for the local endpoint.
- **The embedding model.** all-MiniLM-L6-v2 was measured because it could be fetched. fastembed's
  default, BAAI/bge-small-en-v1.5 (67 MB quantised), is the one to compare where Hugging Face is
  reachable. Both read English only. If the first users' customers write in other languages, a
  multilingual model is needed, such as paraphrase-multilingual-MiniLM-L12-v2 (220 MB): that turns
  on who Bailey's first users are.
- **Passage size.** MiniLM reads at most 256 tokens, so passages here stop at 180 words rather than
  "a few hundred". A model with a longer window allows longer passages.
- **The pilot's real documents:** rerun `index/pipeline.py` on them once the first solution has
  them.

## Reproducing it

`spike/s1/README.md` lists the environments and the commands. Every script prints the JSON report
that is saved in `spike/s1/results/`.
