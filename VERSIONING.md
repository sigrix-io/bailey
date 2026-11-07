# Versioning

Semantic versioning, `MAJOR.MINOR.PATCH`, released from a `vX.Y.Z` tag.

Before 1.0, a MINOR release may change or remove what an earlier one offered,
and the changelog says so when it does. From 1.0:

- **PATCH**: a fix that changes no command, option or file format.
- **MINOR**: a new command, option or check; a new version of the solution
  format read alongside the old ones.
- **MAJOR**: a removed or renamed command or option, or a solution format the
  app stops reading.

## Releasing

A release is a tag. `release.yml` builds the distribution and publishes it on
any `v*` tag pushed to this repository; nothing is uploaded by hand and no API
token exists to leak.

That works because PyPI is configured to trust this repository rather than a
credential, which takes two things that must both be in place before the first
tag, and neither fails loudly if it is missing, so check them rather than
assume:

1. On PyPI, a **trusted publisher** for `sigrix-io/bailey`, workflow
   `release.yml`, environment `pypi`. Before the first release it is a
   *pending* publisher, which does not reserve the name: the name is ours only
   once a release has been published to it.
2. In this repository's settings, an **environment named `pypi`**. The
   publisher's claim names it, so a workflow running outside it is refused.

The version lives in two places, `pyproject.toml` and `src/bailey/__init__.py`;
move both, and `tests/test_package_metadata.py` fails while they differ. Date the
version's section in `CHANGELOG.md` the day the tag goes out. Then:

```sh
git tag v0.1.0 && git push origin v0.1.0
```

Allow about ten minutes after the upload before expecting `uvx bailey` to
resolve the new version: that is the index CDN's cache, not a failed publish.
The release's `verify` job waits it out, installs the new version from PyPI by
name, and fails the run if that never arrives, reports another version, lacks
`py.typed`, or its `bailey` command does not answer with that version. It
does so on a Mac with an Apple chip as well as on Linux. The build job runs the
same checks against the wheel before the upload, because a version on PyPI can
never be reused. It also runs `scripts/check_wheels.py` there, against the
index as it is that day, since a dependency can drop a computer the README
lists between the merge and the tag.

### The image

Once `release.yml` succeeds for a tag, `image.yml` builds `sigrix/bailey` from the
wheel PyPI serves for that version, on amd64 and on arm64, each on its own runner:

1. It opens the app in each (`scripts/check_launch.py`), as CI does on every pull request.
2. It pushes `sigrix/bailey:X.Y.Z-amd64` and `sigrix/bailey:X.Y.Z-arm64`.
3. It makes `sigrix/bailey:X.Y.Z` and `sigrix/bailey:latest` of the two.
4. It pulls them back by name on a clean runner of each architecture and opens the app again.

PyPI takes no credential, but Docker Hub takes one. So this is a workflow apart from
`release.yml`, and only the two jobs in it that push read the credential. Three things
must be in place before the first image. Until they are, the push fails: with a sentence
naming the two secrets when they are missing, and with Docker Hub's own refusal otherwise.

1. On Docker Hub, the repository `sigrix/bailey`, public.
2. An access token with read and write on it.
3. In this repository's settings, an environment named `docker-hub`, with two secrets:
   `REGISTRY_USERNAME`, the Docker Hub account the token belongs to, and `REGISTRY_TOKEN`,
   the token. These are the names Sigrix's own image workflow uses.

A release whose image failed, or that came before the environment existed, gets its
image by running `image.yml` by hand with the version: it builds from what PyPI serves,
so it needs nothing else from the tag.

