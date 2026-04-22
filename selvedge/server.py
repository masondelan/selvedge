"""
Selvedge MCP Server.

Exposes 5 tools that AI coding agents call to track and query codebase changes:
  - log_change   : record a change event
  - diff         : get change history for an entity
  - blame        : get the most recent change + context for an entity
  - history      : filtered history across all entities
  - search       : full-text search across all events
"""

import re
from datetime import datetime, timedelta, timezone

from mcp.server.fastmcp import FastMCP

from .config import get_db_path
from .models import ChangeEvent
from .storage import SelvedgeStorage

mcp = FastMCP(
    "selvedge",
    instructions=(
        "Selvedge tracks semantic changes to codebases and databases. "
        "Call log_change whenever you add, remove, or modify a meaningful entity "
        "(a DB column, table, function, API endpoint, file, dependency, env var, etc.). "
        "Include as much reasoning as you have — why the change was made, what prompted it. "
        "Use diff, blame, history, and search to answer questions about past changes."
    ),
)

_storage: SelvedgeStorage | None = None


def get_storage() -> SelvedgeStorage:
    global _storage
    if _storage is None:
        _storage = SelvedgeStorage(get_db_path())
    return _storage


def _parse_relative_time(since: str) -> str:
    """
    Convert a relative time string like '7d', '3h', '2m', '1y'
    into an ISO timestamp string. Returns the input unchanged if
    it doesn't match the pattern.
    """
    match = re.fullmatch(r"(\d+)([dhmy])", since.strip())
    if not match:
        return since
    n, unit = int(match.group(1)), match.group(2)
    delta_map = {
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
        "m": timedelta(days=n * 30),
        "y": timedelta(days=n * 365),
    }
    cutoff = datetime.now(timezone.utc) - delta_map[unit]
    return cutoff.isoformat()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def log_change(
    entity_path: str,
    change_type: str,
    diff: str = "",
    entity_type: str = "other",
    reasoning: str = "",
    agent: str = "",
    session_id: str = "",
    git_commit: str = "",
    project: str = "",
) -> dict:
    """
    Record a change to a codebase entity.

    Call this immediately after making any meaningful change.

    Args:
        entity_path:  Dot-notation path to the entity.
                      Examples:
                        "users.email"           (DB column)
                        "users"                 (DB table)
                        "src/auth.py::login"    (function)
                        "src/auth.py"           (file)
                        "api/v1/users"          (API route)
                        "deps/stripe"           (dependency)
                        "env/STRIPE_SECRET_KEY" (env variable)

        change_type:  One of: add, remove, modify, rename, retype,
                      create, delete, index_add, index_remove, migrate

        diff:         The actual change — SQL migration, code diff, or
                      a human-readable description of what changed.

        entity_type:  One of: column, table, file, function, class,
                      endpoint, dependency, env_var, index, schema,
                      config, other

        reasoning:    Why the change was made. Include the user's original
                      request, the problem being solved, or any context
                      that won't be obvious from the diff alone.

        agent:        Name/ID of the AI agent making the change
                      (e.g. "claude-code", "cursor", "copilot").

        session_id:   The agent session or conversation ID.
        git_commit:   The git commit hash this change will land in.
        project:      Repository or project name.

    Returns:
        dict with id, timestamp, and status "logged".
    """
    storage = get_storage()
    storage.record_tool_call("log_change", entity_path=entity_path)
    event = ChangeEvent(
        entity_path=entity_path,
        change_type=change_type,
        diff=diff,
        entity_type=entity_type,
        reasoning=reasoning,
        agent=agent,
        session_id=session_id,
        git_commit=git_commit,
        project=project,
    )
    stored = storage.log_event(event)
    return {"id": stored.id, "timestamp": stored.timestamp, "status": "logged"}


@mcp.tool()
def diff(entity_path: str, limit: int = 20) -> list[dict]:
    """
    Get change history for a codebase entity.

    Supports prefix matching — "users" returns history for the users
    table AND all its columns (users.email, users.created_at, etc.).

    Args:
        entity_path: Entity path or prefix (e.g. "users", "users.email",
                     "src/auth.py").
        limit:       Maximum number of events to return (default 20).

    Returns:
        List of change events, newest first.
    """
    storage = get_storage()
    storage.record_tool_call("diff", entity_path=entity_path)
    return storage.get_entity_history(entity_path, limit)


@mcp.tool()
def blame(entity_path: str) -> dict:
    """
    Get the most recent change to an entity — what changed, when,
    which agent made the change, and why.

    Like `git blame` but for semantic entities and AI agents.

    Args:
        entity_path: Exact entity path (e.g. "users.email").

    Returns:
        The most recent ChangeEvent for that entity, or an error dict
        if no history exists.
    """
    storage = get_storage()
    storage.record_tool_call("blame", entity_path=entity_path)
    result = storage.get_blame(entity_path)
    if not result:
        return {"error": f"No history found for '{entity_path}'"}
    return result


@mcp.tool()
def history(
    since: str = "",
    entity_path: str = "",
    project: str = "",
    limit: int = 50,
) -> list[dict]:
    """
    Get change history across all entities, with optional filters.

    Args:
        since:        ISO datetime string OR relative shorthand:
                        "7d"  → last 7 days
                        "24h" → last 24 hours
                        "3m"  → last 3 months
                        "1y"  → last year
        entity_path:  Filter to a specific entity or path prefix.
        project:      Filter to a specific project/repository.
        limit:        Maximum number of results (default 50).

    Returns:
        List of change events, newest first.
    """
    storage = get_storage()
    storage.record_tool_call("history", entity_path=entity_path)
    resolved_since = _parse_relative_time(since) if since else ""
    return storage.get_history(
        since=resolved_since,
        entity_path=entity_path,
        project=project,
        limit=limit,
    )


@mcp.tool()
def search(query: str, limit: int = 20) -> list[dict]:
    """
    Full-text search across entity paths, diffs, reasoning, and agents.

    Useful for questions like:
      - "what changes were made for the billing feature?"
      - "which columns were added by cursor?"
      - "show everything related to authentication"

    Args:
        query: Search string (case-insensitive substring match).
        limit: Maximum number of results (default 20).

    Returns:
        List of matching change events, newest first.
    """
    storage = get_storage()
    storage.record_tool_call("search")
    return storage.search(query, limit)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    mcp.run()


if __name__ == "__main__":
    main()
