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

# selvedge-server speaks MCP over stdio. With no project mounted, SELVEDGE_DB
# defaults to ~/.selvedge/selvedge.db inside the container; mount a project (see
# the run.volumes block in the catalog server.yaml) to use its .selvedge/ store.
ENTRYPOINT ["selvedge-server"]
