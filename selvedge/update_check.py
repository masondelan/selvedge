"""
Background PyPI version check for the Selvedge CLI.

The CLI fires :func:`check_for_update_async` from its root group callback.
A daemon thread does a short, soft-failing GET against PyPI's JSON
endpoint and caches the result on disk. The next time the CLI exits,
:func:`print_notice_if_due` reads the cache and, if a newer version is
available, prints a single-line Rich notice to stderr.

Design rules:

* **Stdlib only.** No new declared dependency for one HTTP GET.
* **MCP server never calls into here.** ``selvedge-server`` stdio is the
  JSON-RPC channel; a stray stderr write from a daemon thread would
  pollute agent logs. The wiring lives in ``cli.py`` only.
* **Never raise.** Every entry point swallows exceptions — a flaky
  network, an unwritable cache directory, or an unparseable PyPI
  response must never affect the command the user actually invoked.
* **Suppression is generous.** ``SELVEDGE_NO_UPDATE_CHECK=1``,
  ``SELVEDGE_QUIET=1``, ``CI`` set in the environment, or a non-TTY
  stderr all disable the check. The check runs at most once per 24h
  per user.
* **Cache is user-global**, not project-local: ``~/.selvedge/update_check.json``.
  A user with ten Selvedge projects shouldn't pay for ten separate
  checks.
"""

from __future__ import annotations

import atexit
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__

if TYPE_CHECKING:
    from rich.console import Console

# --------------------------------------------------------------------------- #
# Constants — also imported by tests, so don't inline these into the call sites.
# --------------------------------------------------------------------------- #

PYPI_JSON_URL = "https://pypi.org/pypi/selvedge/json"
"""PyPI's per-package JSON endpoint. No auth required; rate-limit is generous."""

FETCH_TIMEOUT_SECONDS = 1.5
"""Hard ceiling on the HTTP GET. A slow PyPI must never slow the CLI."""

CHECK_INTERVAL_SECONDS = 24 * 60 * 60
"""How often to recheck. 24h matches `gh` and `npm`; `pip` uses 7 days."""

CACHE_FILENAME = "update_check.json"
"""Lives in ``~/.selvedge/`` so the cache is user-global, not per-project."""

UPGRADE_URL = "https://selvedge.sh/upgrade"
"""Per-channel upgrade instructions live on the site, not hard-coded here."""

SUPPRESSION_ENV_VARS = (
    "SELVEDGE_NO_UPDATE_CHECK",
    "SELVEDGE_QUIET",
    "CI",
)
"""Any of these being truthy disables the check. ``CI`` is the standard
GitHub Actions / GitLab CI / etc. signal — match what `gh` and `npm` do."""

# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #

# One-shot guards so a process that re-enters the CLI (tests, embedded
# use) doesn't spawn multiple checker threads or print the notice twice.
_check_started = False
_notice_printed = False
_state_lock = threading.Lock()


def _cache_path() -> Path:
    """Return the user-global cache path. Always under ``~/.selvedge/``."""
    return Path.home() / ".selvedge" / CACHE_FILENAME


def _stderr_is_tty() -> bool:
    """
    Wrap ``sys.stderr.isatty()`` so it's individually patchable in tests.

    Pytest's capture machinery rewraps ``sys.stderr`` after fixtures run,
    which makes patching the attribute itself unreliable. Tests patch
    this helper directly. In production this is just ``sys.stderr.isatty()``.
    """
    try:
        return bool(sys.stderr.isatty())
    except (AttributeError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# Gating
# --------------------------------------------------------------------------- #


def _env_truthy(name: str) -> bool:
    """Treat ``"1"``, ``"true"``, ``"yes"`` (any case) as truthy. Empty = false."""
    val = os.environ.get(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def _is_dev_install(version: str) -> bool:
    """
    True for editable installs / pre-release builds where update notices
    are noise (pip install -e .  → version often ends in ``.dev0`` etc.).

    Conservative: only suppress on PEP 440 pre/dev/local segments, not on
    stable release suffixes.
    """
    return any(token in version for token in (".dev", "+", "rc", "a", "b"))


def should_check(*, now: float | None = None) -> bool:
    """
    Central gate for the background check.

    Returns False if any of the following hold:

    * Any of :data:`SUPPRESSION_ENV_VARS` is truthy in the environment.
    * ``stderr`` is not a TTY (piping to a file, agent stdio, etc.).
    * The current install looks like a dev build.
    * The cache file says we last checked within :data:`CHECK_INTERVAL_SECONDS`.

    ``now`` is injectable for testing; defaults to ``time.time()``.
    """
    for var in SUPPRESSION_ENV_VARS:
        if _env_truthy(var):
            return False

    # No TTY → almost certainly automation, agent, or output capture.
    # Same gate `gh` and `npm/update-notifier` use.
    if not _stderr_is_tty():
        return False

    if _is_dev_install(__version__):
        return False

    now = now if now is not None else time.time()
    cached = _read_cache()
    if cached is not None:
        checked_at = cached.get("checked_at_epoch", 0)
        if isinstance(checked_at, (int, float)) and now - checked_at < CHECK_INTERVAL_SECONDS:
            return False

    return True


# --------------------------------------------------------------------------- #
# Cache I/O
# --------------------------------------------------------------------------- #


def _read_cache() -> dict | None:
    """Return the cached dict or ``None`` on any failure (missing, malformed, unreadable)."""
    path = _cache_path()
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_cache(latest: str, *, now: float | None = None) -> None:
    """
    Write the cache atomically-ish. Any failure (read-only ``$HOME``,
    permission denied, disk full) is swallowed — the worst case is that
    we re-check on the next run, which is harmless.
    """
    path = _cache_path()
    payload = {
        "checked_at_epoch": now if now is not None else time.time(),
        "latest": latest,
        "current_at_check": __version__,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        tmp.replace(path)
    except OSError:
        return


# --------------------------------------------------------------------------- #
# Version comparison
# --------------------------------------------------------------------------- #


def _parse_version_tuple(version: str) -> tuple[int, ...]:
    """
    Best-effort parse of ``"X.Y.Z"`` into ``(X, Y, Z)``.

    Strips any PEP 440 suffix (``rc1``, ``.dev0``, ``+local``, ``a1``, etc.)
    by truncating at the first non-digit-or-dot character. Returns
    ``(0,)`` for an unparseable string so the comparison degrades to
    "don't show the notice" rather than raising.
    """
    cleaned = []
    for ch in version.strip():
        if ch.isdigit() or ch == ".":
            cleaned.append(ch)
        else:
            break
    stripped = "".join(cleaned).strip(".")
    if not stripped:
        return (0,)
    try:
        return tuple(int(part) for part in stripped.split("."))
    except ValueError:
        return (0,)


def _is_newer(latest: str, current: str) -> bool:
    """
    True iff ``latest`` represents a strictly greater released version
    than ``current``. Uses ``packaging.version`` if available (it ships
    with pip, so it's almost always present), with a tuple-of-ints
    fallback that keeps us honest with the "no external dependencies"
    rule in CLAUDE.md.
    """
    try:
        from packaging.version import InvalidVersion, Version

        try:
            return Version(latest) > Version(current)
        except InvalidVersion:
            pass
    except ImportError:
        pass

    return _parse_version_tuple(latest) > _parse_version_tuple(current)


# --------------------------------------------------------------------------- #
# Network fetch
# --------------------------------------------------------------------------- #


def _fetch_latest(*, timeout: float = FETCH_TIMEOUT_SECONDS) -> str | None:
    """
    GET PyPI's JSON endpoint and return ``info.version``, or ``None`` on
    any failure. Never raises.
    """
    req = urllib.request.Request(
        PYPI_JSON_URL,
        headers={"Accept": "application/json", "User-Agent": f"selvedge/{__version__}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https URL)
            payload = json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    except Exception:
        # Belt-and-suspenders: a custom urlopen monkeypatch in a test
        # could raise something exotic. Update checks must never raise.
        return None

    info = payload.get("info") if isinstance(payload, dict) else None
    if not isinstance(info, dict):
        return None
    version = info.get("version")
    if not isinstance(version, str) or not version:
        return None
    return version


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #


def _run_check() -> None:
    """Thread body — gate, fetch, cache. All exceptions swallowed."""
    try:
        if not should_check():
            return
        latest = _fetch_latest()
        if latest is None:
            return
        _write_cache(latest)
    except Exception:
        # Daemon threads dying silently is fine; bubbling up isn't.
        return


def check_for_update_async() -> None:
    """
    Fire-and-forget version check.

    Spawns a daemon thread the first time it's called in a process;
    subsequent calls are no-ops. Returns immediately. Safe to call
    from anywhere — gating happens inside the thread so callers
    don't have to know the rules.

    NOTE: Do NOT call from ``selvedge/server.py``. The MCP server's
    stdio is the JSON-RPC channel; a notice on stderr would land in
    the calling agent's logs as noise. Wiring is intentionally
    limited to ``selvedge/cli.py``.
    """
    global _check_started
    with _state_lock:
        if _check_started:
            return
        _check_started = True

    try:
        t = threading.Thread(target=_run_check, name="selvedge-update-check", daemon=True)
        t.start()
    except RuntimeError:
        # Some sandboxes disallow thread creation. Fine — no check.
        return


def print_notice_if_due(console: Console | None = None) -> None:
    """
    Print the upgrade notice to stderr if the cache says we're out of date.

    Called from ``atexit`` so the notice appears after the user's
    command output rather than interleaved with it. Idempotent within
    a process.

    ``console`` is optional — if omitted we lazily import Rich and
    instantiate a stderr console. Passing the CLI's own ``err_console``
    keeps styling consistent.
    """
    global _notice_printed
    with _state_lock:
        if _notice_printed:
            return
        _notice_printed = True

    try:
        # Re-check suppression at print time too: if the user is piping
        # output to a file, even a cached notice would contaminate it.
        for var in SUPPRESSION_ENV_VARS:
            if _env_truthy(var):
                return
        if not _stderr_is_tty():
            return

        cached = _read_cache()
        if cached is None:
            return
        latest = cached.get("latest")
        if not isinstance(latest, str) or not latest:
            return
        if not _is_newer(latest, __version__):
            return

        if console is None:
            from rich.console import Console as _Console

            console = _Console(stderr=True)

        console.print(
            f"[yellow]selvedge:[/yellow] v{latest} available "
            f"(you're on {__version__}) — [dim]{UPGRADE_URL}[/dim]"
        )
    except Exception:
        # Same swallow-everything posture as the rest of this module.
        return


def register_atexit_notice() -> None:
    """
    Idempotently register :func:`print_notice_if_due` with ``atexit``.

    Wrapper exists so callers don't have to import ``atexit`` themselves
    and so we can swap the strategy later (e.g. for an explicit hook
    in Click's ``result_callback``) without churning the call sites.
    """
    atexit.register(print_notice_if_due)


def _reset_for_testing() -> None:
    """Reset module-level state. Test-only — do not call from production code."""
    global _check_started, _notice_printed
    with _state_lock:
        _check_started = False
        _notice_printed = False
