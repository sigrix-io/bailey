# syntax=docker/dockerfile:1
#
# sigrix/bailey: Bailey with what a solution's tools need and the search model, so nothing is
# fetched on its first run.
#
#   docker run -p 127.0.0.1:8123:8123 -v bailey:/data sigrix/bailey
#
# - Node.js, npm and npx, and uv and uvx, because a solution's MCP servers start with npx or
#   uvx and a person's computer may have neither (the design's risk 2).
# - The search model, which the app otherwise downloads the first time a collection is too
#   large to send whole (risk 7). It sits outside the data volume, so a new volume has it too.
# - tini as PID 1: it hands a stop to Bailey, and reaps the processes a tool server leaves.
#
# Built from the one wheel in dist/: the release's own in release.yml, and one built from the
# checkout in ci.yml, where scripts/check_launch.py opens the app in it before anything ships.
# Each base is pinned by digest, and Dependabot moves the pins.

FROM node:24-trixie-slim@sha256:54f84b4cf8432b21aaffd5d3ce8d41e12cea0d10d33c09d2f3c9879d49d6b446 AS node

FROM python:3.13-slim-trixie@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1

LABEL org.opencontainers.image.title="Bailey" \
      org.opencontainers.image.description="An open-source AI assistant that runs ready-made solutions, with your own AI key." \
      org.opencontainers.image.source="https://github.com/sigrix-io/bailey" \
      org.opencontainers.image.licenses="Apache-2.0"

RUN apt-get update \
 && apt-get install --yes --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/*

# Node.js from the official image, built for the same Debian release.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# uv and uvx, from PyPI, as Bailey itself comes.
ARG UV_VERSION=0.11.32
RUN pip install --no-cache-dir --root-user-action=ignore "uv==${UV_VERSION}"

COPY dist/ /tmp/dist/
RUN set -eu; set -- /tmp/dist/*.whl; \
    [ "$#" -eq 1 ] && [ -f "$1" ] || { echo "dist/ must hold exactly one wheel, not: $*" >&2; exit 1; }; \
    pip install --no-cache-dir --root-user-action=ignore "$1"; \
    rm -rf /tmp/dist

# The search model, fetched by the app's own download, then loaded once with the network
# off, as the app will load it. fastembed keeps a download in Hugging Face's cache layout;
# Bailey reads the model from a plain folder of its files (BAILEY_SEARCH_MODEL).
ENV BAILEY_SEARCH_MODEL=/opt/bailey/search-model
RUN python - <<'PY'
import os
import shutil
from pathlib import Path

from bailey.knowledge import LocalEmbedder

cache = Path("/opt/bailey/model-cache")
LocalEmbedder(cache).download()
(snapshot,) = cache.glob("models--*/snapshots/*")
shutil.copytree(snapshot, os.environ["BAILEY_SEARCH_MODEL"])  # the files themselves, not the cache's links
shutil.rmtree(cache)
PY
RUN HF_HUB_OFFLINE=1 python - <<'PY'
import os
from pathlib import Path

from bailey.knowledge import LocalEmbedder

model = LocalEmbedder(Path("/nonexistent"), folder=Path(os.environ["BAILEY_SEARCH_MODEL"]))
assert model.ready(), "the search model does not load from its folder"
vectors = model.embed(["Can I return a book I did not like?"])
assert vectors.shape == (1, model.model.dimensions), vectors.shape
print("the search model loads offline:", model.model.name)
PY

# BAILEY_CONTAINER makes Bailey listen on the container's own address, which is what a port
# published with -p reaches; a request still needs a loopback Host and this session's token.
# There is no keychain in a container, so keys go to the encrypted file, under a passphrase
# the page asks for the first time.
ENV BAILEY_HOME=/data \
    BAILEY_KEYS=file \
    BAILEY_CONTAINER=1 \
    HF_HUB_OFFLINE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 1000 --user-group bailey \
 && mkdir /data \
 && chown bailey:bailey /data
USER bailey
WORKDIR /home/bailey
VOLUME /data
EXPOSE 8123

ENTRYPOINT ["tini", "--", "bailey"]
CMD ["--port", "8123", "--no-browser"]
