"""Configuration and database path resolution for Selvedge."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal, NamedTuple

SELVEDGE_DIR_NAME = ".selvedge"
SELVEDGE_DB_NAME = "selvedge.db"

# Module-level guard so we only print the global-fallback warning once
# per process — avoids spamming stderr when a long-running MCP server
# resolves the path many times. The warning is user-facing UX (suppressed
# via SELVEDGE_QUIET) rather than a diagnostic log message.
_warned_fallback = False


# Which step of the resolution chain produced the path. Surfaced by
# ``selvedge doctor`` so users can see why Selvedge picked the DB it did
# without having to grep the source.
DBPathSource = Literal["env", "walkup", "global"]


class ResolvedDBPath(NamedTuple):
    """The DB path plus the resolution step that produced it."""

    path: Path
    source: DBPathSource


def resolve_db_path() -> ResolvedDBPath:
    """
    Resolve the database path AND report which precedence step matched.

    Mirrors :func:`get_db_path` exactly — same resolution order, same
    side effects (creates the parent directory, prints the global-fallback
    warning once per process). Use this when you need to know not just
    *which* DB will be used, but *why* — `selvedge doctor` shows the
    source so the user can see whether `SELVEDGE_DB` is in effect, a
    walkup hit a project DB, or they're on the global fallback.
    """
    global _warned_fallback

    # 1. Explicit env override
    if env_path := os.environ.get("SELVEDGE_DB"):
        p = Path(env_path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return ResolvedDBPath(p, "env")

    # 2. Walk up from CWD looking for an existing project-local DB file
    cwd = Path.cwd().resolve()
    for directory in [cwd, *cwd.parents]:
        candidate = directory / SELVEDGE_DIR_NAME / SELVEDGE_DB_NAME
        if candidate.is_file():
            return ResolvedDBPath(candidate, "walkup")

    # 3. Global fallback
    default = Path.home() / SELVEDGE_DIR_NAME / SELVEDGE_DB_NAME
    if not _warned_fallback and not os.environ.get("SELVEDGE_QUIET"):
        _warned_fallback = True
        sys.stderr.write(
            f"selvedge: using global database at {default}\n"
            "selvedge: run `selvedge init` in your project root to create a project-local DB\n"
        )
    default.parent.mkdir(parents=True, exist_ok=True)
    return ResolvedDBPath(default, "global")


def get_db_path() -> Path:
    """
    Resolve the Selvedge database path.

    Resolution order:
    1. ``SELVEDGE_DB`` environment variable (absolute path override)
    2. Walk up from CWD looking for an existing ``.selvedge/selvedge.db`` file
    3. Fall back to ``~/.selvedge/selvedge.db`` (global default)

    Note: step 2 requires the database FILE to exist, not just the
    ``.selvedge/`` directory. Earlier versions matched on directory
    presence alone, which meant a stray empty ``.selvedge/`` upstream
    could silently shadow the user's intended global DB.

    A one-time warning is printed to stderr when falling back to the
    global default so users notice unintentional global use. Set the
    ``SELVEDGE_QUIET`` environment variable to suppress.

    Use :func:`resolve_db_path` when you also need to know which step
    of the resolution chain produced the path.
    """
    return resolve_db_path().path


def get_selvedge_dir() -> Path:
    """Return the .selvedge directory containing the database."""
    return get_db_path().parent


def init_project(path: Path | None = None) -> Path:
    """
    Create a .selvedge directory at the given path (or CWD).
    Returns the path to the initialized directory.
    """
    root = (path or Path.cwd()).resolve()
    selvedge_dir = root / SELVEDGE_DIR_NAME
    selvedge_dir.mkdir(exist_ok=True)
    return selvedge_dir
