"""``selvedge prune`` — bound the store by trimming old rows.

``tool_calls`` prunes by default on a 90-day window. The **events** table is
gated behind ``--include-events`` and, per the cross-cutting risk register in
``docs/architecture.md``, requires BOTH an interactive confirmation AND
``SELVEDGE_DESTRUCTIVE=1`` in the environment (v0.3.10).

Two gates rather than one because either alone has a known bypass. A
confirmation prompt is defeated by ``--yes`` in a cron entry — the classic
footgun, and the reason ``test_cron_footgun_yes_without_destructive_env_errors``
exists and is named explicitly so a future test-suite cleanup can't quietly
retire it. An env var alone is defeated by a shell profile that exports it
once and forgets. Requiring both means a destructive run has to be deliberate
in two independent places.

``retention_days_events`` defaults to *infinity*: the reasoning behind a
change is the one thing this tool exists to preserve, so deleting it is
always opt-in.

Every prune appends one line to ``.selvedge/prune.log`` so the cadence
is visible later: ``<utc-iso>\\t<count_pruned>\\t<days_threshold>``.
The format mirrors the post-commit ``.selvedge/hook.log`` written by
``diagnostics.hook_log_path()`` — same tab-separated shape, same readability
constraints.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .storage import SelvedgeStorage
from .timeutil import normalize_timestamp

# Fallback when nothing is configured. The live value comes from
# ``retention_days_tool_calls`` in ``.selvedge/config.toml`` (v0.3.10); this
# constant is what that setting defaults to. 90 days is long enough that the
# previous month's agents are still in the data, per the Phase 2.12 risk note.
DEFAULT_DAYS = 90

#: Environment gate for anything that deletes events. Deliberately not a CLI
#: flag: a flag would live in the same command line as ``--yes``, so one
#: careless cron entry could satisfy both gates at once.
DESTRUCTIVE_ENV = "SELVEDGE_DESTRUCTIVE"

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


class DestructiveNotPermitted(RuntimeError):
    """Raised when an events prune is attempted without both gates satisfied.

    An exception rather than a return value on purpose: the caller must not be
    able to ignore it and proceed to delete.
    """


def destructive_allowed(env: dict | None = None) -> bool:
    """True when ``SELVEDGE_DESTRUCTIVE=1`` is set in the environment."""
    import os

    source = os.environ if env is None else env
    return source.get(DESTRUCTIVE_ENV) == "1"


def require_destructive_env(env: dict | None = None) -> None:
    """Raise unless the environment gate is satisfied.

    Called *after* the interactive confirmation so a cron job that passes
    ``--yes`` still stops here — which is the whole point of having two gates
    that live in different places.
    """
    if not destructive_allowed(env):
        raise DestructiveNotPermitted(
            f"deleting events requires {DESTRUCTIVE_ENV}=1 in the environment, "
            "in addition to confirmation. This is deliberate: --yes alone must "
            "never be enough to destroy captured reasoning from a cron job."
        )


def append_events_log_line(
    log_path: Path,
    pruned: int,
    days: int,
    when: datetime | None = None,
) -> None:
    """Append one audit line for an events prune.

    Same tab-separated shape as the tool_calls line with an ``events`` marker,
    so ``prune.log`` stays one readable file and a destructive run is
    distinguishable from routine telemetry trimming at a glance.
    """
    stamp = (when or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    if stamp.endswith("+00:00"):
        stamp = stamp[:-6] + "Z"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp}\tevents\t{pruned}\t{days}\n")
    except OSError:
        return


def run_events_prune(
    db_path: Path,
    days: int,
    now: datetime | None = None,
    env: dict | None = None,
) -> PruneResult:
    """Delete events older than ``days``. Both gates must already be satisfied.

    The environment gate is re-checked here rather than trusted from the
    caller — this function is the last thing between a mistake and deleted
    reasoning, so it does not take anyone's word for it.

    ``days <= 0`` means unlimited retention and deletes nothing; that is the
    default, and it is not an error to ask for it.
    """
    require_destructive_env(env)

    log_path = prune_log_path(db_path)
    if days <= 0:
        append_events_log_line(log_path, 0, days, when=now)
        return PruneResult(pruned=0, days_threshold=days, cutoff="", log_path=log_path)

    cutoff = compute_cutoff(days, now=now)
    pruned = SelvedgeStorage(db_path).prune_events(cutoff)
    append_events_log_line(log_path, pruned, days, when=now)
    return PruneResult(
        pruned=pruned, days_threshold=days, cutoff=cutoff, log_path=log_path
    )
