"""Configuration and database path resolution for Selvedge."""

from pathlib import Path
import os


SELVEDGE_DIR_NAME = ".selvedge"
SELVEDGE_DB_NAME = "selvedge.db"


def get_db_path() -> Path:
    """
    Resolve the Selvedge database path.

    Resolution order:
    1. SELVEDGE_DB environment variable (absolute path override)
    2. Walk up from CWD looking for an existing .selvedge/ directory
    3. Fall back to ~/.selvedge/selvedge.db (global default)
    """
    # 1. Explicit env override
    if env_path := os.environ.get("SELVEDGE_DB"):
        p = Path(env_path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # 2. Walk up from CWD
    cwd = Path.cwd().resolve()
    for directory in [cwd, *cwd.parents]:
        candidate = directory / SELVEDGE_DIR_NAME / SELVEDGE_DB_NAME
        if candidate.parent.exists():
            return candidate

    # 3. Global fallback
    default = Path.home() / SELVEDGE_DIR_NAME / SELVEDGE_DB_NAME
    default.parent.mkdir(parents=True, exist_ok=True)
    return default


def get_selvedge_dir() -> Path:
    """Return the .selvedge directory containing the database."""
    return get_db_path().parent


def init_project(path: Path = None) -> Path:
    """
    Create a .selvedge directory at the given path (or CWD).
    Returns the path to the initialized directory.
    """
    root = (path or Path.cwd()).resolve()
    selvedge_dir = root / SELVEDGE_DIR_NAME
    selvedge_dir.mkdir(exist_ok=True)
    return selvedge_dir
