# Bailey

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

## What comes next

- **0.1: the app.** Chat, assistants, skills loaded when needed, tools with approval, knowledge from your own documents with citations, apps, and installing a solution from a file.
- **0.2: the store.** Sign in, browse, install and update solutions, and publish your own.

## Development

```sh
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest
```

## Licence

Apache-2.0. The Sigrix name and logo are not covered by the licence; see `NOTICE`.
