"""
Selvedge CLI.

Commands:
  selvedge init               Initialize Selvedge in the current project
  selvedge status             Show recent activity summary
  selvedge diff <entity>      Change history for an entity
  selvedge blame <entity>     Most recent change + context for an entity
  selvedge history            Filtered history across all entities
  selvedge search <query>     Full-text search across all events
  selvedge log                Manually log a change event
"""

import json
import sys
from pathlib import Path

import click
from rich import box
from rich.console import Console
from rich.table import Table

from .config import get_db_path, init_project
from .logging_config import configure_logging
from .models import ChangeEvent, ChangeType
from .storage import SelvedgeStorage
from .timeutil import parse_time_string
from .validation import check_reasoning_quality

console = Console()
err_console = Console(stderr=True)


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
def status():
    """Show a summary of recent Selvedge activity."""
    storage = get_storage()
    total = storage.count()
    recent = storage.get_history(limit=5)
    missing_commit = storage.count_missing_git_commit()

    db_path = get_db_path()
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
    console.print()

    if not recent:
        console.print("  [dim]No changes logged yet.[/dim]")
        console.print("  [dim]Connect your AI agent and start tracking.[/dim]")
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
    rows = get_storage().get_entity_history(entity_path, limit)

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
        table.add_row(
            fmt_ts(row.get("timestamp", "")),
            row.get("change_type", ""),
            row.get("agent", "") or "—",
            row.get("diff", "") or "—",
            row.get("reasoning", "") or "—",
        )
    console.print(table)


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
    row = get_storage().get_blame(entity_path)

    if not row:
        console.print(f"[yellow]No history found for '{entity_path}'[/yellow]")
        sys.exit(1)

    if as_json:
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
    if row.get("reasoning"):
        console.print("\n  [dim]Reasoning:[/dim]")
        console.print(f"    {row['reasoning']}")
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

    # Recent calls
    if data.get("recent"):
        console.print("  [bold]Recent tool calls[/bold]")
        for call in data["recent"][:5]:
            entity = f"  [cyan]{call['entity_path']}[/cyan]" if call.get("entity_path") else ""
            status = "[green]✓[/green]" if call.get("success") else "[red]✗[/red]"
            console.print(
                f"    {status}  [dim]{fmt_ts(call['timestamp'])}[/dim]  "
                f"[magenta]{call['tool_name']}[/magenta]{entity}"
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
def log(entity_path, change_type, diff_text, reasoning, entity_type, agent, commit, project, changeset):
    """Manually log a change event.

    \b
    CHANGE_TYPE must be one of:
      add, remove, modify, rename, retype, create, delete,
      index_add, index_remove, migrate

    \b
    Examples:
      selvedge log users.email add --reasoning "Added for auth"
      selvedge log src/auth.py modify --diff "Updated login logic" --agent "me"
      selvedge log payments.amount add --changeset add-stripe-billing
    """
    try:
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
        )
    except ValueError as e:
        err_console.print(f"[red]error:[/red] {e}")
        sys.exit(2)

    storage = get_storage()
    stored = storage.log_event(event)
    suffix = f"  [dim]changeset:{stored.changeset_id}[/dim]" if stored.changeset_id else ""
    console.print(f"[green]✓[/green] Logged [bold]{entity_path}[/bold] ({change_type})  [dim]{stored.id[:8]}[/dim]{suffix}")

    # Surface reasoning-quality warnings so manual entries get the same
    # nudges that agent-driven log_change calls do.
    for warning in check_reasoning_quality(reasoning):
        err_console.print(f"[yellow]warning:[/yellow] {warning}")


# ---------------------------------------------------------------------------
# install-hook
# ---------------------------------------------------------------------------

_HOOK_SCRIPT = """\
#!/bin/sh
# Selvedge post-commit hook
# Backfills git_commit on Selvedge events logged during this session.
# Installed by: selvedge install-hook
if command -v selvedge >/dev/null 2>&1; then
  selvedge backfill-commit --hash "$(git rev-parse HEAD)" --quiet
fi
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
@click.option("--format", "fmt", type=click.Choice(["json", "csv"]), default="json",
              show_default=True, help="Output format")
@click.option("--since", "-s", default="", help=_SINCE_HELP)
@click.option("--entity", "-e", default="", help="Filter to entity path prefix")
@click.option("--project", "-p", default="", help="Filter by project name")
@click.option("--limit", "-n", default=0, help="Max rows (0 = all)")
@click.option("--output", "-o", default="-",
              help="Output file path (default: stdout)")
def export(fmt, since, entity, project, limit, output):
    """Export change history to JSON or CSV.

    \b
    Examples:
      selvedge export                            # all events as JSON to stdout
      selvedge export --format csv -o out.csv   # CSV file
      selvedge export --since 30d --entity users
      selvedge export --format json -o history.json
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

    if fmt == "json":
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
@click.argument("path", type=click.Path(exists=True))
@click.option("--format", "fmt",
              type=click.Choice(["auto", "sql", "alembic"]),
              default="auto", show_default=True,
              help="Migration format (auto-detects by default)")
@click.option("--project", "-p", default="", help="Project name to tag events with")
@click.option("--dry-run", is_flag=True, help="Preview what would be imported, don't write")
@click.option("--json", "as_json", is_flag=True, help="Output events as JSON (implies --dry-run)")
def import_migrations(path, fmt, project, dry_run, as_json):
    """Import migration files to backfill schema change history.

    \b
    PATH can be a single migration file or a directory of migration files.
    Supports raw SQL DDL files and Alembic Python migration files.

    \b
    Examples:
      selvedge import migrations/
      selvedge import migrations/ --project my-api
      selvedge import migrations/0023_add_payments.py --dry-run
      selvedge import schema.sql --format sql
    """
    from .importers import import_path

    target = Path(path)
    events = import_path(target, fmt=fmt, project=project)

    if not events:
        console.print("[yellow]No importable schema changes found.[/yellow]")
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
        for e in events:
            table.add_row(e.entity_path, e.change_type, (e.diff or "")[:60])
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
