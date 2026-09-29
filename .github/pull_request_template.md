## What this changes

<!-- One or two sentences. The diff shows what changed; say why. -->

## Does it change what the doctor reports?

<!--
Delete the rows that do not apply.

- [ ] No: refactor, docs, tests or tooling only.
- [ ] A check's answer or message changed. Say on which machine, and what it
      printed before and after.
- [ ] A new check. It comes with a test on both sides: a machine that has what
      it probes, and one that lacks it.
- [ ] What `--json` prints changed shape. A script may read it; the changelog
      says so.
-->

## The test that fails without it

<!--
One change per pull request, with a test that fails without it. Break the
implementation on purpose, confirm the test goes red, then put it back. Say
what you broke and which test caught it.
-->

## Checks

- [ ] `pytest`
- [ ] `ruff check .`
- [ ] `ruff format --check .`
- [ ] Still the standard library only, with no network call and no file written
- [ ] `CHANGELOG.md` has a line under *Unreleased*
