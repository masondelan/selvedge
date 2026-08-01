"""
Opt-in anonymous usage telemetry for Selvedge.

Selvedge is local-first: the README promises no cloud, and this module
keeps that promise. Telemetry is **off by default**, does nothing until a
user explicitly runs ``selvedge telemetry enable`` (or sets
``SELVEDGE_TELEMETRY=1``), and sends a single tiny heartbeat — never
code, never paths, never reasoning text, never repo names.

The entire payload (schema v1)::

    {
      "schema": 1,
      "install_id": "<random UUID, generated at enable time>",
      "version": "<the installed Selvedge version>",
      "python": "3.12",
      "os": "darwin",
      "arch": "arm64",
      "source": "cli" | "server"
    }

That is the whole thing. The ``install_id`` is a random UUID4 minted on
this machine when telemetry is enabled — it is not derived from hardware,
hostname, username, or anything else identifying, and it is deleted when
telemetry is disabled (re-enabling mints a fresh one). The heartbeat
answers exactly one question — *how many installs were active this week* —
and cannot answer any other.

Design rules (shared with :mod:`selvedge.update_check`):

* **Stdlib only.** One HTTP POST does not justify a dependency.
* **Never raise, never print.** Every entry point swallows exceptions and
  writes nothing to stdout/stderr, which is what makes it safe to fire
  from ``selvedge-server`` where stdio is the JSON-RPC channel.
* **Strictly opt-in.** No consent recorded → nothing is sent, ever.
  ``SELVEDGE_TELEMETRY=0`` / ``SELVEDGE_NO_TELEMETRY=1`` are absolute
  kill switches that beat recorded consent. ``CI`` suppresses sending so
  CI fleets don't inflate the active-install count.
* **At most one ping per 24h per user**, tracked user-globally in
  ``~/.selvedge/telemetry.json`` alongside the consent record.
* **Transparent.** ``selvedge telemetry status`` shows the exact payload
  that would be sent; ``docs/telemetry.md`` documents the receiver.

Full documentation: ``docs/telemetry.md``.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import __version__

# Deliberately shared with the update checker: one definition of "truthy
# env var" and one definition of "dev install" across both background
# network features.
from .update_check import _env_truthy, _is_dev_install

# --------------------------------------------------------------------------- #
# Constants — also imported by tests, so don't inline these into the call sites.
# --------------------------------------------------------------------------- #

TELEMETRY_URL = "https://telemetry.selvedge.sh/v1/ping"
"""Receiving endpoint — a Cloudflare Worker writing to Workers Analytics
Engine. Source lives in ``infra/telemetry-worker/`` in this repo, so the
receiver is exactly as auditable as the sender. Override with
``SELVEDGE_TELEMETRY_URL`` (tests, self-hosting)."""

SEND_TIMEOUT_SECONDS = 2.0
"""Hard ceiling on the POST. A slow endpoint must never slow the CLI or
the MCP server."""

PING_INTERVAL_SECONDS = 24 * 60 * 60
"""At most one heartbeat per 24h per user. Weekly-active-installs needs
nothing finer-grained."""

CONSENT_FILENAME = "telemetry.json"
"""Lives in ``~/.selvedge/`` — consent is per-user, not per-project."""

PAYLOAD_SCHEMA_VERSION = 1
"""Bump only with a matching receiver change and a docs/telemetry.md update."""

KILL_ENV_VARS = ("SELVEDGE_NO_TELEMETRY",)
"""Truthy → telemetry is off regardless of recorded consent. ``SELVEDGE_TELEMETRY=0``
has the same effect (handled separately since it's tri-state)."""

# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #

# One ping attempt per process, maximum. A long-running MCP server calls
# ping_async() once at startup; re-entrant CLI use (tests) shouldn't
# spawn duplicate threads.
_ping_started = False
_state_lock = threading.Lock()


def _consent_path() -> Path:
    """Return the user-global consent/state file path, under ``~/.selvedge/``."""
    return Path.home() / ".selvedge" / CONSENT_FILENAME


# --------------------------------------------------------------------------- #
# Consent file I/O
# --------------------------------------------------------------------------- #


def _read_state() -> dict:
    """Return the consent/state dict, or ``{}`` on any failure (missing,
    malformed, unreadable). Never raises."""
    path = _consent_path()
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(state: dict) -> bool:
    """
    Write the consent/state file atomically-ish. Returns ``True`` on
    success, ``False`` on any failure (read-only ``$HOME``, disk full).
    Failures are non-fatal everywhere this is called.
    """
    path = _consent_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        tmp.replace(path)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Gating
# --------------------------------------------------------------------------- #


def _env_tristate() -> bool | None:
    """
    Interpret ``SELVEDGE_TELEMETRY``: truthy → ``True`` (consent),
    explicit falsy (``0``/``false``/``no``/``off``) → ``False`` (kill),
    unset/anything else → ``None`` (defer to the consent file).
    """
    raw = os.environ.get("SELVEDGE_TELEMETRY")
    if raw is None:
        return None
    val = raw.strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    return None


def is_enabled() -> bool:
    """
    True iff telemetry consent is currently in effect.

    Precedence: kill-switch env vars (:data:`KILL_ENV_VARS`, or
    ``SELVEDGE_TELEMETRY=0``) → ``SELVEDGE_TELEMETRY=1`` → the recorded
    consent in ``~/.selvedge/telemetry.json`` → default **False**.

    Note this is *consent*, not *will send now* — :func:`should_send`
    additionally applies the CI, dev-install, and 24h-interval gates.
    """
    for var in KILL_ENV_VARS:
        if _env_truthy(var):
            return False
    env = _env_tristate()
    if env is not None:
        return env
    return _read_state().get("enabled") is True


def should_send(*, now: float | None = None) -> bool:
    """
    Central gate for the heartbeat. True iff **all** of the following hold:

    * :func:`is_enabled` — explicit consent, no kill switch.
    * ``CI`` is not truthy in the environment (CI fleets would inflate
      the active-install signal).
    * The current install is not a dev build.
    * An ``install_id`` exists or the state file is writable enough to
      mint one.
    * The last ping attempt was more than :data:`PING_INTERVAL_SECONDS` ago.

    ``now`` is injectable for testing; defaults to ``time.time()``.
    """
    if not is_enabled():
        return False
    if _env_truthy("CI"):
        return False
    if _is_dev_install(__version__):
        return False

    now = now if now is not None else time.time()
    last = _read_state().get("last_ping_epoch", 0)
    if isinstance(last, (int, float)) and now - last < PING_INTERVAL_SECONDS:
        return False
    return True


# --------------------------------------------------------------------------- #
# Payload
# --------------------------------------------------------------------------- #


def _load_or_mint_install_id() -> str | None:
    """
    Return the recorded ``install_id``, minting and persisting a fresh
    UUID4 if consent came from the environment and no file id exists yet.
    Returns ``None`` if no id exists and the state file is unwritable —
    an unstable per-process id would corrupt the distinct-install count,
    so in that case we simply don't send.
    """
    state = _read_state()
    existing = state.get("install_id")
    if isinstance(existing, str) and existing:
        return existing
    fresh = str(uuid.uuid4())
    state["install_id"] = fresh
    return fresh if _write_state(state) else None


def build_payload(source: str) -> dict[str, object] | None:
    """
    Build the exact heartbeat payload, or ``None`` if no stable
    ``install_id`` is available.

    ``source`` is ``"cli"`` or ``"server"`` — the only usage dimension
    collected. Shown verbatim by ``selvedge telemetry status`` so users
    can audit what leaves the machine before anything ever does.
    """
    install_id = _load_or_mint_install_id()
    if install_id is None:
        return None
    return {
        "schema": PAYLOAD_SCHEMA_VERSION,
        "install_id": install_id,
        "version": __version__,
        "python": ".".join(str(part) for part in sys.version_info[:2]),
        "os": sys.platform,
        "arch": platform.machine() or "unknown",
        "source": source,
    }


# --------------------------------------------------------------------------- #
# Network send
# --------------------------------------------------------------------------- #


def _endpoint() -> str:
    """Resolve the endpoint, honoring the ``SELVEDGE_TELEMETRY_URL`` override."""
    return os.environ.get("SELVEDGE_TELEMETRY_URL", "").strip() or TELEMETRY_URL


def _send(payload: dict[str, object], *, timeout: float = SEND_TIMEOUT_SECONDS) -> bool:
    """
    POST the heartbeat. Returns success as a bool and never raises.
    No retries by design — a lost heartbeat costs nothing.
    """
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _endpoint(),
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"selvedge/{__version__}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https URL)
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False
    except Exception:
        # Belt-and-suspenders, same posture as update_check: telemetry
        # must never take down the command the user actually ran.
        return False


def _run_ping(source: str) -> None:
    """Thread body — gate, stamp, build, send. All exceptions swallowed."""
    try:
        if not should_send():
            return
        # Stamp before sending: a failing endpoint must not turn every
        # invocation into a fresh network attempt.
        state = _read_state()
        state["last_ping_epoch"] = time.time()
        _write_state(state)

        payload = build_payload(source)
        if payload is not None:
            _send(payload)
    except Exception:
        return


def ping_async(source: str) -> None:
    """
    Fire-and-forget heartbeat.

    Spawns a daemon thread the first time it's called in a process;
    subsequent calls are no-ops. Returns immediately. Safe to call from
    both ``selvedge/cli.py`` and ``selvedge/server.py`` — unlike the
    update check, this module never writes to stdout or stderr, so it
    cannot pollute the MCP stdio channel. All gating happens inside the
    thread so callers don't have to know the rules.
    """
    global _ping_started
    with _state_lock:
        if _ping_started:
            return
        _ping_started = True

    try:
        t = threading.Thread(target=_run_ping, args=(source,), name="selvedge-telemetry", daemon=True)
        t.start()
    except RuntimeError:
        # Some sandboxes disallow thread creation. Fine — no ping.
        return


# --------------------------------------------------------------------------- #
# Consent management (the CLI's backend)
# --------------------------------------------------------------------------- #


def enable() -> dict:
    """
    Record consent and mint a fresh ``install_id``.

    Returns the resulting state dict (also written to disk). Idempotent:
    enabling while already enabled keeps the existing ``install_id`` so
    the install isn't double-counted.
    """
    state = _read_state()
    if not (isinstance(state.get("install_id"), str) and state.get("install_id")):
        state["install_id"] = str(uuid.uuid4())
    state["enabled"] = True
    state["enabled_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write_state(state)
    return state


def disable() -> dict:
    """
    Withdraw consent and delete the ``install_id``.

    Deleting the id is the point: re-enabling later mints a fresh UUID,
    so a disable is a hard break in the data — nothing links the two
    consent periods.
    """
    state = _read_state()
    state["enabled"] = False
    state.pop("install_id", None)
    state.pop("enabled_at", None)
    state.pop("last_ping_epoch", None)
    _write_state(state)
    return state


def consent_state() -> dict[str, object]:
    """
    Full status snapshot for ``selvedge telemetry status``.

    Every field always populated, never ``None``-typed surprises — same
    result-shape convention as the MCP tools (empty string for absent).
    """
    state = _read_state()
    env = _env_tristate()
    killed = any(_env_truthy(var) for var in KILL_ENV_VARS) or env is False
    install_id = state.get("install_id")
    last = state.get("last_ping_epoch")
    return {
        "enabled": is_enabled(),
        "consent_source": (
            "env" if (env is not None or killed) else ("file" if "enabled" in state else "default")
        ),
        "install_id": install_id if isinstance(install_id, str) else "",
        "last_ping": (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last))
            if isinstance(last, (int, float)) and last > 0
            else ""
        ),
        "endpoint": _endpoint(),
        "consent_file": str(_consent_path()),
        "payload_preview": build_payload("cli") if is_enabled() else {},
    }


def _reset_for_testing() -> None:
    """Reset module-level state. Test-only — do not call from production code."""
    global _ping_started
    with _state_lock:
        _ping_started = False
