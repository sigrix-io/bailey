# Spike S1: the agent core and the local index

Throwaway code for [sigrix-io/sigrix#6558](https://github.com/sigrix-io/sigrix/issues/6558). It
answers two questions with measurements: can PydanticAI be Bailey's agent core, and can a user's
documents be searched on their own machine with MarkItDown, fastembed and SQLite? The findings and
the recommendation are in [`docs/spikes/0001-agent-core-and-local-index.md`](../../docs/spikes/0001-agent-core-and-local-index.md).
This directory is how to reproduce them. None of it is Bailey's code, and none of it ships.

## What is here

| Path | What it does |
| --- | --- |
| `agent/tools_server.py` | A stdio MCP server: two read tools, one write tool, and a tool that lists the environment variable names it can see |
| `agent/providers.py` | Anthropic, OpenAI and an OpenAI-compatible server, emulated on their own wire protocols, refusing what the real ones refuse |
| `agent/core.py` | The agent under test: PydanticAI, the MCP server over stdio with only its named variables, approval before a write |
| `agent/check.py` | Six checks on each of the three providers, as one JSON report; `--live` runs them against the real providers |
| `agent/web.py`, `page.*` | The browser half: an answer streamed to a page, and approval answered from it |
| `agent/browser_check.py` | Drives that page in Chromium |
| `agent/mutations.py` | Breaks the core four ways and confirms the checks go red each time |
| `index/corpus.py` | Writes an invented shop's documents: policies, a catalogue and 290 product pages, about 250 pages in all |
| `index/questions.json` | Ten questions with known answers, and two the documents do not answer |
| `index/pipeline.py` | Convert, split, embed, index and search; keyword, vector and hybrid retrieval scored on the ten questions |
| `index/lance_check.py` | The same passages, vectors and questions through LanceDB |
| `results/` | What each script printed for the write-up, and a screenshot of the approval prompt |

## Running it

Each part has its own environment, so each install size is its own.

```sh
python -m venv .venv-agent && .venv-agent/bin/pip install -r requirements-agent.txt
cd agent
../.venv-agent/bin/python check.py           # emulated providers: no keys, no network
../.venv-agent/bin/python check.py --live    # ANTHROPIC_API_KEY, OPENAI_API_KEY, LOCAL_BASE_URL and LOCAL_MODEL
../.venv-agent/bin/python browser_check.py   # SPIKE_CHROMIUM=/path/to/chrome, or `playwright install chromium` first
../.venv-agent/bin/python mutations.py       # each check seen failing
../.venv-agent/bin/python web.py             # the page, to try by hand
```

```sh
python -m venv .venv-index && .venv-index/bin/pip install -r requirements-index.txt
.venv-index/bin/python index/corpus.py corpus
.venv-index/bin/python index/pipeline.py corpus work   # fastembed downloads the model on first use
python -m venv .venv-lance && .venv-lance/bin/pip install -r requirements-lance.txt
.venv-lance/bin/python index/lance_check.py work
```

`pipeline.py --model-dir DIR` loads a model already on disk instead of downloading it. The measured
run used it, because Hugging Face could not be reached from where it ran; the write-up says what
that changes.
