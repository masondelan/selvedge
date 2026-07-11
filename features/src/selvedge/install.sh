#!/usr/bin/env bash
#
# Dev container feature install script for Selvedge.
#
# Installs the `selvedge` PyPI package — which provides both the `selvedge`
# CLI and the `selvedge-server` MCP command — into the image at build time.
# Deterministic and offline-safe beyond the one PyPI download: prefers pipx
# (isolated venv, launchers in /usr/local/bin so every user sees them),
# falls back to a plain pip install with a PEP 668 retry.
#
# Option env vars (injected by the dev container CLI, uppercased):
#   VERSION   "latest" (default) or an exact PyPI version, e.g. "0.3.9"

set -e

VERSION="${VERSION:-latest}"

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: install.sh must run as root (the dev container CLI invokes feature installers as root at image build time)." >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found. Selvedge requires Python >= 3.10." >&2
    echo "Add the Python feature ahead of this one in devcontainer.json, e.g.:" >&2
    echo '    "features": {' >&2
    echo '        "ghcr.io/devcontainers/features/python:1": {},' >&2
    echo '        "ghcr.io/masondelan/selvedge/selvedge:1": {}' >&2
    echo '    }' >&2
    exit 1
fi

if [ -z "$VERSION" ] || [ "$VERSION" = "latest" ]; then
    SPEC="selvedge"
else
    SPEC="selvedge==${VERSION}"
fi

echo "Installing ${SPEC} ..."

if command -v pipx >/dev/null 2>&1; then
    # System-wide pipx install: venv under /usr/local/pipx, launchers in
    # /usr/local/bin, so `selvedge` and `selvedge-server` are on PATH for
    # every user — including the non-root remoteUser. --force keeps image
    # rebuilds idempotent.
    PIPX_HOME=/usr/local/pipx PIPX_BIN_DIR=/usr/local/bin pipx install --force "${SPEC}"
else
    # Plain pip. Debian/Ubuntu images whose system Python is marked
    # "externally managed" (PEP 668) refuse system-wide pip installs, so
    # retry with the explicit override instead of silently failing the
    # image build.
    python3 -m pip install --no-cache-dir --upgrade "${SPEC}" \
        || python3 -m pip install --no-cache-dir --upgrade --break-system-packages "${SPEC}"
fi

if ! command -v selvedge >/dev/null 2>&1; then
    echo "ERROR: selvedge did not land on PATH after install." >&2
    exit 1
fi

echo "Done: $(selvedge --version)"
