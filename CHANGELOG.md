# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the versioning is
described in `VERSIONING.md`.

## [Unreleased]

### Added

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
- Dependabot opens one pull request per ecosystem instead of one per
  dependency. The branch ruleset only merges a pull request that is up to date
  with `main`, so each separate update merged put every other one behind.
  Nothing in the package changes.
- Every GitHub Action the workflows run is pinned to a commit, with its release
  named beside it, and Dependabot keeps those pins and the dev tools current.
  CI ends in one `ci-passed` job for the branch ruleset to require. Nothing in
  the package changes.

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
