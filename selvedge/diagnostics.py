"""Diagnostic engine behind ``selvedge doctor``.

Every other multi-step subsystem in this package — ``verify``, ``backup``,
``prune``, ``setup``, ``watch``, ``prompt`` — already lives in its own module
with a thin Rich renderer in ``cli.py``. ``doctor`` was the sole outlier: 350
lines of pure ``list[dict]`` diagnostic engine with zero Click involvement,
sitting in the middle of the command file.

That cost more than tidiness. ``run_checks`` read ``HOOK_MARKER``, which was
defined 1643 lines further down ``cli.py`` — a forward reference that happened
to resolve only because it was read at call time, not import time. And
``tests/test_doctor.py`` had to import a private ``cli._doctor_checks``,
because there was no public module to import.

The check rows are deliberately plain dicts (``label`` / ``status`` /
``detail``), not a new result type: ``cli.py`` renders them with Rich and
``--json`` dumps them verbatim, and neither needs more than that.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from . import backup as backup_mod
from . import prune as prune_mod
from .config import (
    get_db_path,
    get_setting,
    global_config_path,
    project_config_path,
    resolve_all_settings,
    resolve_db_path,
)
from .logging_config import LOG_LEVEL_ENV
from .migrations import MIGRATIONS, get_applied_versions, latest_version
from .redaction import scan_store_for_secrets
from .storage import SelvedgeStorage

# The sentinel line `selvedge install-hook` writes into `.git/hooks/post-commit`
# so both writing and detecting it agree on one string. Defined here rather
# than in `cli.py` because the doctor check below is the reader and this module
# is the lower of the two — importing the other way would be a cycle.
HOOK_MARKER = "# Selvedge post-commit hook"

# Recognized log levels for the SELVEDGE_LOG_LEVEL env var. Doctor-only: it
# surfaces a typo'd value, which `configure_logging` silently coerces to
# WARNING — easy to miss without doctor flagging it.
_KNOWN_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

HOOK_LOG_NAME = "hook.log"


def hook_log_path() -> Path:
    """Path to the post-commit hook's failure log inside ``.selvedge/``."""
    return get_db_path().parent / HOOK_LOG_NAME


def last_hook_failure() -> str | None:
    """
    Return the last line of the post-commit hook failure log, or None.

    Each failure writes one line in the form ``<utc-iso>\\t<message>``.
    Read the tail rather than the whole file so a chatty hook history
    doesn't blow up `selvedge doctor` or `selvedge status`.
    """
    p = hook_log_path()
    if not p.exists():
        return None
    try:
        # Hook log is line-oriented and small in practice — readlines is fine.
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return lines[-1].strip() if lines else None


def _storage() -> SelvedgeStorage:
    """A storage handle on the resolved database."""
    return SelvedgeStorage(get_db_path())


# Maximum age (in seconds) for the last tool_calls entry to count as
# "recent" — a fresher entry suggests the MCP server is actually wired up
# to this DB right now. Tuned for typical agent-pool cadence: anything
# inside a week feels live, anything older suggests stale wiring.
_RECENT_TOOL_CALL_SECONDS = 7 * 24 * 60 * 60


def check_row(label: str, status: str, detail: str = "") -> dict:
    """Build a single doctor check row, used for both Rich and JSON output."""
    return {"label": label, "status": status, "detail": detail}


def run_checks() -> list[dict]:
    """
    Run all doctor checks and return them in display order.

    Each check is a dict with ``label``, ``status`` (PASS/WARN/FAIL/INFO),
    and ``detail`` (free-form one-liner). Failures don't raise — a single
    broken check shouldn't prevent the rest from running, since the whole
    point of `doctor` is to give the user a complete picture in one shot.
    """
    from datetime import datetime, timezone

    checks: list[dict] = []

    # 1. DB path resolution chain
    resolved = resolve_db_path()
    source_label = {
        "env": "SELVEDGE_DB env var",
        "walkup": "walked up from CWD to project .selvedge/",
        "global": "global fallback (~/.selvedge/)",
    }[resolved.source]
    db_status = "INFO" if resolved.source != "global" else "WARN"
    db_detail = f"{resolved.path}  [via {source_label}]"
    checks.append(check_row("Database path", db_status, db_detail))

    # 2. .selvedge/ existence
    selvedge_dir = resolved.path.parent
    if selvedge_dir.is_dir():
        checks.append(check_row(
            ".selvedge/ directory", "PASS", str(selvedge_dir)
        ))
    else:
        checks.append(check_row(
            ".selvedge/ directory", "FAIL",
            "missing — run `selvedge init` in your project root"
        ))

    # 3. Schema migration version (+ downgrade detection)
    db_exists = resolved.path.is_file()
    if not db_exists:
        checks.append(check_row(
            "Schema version", "WARN",
            "DB file does not exist yet — first write will create it"
        ))
    else:
        try:
            with sqlite3.connect(resolved.path) as conn:
                applied = get_applied_versions(conn)
            declared = {m.version for m in MIGRATIONS}
            target = latest_version()
            missing = sorted(declared - applied)
            extra = sorted(applied - declared)
            if extra:
                # The DB was last opened by a newer Selvedge that knew about
                # migrations this version doesn't. Downgrading is not
                # supported — surface as FAIL so users notice before any
                # write attempts schema work it doesn't understand.
                checks.append(check_row(
                    "Schema version", "FAIL",
                    f"DB has applied migration(s) {extra} that this Selvedge "
                    f"build does not declare — likely opened by a newer version "
                    f"and downgraded; downgrades are not supported"
                ))
            elif not missing:
                checks.append(check_row(
                    "Schema version", "PASS",
                    f"at v{target} (latest)"
                ))
            else:
                checks.append(check_row(
                    "Schema version", "WARN",
                    f"missing migration(s) {missing} — they will apply on next "
                    f"connection through SelvedgeStorage"
                ))
        except sqlite3.Error as e:
            checks.append(check_row("Schema version", "FAIL", f"sqlite error: {e}"))

    # 4. Post-commit hook installed?
    hook_path = Path.cwd() / ".git" / "hooks" / "post-commit"
    if not (Path.cwd() / ".git").exists():
        checks.append(check_row(
            "Post-commit hook", "INFO",
            "not in a git repo — skipping hook check"
        ))
    elif not hook_path.exists():
        checks.append(check_row(
            "Post-commit hook", "WARN",
            "not installed — run `selvedge install-hook` to auto-stamp git_commit"
        ))
    else:
        contents = hook_path.read_text(errors="replace")
        if HOOK_MARKER in contents:
            checks.append(check_row(
                "Post-commit hook", "PASS", str(hook_path)
            ))
        else:
            checks.append(check_row(
                "Post-commit hook", "WARN",
                f"hook exists at {hook_path} but does not contain Selvedge"
            ))

    # 5. Hook failure log — surface the most recent failure if any
    last_fail = last_hook_failure()
    if last_fail:
        checks.append(check_row(
            "Last hook failure", "WARN", last_fail
        ))
    else:
        checks.append(check_row(
            "Last hook failure", "PASS", "no failures recorded"
        ))

    # 6. Last tool_calls entry — proxy for "is the MCP server wired up"
    if not db_exists:
        checks.append(check_row(
            "MCP wiring", "WARN", "no DB yet — connect the MCP server and try again"
        ))
    else:
        try:
            storage = _storage()
            last_ts = storage.get_last_tool_call_timestamp()
        except sqlite3.Error as e:
            checks.append(check_row("MCP wiring", "FAIL", f"sqlite error: {e}"))
        else:
            if last_ts is None:
                checks.append(check_row(
                    "MCP wiring", "WARN",
                    "no tool_calls recorded — run `selvedge setup` to wire "
                    "Selvedge into your AI tools, or restart your agent if "
                    "an MCP entry is already installed"
                ))
            else:
                try:
                    parsed = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - parsed).total_seconds()
                    if age <= _RECENT_TOOL_CALL_SECONDS:
                        checks.append(check_row(
                            "MCP wiring", "PASS", f"last tool_call at {last_ts}"
                        ))
                    else:
                        days = int(age // 86400)
                        checks.append(check_row(
                            "MCP wiring", "WARN",
                            f"last tool_call was {days}d ago ({last_ts}) — "
                            f"agent may have been disconnected"
                        ))
                except ValueError:
                    checks.append(check_row(
                        "MCP wiring", "WARN",
                        f"last tool_call timestamp unparseable: {last_ts}"
                    ))

    # 7. SELVEDGE_LOG_LEVEL value
    raw_level = os.environ.get(LOG_LEVEL_ENV)
    if raw_level is None:
        checks.append(check_row(
            f"{LOG_LEVEL_ENV}", "INFO", "unset (defaults to WARNING)"
        ))
    elif raw_level.upper().strip() in _KNOWN_LOG_LEVELS:
        checks.append(check_row(
            f"{LOG_LEVEL_ENV}", "PASS", raw_level
        ))
    else:
        checks.append(check_row(
            f"{LOG_LEVEL_ENV}", "WARN",
            f"unrecognized value {raw_level!r} — silently treated as WARNING; "
            f"valid: {', '.join(_KNOWN_LOG_LEVELS)}"
        ))

    # 8. Last backup freshness. Severity ladder:
    #   • newest backup ≤7d old  → INFO
    #   • newest backup >7d old  → WARN
    #   • no backups + events table small → INFO (nothing precious to lose yet)
    #   • no backups + events table ≥10k rows → FAIL (precious; back up now)
    # 10k is the "real project" floor — small enough that most teams hit it
    # within weeks, large enough that a no-backups state at that size is a
    # genuine data-loss exposure rather than a CI/scratch DB.
    backups_dir = backup_mod.default_backups_dir(resolved.path)
    last_backup_dt = backup_mod.last_backup_time(backups_dir)
    if last_backup_dt is not None:
        age_td = datetime.now(timezone.utc) - last_backup_dt
        if age_td.days <= 7:
            checks.append(check_row(
                "Last backup", "INFO",
                f"{last_backup_dt.isoformat(timespec='seconds')} ({age_td.days}d ago)"
            ))
        else:
            checks.append(check_row(
                "Last backup", "WARN",
                f"{last_backup_dt.isoformat(timespec='seconds')} ({age_td.days}d ago) — "
                f"run `selvedge backup`"
            ))
    elif db_exists:
        try:
            event_count = SelvedgeStorage(resolved.path).count()
        except sqlite3.Error:
            event_count = 0
        if event_count >= 10_000:
            checks.append(check_row(
                "Last backup", "FAIL",
                f"no backups in {backups_dir} but events table has "
                f"{event_count:,} rows — run `selvedge backup` now"
            ))
        else:
            checks.append(check_row(
                "Last backup", "INFO",
                f"no backups yet ({event_count:,} events; threshold for FAIL is 10,000)"
            ))
    else:
        checks.append(check_row(
            "Last backup", "INFO", "no DB yet — nothing to back up"
        ))

    # 9. Last prune — parsed from .selvedge/prune.log if present.
    # An INFO row when the log exists, a softer INFO when it doesn't:
    # absence is not a failure, just a "you haven't run it yet" signal.
    log_path = prune_mod.prune_log_path(resolved.path)
    last_prune = prune_mod.last_prune_line(log_path)
    if last_prune is None:
        checks.append(check_row(
            "Last prune", "INFO",
            "no prune.log yet — run `selvedge prune` to start trimming "
            "tool_calls"
        ))
    else:
        ts, count, threshold = last_prune
        checks.append(check_row(
            "Last prune", "INFO",
            f"{ts}  ({count} row(s) pruned, {threshold}-day threshold)"
        ))

    # 10. tool_calls row count — WARN when oversized. Counter is
    # exposed as its own storage method so tests can stub it without
    # actually inserting 100k rows.
    if db_exists:
        try:
            tc_count = SelvedgeStorage(resolved.path).count_tool_calls()
        except sqlite3.Error as e:
            checks.append(check_row(
                "tool_calls size", "FAIL", f"sqlite error: {e}"
            ))
        else:
            if tc_count > prune_mod.TOOL_CALLS_WARN_ROWS:
                checks.append(check_row(
                    "tool_calls size", "WARN",
                    f"{tc_count:,} rows (threshold {prune_mod.TOOL_CALLS_WARN_ROWS:,}) — "
                    f"run `selvedge prune` to trim old telemetry"
                ))
            else:
                checks.append(check_row(
                    "tool_calls size", "PASS",
                    f"{tc_count:,} rows (threshold {prune_mod.TOOL_CALLS_WARN_ROWS:,})"
                ))
    else:
        checks.append(check_row(
            "tool_calls size", "INFO", "no DB yet"
        ))

    # 11. Case-collision watch — sibling entity paths that differ only by
    # case. Canonicalization preserves case on purpose (most Linux
    # filesystems are case-sensitive), so these coexist rather than being
    # merged. WARN when present so an unintended typo collision is visible.
    if db_exists:
        try:
            collisions = SelvedgeStorage(resolved.path).find_case_collisions()
        except sqlite3.Error as e:
            checks.append(check_row("Case collisions", "FAIL", f"sqlite error: {e}"))
        else:
            if collisions:
                sample = "; ".join(" vs ".join(c["paths"]) for c in collisions[:3])
                more = f" (+{len(collisions) - 3} more)" if len(collisions) > 3 else ""
                checks.append(check_row(
                    "Case collisions", "WARN",
                    f"{len(collisions)} group(s) of paths differing only by case: "
                    f"{sample}{more} — kept distinct (case-sensitive hosts); "
                    f"reconcile if unintended"
                ))
            else:
                checks.append(check_row(
                    "Case collisions", "PASS",
                    "no sibling paths differ only by case"
                ))
    else:
        checks.append(check_row("Case collisions", "INFO", "no DB yet"))

    # 12. Entity-path migration — surface the last migrate-paths --apply run.
    if db_exists:
        try:
            last_mig = SelvedgeStorage(resolved.path).get_last_path_migration()
        except sqlite3.Error as e:
            checks.append(check_row("Path migration", "FAIL", f"sqlite error: {e}"))
        else:
            if last_mig is None:
                checks.append(check_row(
                    "Path migration", "INFO",
                    "migrate-paths not applied — run `selvedge migrate-paths` "
                    "to preview re-canonicalization"
                ))
            else:
                checks.append(check_row(
                    "Path migration", "INFO",
                    f"{last_mig['timestamp']}  ({last_mig['rows_rewritten']} "
                    f"row(s) rewritten, {last_mig['collisions']} collision(s))"
                ))
    else:
        checks.append(check_row("Path migration", "INFO", "no DB yet"))

    # 13. Stale decisions (v0.3.8) — dated decisions now due for a revisit that
    # are still in active use. Deliberately INFO-tier, NOT WARN: a decision
    # aging out is a nudge, not a fault. Part of this release's signal-to-noise
    # curation pass — keeping the new row out of the WARN count is how the net
    # warning total stays flat (the existing WARN rows were each reviewed and
    # still fire usefully, so none were demoted to compensate).
    if db_exists:
        try:
            stale = SelvedgeStorage(resolved.path).get_stale_decisions(limit=100)
        except sqlite3.Error as e:
            checks.append(check_row("Stale decisions", "FAIL", f"sqlite error: {e}"))
        else:
            if stale:
                sample = ", ".join(s["entity_path"] for s in stale[:3])
                more = f" (+{len(stale) - 3} more)" if len(stale) > 3 else ""
                checks.append(check_row(
                    "Stale decisions", "INFO",
                    f"{len(stale)} decision(s) due for revisit: {sample}{more} — "
                    f"run `selvedge stale` to review"
                ))
            else:
                checks.append(check_row(
                    "Stale decisions", "INFO",
                    "none due for revisit (a decision surfaces here once its "
                    "revisit_after passes and the entity is still in active use)"
                ))
    else:
        checks.append(check_row("Stale decisions", "INFO", "no DB yet"))

    checks.extend(_db_size_checks(resolved.path))
    checks.extend(_config_precedence_checks())
    checks.extend(_redaction_scan_checks(resolved.path))

    return checks


def _db_size_checks(db_path: Path) -> list[dict]:
    """Warn once the store passes `db_size_warn_mb` (v0.3.10).

    SQLite handles a large file fine; the reason to surface it is that the
    store is meant to be committed alongside the repo, and a few hundred MB in
    version control is a problem long before it is a query problem.
    """
    warn_mb = get_setting("db_size_warn_mb")
    if not db_path.is_file():
        return [check_row("Database size", "INFO", "no DB yet")]
    try:
        size_mb = db_path.stat().st_size / (1024 * 1024)
    except OSError as e:
        return [check_row("Database size", "INFO", f"unreadable: {e}")]

    detail = f"{size_mb:.1f} MB"
    if warn_mb and size_mb >= warn_mb:
        return [check_row(
            "Database size", "WARN",
            f"{detail} — over the {warn_mb} MB warning threshold. "
            "`selvedge prune` drops old tool-call telemetry; see "
            "`db_size_warn_mb` in .selvedge/config.toml to change the threshold.",
        )]
    threshold = f"threshold {warn_mb} MB" if warn_mb else "no threshold set"
    return [check_row("Database size", "PASS", f"{detail} ({threshold})")]


def _config_precedence_checks() -> list[dict]:
    """One row per setting: effective value AND which step produced it.

    The same shape as the DB-path row above, and for the same reason — a
    user should be able to see *why* a setting has the value it does without
    reading the source or guessing whether their config file was even found.
    """
    rows = [check_row(
        "Config file",
        "INFO",
        f"project: {project_config_path()}"
        f"{'' if project_config_path().is_file() else ' (absent)'}"
        f" | global: {global_config_path()}"
        f"{'' if global_config_path().is_file() else ' (absent)'}",
    )]
    source_labels = {
        "flag": "CLI flag",
        "env": "env var",
        "project": "project config.toml",
        "global": "global config.toml",
        "default": "built-in default",
    }
    for name, resolved in resolve_all_settings().items():
        value = ",".join(resolved.value) if isinstance(resolved.value, list) else resolved.value
        shown = value if value != "" else "(none)"
        rows.append(check_row(
            f"config: {name}", "INFO",
            f"{shown}  [via {source_labels[resolved.source]}]",
        ))
    return rows


def _redaction_scan_checks(db_path: Path) -> list[dict]:
    """Report secret-shaped strings already sitting in the store (v0.3.10).

    The write-time check only sees new events. This is the retrospective
    half: the risk register's entry on verbatim reasoning in a committed
    store is only honestly mitigated if a user can find out whether anything
    already landed.
    """
    if not db_path.is_file():
        return [check_row("Secret-shaped content", "INFO", "no DB yet")]
    try:
        hits = scan_store_for_secrets(SelvedgeStorage(db_path))
    except sqlite3.Error as e:
        return [check_row("Secret-shaped content", "INFO", f"scan failed: {e}")]
    if not hits:
        return [check_row(
            "Secret-shaped content", "PASS",
            "no secret-shaped strings found in stored reasoning or diffs",
        )]
    shown = ", ".join(f"{h['event_id'][:8]}:{h['pattern']}" for h in hits[:3])
    more = f" (+{len(hits) - 3} more)" if len(hits) > 3 else ""
    return [check_row(
        "Secret-shaped content", "WARN",
        f"{len(hits)} event(s) contain secret-shaped strings — {shown}{more}. "
        "The store is committed alongside your repo; rotate anything real.",
    )]


