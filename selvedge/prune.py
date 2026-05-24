"""``selvedge prune`` — bound the noise table by trimming old ``tool_calls`` rows.

Only ``tool_calls`` is pruned in v0.3.6. The events table is off-limits
until ``.selvedge/config.toml`` lands in v0.3.10 — the destructive
events-prune path will require both ``SELVEDGE_DESTRUCTIVE=1`` and an
interactive confirmation prompt, per the cross-cutting risk register in
``docs/architecture.md``.

Every prune appends one line to ``.selvedge/prune.log`` so the cadence
is visible later: ``<utc-iso>\\t<count_pruned>\\t<days_threshold>``.
The format mirrors the post-commit ``.selvedge/hook.log`` written by
``cli.hook_log_path()`` — same tab-separated shape, same readability
constraints.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .storage import SelvedgeStorage
from .timeutil import normalize_timestamp

# Hardcoded for v0.3.6. Becomes ``retention_days_tool_calls`` in
# ``.selvedge/config.toml`` when the config file lands in v0.3.10. The
# 90-day default is long enough that the previous month's agents are
# still in the data, per the Phase 2.12 risk note.
DEFAULT_DAYS = 90

# Doctor WARNs above this row count in ``tool_calls``. Threshold is
# intentional — large enough that small projects don't hit it, low
# enough that an unattended prune-less project gets a nudge before the
# table starts eating noticeable disk. Revisit once v0.3.5/v0.3.6
# telemetry has bedded in.
TOOL_CALLS_WARN_ROWS = 100_000

PRUNE_LOG_NAME = "prune.log"


@dataclass(frozen=True)
class PruneResult:
    """Outcome of one ``selvedge prune`` invocation."""

    pruned: int
    days_threshold: int
    cutoff: str
    log_path: Path

    def to_dict(self) -> dict:
        return {
            "pruned": self.pruned,
            "days_threshold": self.days_threshold,
            "cutoff": self.cutoff,
            "log_path": str(self.log_path),
        }


def prune_log_path(db_path: Path) -> Path:
    """Path to ``.selvedge/prune.log`` next to the live DB."""
    return db_path.parent / PRUNE_LOG_NAME


def compute_cutoff(days: int, now: datetime | None = None) -> str:
    """Return the UTC ISO cutoff for ``days`` retention."""
    when = now or datetime.now(timezone.utc)
    return normalize_timestamp((when - timedelta(days=days)).isoformat())


def append_log_line(log_path: Path, pruned: int, days: int, when: datetime | None = None) -> None:
    """Append one tab-separated audit line to ``prune.log``.

    Tolerates a missing parent dir (creates it) and best-effort swallows
    write errors — losing one audit line is preferable to failing the
    prune itself.
    """
    stamp = (when or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    if stamp.endswith("+00:00"):
        stamp = stamp[:-6] + "Z"
    line = f"{stamp}\t{pruned}\t{days}\n"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        return


def run_prune(
    db_path: Path,
    days: int = DEFAULT_DAYS,
    now: datetime | None = None,
) -> PruneResult:
    """Trim ``tool_calls`` rows older than ``days`` and log the run.

    Always writes to ``prune.log`` — even when ``pruned == 0`` — so a
    stable cadence is visible in the log tail and ``selvedge doctor``
    can surface the most recent run regardless of whether it deleted
    anything.
    """
    cutoff = compute_cutoff(days, now=now)
    storage = SelvedgeStorage(db_path)
    pruned = storage.prune_tool_calls(cutoff)

    log_path = prune_log_path(db_path)
    append_log_line(log_path, pruned, days, when=now)

    return PruneResult(
        pruned=pruned,
        days_threshold=days,
        cutoff=cutoff,
        log_path=log_path,
    )


def last_prune_line(log_path: Path) -> tuple[str, int, int] | None:
    """Parse the most recent ``.selvedge/prune.log`` line.

    Returns ``(timestamp, count_pruned, days_threshold)`` or ``None`` if
    the log doesn't exist, is empty, or the last line is unparseable.
    Used by ``selvedge doctor`` to render the "Last prune" row.
    """
    if not log_path.is_file():
        return None
    try:
        lines = [ln for ln in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except OSError:
        return None
    if not lines:
        return None
    parts = lines[-1].split("\t")
    if len(parts) < 3:
        return None
    try:
        return parts[0], int(parts[1]), int(parts[2])
    except ValueError:
        return None
