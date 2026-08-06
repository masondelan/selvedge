"""Configuration and database path resolution for Selvedge.

Two things live here: DB-path resolution (the original job) and, since
v0.3.10, the ``.selvedge/config.toml`` settings layer.

**Precedence, canonical.** ``SELVEDGE_DB`` always wins for DB-path
resolution — config cannot override the environment there, because the file
that would do the overriding is found *by* resolving the path. For every
other setting:

    CLI flag > env var > project ``.selvedge/config.toml``
             > global ``~/.selvedge/config.toml`` > hardcoded default

:func:`resolve_setting` reports which of those steps produced a value, and
``selvedge doctor`` surfaces it — the same shape as the DB-path source, so a
user can see *why* a setting has the value it does without reading source.

Missing file, unparseable file, or an out-of-range value all fall back to the
next step down rather than raising. A config file is an optimization; a
broken one must not stop Selvedge from recording changes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Literal, NamedTuple

SELVEDGE_DIR_NAME = ".selvedge"
SELVEDGE_DB_NAME = "selvedge.db"
CONFIG_FILE_NAME = "config.toml"

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


# ---------------------------------------------------------------------------
# Settings — .selvedge/config.toml
# ---------------------------------------------------------------------------

#: Sentinel for "no limit". Used by the retention settings, where the safe
#: default is to never delete anything: a user must opt in to an events
#: retention window, and `0` reads more clearly in a TOML file than a magic
#: large number.
UNLIMITED = 0

#: Which precedence step produced a setting's effective value.
SettingSource = Literal["flag", "env", "project", "global", "default"]


class SettingSpec(NamedTuple):
    """One configurable setting: its default, env var, and how to read it."""

    default: Any
    env: str
    kind: str  # "int" | "str_list"
    help: str


#: Every setting Selvedge reads from config.toml, in doctor's display order.
#: Keys are flat and top-level in the TOML file; the pre-existing
#: ``[hook] watch_globs`` keeps its section and is read separately by the
#: hook, which cannot afford this module's import on its hot path.
SETTINGS: dict[str, SettingSpec] = {
    "retention_days_events": SettingSpec(
        UNLIMITED, "SELVEDGE_RETENTION_DAYS_EVENTS", "int",
        "Delete events older than N days. 0 = never (the default): losing "
        "captured reasoning is the one thing this tool exists to prevent.",
    ),
    "retention_days_tool_calls": SettingSpec(
        90, "SELVEDGE_RETENTION_DAYS_TOOL_CALLS", "int",
        "Delete local tool-call telemetry older than N days. 0 = never.",
    ),
    "backup_keep_last": SettingSpec(
        7, "SELVEDGE_BACKUP_KEEP_LAST", "int",
        "How many rotated backups to keep.",
    ),
    "diff_bytes": SettingSpec(
        65536, "SELVEDGE_DIFF_BYTES", "int",
        "Truncate a logged diff beyond this many bytes. 0 = no limit.",
    ),
    "reasoning_bytes": SettingSpec(
        32768, "SELVEDGE_REASONING_BYTES", "int",
        "Truncate logged reasoning beyond this many bytes. 0 = no limit.",
    ),
    "db_size_warn_mb": SettingSpec(
        500, "SELVEDGE_DB_SIZE_WARN_MB", "int",
        "`doctor` warns once the database exceeds this size. 0 = never warn.",
    ),
    "stale_days": SettingSpec(
        UNLIMITED, "SELVEDGE_STALE_DAYS", "int",
        "Fallback age in days for `stale_decisions` when a decision carries "
        "no explicit revisit_after. 0 = off (the default).",
    ),
    "redaction_patterns": SettingSpec(
        [], "SELVEDGE_REDACTION_PATTERNS", "str_list",
        "Extra secret-shaped regexes to warn about at log_change time. "
        "Extends the built-in set rather than replacing it.",
    ),
}


class ResolvedSetting(NamedTuple):
    """A setting's effective value plus the precedence step that produced it."""

    value: Any
    source: SettingSource


def _parse_toml(path: Path) -> dict:
    """Read one TOML file, or ``{}`` on any failure.

    ``tomllib`` is stdlib from 3.11; on the declared 3.10 floor the declared
    ``tomli`` dependency provides it. Absence is still handled rather than
    raising — a source checkout with neither must degrade to defaults, not
    crash on every command.
    """
    if not path.is_file():
        return {}
    try:
        import tomllib
    except ImportError:  # pragma: no cover — 3.10 only
        try:
            import tomli as tomllib
        except ImportError:
            return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def project_config_path() -> Path:
    """The project-local ``.selvedge/config.toml``, next to the resolved DB."""
    return get_db_path().parent / CONFIG_FILE_NAME


def global_config_path() -> Path:
    """The user-level ``~/.selvedge/config.toml``."""
    return Path.home() / SELVEDGE_DIR_NAME / CONFIG_FILE_NAME


def _coerce(raw: Any, kind: str) -> Any | None:
    """Coerce a config/env value to the setting's type, or None if unusable.

    Returning None rather than raising is what makes a bad value fall through
    to the next precedence step instead of taking down the command.
    """
    if kind == "int":
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None
    if kind == "str_list":
        if isinstance(raw, str):
            items = [p.strip() for p in raw.split(",")]
        elif isinstance(raw, list):
            items = [str(p).strip() for p in raw]
        else:
            return None
        return [i for i in items if i]
    return None


def resolve_setting(name: str, flag_value: Any = None) -> ResolvedSetting:
    """Resolve one setting through the full precedence chain.

    ``flag_value`` is whatever a CLI flag supplied, or None when the flag was
    not passed — so a command can hand its option straight in and get correct
    precedence without reimplementing the chain.
    """
    spec = SETTINGS[name]

    if flag_value is not None:
        coerced = _coerce(flag_value, spec.kind)
        if coerced is not None:
            return ResolvedSetting(coerced, "flag")

    env_raw = os.environ.get(spec.env)
    if env_raw is not None:
        coerced = _coerce(env_raw, spec.kind)
        if coerced is not None:
            return ResolvedSetting(coerced, "env")

    file_steps: tuple[tuple[SettingSource, Path], ...] = (
        ("project", project_config_path()),
        ("global", global_config_path()),
    )
    for source, path in file_steps:
        data = _parse_toml(path)
        if name in data:
            coerced = _coerce(data[name], spec.kind)
            if coerced is not None:
                return ResolvedSetting(coerced, source)

    default = spec.default
    return ResolvedSetting(list(default) if isinstance(default, list) else default, "default")


def get_setting(name: str, flag_value: Any = None) -> Any:
    """The effective value of one setting. See :func:`resolve_setting`."""
    return resolve_setting(name, flag_value).value


def resolve_all_settings() -> dict[str, ResolvedSetting]:
    """Every setting with its effective value and source, in declared order."""
    return {name: resolve_setting(name) for name in SETTINGS}
