"""
Selvedge CLI.

Commands:
  selvedge setup              Interactive first-run wizard (v0.3.4)
  selvedge prompt             Print or install the canonical agent prompt
  selvedge watch              Live-tail of new events as they're logged
  selvedge init               Initialize Selvedge in the current project
  selvedge status             Show recent activity summary
  selvedge doctor             PASS/WARN/FAIL ambient-state health check
  selvedge diff <entity>      Change history for an entity
  selvedge blame <entity>     Most recent change + context for an entity
  selvedge history            Filtered history across all entities
  selvedge search <query>     Full-text search across all events
  selvedge prior-attempts     Prior attempts on an entity + inferred outcome
  selvedge index              Build the optional semantic embeddings index
  selvedge stale              Dated decisions now due for a revisit
  selvedge log                Manually log a change event
  selvedge supersede          Re-open a reverted decision (append-only)
  selvedge migrate-paths      Re-canonicalize stored entity paths (backfill)
  selvedge stats              Tool-call coverage report
  selvedge install-hook       Install git post-commit hook
  selvedge backfill-commit    Stamp git_commit on recent events
  selvedge import             Import migration files / git-history reverts
  selvedge export             Export history as JSON or CSV
  selvedge prune              Trim old tool_calls rows (90-day retention)
"""

import json
import os
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

import click
from rich import box
from rich.console import Console
from rich.table import Table

from . import backup as backup_mod
from . import prune as prune_mod
from . import telemetry as telemetry_mod
from . import update_check as update_check_mod
from . import verify as verify_mod
from .config import get_db_path, init_project, resolve_db_path
from .logging_config import LOG_LEVEL_ENV, configure_logging
from .migrations import MIGRATIONS, get_applied_versions, latest_version
from .models import ChangeEvent, ChangeType
from .storage import SelvedgeStorage
from .timeutil import normalize_revisit_after, parse_time_string, parse_window_minutes
from .validation import (
    check_entity_path_shape,
    check_reasoning_quality,
    check_revisit_nudge,
)

console = Console()
err_console = Console(stderr=True)


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_storage() -> SelvedgeStorage:
    return SelvedgeStorage(get_db_path())


def resolve_since(since: str) -> str:
    """
    Resolve a CLI ``--since`` value to a canonical UTC ISO timestamp,
    or exit with a friendly error if the input is unparseable.

    Empty input passes through as empty (the caller's no-op signal).
    """
    if not since:
        return ""
    try:
        return parse_time_string(since)
    except ValueError as e:
        err_console.print(f"[red]error:[/red] {e}")
        sys.exit(2)


def fmt_ts(ts: str) -> str:
    """Trim ISO timestamp to readable form."""
    return ts[:19].replace("T", " ") if ts else "—"


def render_summary(rows: list[dict], since: str = "") -> None:
    """
    Render a human-readable changelog grouped by changeset or session.

    Groups events by changeset_id (preferred) or session_id, then prints
    one block per group with a header line and a bulleted list of changes.
    Designed for incident response: `selvedge history --since 24h --summarize`.
    """
    if not rows:
        console.print("[yellow]No events found.[/yellow]")
        return

    # Group events: prefer changeset_id, fall back to session_id, then "ungrouped"
    from collections import defaultdict

    groups: dict[str, list[dict]] = defaultdict(list)
    for row in reversed(rows):  # oldest-first within groups
        key = row.get("changeset_id") or row.get("session_id") or "_ungrouped_"
        groups[key].append(row)

    period = f" since [bold]{since}[/bold]" if since else ""
    console.print(f"\n[bold]Changelog[/bold]{period}  [dim]({len(rows)} events, {len(groups)} group(s))[/dim]\n")

    for group_key, events in groups.items():
        # Header: group identity + time range + agent
        first, last = events[0], events[-1]
        agent = first.get("agent") or last.get("agent") or "unknown"
        t_from = fmt_ts(first["timestamp"])
        t_to   = fmt_ts(last["timestamp"])
        project = first.get("project") or ""

        if group_key == "_ungrouped_":
            label = "[dim]ungrouped[/dim]"
        elif first.get("changeset_id"):
            label = f"[bold cyan]changeset:[/bold cyan] [cyan]{group_key}[/cyan]"
        else:
            label = f"[bold]session:[/bold] [dim]{group_key[:16]}…[/dim]"

        header_parts = [label, f"[magenta]{agent}[/magenta]"]
        if project:
            header_parts.append(f"[dim]{project}[/dim]")
        header_parts.append(f"[dim]{t_from}[/dim]")
        if t_from != t_to:
            header_parts.append(f"[dim]→ {t_to}[/dim]")

        console.print("  " + "  ".join(header_parts))

        for ev in events:
            reasoning = ev.get("reasoning", "").strip()
            snippet = (reasoning[:80] + "…") if len(reasoning) > 80 else reasoning
            entity  = ev.get("entity_path", "")
            change  = ev.get("change_type", "")
            note    = f"  [dim]{snippet}[/dim]" if snippet else ""
            console.print(f"    [dim]·[/dim] [green]{change}[/green] [bold]{entity}[/bold]{note}")

        console.print()


def render_events(rows: list[dict], title: str = "") -> None:
    if not rows:
        console.print("[yellow]No events found.[/yellow]")
        return

    table = Table(
        title=title,
        box=box.SIMPLE_HEAD,
        show_lines=False,
        header_style="bold",
    )
    table.add_column("Timestamp", style="dim", no_wrap=True)
    table.add_column("Entity", style="bold cyan")
    table.add_column("Change", style="green")
    table.add_column("Agent", style="magenta")
    table.add_column("Reasoning")

    for row in rows:
        table.add_row(
            fmt_ts(row.get("timestamp", "")),
            row.get("entity_path", ""),
            row.get("change_type", ""),
            row.get("agent", "") or "—",
            row.get("reasoning", "") or "—",
        )
    console.print(table)


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(package_name="selvedge")
def cli():
    """Selvedge — change tracking for AI-era codebases."""
    # Configure structured logging once per CLI invocation. Verbosity is
    # controlled by SELVEDGE_LOG_LEVEL (default WARNING).
    configure_logging()

    # Background PyPI version check. Fires a daemon thread that fetches
    # the latest published version into ~/.selvedge/update_check.json
    # (at most once per 24h, gated by TTY / CI / suppression env vars).
    # The notice itself is printed on atexit so it appears after the
    # command's output rather than interleaved with it. All entry points
    # in update_check are no-raise — a network blip never affects the
    # CLI. Intentionally not wired into selvedge-server: see the module
    # docstring in selvedge/update_check.py.
    update_check_mod.check_for_update_async()
    update_check_mod.register_atexit_notice()

    # Opt-in anonymous heartbeat (off by default; `selvedge telemetry enable`).
    # Same no-raise posture as the update check; unlike it, telemetry never
    # writes to stdout/stderr, so the server fires it too. All gating —
    # consent, kill switches, CI, dev installs, the 24h interval — happens
    # inside the daemon thread. See selvedge/telemetry.py + docs/telemetry.md.
    telemetry_mod.ping_async("cli")


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--path", "-p", default=".", help="Project root (default: current directory)")
def init(path):
    """Initialize Selvedge in the current project."""
    root = Path(path).resolve()
    selvedge_dir = init_project(root)
    db_path = selvedge_dir / "selvedge.db"

    # Touch the DB to confirm it's writable
    SelvedgeStorage(db_path)

    # Pre-emptively keep backup snapshots out of git. v0.3.5 introduces
    # `.selvedge/backups/`; adding the line at init time avoids the
    # first-backup race on shared repos. ensure_gitignore_entry is
    # idempotent — re-running init on an existing project is safe.
    backup_mod.ensure_gitignore_entry(root)

    console.print("\n[bold green]✓ Selvedge initialized[/bold green]")
    console.print(f"  Directory:  [dim]{selvedge_dir}[/dim]")
    console.print(f"  Database:   [dim]{db_path}[/dim]")
    console.print()
    console.print("  [bold]Next step:[/bold] add Selvedge to your Claude Code MCP config:")
    console.print(
        """
  [dim]{
    "mcpServers": {
      "selvedge": {
        "command": "selvedge-server"
      }
    }
  }[/dim]
"""
    )
    console.print(
        "  Commit [bold].selvedge/[/bold] to share history with your team, "
        "or add it to .gitignore to keep it local."
    )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def status(as_json):
    """Show a summary of recent Selvedge activity."""
    storage = get_storage()
    total = storage.count()
    recent = storage.get_history(limit=5)
    missing_commit = storage.count_missing_git_commit()
    hook_failure = last_hook_failure()

    db_path = get_db_path()

    if as_json:
        # `status` is the command the quickstart leads with and the shape a
        # CI or dashboard job wants, so it gets --json like every other read
        # command. Every field always populated, never null — same convention
        # as the MCP result types.
        click.echo(json.dumps({
            "db_path": str(db_path),
            "total_events": total,
            "missing_git_commit": missing_commit,
            "last_hook_failure": hook_failure or "",
            "recent": recent,
            "diagnosis": [] if recent else _diagnose_empty_state(storage),
        }, indent=2))
        return

    console.print(f"\n[bold]Selvedge[/bold]  [dim]{db_path}[/dim]")
    console.print(f"  [bold]{total}[/bold] total events logged")
    if missing_commit:
        # Surface unstamped events so users notice when the post-commit hook
        # isn't installed (or didn't fire in time) — these are hard to
        # correlate back to code without a git_commit reference.
        console.print(
            f"  [yellow]{missing_commit}[/yellow] event(s) missing [bold]git_commit[/bold]  "
            "[dim]run `selvedge install-hook` to auto-stamp future commits[/dim]"
        )
    if hook_failure:
        # The post-commit hook silently dies if `selvedge` isn't on the
        # shell PATH that git launches. Surface the most recent failure
        # line here so the user notices instead of wondering why git_commit
        # values stopped landing.
        console.print(
            f"  [red]post-commit hook last failed:[/red] [dim]{hook_failure}[/dim]"
        )
    console.print()

    if not recent:
        # Differentiated empty-state — surface the most likely cause
        # of "no events" so the user knows what to do next instead of
        # staring at a generic message. Three branches:
        #   1. MCP entry installed somewhere but no tool_calls received
        #      → agent likely needs a restart to pick up the new config
        #   2. MCP entry NOT installed anywhere we can detect
        #      → user hasn't run setup yet
        #   3. We can't tell either way (no agents detected)
        #      → fall back to a friendly nudge toward `selvedge setup`
        diagnosis = _diagnose_empty_state(storage)
        for line in diagnosis:
            console.print(f"  {line}")
        return

    console.print("  [bold]Recent changes[/bold]")
    for row in recent:
        agent = f"[magenta]{row['agent']}[/magenta]  " if row.get("agent") else ""
        console.print(
            f"    [dim]{fmt_ts(row['timestamp'])}[/dim]  "
            f"[cyan]{row['entity_path']}[/cyan]  "
            f"[green]{row['change_type']}[/green]  "
            f"{agent}"
        )
    console.print()


# ---------------------------------------------------------------------------
# doctor (health check)
# ---------------------------------------------------------------------------


# Recognized log levels for the SELVEDGE_LOG_LEVEL env var. Used by the
# doctor command to surface a typo'd value (which is silently coerced to
# WARNING by configure_logging — easy to miss without doctor flagging it).
_KNOWN_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# Maximum age (in seconds) for the last tool_calls entry to count as
# "recent" — a fresher entry suggests the MCP server is actually wired up
# to this DB right now. Tuned for typical agent-pool cadence: anything
# inside a week feels live, anything older suggests stale wiring.
_RECENT_TOOL_CALL_SECONDS = 7 * 24 * 60 * 60


def _check(label: str, status: str, detail: str = "") -> dict:
    """Build a single doctor check row, used for both Rich and JSON output."""
    return {"label": label, "status": status, "detail": detail}


def _doctor_checks() -> list[dict]:
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
    checks.append(_check("Database path", db_status, db_detail))

    # 2. .selvedge/ existence
    selvedge_dir = resolved.path.parent
    if selvedge_dir.is_dir():
        checks.append(_check(
            ".selvedge/ directory", "PASS", str(selvedge_dir)
        ))
    else:
        checks.append(_check(
            ".selvedge/ directory", "FAIL",
            "missing — run `selvedge init` in your project root"
        ))

    # 3. Schema migration version (+ downgrade detection)
    db_exists = resolved.path.is_file()
    if not db_exists:
        checks.append(_check(
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
                checks.append(_check(
                    "Schema version", "FAIL",
                    f"DB has applied migration(s) {extra} that this Selvedge "
                    f"build does not declare — likely opened by a newer version "
                    f"and downgraded; downgrades are not supported"
                ))
            elif not missing:
                checks.append(_check(
                    "Schema version", "PASS",
                    f"at v{target} (latest)"
                ))
            else:
                checks.append(_check(
                    "Schema version", "WARN",
                    f"missing migration(s) {missing} — they will apply on next "
                    f"connection through SelvedgeStorage"
                ))
        except sqlite3.Error as e:
            checks.append(_check("Schema version", "FAIL", f"sqlite error: {e}"))

    # 4. Post-commit hook installed?
    hook_path = Path.cwd() / ".git" / "hooks" / "post-commit"
    if not (Path.cwd() / ".git").exists():
        checks.append(_check(
            "Post-commit hook", "INFO",
            "not in a git repo — skipping hook check"
        ))
    elif not hook_path.exists():
        checks.append(_check(
            "Post-commit hook", "WARN",
            "not installed — run `selvedge install-hook` to auto-stamp git_commit"
        ))
    else:
        contents = hook_path.read_text(errors="replace")
        if _HOOK_MARKER in contents:
            checks.append(_check(
                "Post-commit hook", "PASS", str(hook_path)
            ))
        else:
            checks.append(_check(
                "Post-commit hook", "WARN",
                f"hook exists at {hook_path} but does not contain Selvedge"
            ))

    # 5. Hook failure log — surface the most recent failure if any
    last_fail = last_hook_failure()
    if last_fail:
        checks.append(_check(
            "Last hook failure", "WARN", last_fail
        ))
    else:
        checks.append(_check(
            "Last hook failure", "PASS", "no failures recorded"
        ))

    # 6. Last tool_calls entry — proxy for "is the MCP server wired up"
    if not db_exists:
        checks.append(_check(
            "MCP wiring", "WARN", "no DB yet — connect the MCP server and try again"
        ))
    else:
        try:
            storage = get_storage()
            last_ts = storage.get_last_tool_call_timestamp()
        except sqlite3.Error as e:
            checks.append(_check("MCP wiring", "FAIL", f"sqlite error: {e}"))
        else:
            if last_ts is None:
                checks.append(_check(
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
                        checks.append(_check(
                            "MCP wiring", "PASS", f"last tool_call at {last_ts}"
                        ))
                    else:
                        days = int(age // 86400)
                        checks.append(_check(
                            "MCP wiring", "WARN",
                            f"last tool_call was {days}d ago ({last_ts}) — "
                            f"agent may have been disconnected"
                        ))
                except ValueError:
                    checks.append(_check(
                        "MCP wiring", "WARN",
                        f"last tool_call timestamp unparseable: {last_ts}"
                    ))

    # 7. SELVEDGE_LOG_LEVEL value
    raw_level = os.environ.get(LOG_LEVEL_ENV)
    if raw_level is None:
        checks.append(_check(
            f"{LOG_LEVEL_ENV}", "INFO", "unset (defaults to WARNING)"
        ))
    elif raw_level.upper().strip() in _KNOWN_LOG_LEVELS:
        checks.append(_check(
            f"{LOG_LEVEL_ENV}", "PASS", raw_level
        ))
    else:
        checks.append(_check(
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
            checks.append(_check(
                "Last backup", "INFO",
                f"{last_backup_dt.isoformat(timespec='seconds')} ({age_td.days}d ago)"
            ))
        else:
            checks.append(_check(
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
            checks.append(_check(
                "Last backup", "FAIL",
                f"no backups in {backups_dir} but events table has "
                f"{event_count:,} rows — run `selvedge backup` now"
            ))
        else:
            checks.append(_check(
                "Last backup", "INFO",
                f"no backups yet ({event_count:,} events; threshold for FAIL is 10,000)"
            ))
    else:
        checks.append(_check(
            "Last backup", "INFO", "no DB yet — nothing to back up"
        ))

    # 9. Last prune — parsed from .selvedge/prune.log if present.
    # An INFO row when the log exists, a softer INFO when it doesn't:
    # absence is not a failure, just a "you haven't run it yet" signal.
    log_path = prune_mod.prune_log_path(resolved.path)
    last_prune = prune_mod.last_prune_line(log_path)
    if last_prune is None:
        checks.append(_check(
            "Last prune", "INFO",
            "no prune.log yet — run `selvedge prune` to start trimming "
            "tool_calls"
        ))
    else:
        ts, count, threshold = last_prune
        checks.append(_check(
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
            checks.append(_check(
                "tool_calls size", "FAIL", f"sqlite error: {e}"
            ))
        else:
            if tc_count > prune_mod.TOOL_CALLS_WARN_ROWS:
                checks.append(_check(
                    "tool_calls size", "WARN",
                    f"{tc_count:,} rows (threshold {prune_mod.TOOL_CALLS_WARN_ROWS:,}) — "
                    f"run `selvedge prune` to trim old telemetry"
                ))
            else:
                checks.append(_check(
                    "tool_calls size", "PASS",
                    f"{tc_count:,} rows (threshold {prune_mod.TOOL_CALLS_WARN_ROWS:,})"
                ))
    else:
        checks.append(_check(
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
            checks.append(_check("Case collisions", "FAIL", f"sqlite error: {e}"))
        else:
            if collisions:
                sample = "; ".join(" vs ".join(c["paths"]) for c in collisions[:3])
                more = f" (+{len(collisions) - 3} more)" if len(collisions) > 3 else ""
                checks.append(_check(
                    "Case collisions", "WARN",
                    f"{len(collisions)} group(s) of paths differing only by case: "
                    f"{sample}{more} — kept distinct (case-sensitive hosts); "
                    f"reconcile if unintended"
                ))
            else:
                checks.append(_check(
                    "Case collisions", "PASS",
                    "no sibling paths differ only by case"
                ))
    else:
        checks.append(_check("Case collisions", "INFO", "no DB yet"))

    # 12. Entity-path migration — surface the last migrate-paths --apply run.
    if db_exists:
        try:
            last_mig = SelvedgeStorage(resolved.path).get_last_path_migration()
        except sqlite3.Error as e:
            checks.append(_check("Path migration", "FAIL", f"sqlite error: {e}"))
        else:
            if last_mig is None:
                checks.append(_check(
                    "Path migration", "INFO",
                    "migrate-paths not applied — run `selvedge migrate-paths` "
                    "to preview re-canonicalization"
                ))
            else:
                checks.append(_check(
                    "Path migration", "INFO",
                    f"{last_mig['timestamp']}  ({last_mig['rows_rewritten']} "
                    f"row(s) rewritten, {last_mig['collisions']} collision(s))"
                ))
    else:
        checks.append(_check("Path migration", "INFO", "no DB yet"))

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
            checks.append(_check("Stale decisions", "FAIL", f"sqlite error: {e}"))
        else:
            if stale:
                sample = ", ".join(s["entity_path"] for s in stale[:3])
                more = f" (+{len(stale) - 3} more)" if len(stale) > 3 else ""
                checks.append(_check(
                    "Stale decisions", "INFO",
                    f"{len(stale)} decision(s) due for revisit: {sample}{more} — "
                    f"run `selvedge stale` to review"
                ))
            else:
                checks.append(_check(
                    "Stale decisions", "INFO",
                    "none due for revisit (a decision surfaces here once its "
                    "revisit_after passes and the entity is still in active use)"
                ))
    else:
        checks.append(_check("Stale decisions", "INFO", "no DB yet"))

    return checks


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def doctor(as_json):
    """Check Selvedge's ambient state and report PASS/WARN/FAIL per check.

    \b
    Walks the things agents typically run into:
      • which DB path is being resolved (and which step of the precedence chain hit)
      • whether .selvedge/ exists where it should
      • whether the schema is at the latest migration version
        (FAIL if schema_migrations contains an unknown version — downgrade)
      • whether the post-commit hook is installed
      • whether the post-commit hook has been failing silently
      • last tool_calls entry timestamp (proxy for "is the agent wired up?")
      • whether SELVEDGE_LOG_LEVEL is set to a recognized value
      • last backup freshness (INFO ≤7d, WARN >7d, FAIL when no backups
        exist AND the events table has ≥10k rows)
      • last prune from .selvedge/prune.log (timestamp, rows pruned, threshold)
      • tool_calls table size (WARN above 100k rows — run `selvedge prune`)
      • stale decisions due for a revisit (INFO — run `selvedge stale`)

    \b
    Exit codes:
      0 — all PASS/INFO/WARN
      1 — any FAIL
    """
    checks = _doctor_checks()

    if as_json:
        click.echo(json.dumps({"checks": checks}, indent=2))
        sys.exit(1 if any(c["status"] == "FAIL" for c in checks) else 0)

    console.print("\n[bold]Selvedge doctor[/bold]\n")

    style_for = {
        "PASS": "[green]✓ PASS[/green]",
        "WARN": "[yellow]! WARN[/yellow]",
        "FAIL": "[red]✗ FAIL[/red]",
        "INFO": "[cyan]i INFO[/cyan]",
    }

    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
    table.add_column("Check", style="bold")
    table.add_column("Status", no_wrap=True)
    table.add_column("Detail", style="dim", overflow="fold")

    for c in checks:
        table.add_row(
            c["label"],
            style_for.get(c["status"], c["status"]),
            c["detail"],
        )
    console.print(table)

    fail_count = sum(1 for c in checks if c["status"] == "FAIL")
    warn_count = sum(1 for c in checks if c["status"] == "WARN")
    if fail_count:
        console.print(f"\n[red]{fail_count} check(s) failed.[/red]")
        sys.exit(1)
    if warn_count:
        console.print(f"\n[yellow]{warn_count} warning(s).[/yellow]")
    else:
        console.print("\n[green]All checks passed.[/green]")
    console.print()


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--strict",
    is_flag=True,
    help="Treat should-warn checks as failures too.",
)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def verify(strict, as_json):
    """Run correctness checks against the Selvedge store.

    \b
    Two tiers:
      must_fail   — SQLite corruption, schema mismatch, invariant violations
                    (empty entity_path, unknown change_type, bad timestamps)
      should_warn — soft signals (singleton changesets, events past the
                    backfill window with no git_commit)

    \b
    Exit codes:
      0 — clean, or only should-warn rows triggered
      1 — any must-fail row triggered (or any row triggered with --strict)

    Wire ``selvedge verify`` into CI without ``|| true`` on day one — the
    should-warn tier means transient soft signals don't break the build.
    Add ``--strict`` once your team is comfortable promoting them.
    """
    db_path = get_db_path()
    results = verify_mod.run_checks(db_path)
    code = verify_mod.exit_code(results, strict=strict)

    if as_json:
        click.echo(
            json.dumps(
                {
                    "strict": strict,
                    "db_path": str(db_path),
                    "checks": [r.to_dict() for r in results],
                    "exit_code": code,
                },
                indent=2,
            )
        )
        sys.exit(code)

    console.print(f"\n[bold]Selvedge verify[/bold]  [dim]{db_path}[/dim]\n")

    style_for = {
        "PASS": "[green]✓ PASS[/green]",
        "WARN": "[yellow]! WARN[/yellow]",
        "FAIL": "[red]✗ FAIL[/red]",
    }

    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
    table.add_column("Check", style="bold")
    table.add_column("Tier", no_wrap=True, style="dim")
    table.add_column("Status", no_wrap=True)
    table.add_column("Detail", style="dim", overflow="fold")

    for r in results:
        table.add_row(
            r.id,
            r.tier,
            style_for.get(r.status, r.status),
            r.detail,
        )
    console.print(table)

    fail_count = sum(1 for r in results if r.status == "FAIL")
    warn_count = sum(1 for r in results if r.status == "WARN")

    if fail_count:
        console.print(f"\n[red]{fail_count} must-fail check(s) failed.[/red]")
    if warn_count:
        msg = f"[yellow]{warn_count} should-warn check(s).[/yellow]"
        if strict:
            msg += " [red](escalated by --strict)[/red]"
        console.print(f"\n{msg}")
    if not fail_count and not warn_count:
        console.print("\n[green]All checks passed.[/green]")
    console.print()
    sys.exit(code)


# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--output",
    "-o",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Destination file. Default: .selvedge/backups/selvedge-YYYYMMDD-HHMMSS.db",
)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def backup(output_path, as_json):
    """Snapshot the Selvedge store via SQLite VACUUM INTO.

    \b
    Rotation: keeps the most recent 7 backups in ``.selvedge/backups/``.
    Custom ``--output`` destinations outside that directory are never
    pruned. Backups are kept out of git via ``.selvedge/backups/`` in
    the project ``.gitignore`` — appended by ``selvedge init`` (v0.3.5+)
    and by the first ``selvedge backup`` run on existing repos.
    """
    db_path = get_db_path()
    project_root = Path.cwd()

    # First-run gitignore hygiene for repos that initialized pre-v0.3.5.
    backup_mod.ensure_gitignore_entry(project_root)

    try:
        result = backup_mod.create_backup(db_path, output=output_path)
    except FileNotFoundError as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
        else:
            err_console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2))
        return

    console.print(
        f"\n[bold green]✓ Backup written[/bold green]  "
        f"[dim]{result.output_path}[/dim]"
    )
    console.print(f"  Size:    [dim]{result.bytes_written:,} bytes[/dim]")
    if result.pruned:
        console.print(f"  Pruned:  [dim]{len(result.pruned)} older snapshot(s)[/dim]")
    console.print()


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--days",
    default=prune_mod.DEFAULT_DAYS,
    show_default=True,
    type=int,
    help=(
        "Delete tool_calls older than this many days. Default of 90 days is "
        "long enough that the previous month's agents are still in the data."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def prune(days, as_json):
    """Trim old ``tool_calls`` rows. v0.3.6 prunes telemetry only.

    \b
    Default retention is 90 days. Pass ``--days N`` to override.
    Every run appends a one-liner to ``.selvedge/prune.log`` so the
    cadence is visible in ``selvedge doctor``.

    \b
    No ``--include-events`` flag in v0.3.6 — the destructive
    events-prune path waits for ``.selvedge/config.toml`` in v0.3.10
    and requires both ``SELVEDGE_DESTRUCTIVE=1`` and an interactive
    confirmation.

    \b
    Examples:
      selvedge prune
      selvedge prune --days 30
      selvedge prune --json
    """
    db_path = get_db_path()
    result = prune_mod.run_prune(db_path, days=days)

    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2))
        return

    console.print(
        f"\n[bold green]✓ Pruned[/bold green] "
        f"[bold]{result.pruned}[/bold] tool_call row(s) older than "
        f"[bold]{result.days_threshold}[/bold] day(s)"
    )
    console.print(f"  Cutoff:  [dim]{result.cutoff}[/dim]")
    console.print(f"  Log:     [dim]{result.log_path}[/dim]")
    console.print()


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("entity_path")
@click.option("--limit", "-n", default=20, show_default=True, help="Number of entries")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def diff(entity_path, limit, as_json):
    """Show change history for an entity.

    \b
    Examples:
      selvedge diff users
      selvedge diff users.email
      selvedge diff src/auth.py::login
    """
    storage = get_storage()
    # Record on the shared coverage counter so `selvedge stats` and the
    # stale-decisions active-use signal see CLI reads too, mirroring the MCP
    # `diff` tool and the `prior-attempts` CLI command.
    storage.record_tool_call("diff", entity_path=entity_path, agent="cli")
    rows = storage.get_entity_history(entity_path, limit)

    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return

    if not rows:
        console.print(f"[yellow]No history found for '{entity_path}'[/yellow]")
        return

    table = Table(
        title=f"History: {entity_path}",
        box=box.SIMPLE_HEAD,
        show_lines=True,
        header_style="bold",
    )
    table.add_column("Timestamp", style="dim", no_wrap=True)
    table.add_column("Change", style="green")
    table.add_column("Agent", style="magenta")
    table.add_column("Diff")
    table.add_column("Reasoning")

    for row in rows:
        change = row.get("change_type", "")
        if row.get("superseded_by"):
            change = f"{change} [dim](superseded)[/dim]"
        table.add_row(
            fmt_ts(row.get("timestamp", "")),
            change,
            row.get("agent", "") or "—",
            row.get("diff", "") or "—",
            row.get("reasoning", "") or "—",
        )
    console.print(table)

    # Derived decision state (v0.3.9.1) — the clear current-status line
    # under the trail.
    decision = storage.get_decision_status(entity_path)
    status_styles = {"reverted": "red", "reopened": "cyan", "active": "green"}
    style = status_styles.get(decision["status"], "dim")
    console.print(f"  [dim]Status[/dim]  [{style}]{decision['status_line']}[/{style}]\n")


# ---------------------------------------------------------------------------
# blame
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("entity_path")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def blame(entity_path, as_json):
    """Show who last changed an entity and why.

    \b
    Examples:
      selvedge blame users.email
      selvedge blame api/v1/payments
    """
    storage = get_storage()
    # Record on the shared coverage counter (see the `diff` command) so a CLI
    # blame counts as active use of the entity for stale-decisions weighting.
    storage.record_tool_call("blame", entity_path=entity_path, agent="cli")
    row = storage.get_blame(entity_path)

    if not row:
        # Under --json the miss must still be JSON on stdout. `console` is
        # stdout, so printing Rich markup here made `blame` the one command
        # in the CLI whose --json output didn't parse — on the miss case,
        # which is the common one, and which the shipped agent prompt tells
        # agents to reach for.
        if as_json:
            click.echo(json.dumps({"error": f"No history found for '{entity_path}'"}))
        else:
            console.print(f"[yellow]No history found for '{entity_path}'[/yellow]")
        sys.exit(1)

    if as_json:
        # Mirror the MCP blame tool: include the derived decision state so
        # the two surfaces can't diverge.
        decision = storage.get_decision_status(entity_path)
        row["status"] = decision["status"]
        row["superseded_by"] = next(
            (e["superseded_by"] for e in decision["trail"] if e["id"] == row["id"]),
            "",
        )
        click.echo(json.dumps(row, indent=2))
        return

    console.print(f"\n[bold cyan]{entity_path}[/bold cyan]")
    console.print(f"  [dim]Changed[/dim]    {fmt_ts(row['timestamp'])}")
    console.print(f"  [dim]Type[/dim]       [green]{row['change_type']}[/green]")

    if row.get("entity_type"):
        console.print(f"  [dim]Entity[/dim]     {row['entity_type']}")
    if row.get("agent"):
        console.print(f"  [dim]Agent[/dim]      [magenta]{row['agent']}[/magenta]")
    if row.get("session_id"):
        console.print(f"  [dim]Session[/dim]    {row['session_id']}")
    if row.get("git_commit"):
        console.print(f"  [dim]Commit[/dim]     [yellow]{row['git_commit']}[/yellow]")
    if row.get("project"):
        console.print(f"  [dim]Project[/dim]    {row['project']}")
    if row.get("diff"):
        console.print(f"  [dim]Diff[/dim]       {row['diff']}")
    if row.get("constraint"):
        console.print(f"  [dim]Constraint[/dim] {row['constraint']}")
    if row.get("stale_when"):
        console.print(f"  [dim]Stale when[/dim] {row['stale_when']}")
    if row.get("supersedes"):
        console.print(f"  [dim]Supersedes[/dim] {row['supersedes'][:8]}")
    if row.get("reasoning"):
        console.print("\n  [dim]Reasoning:[/dim]")
        console.print(f"    {row['reasoning']}")

    # Derived decision state (v0.3.9.1) — the clear current-status line.
    decision = storage.get_decision_status(entity_path)
    status_styles = {"reverted": "red", "reopened": "cyan", "active": "green"}
    style = status_styles.get(decision["status"], "dim")
    console.print(f"\n  [dim]Status[/dim]     [{style}]{decision['status_line']}[/{style}]")
    console.print()


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


_SINCE_HELP = (
    "Since date or relative shorthand: 24h, 7d, 15m (minutes), 5mo (months), 1y. "
    "Note: 'm' is minutes, 'mo' is months. Unparseable values exit with an error."
)


@cli.command()
@click.option("--since", "-s", default="", help=_SINCE_HELP)
@click.option("--entity", "-e", default="", help="Filter to entity path prefix")
@click.option("--project", "-p", default="", help="Filter by project name")
@click.option("--changeset", "-c", default="", help="Filter to a specific changeset ID")
@click.option("--limit", "-n", default=50, show_default=True, help="Number of entries")
@click.option("--summarize", is_flag=True, help="Render a human-readable changelog grouped by session/changeset")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def history(since, entity, project, changeset, limit, summarize, as_json):
    """Browse change history across all entities.

    \b
    Examples:
      selvedge history
      selvedge history --since 7d
      selvedge history --since 24h --summarize
      selvedge history --entity users --since 30d
      selvedge history --project my-api
      selvedge history --changeset add-stripe-billing
    """
    resolved_since = resolve_since(since)
    rows = get_storage().get_history(
        since=resolved_since,
        entity_path=entity,
        project=project,
        changeset_id=changeset,
        limit=limit,
    )

    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return

    if summarize:
        render_summary(rows, since=since or "all time")
        return

    title_parts = ["History"]
    if entity:
        title_parts.append(entity)
    if changeset:
        title_parts.append(f"changeset:{changeset}")
    if since:
        title_parts.append(f"since {since}")

    render_events(rows, " · ".join(title_parts))


# ---------------------------------------------------------------------------
# changeset
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("changeset_id", required=False, default="")
@click.option("--list", "list_all", is_flag=True, help="List all changesets")
@click.option("--project", "-p", default="", help="Filter by project")
@click.option("--since", "-s", default="", help=_SINCE_HELP)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def changeset(changeset_id, list_all, project, since, as_json):
    """Show events in a changeset, or list all changesets.

    \b
    A changeset groups related changes from a single feature or task.
    Agents set changeset_id in log_change() to link related events together.

    \b
    Examples:
      selvedge changeset add-stripe-billing
      selvedge changeset --list
      selvedge changeset --list --since 7d
    """
    storage = get_storage()

    if list_all or not changeset_id:
        resolved_since = resolve_since(since)
        rows = storage.list_changesets(project=project, since=resolved_since)

        if as_json:
            click.echo(json.dumps(rows, indent=2))
            return

        if not rows:
            console.print("[yellow]No changesets found.[/yellow]")
            return

        table = Table(
            title="Changesets",
            box=box.SIMPLE_HEAD,
            show_lines=False,
            header_style="bold",
        )
        table.add_column("Changeset ID", style="cyan")
        table.add_column("Events", justify="right")
        table.add_column("First event", style="dim", no_wrap=True)
        table.add_column("Last event", style="dim", no_wrap=True)
        table.add_column("Project", style="dim")

        for row in rows:
            table.add_row(
                row["changeset_id"],
                str(row["event_count"]),
                fmt_ts(row["first_event"]),
                fmt_ts(row["last_event"]),
                row.get("project") or "—",
            )
        console.print(table)
        return

    rows = storage.get_changeset(changeset_id)

    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return

    if not rows:
        console.print(f"[yellow]No events found for changeset '{changeset_id}'[/yellow]")
        return

    render_summary(rows)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("query")
@click.option("--limit", "-n", default=20, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def search(query, limit, as_json):
    """Full-text search across entity paths, diffs, and reasoning.

    \b
    Examples:
      selvedge search "billing"
      selvedge search "stripe"
      selvedge search "auth"
    """
    rows = get_storage().search(query, limit)

    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return

    render_events(rows, f'Search: "{query}"')


# ---------------------------------------------------------------------------
# prior-attempts (CLI parity for the prior_attempts MCP tool)
# ---------------------------------------------------------------------------


@cli.command("prior-attempts")
@click.argument("entity", required=False, default="")
@click.option(
    "--description",
    "-d",
    default="",
    help="Free-text description to match instead of an exact ENTITY.",
)
@click.option(
    "--fuzzy",
    "-f",
    "fuzzy",
    default="",
    help="Semantic query: also surface attempts on entities whose prior "
    "reasoning is SIMILAR to this text (catches renames like payment_token "
    "vs card_token). Needs the optional extra: pip install "
    '"selvedge[semantic]" + `selvedge index`; falls back to substring '
    "matching otherwise.",
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="Widen recall to proximity_low (include still-active and far-apart reverts).",
)
@click.option(
    "--window",
    default="7d",
    show_default=True,
    help="Proximity window for the add→remove revert heuristic (e.g. 7d, 60m, or "
    "a plain integer of minutes).",
)
@click.option("--limit", "-n", default=20, show_default=True, help="Number of results")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def prior_attempts_cmd(entity, description, fuzzy, show_all, window, limit, as_json):
    """Show prior change attempts on an entity, each with an inferred outcome.

    \b
    A thin presenter over the same store the `prior_attempts` MCP tool reads,
    so the two surfaces can't diverge — `--json` emits the identical list the
    tool returns. Pass an ENTITY (exact, with `.`-prefix matching) OR
    --description for free-text mode; ENTITY wins if both are given.
    --fuzzy adds semantically similar records on top of either, clearly
    labeled (match_type="fuzzy" with a similarity score).

    \b
    Conservative by default: only the clear "tried, then reverted within the
    window" signal shows. An empty result is the normal, good answer (exit 0) —
    pass --all to widen recall.

    \b
    Examples:
      selvedge prior-attempts users.auth_token
      selvedge prior-attempts --description "sso token" --all
      selvedge prior-attempts users.card_token --fuzzy "tokenized payment credentials"
      selvedge prior-attempts src/auth.py::login --window 30d --json
    """
    if not entity and not description and not fuzzy:
        err_console.print(
            "[red]error:[/red] provide an ENTITY, --description, or --fuzzy"
        )
        sys.exit(2)

    try:
        window_minutes = parse_window_minutes(window)
    except ValueError as e:
        err_console.print(f"[red]error:[/red] {e}")
        sys.exit(2)

    min_confidence = "proximity_low" if show_all else "proximity_high"

    storage = get_storage()
    # Record on the same coverage counter as the MCP tool so `selvedge stats`
    # reflects both surfaces. A prior_attempts lookup also counts as active-use
    # of the entity for the stale-decisions weighting.
    storage.record_tool_call("prior_attempts", entity_path=entity, agent="cli")
    rows = []
    if entity or description:
        rows = storage.get_prior_attempts(
            entity_path=entity,
            query=description,
            min_confidence=min_confidence,
            window_minutes=window_minutes,
            limit=limit,
        )

    if fuzzy:
        from . import semantic

        try:
            rows += semantic.fuzzy_prior_attempts(
                storage,
                fuzzy,
                min_confidence=min_confidence,
                window_minutes=window_minutes,
                limit=limit,
                exclude_paths={r["entity_path"] for r in rows},
                exclude_ids={r["id"] for r in rows},
            )
        except (semantic.SemanticUnavailable, semantic.IndexMissing) as e:
            from rich.markup import escape

            # escape(): the hint contains "[semantic]", which Rich would
            # otherwise swallow as a markup tag.
            err_console.print(f"[yellow]fuzzy unavailable:[/yellow] {escape(str(e))}")
            err_console.print("[dim]falling back to substring matching[/dim]")
            existing_paths = {r["entity_path"] for r in rows}
            rows += [
                r
                for r in storage.get_prior_attempts(
                    query=fuzzy,
                    min_confidence=min_confidence,
                    window_minutes=window_minutes,
                    limit=limit,
                )
                if r["entity_path"] not in existing_paths
            ]

    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return

    if not rows:
        target = entity or f'"{description or fuzzy}"'
        console.print(f"[green]No prior attempts found for {target}.[/green]")
        if not show_all:
            console.print(
                "[dim]Nothing clearly tried-and-reverted — pass [bold]--all[/bold] "
                "to widen recall.[/dim]"
            )
        return  # empty is the normal, good answer — exit 0

    label = entity or f'"{description or fuzzy}"'
    console.print(f"\n[bold]Prior attempts[/bold]  [dim]{label}[/dim]\n")
    outcome_styles = {"reverted": "red", "reopened": "cyan", "active": "yellow"}
    for r in rows:
        outcome = r.get("outcome", "")
        confidence = r.get("confidence", "")
        outcome_style = outcome_styles.get(outcome, "yellow")
        fuzzy_tag = (
            f"  [magenta]~fuzzy {r.get('similarity', 0.0):.2f}[/magenta]"
            if r.get("match_type") == "fuzzy"
            else ""
        )
        console.print(
            f"  [dim]{fmt_ts(r.get('timestamp', ''))}[/dim]  "
            f"[cyan]{r.get('entity_path', '')}[/cyan]  "
            f"[green]{r.get('change_type', '')}[/green]  "
            f"[{outcome_style}]{outcome}[/{outcome_style}]  "
            f"[dim]({confidence})[/dim]{fuzzy_tag}"
        )
        # The trail, one line per step: tried → reverted → re-opened.
        if r.get("reasoning"):
            console.print(f"    [dim]tried:[/dim]     {r['reasoning']}")
        if outcome in ("reverted", "reopened") and r.get("outcome_reasoning"):
            console.print(f"    [dim]reverted:[/dim]  {r['outcome_reasoning']}")
        if outcome == "reopened" and r.get("supersede_reasoning"):
            console.print(f"    [dim]re-opened:[/dim] {r['supersede_reasoning']}")
        console.print()

    # One clear current-status line per distinct entity in the result set.
    statuses = {r["entity_path"]: r.get("current_status", "") for r in rows}
    status_styles = {"reverted": "red", "reopened": "cyan", "active": "green"}
    for path, status in sorted(statuses.items()):
        if not status:
            continue
        style = status_styles.get(status, "dim")
        console.print(
            f"  [bold]current status[/bold]  [cyan]{path}[/cyan]  "
            f"[{style}]{status}[/{style}]"
        )
    console.print()


# ---------------------------------------------------------------------------
# index (optional semantic embeddings — v0.3.9.1)
# ---------------------------------------------------------------------------


@cli.command("index")
@click.option(
    "--model",
    "model_name",
    default="",
    help="model2vec model to embed with (default: minishlab/potion-base-8M).",
)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def index_cmd(model_name, as_json):
    """Build or update the semantic embeddings index over reasoning text.

    \b
    Powers `selvedge prior-attempts --fuzzy` — recall that survives renames
    (payment_token vs card_token). Requires the optional extra:

        pip install "selvedge[semantic]"

    \b
    Incremental: only new or changed reasoning is re-embedded, so re-running
    after a work session is cheap. The FIRST run downloads the embedding
    model (~30 MB, cached); indexing and querying are fully local after
    that. The core store is untouched — embeddings live in their own table,
    and nothing in Selvedge's write/read paths depends on it.
    """
    from . import semantic

    if not semantic.is_available():
        from rich.markup import escape

        # escape(): the hint contains "[semantic]", which Rich would
        # otherwise swallow as a markup tag.
        err_console.print(f"[yellow]{escape(semantic.INSTALL_HINT)}[/yellow]")
        sys.exit(1)

    kwargs = {"model_name": model_name} if model_name else {}
    report = semantic.build_index(get_storage(), **kwargs)

    if as_json:
        click.echo(json.dumps(report, indent=2))
        return

    console.print(
        f"[green]✓[/green] Semantic index up to date  "
        f"[dim]model: {report['model']}[/dim]"
    )
    console.print(
        f"  [bold]{report['indexed']}[/bold] embedded, "
        f"[dim]{report['skipped']} unchanged, "
        f"{report['total_indexed']} total in index[/dim]"
    )


# ---------------------------------------------------------------------------
# stale (dated decisions due for a revisit — v0.3.8)
# ---------------------------------------------------------------------------


@cli.command("stale")
@click.option("--entity", "-e", default="", help="Filter to an entity or path prefix")
@click.option("--project", "-p", default="", help="Filter by project name")
@click.option(
    "--agent",
    "-a",
    default="",
    help="Filter to the agent that logged the decision",
)
@click.option("--limit", "-n", default=20, show_default=True, help="Number of results")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def stale_cmd(entity, project, agent, limit, as_json):
    """Show decisions now due for a revisit.

    \b
    Two surfacing rules (see the `flag` field):
      revisit_due       — `revisit_after` has passed AND the entity is still
                          live (recently queried, or its changeset kept
                          moving). Pure age never surfaces, so an
                          old-but-correct decision nobody touches won't nag.
      review_suggested  — a later change event keyword-matched the decision's
                          `stale_when` condition (v0.3.9.1). Surfacing only —
                          follow up with `selvedge supersede` if the
                          condition really was triggered.
    `--json` is built for cron / Slack / digest jobs.

    \b
    Examples:
      selvedge stale
      selvedge stale --entity users --json
      selvedge stale --project my-api --agent claude-code
    """
    storage = get_storage()
    rows = storage.get_stale_decisions(
        entity_path=entity, project=project, agent=agent, limit=limit
    )

    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return

    if not rows:
        console.print("[green]No decisions are due for a revisit.[/green]")
        console.print(
            "[dim]Decisions surface here once their [bold]revisit_after[/bold] "
            "passes and the entity is still in active use.[/dim]"
        )
        return

    table = Table(
        title="Decisions due for revisit",
        box=box.SIMPLE_HEAD,
        show_lines=False,
        header_style="bold",
    )
    # No "Logged" column — with the Flag column added (v0.3.9.1) it pushed
    # the table past 80 cols and Rich truncated the entity paths, which are
    # the one thing this view must always show. The due date carries the
    # relevant time signal.
    table.add_column("Entity", style="bold cyan", overflow="fold")
    table.add_column("Change", style="green")
    table.add_column("Flag", no_wrap=True)
    table.add_column("Due", style="dim", no_wrap=True)
    table.add_column("Overdue", justify="right")
    table.add_column("Why", overflow="fold")
    for r in rows:
        flag = r.get("flag", "revisit_due")
        flag_cell = (
            "[yellow]review[/yellow]"
            if flag == "review_suggested"
            else "[red]due[/red]"
        )
        overdue = f"{r.get('days_overdue', 0)}d" if r.get("revisit_due") else "—"
        table.add_row(
            r.get("entity_path", ""),
            r.get("change_type", ""),
            flag_cell,
            fmt_ts(r.get("revisit_due", "")),
            overdue,
            r.get("stale_reason", ""),
        )
    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# stats (tool-call coverage)
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--since", "-s", default="", help=_SINCE_HELP)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def stats(since, as_json):
    """Show MCP tool call statistics and log_change coverage.

    \b
    The coverage ratio tells you how often agents are actually calling
    log_change relative to total tool invocations. A low ratio suggests
    agents are querying history but not logging new changes.

    \b
    Examples:
      selvedge stats
      selvedge stats --since 7d
    """
    resolved_since = resolve_since(since)
    data = get_storage().get_tool_stats(since=resolved_since)

    if as_json:
        click.echo(json.dumps(data, indent=2))
        return

    total = data["total_calls"]
    log_calls = data["log_change_calls"]
    ratio = data["log_change_ratio"]

    period = f" (since {since})" if since else ""
    console.print(f"\n[bold]Selvedge tool call stats[/bold]{period}\n")

    if total == 0:
        console.print("  [dim]No tool calls recorded yet.[/dim]")
        console.print("  [dim]Tool call tracking starts once the MCP server is connected.[/dim]\n")
        return

    # Coverage bar
    bar_width = 30
    filled = int(ratio * bar_width)
    bar = "[green]" + "█" * filled + "[/green]" + "[dim]" + "░" * (bar_width - filled) + "[/dim]"
    coverage_color = "green" if ratio >= 0.2 else "yellow" if ratio >= 0.1 else "red"
    console.print(f"  log_change coverage  {bar}  [{coverage_color}]{ratio:.0%}[/{coverage_color}]")
    console.print(f"  [dim]{log_calls} log_change calls out of {total} total[/dim]\n")

    # Per-tool breakdown
    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
    table.add_column("Tool", style="cyan")
    table.add_column("Calls", justify="right")
    table.add_column("Share", justify="right", style="dim")

    for tool_name, count in data["by_tool"].items():
        share = f"{count/total:.0%}"
        style = "bold" if tool_name == "log_change" else ""
        table.add_row(tool_name, str(count), share, style=style)

    console.print(table)

    # Per-agent breakdown — surfaces the case where one agent is
    # well-instrumented but another isn't logging changes.
    by_agent = data.get("by_agent") or {}
    if by_agent:
        agent_table = Table(
            box=box.SIMPLE_HEAD, show_header=True, header_style="bold"
        )
        agent_table.add_column("Agent", style="magenta")
        agent_table.add_column("Calls", justify="right")
        agent_table.add_column("log_change", justify="right")
        agent_table.add_column("Coverage", justify="right")
        for agent_name, breakdown in by_agent.items():
            agent_total = int(breakdown["total"])
            agent_log = int(breakdown["log_change"])
            agent_ratio = float(breakdown["ratio"])
            color = (
                "green" if agent_ratio >= 0.2
                else "yellow" if agent_ratio >= 0.1
                else "red"
            )
            agent_table.add_row(
                agent_name,
                str(agent_total),
                str(agent_log),
                f"[{color}]{agent_ratio:.0%}[/{color}]",
            )
        console.print(agent_table)

    # Reasoning quality — count of stored events whose reasoning fails
    # the validator. A non-zero value means the agent saw a warning at
    # log time and shipped the event anyway.
    missing = data.get("missing_reasoning", 0)
    if missing:
        console.print(
            f"  [yellow]{missing}[/yellow] event(s) with low-quality reasoning  "
            "[dim](empty, too short, or generic placeholder)[/dim]\n"
        )

    # Recent calls
    if data.get("recent"):
        console.print("  [bold]Recent tool calls[/bold]")
        for call in data["recent"][:5]:
            entity = f"  [cyan]{call['entity_path']}[/cyan]" if call.get("entity_path") else ""
            status = "[green]✓[/green]" if call.get("success") else "[red]✗[/red]"
            agent_str = (
                f"  [magenta dim]{call['agent']}[/magenta dim]"
                if call.get("agent") else ""
            )
            console.print(
                f"    {status}  [dim]{fmt_ts(call['timestamp'])}[/dim]  "
                f"[magenta]{call['tool_name']}[/magenta]{entity}{agent_str}"
            )
    console.print()


# ---------------------------------------------------------------------------
# log (manual entry)
# ---------------------------------------------------------------------------


_CHANGE_TYPE_CHOICES = [ct.value for ct in ChangeType]


@cli.command()
@click.argument("entity_path")
@click.argument(
    "change_type",
    type=click.Choice(_CHANGE_TYPE_CHOICES, case_sensitive=False),
    metavar="CHANGE_TYPE",
)
@click.option("--diff", "-d", "diff_text", default="", help="The change diff or description")
@click.option("--reasoning", "-r", default="", help="Why the change was made")
@click.option("--entity-type", default="other", help="Entity type (column, table, file, ...)")
@click.option("--agent", default="", help="Agent or author name")
@click.option("--commit", default="", help="Git commit hash")
@click.option("--project", default="", help="Project name")
@click.option("--changeset", "-c", default="", help="Changeset ID to group related changes")
@click.option(
    "--revisit-after",
    default="",
    help="Revisit date for an architectural decision: an ISO date or a relative "
    "offset from now (e.g. '90d', '6mo'). Surfaced later by `selvedge stale`.",
)
@click.option(
    "--rename-from",
    default="",
    help="Old path when CHANGE_TYPE is 'rename'; ENTITY_PATH is the new path. "
    "Records the dual-event rename pattern (rename on old + create on new).",
)
@click.option(
    "--constraint",
    default="",
    help="The testable principle behind this decision, kept queryable "
    "(e.g. 'card data in our own DB = PCI scope').",
)
@click.option(
    "--stale-when",
    default="",
    help="What would invalidate this decision, as a short phrase "
    "(e.g. 'payment provider changed'). `selvedge stale` matches it against "
    "later events and flags 'review suggested'.",
)
@click.option(
    "--supersedes",
    default="",
    help="Event id this change overrides. Only valid with CHANGE_TYPE "
    "'supersede'; empty auto-links the latest remove/delete on the entity. "
    "Prefer `selvedge supersede` for the guided flow.",
)
def log(entity_path, change_type, diff_text, reasoning, entity_type, agent, commit, project, changeset, revisit_after, rename_from, constraint, stale_when, supersedes):
    """Manually log a change event.

    \b
    CHANGE_TYPE must be one of:
      add, remove, modify, rename, retype, create, delete,
      index_add, index_remove, migrate, revert, supersede

    \b
    Examples:
      selvedge log users.email add --reasoning "Added for auth"
      selvedge log src/auth.py modify --diff "Updated login logic" --agent "me"
      selvedge log payments.amount add --changeset add-stripe-billing
      selvedge log users table add --revisit-after 90d --reasoning "..."
      selvedge log src/auth/session.py::login rename --rename-from src/auth.py::login
    """
    if rename_from and change_type != "rename":
        err_console.print(
            "[red]error:[/red] --rename-from is only valid with CHANGE_TYPE 'rename'"
        )
        sys.exit(2)
    if supersedes and change_type != "supersede":
        err_console.print(
            "[red]error:[/red] --supersedes is only valid with CHANGE_TYPE 'supersede'"
        )
        sys.exit(2)

    # Normalize --revisit-after with the same grammar as --since (relative
    # offsets preserved, absolute dates canonicalized) before it reaches storage.
    try:
        normalized_revisit = normalize_revisit_after(revisit_after)
    except ValueError as e:
        err_console.print(f"[red]error:[/red] {e}")
        sys.exit(2)

    storage = get_storage()
    # Record on the shared coverage counter, mirroring the MCP `log_change`
    # tool. Without this `selvedge stats` counted CLI *reads* in the
    # denominator but no CLI *writes* in the numerator, so a correctly-logged
    # CLI workflow reported 0% log_change coverage and the report advised the
    # user to do the very thing they had just done.
    storage.record_tool_call("log_change", entity_path=entity_path, agent=agent or "cli")
    try:
        if rename_from:
            events = storage.log_rename(
                old_path=rename_from,
                new_path=entity_path,
                entity_type=entity_type,
                diff=diff_text,
                reasoning=reasoning,
                agent=agent,
                git_commit=commit,
                project=project,
                changeset_id=changeset,
            )
            stored = events[-1]
        elif change_type == "supersede":
            # Route through the supersede writer so the link to the prior
            # reverted event is validated/auto-resolved in one place — same
            # path the MCP log_change tool takes.
            stored = storage.log_supersede(
                entity_path,
                reasoning=reasoning,
                constraint=constraint,
                stale_when=stale_when,
                supersedes=supersedes,
                agent=agent,
                git_commit=commit,
                project=project,
                changeset_id=changeset,
            )
        else:
            event = ChangeEvent(
                entity_path=entity_path,
                change_type=change_type,
                diff=diff_text,
                entity_type=entity_type,
                reasoning=reasoning,
                agent=agent,
                git_commit=commit,
                project=project,
                changeset_id=changeset,
                revisit_after=normalized_revisit,
                constraint=constraint,
                stale_when=stale_when,
            )
            stored = storage.log_event(event)
    except ValueError as e:
        err_console.print(f"[red]error:[/red] {e}")
        sys.exit(2)

    suffix = f"  [dim]changeset:{stored.changeset_id}[/dim]" if stored.changeset_id else ""
    if rename_from:
        console.print(
            f"[green]✓[/green] Renamed [bold]{rename_from}[/bold] → "
            f"[bold]{stored.entity_path}[/bold]  [dim]{stored.id[:8]}[/dim]{suffix}"
        )
    elif change_type == "supersede":
        console.print(
            f"[green]✓[/green] Superseded [dim]{stored.supersedes[:8]}[/dim] on "
            f"[bold]{stored.entity_path}[/bold] — decision re-opened  "
            f"[dim]{stored.id[:8]}[/dim]{suffix}"
        )
    else:
        console.print(
            f"[green]✓[/green] Logged [bold]{stored.entity_path}[/bold] ({change_type})  "
            f"[dim]{stored.id[:8]}[/dim]{suffix}"
        )

    # Surface reasoning-quality + entity-path-shape + revisit-date warnings so
    # manual entries get the same nudges that agent-driven log_change calls do.
    warnings = check_reasoning_quality(reasoning)
    warnings += check_entity_path_shape(stored.entity_path, stored.entity_type)
    warnings += check_revisit_nudge(
        stored.change_type, stored.entity_type, stored.revisit_after
    )
    for warning in warnings:
        err_console.print(f"[yellow]warning:[/yellow] {warning}")


# ---------------------------------------------------------------------------
# supersede (re-open a reverted decision, v0.3.9.1)
# ---------------------------------------------------------------------------


@cli.command("supersede")
@click.argument("entity_path")
@click.option(
    "--reasoning",
    "-r",
    required=True,
    help="Why the reverted decision no longer stands — what changed in the world.",
)
@click.option(
    "--constraint",
    default="",
    help="The (now-lifted or new) testable principle, kept queryable.",
)
@click.option(
    "--stale-when",
    default="",
    help="What would invalidate THIS new verdict, for future stale-checking.",
)
@click.option(
    "--supersedes",
    default="",
    help="Explicit event id to override. Default: the entity's most recent "
    "remove/delete event.",
)
@click.option("--agent", default="", help="Agent or author name")
@click.option("--commit", default="", help="Git commit hash")
@click.option("--project", default="", help="Project name")
@click.option("--changeset", "-c", default="", help="Changeset ID to group related changes")
@click.option("--json", "as_json", is_flag=True, help="Output the stored event as JSON")
def supersede_cmd(entity_path, reasoning, constraint, stale_when, supersedes, agent, commit, project, changeset, as_json):
    """Re-open a reverted decision — append-only, never rewrites history.

    \b
    A reverted decision is not a permanent ban: when the constraint that
    killed it no longer holds (provider changed, requirement dropped, scale
    tipped), record a supersede. Selvedge logs a new change event of type
    'supersede' linking the prior reverted event; prior-attempts / blame /
    diff then read the full trail: tried → reverted → re-opened.

    \b
    There is deliberately no automatic un-retiring — this command IS the
    explicit re-open step.

    \b
    Examples:
      selvedge supersede payments.card_token -r "Provider now vaults cards — PCI constraint gone."
      selvedge supersede users.sso_token -r "..." --constraint "tokens must be short-lived"
      selvedge supersede users.sso_token -r "..." --supersedes 3fa2b1c0-...
    """
    storage = get_storage()
    storage.record_tool_call("log_change", entity_path=entity_path, agent=agent or "cli")
    try:
        stored = storage.log_supersede(
            entity_path,
            reasoning=reasoning,
            constraint=constraint,
            stale_when=stale_when,
            supersedes=supersedes,
            agent=agent,
            git_commit=commit,
            project=project,
            changeset_id=changeset,
        )
    except ValueError as e:
        err_console.print(f"[red]error:[/red] {e}")
        sys.exit(2)

    decision = storage.get_decision_status(stored.entity_path)

    if as_json:
        click.echo(
            json.dumps(
                {
                    **stored.to_dict(),
                    "status": decision["status"],
                    "status_line": decision["status_line"],
                },
                indent=2,
            )
        )
        return

    console.print(
        f"[green]✓[/green] Superseded [dim]{stored.supersedes[:8]}[/dim] on "
        f"[bold]{stored.entity_path}[/bold]  [dim]{stored.id[:8]}[/dim]"
    )
    console.print(f"  [dim]Status[/dim]  [green]{decision['status_line']}[/green]")

    for warning in check_reasoning_quality(reasoning):
        err_console.print(f"[yellow]warning:[/yellow] {warning}")


# ---------------------------------------------------------------------------
# migrate-paths
# ---------------------------------------------------------------------------


@cli.command("migrate-paths")
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    help="Write the re-canonicalized paths. Without this flag it's a dry run "
    "(the default) — nothing is written.",
)
@click.option(
    "--agent",
    default="",
    help="Label this migration run with an agent/author name in the audit row.",
)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def migrate_paths(apply_changes, agent, as_json):
    """Re-canonicalize stored entity paths (one-shot backfill).

    \b
    DRY RUN IS THE DEFAULT — nothing is written until you pass --apply.
    A dry run prints:
      • how many rows would be rewritten (old path → canonical path), and
      • a COLLISIONS report: distinct pre-canonicalization paths that would
        converge to the same canonical value — i.e. histories that --apply
        would MERGE. Inspect these before applying.

    \b
    Idempotent: re-running after --apply rewrites nothing. Each --apply run
    records one audit row in the path_migrations table (visible to doctor).
    """
    storage = get_storage()
    report = storage.recanonicalize_paths(apply=apply_changes, agent=agent)

    if as_json:
        click.echo(json.dumps(report, indent=2))
        return

    mode = "apply" if report["applied"] else "dry run"
    console.print(f"\n[bold]Selvedge migrate-paths[/bold] [dim]({mode})[/dim]\n")

    rewrites = report["rewrites"]
    collisions = report["collisions"]

    if not rewrites:
        console.print(
            f"[green]✓[/green] All {report['rows_scanned']} entity path(s) are "
            "already canonical — nothing to migrate."
        )
        console.print()
        return

    # Planned / performed rewrites.
    verb = "Rewrote" if report["applied"] else "Would rewrite"
    console.print(
        f"{verb} [bold]{len(rewrites)}[/bold] of {report['rows_scanned']} "
        "row(s):\n"
    )
    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
    table.add_column("id", style="dim", no_wrap=True)
    table.add_column("from")
    table.add_column("→", no_wrap=True)
    table.add_column("to", style="green")
    _DISPLAY_CAP = 50
    for rw in rewrites[:_DISPLAY_CAP]:
        table.add_row(rw["id"][:8], rw["from"], "→", rw["to"])
    console.print(table)
    if len(rewrites) > _DISPLAY_CAP:
        console.print(f"[dim]… and {len(rewrites) - _DISPLAY_CAP} more[/dim]")

    # Collisions report — the safety surface.
    if collisions:
        console.print(
            f"\n[yellow]! {len(collisions)} collision(s)[/yellow] — distinct "
            "paths that converge to the same canonical value. Applying "
            "[bold]merges[/bold] their histories:\n"
        )
        ctable = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
        ctable.add_column("canonical", style="bold")
        ctable.add_column("merged from")
        for c in collisions:
            ctable.add_row(c["canonical"], "  +  ".join(c["paths"]))
        console.print(ctable)

    if report["applied"]:
        console.print(
            f"\n[green]✓[/green] Applied. Rewrote {report['rows_rewritten']} "
            "row(s); audit row recorded in path_migrations."
        )
    else:
        console.print(
            "\n[dim]Dry run — pass [bold]--apply[/bold] to write these "
            "changes.[/dim]"
        )
    console.print()


# ---------------------------------------------------------------------------
# install-hook
# ---------------------------------------------------------------------------

_HOOK_SCRIPT = """\
#!/bin/sh
# Selvedge post-commit hook
# Backfills git_commit on Selvedge events logged during this session.
# Installed by: selvedge install-hook
_selvedge_log_failure() {
    _root="$(git rev-parse --show-toplevel 2>/dev/null)"
    if [ -n "$_root" ] && [ -d "$_root/.selvedge" ]; then
        printf '%sZ\\t%s\\n' "$(date -u +%Y-%m-%dT%H:%M:%S)" "$1" \\
            >> "$_root/.selvedge/hook.log"
    fi
}
if ! command -v selvedge >/dev/null 2>&1; then
    # Most common silent-fail mode: git launches a shell with a stripped
    # PATH (especially under macOS GUI clients) and `selvedge` isn't on it.
    # Without this log line, the symptom is just "git_commit values stopped
    # landing" with no breadcrumb to follow.
    _selvedge_log_failure "selvedge command not on PATH"
    exit 0
fi
_selvedge_err=$(selvedge backfill-commit \\
    --hash "$(git rev-parse HEAD)" --quiet 2>&1) || \\
    _selvedge_log_failure "backfill-commit failed: $_selvedge_err"
"""

_HOOK_MARKER = "# Selvedge post-commit hook"


@cli.command("install-hook")
@click.option("--path", "-p", default=".", help="Project root (default: current directory)")
@click.option("--window", default=60, show_default=True,
              help="Minutes back to search for events to backfill")
def install_hook(path, window):
    """Install a git post-commit hook that auto-backfills git_commit.

    \b
    After every `git commit`, the hook runs `selvedge backfill-commit`
    which finds events logged in the last N minutes with no git_commit
    and stamps them with the new commit hash.

    \b
    The default window is 60 minutes — wide enough that a long agent
    session won't lose its events when the user finally commits.

    \b
    If a post-commit hook already exists, the Selvedge block is appended
    rather than overwriting the existing script.

    \b
    Examples:
      selvedge install-hook
      selvedge install-hook --window 120
    """
    root = Path(path).resolve()
    git_dir = root / ".git"
    if not git_dir.exists():
        err_console.print(f"[red]error:[/red] no .git directory found at {root}")
        err_console.print("run this command from inside a git repository")
        sys.exit(1)

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "post-commit"

    if hook_path.exists():
        existing = hook_path.read_text()
        if _HOOK_MARKER in existing:
            console.print("[yellow]Selvedge hook already installed.[/yellow]")
            console.print(f"  [dim]{hook_path}[/dim]")
            return
        # Append to existing hook
        updated = existing.rstrip("\n") + "\n\n" + _HOOK_SCRIPT
        hook_path.write_text(updated)
        console.print("[green]✓[/green] Appended Selvedge hook to existing post-commit")
    else:
        hook_path.write_text(_HOOK_SCRIPT)
        console.print("[green]✓[/green] Installed post-commit hook")

    hook_path.chmod(0o755)
    console.print(f"  [dim]{hook_path}[/dim]")
    console.print()
    console.print(
        "  After each [bold]git commit[/bold], Selvedge will automatically backfill\n"
        f"  [bold]git_commit[/bold] on events logged in the last [bold]{window}[/bold] minutes."
    )


# ---------------------------------------------------------------------------
# backfill-commit  (called by the git hook, also usable directly)
# ---------------------------------------------------------------------------


@cli.command("backfill-commit")
@click.option("--hash", "commit_hash", required=True, help="Git commit hash to stamp")
@click.option("--window", default=60, show_default=True,
              help="Minutes back to search for events to backfill")
@click.option("--quiet", is_flag=True, help="Suppress output (used by git hook)")
def backfill_commit(commit_hash, window, quiet):
    """Backfill git_commit on recent events that don't have one.

    \b
    Normally called automatically by the post-commit hook. You can also
    run it manually to stamp a specific commit hash onto recent events.

    \b
    Examples:
      selvedge backfill-commit --hash abc1234
      selvedge backfill-commit --hash $(git rev-parse HEAD) --window 120
    """
    updated = get_storage().backfill_git_commit(commit_hash, window_minutes=window)
    if not quiet:
        if updated:
            console.print(
                f"[green]✓[/green] Backfilled [bold]{updated}[/bold] event(s) "
                f"→ [yellow]{commit_hash[:12]}[/yellow]"
            )
        else:
            console.print(f"[dim]No events to backfill for {commit_hash[:12]}[/dim]")


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--format", "fmt",
              type=click.Choice(["json", "csv", "agent-trace"]), default="json",
              show_default=True, help="Output format")
@click.option("--since", "-s", default="", help=_SINCE_HELP)
@click.option("--entity", "-e", default="", help="Filter to entity path prefix")
@click.option("--project", "-p", default="", help="Filter by project name")
@click.option("--limit", "-n", default=0, help="Max rows (0 = all)")
@click.option("--ndjson", is_flag=True,
              help="agent-trace only: one trace record per line (NDJSON)")
@click.option("--collapse-by-session", "collapse", is_flag=True,
              help="agent-trace only: merge events sharing a session_id into one record")
@click.option("--output", "-o", default="-",
              help="Output file path (default: stdout)")
def export(fmt, since, entity, project, limit, ndjson, collapse, output):
    """Export change history to JSON, CSV, or Agent Trace.

    \b
    Examples:
      selvedge export                            # all events as JSON to stdout
      selvedge export --format csv -o out.csv   # CSV file
      selvedge export --since 30d --entity users
      selvedge export --format agent-trace -o trace.json
      selvedge export --format agent-trace --ndjson -o trace.ndjson
      selvedge export --format agent-trace --collapse-by-session -o trace.json

    \b
    The agent-trace format emits Agent Trace v0.1.0 records
    (https://github.com/cursor/agent-trace) — one per change event by default.
    Selvedge is a compatible producer; reasoning and entity-level provenance
    travel in each record's metadata under the "dev.selvedge" namespace.
    """
    import csv as csv_mod
    import io

    resolved_since = resolve_since(since)
    effective_limit = limit if limit > 0 else 1_000_000
    rows = get_storage().get_history(
        since=resolved_since,
        entity_path=entity,
        project=project,
        limit=effective_limit,
    )

    if fmt == "agent-trace":
        from .exporters.agent_trace import (
            SELVEDGE_NS,
            events_to_trace_records,
            export_preamble,
        )

        records = events_to_trace_records(rows, collapse_by_session=collapse)
        if ndjson:
            content = "\n".join(json.dumps(r) for r in records)
        else:
            header = export_preamble()[SELVEDGE_NS]
            content = json.dumps({**header, "records": records}, indent=2)
    elif fmt == "json":
        content = json.dumps(rows, indent=2)
    else:
        buf = io.StringIO()
        if rows:
            writer = csv_mod.DictWriter(buf, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        content = buf.getvalue()

    if output == "-":
        click.echo(content)
    else:
        Path(output).write_text(content)
        console.print(
            f"[green]✓[/green] Exported [bold]{len(rows)}[/bold] events "
            f"→ [dim]{output}[/dim]"
        )


# ---------------------------------------------------------------------------
# import (migration files)
# ---------------------------------------------------------------------------


@cli.command("import")
@click.argument("path", type=click.Path(), required=False, default=None)
@click.option("--format", "fmt",
              type=click.Choice(["auto", "sql", "alembic", "agent-trace"]),
              default="auto", show_default=True,
              help="Input format (auto-detects migrations by default)")
@click.option("--from-git", "from_git", is_flag=True,
              help="Import reverts from git history instead of migration files. "
              "PATH becomes the repository root (default: current directory).")
@click.option("--since", "since", default="",
              help="--from-git only: limit history to commits after a git REF "
              "(exclusive, REF..HEAD) or a date git understands (e.g. "
              "'2025-01-01', '6 months ago').")
@click.option("--project", "-p", default="", help="Project name to tag events with")
@click.option("--dry-run", is_flag=True, help="Preview what would be imported, don't write")
@click.option("--json", "as_json", is_flag=True, help="Output events as JSON (implies --dry-run)")
def import_migrations(path, fmt, from_git, since, project, dry_run, as_json):
    """Import migration files, an Agent Trace file, or git-history reverts.

    \b
    PATH can be a single migration file or a directory of migration files.
    Supports raw SQL DDL files and Alembic Python migration files. With
    --format agent-trace, PATH is an Agent Trace JSON/NDJSON file (round-trips
    a `selvedge export --format agent-trace`; foreign producers import
    best-effort with change_type="modify" and empty reasoning).

    \b
    With --from-git, PATH is a git repository root (default: `.`) and the
    import walks history for reverts that predate Selvedge: commits whose
    message mentions "revert" plus commits that deleted files. Each becomes
    a change_type="revert" event (agent="git-import", reasoning = commit
    subject+body) so prior_attempts and the enforcement hook see them.
    Idempotent — re-running skips (commit, entity) pairs already imported,
    regardless of --project.

    \b
    Identity boundary (know this): a DROP TABLE / DROP COLUMN in the revert
    diff is seeded at the ENTITY level (e.g. `users.card_token`), so it
    matches a re-add in any file. Every other diff shape — ORM model columns,
    serializer fields, non-SQL code — is seeded at the FILE level, so a
    reverted entity that resurfaces in a *different* file is missed.
    Cross-file entity extraction is planned (see the phase plan). Also
    missed: reverts folded into unrelated commits.

    \b
    Examples:
      selvedge import migrations/
      selvedge import migrations/ --project my-api
      selvedge import migrations/0023_add_payments.py --dry-run
      selvedge import schema.sql --format sql
      selvedge import trace.json --format agent-trace
      selvedge import --from-git
      selvedge import --from-git --since v1.4.0 --dry-run
      selvedge import ../other-repo --from-git --since "6 months ago"
    """
    if from_git:
        from .gitimport import GIT_IMPORT_AGENT, GitImportError, collect_revert_events

        repo = Path(path) if path else Path(".")
        if not repo.is_dir():
            err_console.print(f"[red]error:[/red] {repo} is not a directory")
            sys.exit(2)
        try:
            events = collect_revert_events(repo, since=since, project=project)
        except GitImportError as e:
            err_console.print(f"[red]error:[/red] {e}")
            sys.exit(2)

        storage = get_storage()
        existing = storage.get_agent_event_keys(GIT_IMPORT_AGENT)
        from .storage import canonicalize_entity_path

        fresh = [
            ev for ev in events
            if (ev.git_commit, canonicalize_entity_path(ev.entity_path)) not in existing
        ]
        skipped = len(events) - len(fresh)

        if as_json:
            click.echo(json.dumps([ev.to_dict() for ev in fresh], indent=2))
            return
        if not fresh:
            if skipped:
                console.print(
                    f"[green]✓[/green] Nothing new to import — all {skipped} "
                    "revert event(s) already present (idempotent re-run)."
                )
            else:
                console.print("[yellow]No revert history found to import.[/yellow]")
            return
        if dry_run:
            table = Table(
                title=f"Dry run — {len(fresh)} revert events from git history",
                box=box.SIMPLE_HEAD,
                show_lines=False,
                header_style="bold",
            )
            table.add_column("Commit", style="yellow", no_wrap=True)
            table.add_column("Entity", style="cyan")
            table.add_column("Reasoning")
            for ev in fresh:
                # `or [""]`: a commit created with --allow-empty-message
                # yields reasoning="" and splitlines() returns [].
                first_line = (ev.reasoning.splitlines() or [""])[0]
                table.add_row(ev.git_commit[:8], ev.entity_path, first_line[:60])
            console.print(table)
            if skipped:
                console.print(f"  [dim]{skipped} already-imported event(s) skipped.[/dim]")
            console.print(
                f"  [dim]Run without --dry-run to import these {len(fresh)} events.[/dim]"
            )
            return

        storage.log_event_batch(fresh)
        msg = (
            f"[green]✓[/green] Imported [bold]{len(fresh)}[/bold] revert "
            f"event(s) from git history"
        )
        if skipped:
            msg += f"  [dim]({skipped} already present, skipped)[/dim]"
        console.print(msg)
        return

    if path is None:
        err_console.print("[red]error:[/red] PATH is required (or pass --from-git)")
        sys.exit(2)
    if since:
        err_console.print("[red]error:[/red] --since is only valid with --from-git")
        sys.exit(2)
    target = Path(path)
    if not target.exists():
        err_console.print(f"[red]error:[/red] path does not exist: {target}")
        sys.exit(2)

    if fmt == "agent-trace":
        from .exporters.agent_trace import load_trace_records, trace_record_to_events
        from .models import ChangeEvent

        records = load_trace_records(target.read_text())
        events = []
        seen_ids: set[str] = set()
        for record in records:
            for event_dict in trace_record_to_events(record):
                try:
                    event = ChangeEvent(**event_dict)
                except (ValueError, TypeError):
                    continue
                # Dedupe within the batch so a re-derived id can't abort the
                # single-transaction insert on a PRIMARY KEY collision.
                if event.id in seen_ids:
                    continue
                seen_ids.add(event.id)
                if project:
                    event.project = project
                events.append(event)
    else:
        from .importers import import_path

        events = import_path(target, fmt=fmt, project=project)

    if not events:
        console.print("[yellow]No importable changes found.[/yellow]")
        return

    if as_json:
        click.echo(json.dumps([e.to_dict() for e in events], indent=2))
        return

    if dry_run:
        table = Table(
            title=f"Dry run — {len(events)} events from {target.name}",
            box=box.SIMPLE_HEAD,
            show_lines=False,
            header_style="bold",
        )
        table.add_column("Entity", style="cyan")
        table.add_column("Change", style="green")
        table.add_column("Diff")
        for ev in events:
            table.add_row(ev.entity_path, ev.change_type, (ev.diff or "")[:60])
        console.print(table)
        console.print(f"  [dim]Run without --dry-run to import these {len(events)} events.[/dim]")
        return

    # Single transaction — orders of magnitude faster than one INSERT per
    # event for large Alembic histories, and the import is atomic.
    storage = get_storage()
    storage.log_event_batch(events)

    console.print(
        f"[green]✓[/green] Imported [bold]{len(events)}[/bold] events from "
        f"[dim]{target.name}[/dim]"
    )


# ---------------------------------------------------------------------------
# Empty-state diagnosis (used by `selvedge status` and `doctor`)
# ---------------------------------------------------------------------------


# How long after installing the MCP entry we stop assuming "agent just
# needs a restart" and start treating the silence as the user's
# problem. Five minutes is the documented v0.3.4 cutoff.
_AGENT_RESTART_GRACE_SECONDS = 5 * 60


def _diagnose_empty_state(storage: SelvedgeStorage) -> list[str]:
    """Return Rich-formatted diagnosis lines for the no-events empty state.

    The decision tree:
      - If a Selvedge MCP entry exists in any detected agent's config
        AND the config was modified more than 5 minutes ago AND no
        tool_calls have been received → the agent probably needs a
        restart. Surface the config path so the user knows where to
        look.
      - Otherwise → point at `selvedge setup` so they install the
        wiring instead of guessing.

    The returned list is rendered one item per line by ``status``.
    Reused by ``doctor`` so both commands give consistent advice.
    """
    from datetime import datetime, timezone

    from .setup import detect_agents

    try:
        agents = detect_agents(project=Path.cwd())
    except OSError:
        agents = []

    # Find any detected agent whose config file actually contains a
    # ``selvedge`` mcpServers entry. Skip agents without a config_path
    # (Copilot doesn't have a JSON registry).
    installed_in: list[Path] = []
    for agent in agents:
        if agent.config_path is None or not agent.config_path.exists():
            continue
        try:
            data = json.loads(agent.config_path.read_text() or "{}")
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        servers = data.get("mcpServers")
        if isinstance(servers, dict) and "selvedge" in servers:
            installed_in.append(agent.config_path)

    if installed_in:
        # The wiring is in place; the most likely cause is "agent
        # process is running but loaded the config before our entry
        # was added." Compare config mtime against now — if recent,
        # tell the user the agent just needs a restart.
        most_recent = max(p.stat().st_mtime for p in installed_in)
        age = datetime.now(timezone.utc).timestamp() - most_recent
        if age < _AGENT_RESTART_GRACE_SECONDS:
            paths = ", ".join(str(p) for p in installed_in)
            return [
                "[yellow]MCP entry installed but no tool_calls received yet.[/yellow]",
                f"[dim]Config(s): {paths}[/dim]",
                "[dim]Try restarting your agent — the running process "
                "loaded its config before we added Selvedge.[/dim]",
            ]
        # Older than 5 minutes → real problem worth diagnosing harder.
        return [
            "[yellow]MCP entry installed but no tool_calls received.[/yellow]",
            f"[dim]Config(s): {', '.join(str(p) for p in installed_in)}[/dim]",
            "[dim]Run [bold]selvedge doctor[/bold] for a full health check.[/dim]",
        ]

    # No MCP entry anywhere → user hasn't run setup
    return [
        "[dim]No changes logged yet.[/dim]",
        "[dim]Run [bold]selvedge setup[/bold] to wire Selvedge into your AI tools.[/dim]",
    ]


# ---------------------------------------------------------------------------
# setup (interactive first-run wizard, v0.3.4)
# ---------------------------------------------------------------------------


@cli.command("setup")
@click.option(
    "--path",
    "-p",
    default=".",
    help="Project root to set up (default: current directory)",
)
@click.option(
    "--non-interactive",
    is_flag=True,
    help=(
        "Don't prompt. Without --yes this is a dry run — every step is "
        "marked 'skipped' and no files are touched. Pair with --yes to "
        "actually run the wizard unattended (CI / devcontainer)."
    ),
)
@click.option(
    "-y",
    "--yes",
    "assume_yes",
    is_flag=True,
    help="Answer 'yes' to every confirmation. Pair with --non-interactive for CI.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite existing non-matching MCP entries instead of treating them as conflicts.",
)
@click.option(
    "--skip-init",
    is_flag=True,
    help="Don't run `selvedge init` — useful when the project already has a .selvedge/.",
)
@click.option(
    "--skip-hook",
    is_flag=True,
    help="Don't install the git post-commit hook.",
)
@click.option(
    "--skip-enforcement-hook",
    is_flag=True,
    help="Don't install the Claude Code PreToolUse enforcement hook "
    "(.claude/settings.json).",
)
def setup(path, non_interactive, assume_yes, force, skip_init, skip_hook, skip_enforcement_hook):
    """Interactive first-run wizard — wires Selvedge into your AI tools.

    \b
    Detects which AI tools you have installed (Claude Code, Cursor,
    GitHub Copilot), offers to:
      • install Selvedge's MCP entry into each tool's config
      • drop the canonical agent-instructions block into CLAUDE.md /
        .cursorrules / copilot-instructions.md
      • install the PreToolUse enforcement hook into the project's
        .claude/settings.json (Claude Code only) — blocks schema/migration
        edits until prior_attempts has been checked this session
      • run `selvedge init` if .selvedge/ doesn't exist
      • install the post-commit hook for git_commit backfill

    \b
    Every modified file gets a `.bak` written next to it before any
    change reaches disk. Re-running the wizard is a no-op (idempotent).
    Existing MCP entries that differ from ours raise a conflict — pass
    --force to overwrite, or update them by hand.

    \b
    For CI bootstrap and devcontainer postCreateCommand:
      selvedge setup --non-interactive --yes
    """
    from .setup import run_wizard

    project = Path(path).resolve()

    # Resolve the confirm callback. ``--non-interactive`` without
    # ``--yes`` means "list what would be done but don't write" — we
    # implement that by saying "no" to every prompt.
    if non_interactive:
        if assume_yes:
            confirm: Callable[[str, bool], bool] | None = lambda *_: True  # noqa: E731
        else:
            confirm = lambda *_: False  # noqa: E731
    else:
        confirm = None  # use the default click.confirm

    outcome = run_wizard(
        project=project,
        interactive=not non_interactive,
        force=force,
        install_hook=not skip_hook,
        init_project_dir=not skip_init,
        install_enforcement_hook=not skip_enforcement_hook,
        confirm=confirm,
    )

    _render_wizard_summary(outcome)
    if outcome.exit_code:
        sys.exit(outcome.exit_code)


def _render_wizard_summary(outcome) -> None:
    """Render the wizard's per-step result table at end of run."""
    style_for = {
        "ok": "[green]✓ ok[/green]",
        "noop": "[dim]· noop[/dim]",
        "skipped": "[yellow]→ skipped[/yellow]",
        "error": "[red]✗ error[/red]",
    }

    console.print("\n[bold]Setup summary[/bold]\n")
    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
    table.add_column("Step", style="bold")
    table.add_column("Status", no_wrap=True)
    table.add_column("Detail", style="dim", overflow="fold")
    for step in outcome.steps:
        table.add_row(
            step.label,
            style_for.get(step.status, step.status),
            step.detail,
        )
    console.print(table)

    backups = [s for s in outcome.steps if s.backup_path is not None]
    if backups:
        console.print("\n  [bold]Backups written:[/bold]")
        for step in backups:
            console.print(f"    [dim]{step.backup_path}[/dim]  (from {step.label})")

    if outcome.exit_code:
        console.print(
            "\n[red]Setup completed with errors.[/red] "
            "[dim]Fix the offending file(s) and re-run with --force if needed.[/dim]\n"
        )
    else:
        console.print(
            "\n[green]Setup complete.[/green] "
            "[dim]Restart your agent to pick up the new MCP entry.[/dim]\n"
        )


# ---------------------------------------------------------------------------
# prompt (print / install canonical agent instructions, v0.3.4)
# ---------------------------------------------------------------------------


@cli.command("prompt")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["block", "skill"]),
    default="block",
    help=(
        "Output shape: 'block' (CLAUDE.md-ready, sentinel-bracketed) or "
        "'skill' (a plugin SKILL.md with frontmatter). Default: block."
    ),
)
@click.option(
    "--install",
    "install_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Append/update the prompt block in this file (idempotent, sentinel-bracketed).",
)
@click.option(
    "--no-backup",
    is_flag=True,
    help="Skip writing the .bak file before --install modifies an existing file.",
)
def prompt_cmd(fmt, install_path, no_backup):
    """Print the canonical Selvedge agent-instructions block.

    \b
    With no flags, prints the block to stdout — pipe-friendly:
      selvedge prompt | tee -a CLAUDE.md

    \b
    With --install <file>, idempotently writes the block to <file>:
      • file doesn't exist  → created with just the block
      • file exists, no Selvedge block → block appended after a blank line
      • file exists, has an old Selvedge block → block replaced in place
      • file already has the current block → unchanged

    \b
    The block is wrapped in <!-- selvedge:start --> / <!-- selvedge:end -->
    sentinel markers so re-running --install never duplicates content.
    A `.bak` is written before any modification unless --no-backup is set.

    \b
    --format skill prints a Claude Code plugin SKILL.md (frontmatter + the
    same block body). It's what regenerates the bundled plugin skill, so the
    skill can't drift from this block:
      selvedge prompt --format skill > skills/selvedge/SKILL.md

    \b
    Examples:
      selvedge prompt
      selvedge prompt --install CLAUDE.md
      selvedge prompt --install .cursorrules --no-backup
      selvedge prompt --format skill
    """
    from .prompt import PROMPT_BLOCK, install_to_file, render_block, render_skill

    if fmt == "skill":
        if install_path is not None:
            raise click.UsageError(
                "--install writes a sentinel-bracketed block into an existing "
                "file and only applies to --format block. A skill is a whole "
                "file Selvedge owns — redirect instead:\n"
                "  selvedge prompt --format skill > skills/selvedge/SKILL.md"
            )
        # nl=False so stdout is byte-for-byte render_skill() (it already ends
        # in a newline); the redirect above then writes the exact file the
        # drift test checks.
        click.echo(render_skill(), nl=False)
        _ = PROMPT_BLOCK
        return

    if install_path is None:
        click.echo(render_block())
        return

    action, backup = install_to_file(install_path, write_backup=not no_backup)
    label = {
        "created": "Created",
        "appended": "Appended block to",
        "updated": "Updated existing block in",
        "unchanged": "Already up to date in",
    }[action]
    color = "green" if action != "unchanged" else "dim"
    console.print(
        f"[{color}]{'✓' if action != 'unchanged' else '·'}[/{color}] "
        f"{label} [bold]{install_path}[/bold]"
    )
    if backup is not None:
        console.print(f"  [dim]backup: {backup}[/dim]")
    # Stash the prompt body so static analyzers don't complain about
    # the import being unused on the print-only path.
    _ = PROMPT_BLOCK


# ---------------------------------------------------------------------------
# watch (live tail, v0.3.4)
# ---------------------------------------------------------------------------


@cli.command("watch")
@click.option("--since", "-s", default="", help=_SINCE_HELP)
@click.option("--entity", "-e", default="", help="Filter to entity path or prefix.")
@click.option("--project", "-p", default="", help="Filter to a project name.")
@click.option("--agent", "-a", default="", help="Filter to a single agent name.")
@click.option(
    "--interval",
    "-i",
    default=1.0,
    show_default=True,
    type=float,
    help="Poll interval in seconds (range: 0.1 to 60).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit one JSON object per event (pipe-friendly).",
)
def watch_cmd(since, entity, project, agent, interval, as_json):
    """Live-tail of new events as they're logged.

    \b
    Polls the SQLite store at --interval (default 1s) and prints each
    new event as it lands. Same filters as `selvedge history`. WAL
    mode means watch never blocks the writer, but it does add one
    SQLite SELECT per second while running — opt-in foreground only,
    not a daemon.

    \b
    Examples:
      selvedge watch
      selvedge watch --since 1h
      selvedge watch --entity users --agent claude-code
      selvedge watch --project my-api --json | jq .
      selvedge watch --interval 5
    """
    from .watch import render_header
    from .watch import watch as run_watch

    resolved_since = resolve_since(since)
    db_path = get_db_path()

    if not as_json:
        render_header(
            console,
            db_path=str(db_path),
            interval=interval,
            since=since,
            entity_path=entity,
            project=project,
            agent=agent,
        )

    try:
        exit_code = run_watch(
            since=resolved_since,
            entity_path=entity,
            project=project,
            agent=agent,
            interval=interval,
            as_json=as_json,
            console=console,
        )
    except ValueError as e:
        err_console.print(f"[red]error:[/red] {e}")
        sys.exit(2)
    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# telemetry
# ---------------------------------------------------------------------------


@cli.group(invoke_without_command=True)
@click.pass_context
def telemetry(ctx):
    """Manage opt-in anonymous usage telemetry (off by default).

    \b
    Selvedge never sends anything unless you explicitly enable this.
    The heartbeat is a single tiny JSON ping, at most once per day:
    version, Python major.minor, OS, and a random UUID minted on
    enable (deleted on disable). No code, no paths, no reasoning
    text, no repo names — run `selvedge telemetry status` to see the
    exact payload. Full docs: docs/telemetry.md.

    \b
    Examples:
      selvedge telemetry            # same as `status`
      selvedge telemetry enable
      selvedge telemetry disable
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(telemetry_status)


@telemetry.command("enable")
def telemetry_enable():
    """Turn the anonymous heartbeat on (records consent, mints a random ID)."""
    state = telemetry_mod.enable()
    payload = telemetry_mod.build_payload("cli") or {}
    console.print("\n[bold green]✓ Telemetry enabled[/bold green]")
    console.print(f"  Install ID:  [dim]{state.get('install_id', '')}[/dim] [dim](random UUID, not machine-derived)[/dim]")
    console.print(f"  Consent:     [dim]{telemetry_mod._consent_path()}[/dim]")
    console.print("\n  The complete payload, sent at most once per day:\n")
    console.print(f"  [dim]{json.dumps(payload, indent=2)}[/dim]")
    console.print("\n  Disable any time: [bold]selvedge telemetry disable[/bold]\n")


@telemetry.command("disable")
def telemetry_disable():
    """Turn the heartbeat off and delete the install ID."""
    telemetry_mod.disable()
    console.print("\n[bold green]✓ Telemetry disabled[/bold green]")
    console.print("  [dim]The install ID was deleted; re-enabling mints a fresh one,[/dim]")
    console.print("  [dim]so nothing links usage across consent periods.[/dim]\n")


@telemetry.command("status")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def telemetry_status(as_json):
    """Show telemetry state and the exact payload that would be sent."""
    data = telemetry_mod.consent_state()

    if as_json:
        click.echo(json.dumps(data, indent=2))
        return

    enabled = data["enabled"]
    label = "[bold green]enabled[/bold green]" if enabled else "[bold]disabled[/bold] [dim](default)[/dim]"
    console.print(f"\n[bold]Telemetry:[/bold] {label}")
    console.print(f"  Consent source:  [dim]{data['consent_source']}[/dim]")
    console.print(f"  Consent file:    [dim]{data['consent_file']}[/dim]")
    console.print(f"  Endpoint:        [dim]{data['endpoint']}[/dim]")
    if data["install_id"]:
        console.print(f"  Install ID:      [dim]{data['install_id']}[/dim]")
    if data["last_ping"]:
        console.print(f"  Last ping:       [dim]{data['last_ping']}[/dim]")
    if enabled and data["payload_preview"]:
        console.print("\n  The complete payload, sent at most once per day:\n")
        console.print(f"  [dim]{json.dumps(data['payload_preview'], indent=2)}[/dim]")
    else:
        console.print("\n  [dim]Nothing is ever sent while disabled. Enable with:[/dim]")
        console.print("  [bold]selvedge telemetry enable[/bold]")
    console.print()
