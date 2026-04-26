"""
Live tail of newly-logged Selvedge events.

Polls the SQLite store at a configurable interval and renders each new
event as it lands. Use case: trust-but-verify for users who want to see
what their agent is actually capturing in real time, or a debugging
surface that's nicer than running ``selvedge status`` repeatedly.

Why polling instead of inotify or pub/sub:

  - **Zero new dependencies.** Selvedge's install footprint stays at
    ``mcp + click + rich``. inotify/watchdog would add a wheel-only
    dependency for a CLI feature that runs in foreground.
  - **WAL-friendly.** SQLite WAL mode means a polling SELECT never
    blocks the writer. The cost of a 1-second poll is one indexed
    query against ``timestamp`` per second — negligible.
  - **Cross-platform.** inotify is Linux-only; the watchdog wrapper
    has flaky semantics on macOS+Network volumes. Polling Just Works.

The loop is interruptible via Ctrl-C and exits cleanly with status 0.
On ``database is locked`` errors the storage layer's connection-with-
retry handles the transient case; only persistent failures bubble up
to the user.
"""

from __future__ import annotations

import json
import signal
import time
from collections.abc import Callable

from rich.console import Console

from .config import get_db_path
from .storage import SelvedgeStorage
from .timeutil import utc_now_iso

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default poll interval. One second matches developer expectation for
#: "live tail" feedback (same cadence as ``tail -f`` on most terminals).
#: Below ~250ms the cost shows up in CPU/IO without changing perceived
#: responsiveness; above ~5s feels laggy.
DEFAULT_POLL_INTERVAL_SECONDS: float = 1.0

#: Hard floor on poll interval. Lower than this and we're spinning.
MIN_POLL_INTERVAL_SECONDS: float = 0.1

#: Hard ceiling — any longer and the user should be using
#: ``selvedge history --since 1h`` instead of a live tail.
MAX_POLL_INTERVAL_SECONDS: float = 60.0


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def _matches_filters(
    row: dict,
    *,
    entity_path: str = "",
    project: str = "",
    agent: str = "",
) -> bool:
    """Return True if ``row`` passes all CLI filters.

    These mirror ``selvedge history`` exactly so users don't have a
    second filter vocabulary to learn. ``entity_path`` is prefix-aware
    (an exact match OR ``<entity_path>.<anything>``).
    """
    if entity_path:
        ep = row.get("entity_path", "")
        if ep != entity_path and not ep.startswith(entity_path + "."):
            return False
    if project and row.get("project", "") != project:
        return False
    if agent and row.get("agent", "") != agent:
        return False
    return True


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_event_row(console: Console, row: dict) -> None:
    """Print a single event as a one-line summary, Rich-formatted.

    One line per event keeps the watch output greppable and scrollable
    in long sessions. The reasoning is truncated to keep the line under
    a typical terminal width — ``selvedge blame <entity>`` recovers the
    full text when it matters.
    """
    ts = (row.get("timestamp", "") or "")[:19].replace("T", " ")
    entity = row.get("entity_path", "")
    change = row.get("change_type", "")
    agent = row.get("agent", "") or ""
    reasoning = (row.get("reasoning", "") or "").strip()
    if len(reasoning) > 70:
        reasoning = reasoning[:67] + "…"

    parts = [
        f"[dim]{ts}[/dim]",
        f"[green]{change}[/green]",
        f"[bold cyan]{entity}[/bold cyan]",
    ]
    if agent:
        parts.append(f"[magenta]{agent}[/magenta]")
    if reasoning:
        parts.append(f"[dim]{reasoning}[/dim]")
    console.print("  " + "  ".join(parts))


def _render_event_json(console: Console, row: dict) -> None:
    """Emit one JSON object per line — the unix-pipe-friendly path.

    ``soft_wrap=True`` is critical: Rich's default ``Console.print``
    wraps at the terminal width, which would split a long event payload
    across multiple lines and break ``jq``, ``grep -F``, or any other
    line-oriented downstream tool. Soft-wrap preserves the single-line
    contract.
    """
    console.print(
        json.dumps(row, separators=(",", ":")), highlight=False, soft_wrap=True
    )


# ---------------------------------------------------------------------------
# Public entry — drives the poll loop
# ---------------------------------------------------------------------------


def watch(
    *,
    since: str = "",
    entity_path: str = "",
    project: str = "",
    agent: str = "",
    interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    as_json: bool = False,
    console: Console | None = None,
    storage: SelvedgeStorage | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_iterations: int | None = None,
) -> int:
    """Run the live-tail loop. Returns 0 on Ctrl-C, non-zero on error.

    Most parameters mirror ``selvedge history`` — same vocabulary, same
    semantics — so users can move from history-then-poll to live-tail
    without rebuilding their mental model. ``as_json`` flips rendering
    to one-JSON-object-per-line for piping into other tools.

    Test seams:

      - ``console`` lets tests capture rendered output
      - ``storage`` lets tests inject a fixture-backed DB without
        touching the real one
      - ``sleep`` lets tests skip real wall-clock waits
      - ``max_iterations`` lets tests exit deterministically after N
        polls instead of needing to send SIGINT into a subprocess
    """
    if not (MIN_POLL_INTERVAL_SECONDS <= interval <= MAX_POLL_INTERVAL_SECONDS):
        # Clamp loudly — silent clamping hides usability bugs (people
        # try ``--interval 0.01`` and assume the rest of the command
        # works while CPU is on fire).
        raise ValueError(
            f"--interval must be between {MIN_POLL_INTERVAL_SECONDS} and "
            f"{MAX_POLL_INTERVAL_SECONDS} seconds (got {interval})"
        )

    console = console or Console()
    if storage is None:
        storage = SelvedgeStorage(get_db_path())

    # Cursor: events with timestamp strictly greater than this are
    # new since the last poll. Initialize to ``since`` if provided
    # (so ``--since 1h`` also serves as a "catch up first, then tail"
    # convenience), or to "now" so the user only sees events going
    # forward from invocation time.
    cursor = since or utc_now_iso()

    # Render any catch-up events from the initial ``since`` window in
    # chronological order so the watch session doesn't open with empty
    # silence when the user explicitly asked for backfill.
    if since:
        catchup = storage.get_history(since=since, limit=10_000)
        # get_history returns newest-first; flip for time-ordered
        # display, mirroring how new events stream in below.
        for row in reversed(catchup):
            if not _matches_filters(
                row, entity_path=entity_path, project=project, agent=agent
            ):
                continue
            _emit(console, row, as_json=as_json)
            cursor = row.get("timestamp", cursor)

    # Trap SIGINT into a graceful exit — tells the user we heard them
    # and prevents Click from printing a Python traceback for what is
    # the documented way to leave watch.
    interrupted = {"flag": False}

    def _handle_sigint(signum: int, frame: object | None) -> None:  # noqa: ARG001
        interrupted["flag"] = True

    previous = signal.signal(signal.SIGINT, _handle_sigint)
    try:
        iteration = 0
        while not interrupted["flag"]:
            new_rows = _poll_once(
                storage,
                cursor=cursor,
                entity_path=entity_path,
                project=project,
                agent=agent,
            )
            for row in new_rows:
                _emit(console, row, as_json=as_json)
                cursor = row.get("timestamp", cursor)

            iteration += 1
            if max_iterations is not None and iteration >= max_iterations:
                break

            sleep(interval)
    finally:
        signal.signal(signal.SIGINT, previous)

    return 0


def _poll_once(
    storage: SelvedgeStorage,
    *,
    cursor: str,
    entity_path: str,
    project: str,
    agent: str,
) -> list[dict]:
    """Return new events strictly after ``cursor``, oldest-first.

    Reuses ``get_history`` (``timestamp >= since``) and filters out the
    cursor row itself, so we don't re-emit the most recent event on
    every poll. Filtering by ``agent`` is done in Python because
    ``get_history`` doesn't take an agent filter — agents are sparse,
    so the post-filter cost is trivial.
    """
    rows = storage.get_history(
        since=cursor,
        entity_path=entity_path,
        project=project,
        limit=10_000,
    )
    # ``get_history`` is newest-first and inclusive on ``since``. Flip to
    # oldest-first and drop the inclusive boundary.
    fresh = [r for r in reversed(rows) if r.get("timestamp", "") > cursor]
    if agent:
        fresh = [r for r in fresh if r.get("agent", "") == agent]
    return fresh


def _emit(console: Console, row: dict, *, as_json: bool) -> None:
    if as_json:
        _render_event_json(console, row)
    else:
        _render_event_row(console, row)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def render_header(
    console: Console,
    *,
    db_path: str,
    interval: float,
    since: str,
    entity_path: str,
    project: str,
    agent: str,
) -> None:
    """Render the one-line "watching ..." preamble.

    Surfaces the active filters and the polling cadence so the user can
    confirm at a glance that they typed the right command. Also makes
    a recorded screencast of the watch session self-documenting.
    """
    parts = [
        f"[bold]Watching[/bold] [dim]{db_path}[/dim]",
        f"[dim]every {interval}s[/dim]",
    ]
    if since:
        parts.append(f"[dim]since {since}[/dim]")
    filter_parts: list[str] = []
    if entity_path:
        filter_parts.append(f"entity={entity_path}")
    if project:
        filter_parts.append(f"project={project}")
    if agent:
        filter_parts.append(f"agent={agent}")
    if filter_parts:
        parts.append("[dim]filters: " + ", ".join(filter_parts) + "[/dim]")
    console.print(" · ".join(parts))
    console.print("  [dim](Ctrl-C to exit)[/dim]\n")
