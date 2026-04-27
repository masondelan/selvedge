#!/usr/bin/env bash
#
# Record the Selvedge demo gif end-to-end.
#
#   1. Seed a fresh demo DB (scripts/demo.selvedge.db) using
#      scripts/demo-seed.py.
#   2. Render docs/demo.gif from scripts/demo.tape with vhs.
#
# Run from the repo root:
#
#     scripts/record-demo.sh
#
# Prerequisites (one-time):
#
#     # macOS
#     brew install vhs
#
#     # any platform with Go installed
#     go install github.com/charmbracelet/vhs@latest
#
# vhs needs `ttyd` and `ffmpeg` available on PATH; brew installs them as
# dependencies. On Linux see https://github.com/charmbracelet/vhs#installation
#
# The script is idempotent — every run truncates the demo DB and the
# gif before regenerating, so consecutive recordings are byte-stable
# until either the seeder data or the tape file changes.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DEMO_DB="$REPO_ROOT/scripts/demo.selvedge.db"
DEMO_GIF="$REPO_ROOT/docs/demo.gif"
TAPE_FILE="$REPO_ROOT/scripts/demo.tape"

# Pick the Python that has Selvedge installed. Prefer the project venv
# if it exists; fall back to the user's default `python3` so the script
# also works in a fresh clone before .venv is set up.
if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    PY="$REPO_ROOT/.venv/bin/python"
else
    PY="$(command -v python3)"
fi

# ---------------------------------------------------------------------------
# Sanity checks — fail loudly if vhs isn't on PATH instead of recording
# nothing and silently exiting 0.
# ---------------------------------------------------------------------------
if ! command -v vhs >/dev/null 2>&1; then
    cat >&2 <<'EOF'
error: `vhs` not found on PATH.

Install it first:
  macOS:        brew install vhs
  any platform: go install github.com/charmbracelet/vhs@latest

vhs depends on `ttyd` and `ffmpeg`. brew installs both as dependencies.
EOF
    exit 1
fi

# ---------------------------------------------------------------------------
# 1. Seed the demo DB.
# ---------------------------------------------------------------------------
echo "==> Seeding demo DB at $DEMO_DB"
SELVEDGE_DB="$DEMO_DB" "$PY" "$REPO_ROOT/scripts/demo-seed.py"

# ---------------------------------------------------------------------------
# 2. Render the gif.
# ---------------------------------------------------------------------------
echo "==> Recording $DEMO_GIF from $TAPE_FILE"
mkdir -p "$(dirname "$DEMO_GIF")"
rm -f "$DEMO_GIF"

# vhs reads `Output` and `Env` directives from the tape file, but we
# also export SELVEDGE_DB explicitly so a `$SELVEDGE_DB`-using shell
# inside the tape sees it without depending on tape-level Env honoring
# (older vhs versions had inconsistent Env behavior).
export SELVEDGE_DB="$DEMO_DB"
export FORCE_COLOR="1"

vhs "$TAPE_FILE"

if [[ ! -f "$DEMO_GIF" ]]; then
    echo "error: vhs ran but $DEMO_GIF does not exist" >&2
    exit 1
fi

bytes=$(wc -c <"$DEMO_GIF" | tr -d ' ')
echo "==> Wrote $DEMO_GIF (${bytes} bytes)"
echo
echo "Next: replace the DEMO GIF placeholder in README.md with"
echo "  ![Selvedge demo](docs/demo.gif)"
