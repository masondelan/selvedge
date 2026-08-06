# syntax=docker/dockerfile:1
#
# Container image for the Selvedge MCP server — used by the Docker MCP Catalog
# (https://github.com/docker/mcp-registry). The catalog builds this image from
# the pinned `source.commit` in its servers/selvedge/server.yaml, so the image
# matches that commit exactly. It runs `selvedge-server` over stdio: no network,
# no telemetry, all history in a local SQLite file under .selvedge/.
#
# Native installs (`pip install selvedge` / `uvx --from selvedge selvedge-server`)
# remain the primary path; this Dockerfile exists so Selvedge can also be a
# one-command install inside Docker Desktop's MCP Toolkit.

FROM python:3.12-slim

# Selvedge has a small, declared dependency set and no build-time native deps.
# Install from the checked-out source so the image content == the pinned commit.
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir .

# selvedge-server speaks MCP over stdio. Pin the store explicitly rather than
# letting the entrypoint resolve it: `SELVEDGE_DB` is step 1 of the resolution
# chain, so this makes it impossible for the walk-up in step 2 to select
# anything under /app, even if a future build-context change slips a
# `.selvedge/` back in. (`.dockerignore` is the primary defense; this is the
# backstop, because the failure is silent — the server comes up healthy and
# serves the wrong history.) Selvedge creates the parent directory on first
# use. Mount a host directory at /data to persist a store across runs:
#
#   docker run -i --rm -v "$PWD/.selvedge:/data" <image>
#
ENV SELVEDGE_DB=/data/selvedge.db
ENTRYPOINT ["selvedge-server"]
