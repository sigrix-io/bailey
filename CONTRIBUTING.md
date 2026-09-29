# Contributing

Thanks for looking. Bailey is at its first release, which is a single
command, `bailey doctor`; the assistant itself is being built toward 0.1.

## What fits now

- A check the doctor gets wrong on a real machine: a Python build, an
  operating system or a tool installer it misreads. Say what it printed and
  what was actually there.
- A clearer sentence when a check fails. Say what is missing and what to do;
  never guess at a cause the check did not see.

## What does not

- A check that reaches the network, writes a file or reads a secret. The
  doctor reads this machine and nothing else.
- A dependency. The doctor is the standard library, so `uvx bailey doctor`
  installs one package.

## Working on it

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest
```

Each check takes what it probes as an argument, so a test can hand it a
machine that lacks something. A new check comes with a test on both sides.

## Pull requests

- One change per pull request, with a test that fails without it.
- `CHANGELOG.md` gets a line under *Unreleased*.
