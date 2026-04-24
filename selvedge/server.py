"""
Selvedge MCP Server.

Exposes 6 tools that AI coding agents call to track and query codebase changes:
  - log_change   : record a change event
  - diff         : get change history for an entity
  - blame        : get the most recent change + context for an entity
  - history      : filtered history across all entities
  - changeset    : retrieve all events in a named feature/task group
  - search       : full-text search across all events
"""

import re

from mcp.server.fastmcp import FastMCP

from .config import get_db_path
from .models import ChangeEvent
from .storage import SelvedgeStorage
from .timeutil import parse_time_string

mcp = FastMCP(
    "selvedge",
    instructions=(
        "Selvedge tracks semantic changes to codebases and databases. "
        "Call log_change whenever you add, remove, or modify a meaningful entity "
        "(a DB column, table, function, API endpoint, file, dependency, env var, etc.). "
        "Include as much reasoning as you have — why the change was made, what prompted it. "
        "Use diff, blame, history, changeset, and search to answer questions about past changes."
    ),
)

_storage: SelvedgeStorage | None = None


def get_storage() -> SelvedgeStorage:
    global _storage
    if _storage is None:
        _storage = SelvedgeStorage(get_db_path())
    return _storage


# Patterns that indicate an agent logged a placeholder instead of real reasoning.
# These are checked case-insensitively against the stripped reasoning string.
_GENERIC_REASONING_PATTERNS = [
    r"^user request$",
    r"^as requested$",
    r"^per request$",
    r"^done$",
    r"^updated?$",
    r"^changed?$",
    r"^fixed?$",
    r"^added?$",
    r"^removed?$",
    r"^n/?a$",
    r"^none$",
    r"^todo$",
    r"^see (diff|code|pr)$",
]

_REASONING_MIN_LENGTH = 20


def _check_reasoning_quality(reasoning: str) -> list[str]:
    """
    Return a list of human-readable warning strings if the reasoning field
    looks low-quality. Empty list means the reasoning looks fine.
    """
    warnings: list[str] = []
    stripped = reasoning.strip()

    if not stripped:
        warnings.append(
            "reasoning is empty — log WHY this change was made, not just what. "
            "Include the user's request or the problem being solved."
        )
        return warnings  # No point checking length/patterns if empty

    if len(stripped) < _REASONING_MIN_LENGTH:
        warnings.append(
            f"reasoning is very short ({len(stripped)} chars). "
            "Aim for at least a sentence describing the intent behind this change."
        )

    for pattern in _GENERIC_REASONING_PATTERNS:
        if re.fullmatch(pattern, stripped, re.IGNORECASE):
            warnings.append(
                f"reasoning looks generic ({stripped!r}). "
                "Describe the actual intent: what problem this solves, what the user asked for, "
                "or why this approach was chosen."
            )
            break

    return warnings


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
    changeset_id: str = "",
) -> dict:
    """
    Record a change to a codebase entity.

    Call this immediately after making any meaningful change.

    Args:
        entity_path:  Dot-notation path to the entity. Required and non-empty.
                      Examples:
                        "users.email"           (DB column)
                        "users"                 (DB table)
                        "src/auth.py::login"    (function)
                        "src/auth.py"           (file)
                        "api/v1/users"          (API route)
                        "deps/stripe"           (dependency)
                        "env/STRIPE_SECRET_KEY" (env variable)

        change_type:  One of: add, remove, modify, rename, retype,
                      create, delete, index_add, index_remove, migrate.
                      Invalid values are rejected — pick the closest match.

        diff:         The actual change — SQL migration, code diff, or
                      a human-readable description of what changed.

        entity_type:  One of: column, table, file, function, class,
                      endpoint, dependency, env_var, index, schema,
                      config, other. Unknown values are coerced to "other".

        reasoning:    Why the change was made. Include the user's original
                      request, the problem being solved, or any context
                      that won't be obvious from the diff alone.
                      Good reasoning: "User asked to add 2FA — needs phone
                      number to send SMS verification codes."
                      Avoid generic reasoning like "user request" or "done".

        agent:        Name/ID of the AI agent making the change
                      (e.g. "claude-code", "cursor", "copilot").

        session_id:   The agent session or conversation ID.
        git_commit:   The git commit hash this change will land in.
        project:      Repository or project name.
        changeset_id: Optional grouping ID for related changes that belong
                      to the same feature or task. Generate a UUID or use
                      a short slug like "add-stripe-billing". All events
                      sharing a changeset_id can be queried together via
                      the `changeset` tool.

    Returns:
        dict with id, timestamp, status "logged", and any quality warnings.
        On validation failure returns ``{"status": "error", "error": "..."}``.
    """
    storage = get_storage()
    storage.record_tool_call("log_change", entity_path=entity_path)
    try:
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
            changeset_id=changeset_id,
        )
    except ValueError as e:
        return {"status": "error", "error": str(e)}

    stored = storage.log_event(event)

    warnings = _check_reasoning_quality(reasoning)
    result: dict = {"id": stored.id, "timestamp": stored.timestamp, "status": "logged"}
    if warnings:
        result["warnings"] = warnings
    return result


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
    changeset_id: str = "",
    limit: int = 50,
) -> list[dict]:
    """
    Get change history across all entities, with optional filters.

    Args:
        since:         ISO datetime string OR relative shorthand:
                         "24h" → last 24 hours
                         "7d"  → last 7 days
                         "15m" → last 15 minutes
                         "5mo" → last 5 months (use 'mo' or 'mon')
                         "1y"  → last year
                       Note: 'm' means minutes, 'mo' means months.
                       Unparseable values produce an error rather than
                       silently returning empty results.
        entity_path:   Filter to a specific entity or path prefix.
        project:       Filter to a specific project/repository.
        changeset_id:  Filter to a specific changeset (feature/task group).
        limit:         Maximum number of results (default 50).

    Returns:
        List of change events, newest first. On unparseable ``since`` input,
        returns ``[{"error": "..."}]`` so the caller sees the problem.
    """
    storage = get_storage()
    storage.record_tool_call("history", entity_path=entity_path)
    if since:
        try:
            resolved_since = parse_time_string(since)
        except ValueError as e:
            return [{"error": str(e)}]
    else:
        resolved_since = ""
    return storage.get_history(
        since=resolved_since,
        entity_path=entity_path,
        project=project,
        changeset_id=changeset_id,
        limit=limit,
    )


@mcp.tool()
def changeset(changeset_id: str) -> list[dict]:
    """
    Get all changes that belong to a specific changeset.

    A changeset groups related changes made as part of a single feature
    or task. For example, "add-stripe-billing" might include events for
    a new DB table, several columns, a new endpoint, and a dependency.

    Args:
        changeset_id: The changeset identifier (as passed to log_change).

    Returns:
        List of change events in the changeset, oldest first.
        Returns an error dict if the changeset has no events.
    """
    storage = get_storage()
    storage.record_tool_call("changeset")
    events = storage.get_changeset(changeset_id)
    if not events:
        return [{"error": f"No events found for changeset '{changeset_id}'"}]
    return events


@mcp.tool()
def search(query: str, limit: int = 20) -> list[dict]:
    """
    Full-text search across entity paths, diffs, reasoning, and agents.

    Useful for questions like:
      - "what changes were made for the billing feature?"
      - "which columns were added by cursor?"
      - "show everything related to authentication"

    SQL LIKE wildcards in the query (``_`` and ``%``) are escaped, so
    searching for ``stripe_customer_id`` matches the literal underscore
    rather than any single character.

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
