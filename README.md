# Bailey

[![PyPI](https://img.shields.io/pypi/v/bailey)](https://pypi.org/project/bailey/)
[![Python](https://img.shields.io/pypi/pyversions/bailey)](https://pypi.org/project/bailey/)
[![CI](https://github.com/sigrix-io/bailey/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/sigrix-io/bailey/actions/workflows/ci.yml)
[![Licence](https://img.shields.io/github/license/sigrix-io/bailey)](https://github.com/sigrix-io/bailey/blob/main/LICENSE)

An open-source AI assistant that runs ready-made solutions: on your own machine, with your own AI key.

> **Early days.** This is release 0.0.1. It installs one command, `bailey doctor`, which checks that your computer has what the assistant will need. The assistant itself arrives in 0.1.

## What Bailey is for

Getting AI to do one real job reliably is still a project. Someone writes the instructions, connects the tools, supplies the documents and the keys, checks that it works, and keeps it working when something changes.

Bailey runs **solutions** instead: complete, versioned packages for one job, such as answering customer emails, qualifying leads or writing a weekly report. A solution carries its instructions, skills, tools, knowledge and small apps. Bailey installs it in one step and asks only for the keys it needs.

- **Your machine, your key.** It runs locally and opens in your browser, with the AI provider you choose: Anthropic, OpenAI, or any OpenAI-compatible endpoint, local models included.
- **It asks before it acts.** Every tool is either read or write, and a write tool asks before it runs: once, always for this assistant, or no.
- **Your documents stay yours.** Files you add are indexed on your computer, and answers cite the passage they came from.
- **Open formats.** Skills are Agent Skills, tools are MCP servers, apps speak [Postern](https://github.com/sigrix-io/postern), and a solution is an Agent Plugins folder, so its parts also load in other clients.
- **No telemetry.**

Solutions can be free, or bought on [Sigrix](https://sigrix.io), where builders sell what they make for Bailey.

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
| SQLite extension loading | the vector index, which finds passages by meaning | no |
| uv | tools published as Python packages, which start with `uvx` | no |
| Node.js | tools published as npm packages, which start with `npx` | no |

It exits 1 when a required check fails, and `bailey doctor --json` prints the same answers for a script. It reads your machine only: no network call, no file written.

## The solution format

A solution is an Agent Plugins folder plus one file, `assistant.json`, which says how its parts fit together: which skill holds the instructions, which skills load when needed, which tools write, and which documents to ask you for. Its schema is published in [`schemas/`](schemas/README.md), so anyone can build a solution Bailey runs.

## What comes next

- **0.1: the app.** Chat, assistants, skills loaded when needed, tools with approval, knowledge from your own documents with citations, apps, and installing a solution from a file.
- **0.2: the store.** Sign in, browse, install and update solutions, and publish your own.

## Where it fits

Bailey is one of the open-source projects [Sigrix](https://sigrix.io) publishes, and the one the solutions sold there are built for. A solution's apps build on two of the others:

- **[Postern](https://github.com/sigrix-io/postern)**, the open protocol the apps speak: four HTTP verbs an agent serves, and the licence check the packaging standards leave out.
- **[Gatehouse](https://github.com/sigrix-io/gatehouse)**, the page a person runs an agent from in the browser, for any runner that serves those verbs. The app planned for 0.1 draws a solution's apps with it.

Every project Sigrix publishes, and a map of how they connect: [sigrix.io/open-source](https://sigrix.io/open-source).

## Development

```sh
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest
```

## Licence

Apache-2.0. The Sigrix name and logo are not covered by the licence; see `NOTICE`.
