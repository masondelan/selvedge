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
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich import box

from .config import get_db_path, init_project
from .models import ChangeEvent, ChangeType, EntityType
from .storage import SelvedgeStorage

console = Console()
err_console = Console(stderr=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_storage() -> SelvedgeStorage:
    return SelvedgeStorage(get_db_path())


def parse_relative_time(since: str) -> str:
    match = re.fullmatch(r"(\d+)([dhmy])", since.strip())
    if not match:
        return since
    n, unit = int(match.group(1)), match.group(2)
    delta = {
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
        "m": timedelta(days=n * 30),
        "y": timedelta(days=n * 365),
    }[unit]
    return (datetime.now(timezone.utc) - delta).isoformat()


def fmt_ts(ts: str) -> str:
    """Trim ISO timestamp to readable form."""
    return ts[:19].replace("T", " ") if ts else "—"


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
    pass


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

    console.print(f"\n[bold green]✓ Selvedge initialized[/bold green]")
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

    db_path = get_db_path()
    console.print(f"\n[bold]Selvedge[/bold]  [dim]{db_path}[/dim]")
    console.print(f"  [bold]{total}[/bold] total events logged\n")

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
        console.print(f"\n  [dim]Reasoning:[/dim]")
        console.print(f"    {row['reasoning']}")
    console.print()


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--since", "-s", default="", help="Since date or relative: 7d, 24h, 3m, 1y")
@click.option("--entity", "-e", default="", help="Filter to entity path prefix")
@click.option("--project", "-p", default="", help="Filter by project name")
@click.option("--limit", "-n", default=50, show_default=True, help="Number of entries")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def history(since, entity, project, limit, as_json):
    """Browse change history across all entities.

    \b
    Examples:
      selvedge history
      selvedge history --since 7d
      selvedge history --entity users --since 30d
      selvedge history --project my-api
    """
    resolved_since = parse_relative_time(since) if since else ""
    rows = get_storage().get_history(
        since=resolved_since,
        entity_path=entity,
        project=project,
        limit=limit,
    )

    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return

    title_parts = ["History"]
    if entity:
        title_parts.append(entity)
    if since:
        title_parts.append(f"since {since}")

    render_events(rows, " · ".join(title_parts))


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
@click.option("--since", "-s", default="", help="Since date or relative: 7d, 24h, 3m, 1y")
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
    resolved_since = parse_relative_time(since) if since else ""
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


@cli.command()
@click.argument("entity_path")
@click.argument("change_type")
@click.option("--diff", "-d", "diff_text", default="", help="The change diff or description")
@click.option("--reasoning", "-r", default="", help="Why the change was made")
@click.option("--entity-type", default="other", help="Entity type (column, table, file, ...)")
@click.option("--agent", default="", help="Agent or author name")
@click.option("--commit", default="", help="Git commit hash")
@click.option("--project", default="", help="Project name")
def log(entity_path, change_type, diff_text, reasoning, entity_type, agent, commit, project):
    """Manually log a change event.

    \b
    Examples:
      selvedge log users.email add --reasoning "Added for auth"
      selvedge log src/auth.py modify --diff "Updated login logic" --agent "me"
    """
    event = ChangeEvent(
        entity_path=entity_path,
        change_type=change_type,
        diff=diff_text,
        entity_type=entity_type,
        reasoning=reasoning,
        agent=agent,
        git_commit=commit,
        project=project,
    )
    storage = get_storage()
    stored = storage.log_event(event)
    console.print(f"[green]✓[/green] Logged [bold]{entity_path}[/bold] ({change_type})  [dim]{stored.id[:8]}[/dim]")
