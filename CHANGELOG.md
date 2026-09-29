# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the versioning is
described in `VERSIONING.md`.

## [Unreleased]

### Changed

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
