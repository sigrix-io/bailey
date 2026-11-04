<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/sigrix-io/bailey/main/.github/assets/logo-on-dark.svg">
    <img src="https://raw.githubusercontent.com/sigrix-io/bailey/main/.github/assets/logo.svg" alt="Bailey" width="120">
  </picture>
</p>

# Bailey

[![PyPI](https://img.shields.io/pypi/v/bailey)](https://pypi.org/project/bailey/)
[![Python](https://img.shields.io/pypi/pyversions/bailey)](https://pypi.org/project/bailey/)
[![CI](https://github.com/sigrix-io/bailey/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/sigrix-io/bailey/actions/workflows/ci.yml)
[![Licence](https://img.shields.io/github/license/sigrix-io/bailey)](https://github.com/sigrix-io/bailey/blob/main/LICENSE)

An open-source AI assistant that runs ready-made solutions: on your own machine, with your own AI key.

> **0.1: the app, without the store.** A solution installs from a file, a ZIP or a folder, and does its job on your computer. Signing in, the store and updates arrive in 0.2.

## What Bailey is for

Getting AI to do one real job reliably is still a project. Someone writes the instructions, connects the tools, supplies the documents and the keys, checks that it works, and keeps it working when something changes.

Bailey runs **solutions** instead: complete, versioned packages for one job, such as answering customer emails, qualifying leads or writing a weekly report. A solution carries its instructions, skills, tools, knowledge and small apps. Bailey installs it in one step and asks only for the keys it needs.

- **Your machine, your key.** It runs locally and opens in your browser, with the AI provider you choose: Anthropic, OpenAI, or any OpenAI-compatible endpoint, local models included.
- **It asks before it acts.** Every tool is either read or write, and a write tool asks before it runs: once, always for this assistant, or no.
- **Your documents stay yours.** Files you add are indexed on your computer, and answers cite the passage they came from.
- **Open formats.** Skills are Agent Skills, tools are MCP servers, apps speak [Postern](https://github.com/sigrix-io/postern), and a solution is an Agent Plugins folder, so its parts also load in other clients.
- **No telemetry.**

Solutions can be free, or bought on [Sigrix](https://sigrix.io), where builders sell what they make for Bailey.

## See it in seven minutes

Four short lessons on [Sigrix Learn](https://sigrix.io/learn/tag/bailey), each with its chapters and a transcript. They are filmed in the app itself, on a scripted model, so each comes out the same every time it is recorded.

1. [Your first solution](https://sigrix.io/learn/bailey-lesson-1-your-first-solution): choose your AI, install a solution, and give it a real job, answered from your own documents.
2. [Tools that ask before they act](https://sigrix.io/learn/bailey-lesson-2-tools-that-ask-before-they-act): what a solution's tools may do, and how you answer one that asks first.
3. [Apps that run a crew](https://sigrix.io/learn/bailey-lesson-3-apps-that-run-a-crew): a solution's app, set up once, running its crew of agents from the form it draws.
4. [Check a solution](https://sigrix.io/learn/bailey-lesson-4-check-a-solution): `bailey doctor` and `bailey check`, from your terminal.

## Start it

```sh
uvx bailey
```

It starts on this computer's own address and opens in your browser. `pipx run bailey` works as well, or `pip install bailey` and then `bailey`; each needs Python 3.11 or newer, and `uvx` comes with [uv](https://docs.astral.sh/uv/). The address it prints carries this session's token, which changes each time Bailey starts, so a page that is not Bailey's cannot talk to it.

Or with Docker, which also brings what a solution's tools need, Node.js and uv, and the search model:

```sh
docker run -p 127.0.0.1:8123:8123 -v bailey:/data sigrix/bailey
```

Open the address it prints. Your settings, assistants, conversations, documents and keys stay in the `bailey` volume. A container has no keychain, so Bailey keeps your keys there in a file encrypted under a passphrase, which the page asks you to choose the first time, and to type each time Bailey starts; `-e BAILEY_PASSPHRASE=…` gives it instead. The image runs on amd64 and arm64.

Then, in the page:

1. In Settings, choose your AI provider and give its key, which goes to your computer's keychain, or to the encrypted file where there is none.
2. Under Assistants, install a solution from a file: a ZIP, or a folder, which is what Safari makes of a download. Before anything is written, an install screen says what it does, which keys it needs and what reads them, the documents it asks for, which tools change something, and what it leaves out. Anything installed from a file is marked unverified, since nobody has checked it. A Sigrix download installs as it is.
3. Give it the documents it asks for, or try it on the made-up samples a solution comes with first.

Or start from one of Bailey's own templates, and edit it as a form.

### What it downloads, and when

Besides Bailey itself, three things, each when it is first needed:

- **The search model**, about 90 MB from Hugging Face, the first time a collection of documents is too large to send whole, once you say yes to it. The Docker image carries it. On a computer that cannot reach Hugging Face, put the files of `Qdrant/all-MiniLM-L6-v2-onnx` in a folder and set `BAILEY_SEARCH_MODEL` to that folder.
- **An app's environment**, with CrewAI and its libraries: about 250 MB from PyPI, once you say yes to it, built with `uv` when it is installed, and about 900 MB on disk.
- **A tool server's package**, which `uvx` or `npx` fetches the first time a conversation starts that server, as the solution's `mcp.json` says. The install screen names each server before anything is installed.

## Check your machine

```sh
uvx bailey doctor
```

`pipx run bailey doctor` works as well. It checks, and says how to fix what is missing:

| Check | Why Bailey needs it | Required |
| --- | --- | --- |
| Python 3.11 or newer | Bailey itself | yes |
| SQLite with full-text search (FTS5) | searching your documents by keyword | yes |
| A loopback address | the app's local server, which only this computer can reach | yes |
| uv | tools published as Python packages, which start with `uvx` | no |
| Node.js | tools published as npm packages, which start with `npx` | no |
| A data folder Bailey can write in | your settings and conversations, kept on this computer | yes |
| A keychain | keeping your AI keys out of plain files; without one they go to a file encrypted under a passphrase | no |

It exits 1 when a required check fails, and `bailey doctor --json` prints the same answers for a script. It reads your machine only: no network call, no file written.

## Check a solution

```sh
bailey check path/to/solution
```

`bailey check` runs a solution, a folder or a ZIP, with no browser, in a Bailey of its own that keeps nothing. It runs the solution's examples, and for each document the solution asks for, one question its sample answers and one that no document answers. It says which cases pass, and why the others fail. It runs on the provider, model and key in your settings, or on `--provider` and `--model` with the key in `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`. A tool that changes something is refused, never run. It exits 1 when a case fails and 2 when it cannot run, and `--json` prints the report, provider and model included, for a script or a CI job.

## The solution format

A solution is an Agent Plugins folder plus one file, `assistant.json`, which says how its parts fit together: which skill holds the instructions, which skills load when needed, which tools write, and which documents to ask you for. Its schema is published in [`schemas/`](schemas/README.md), so anyone can build a solution Bailey runs.

A solution names the keys it needs and never carries one: Bailey refuses a package with a key's value in it, and marks anything installed from a file unverified, since nobody has checked it.

## What comes next

- **0.2: the store.** Sign in with your Sigrix account, browse, install and update solutions in the app, and publish your own.

## Where it fits

Bailey is one of the open-source projects [Sigrix](https://sigrix.io) publishes, and the one the solutions sold there are built for. A solution's apps build on two of the others:

- **[Postern](https://github.com/sigrix-io/postern)**, the open protocol the apps speak: four HTTP verbs an agent serves, and the licence check the packaging standards leave out.
- **[Gatehouse](https://github.com/sigrix-io/gatehouse)**, the page a person runs an agent from in the browser, for any runner that serves those verbs. Bailey draws a solution's apps with it.

Every project Sigrix publishes, and a map of how they connect: [sigrix.io/open-source](https://sigrix.io/open-source).

## Development

```sh
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest
```

From a checkout, `bailey` starts the app, and `CONTRIBUTING.md` says how to keep a development setup apart
from your own. The image builds from a wheel: `python -m build --wheel`, then `docker build .`, and
`scripts/check_launch.py --image <tag>` opens the app in it as CI does.

## Licence

Apache-2.0. The Sigrix name and logo are not covered by the licence; see `NOTICE`.
