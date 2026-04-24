"""SQLite storage layer for Selvedge."""

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from .models import ChangeEvent
from .timeutil import normalize_timestamp, utc_now_iso


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id           TEXT PRIMARY KEY,
    timestamp    TEXT NOT NULL,
    entity_type  TEXT NOT NULL DEFAULT 'other',
    entity_path  TEXT NOT NULL,
    change_type  TEXT NOT NULL,
    diff         TEXT NOT NULL DEFAULT '',
    reasoning    TEXT NOT NULL DEFAULT '',
    agent        TEXT NOT NULL DEFAULT '',
    session_id   TEXT NOT NULL DEFAULT '',
    git_commit   TEXT NOT NULL DEFAULT '',
    project      TEXT NOT NULL DEFAULT '',
    changeset_id TEXT NOT NULL DEFAULT '',
    metadata     TEXT NOT NULL DEFAULT '{}'
);
"""

CREATE_TOOL_CALLS_SQL = """
CREATE TABLE IF NOT EXISTS tool_calls (
    id          TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    entity_path TEXT NOT NULL DEFAULT '',
    success     INTEGER NOT NULL DEFAULT 1,
    error_msg   TEXT NOT NULL DEFAULT ''
);
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_entity_path   ON events(entity_path);",
    "CREATE INDEX IF NOT EXISTS idx_timestamp     ON events(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_project       ON events(project);",
    "CREATE INDEX IF NOT EXISTS idx_change_type   ON events(change_type);",
    "CREATE INDEX IF NOT EXISTS idx_tc_tool_name  ON tool_calls(tool_name);",
    "CREATE INDEX IF NOT EXISTS idx_tc_timestamp  ON tool_calls(timestamp);",
]

# Columns added after initial release — applied via _migrate_db() for existing DBs.
# Each entry is (table, column, col_def, post_index_sql | None).
MIGRATION_COLUMNS: list[tuple[str, str, str, str | None]] = [
    (
        "events",
        "changeset_id",
        "TEXT NOT NULL DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS idx_changeset_id ON events(changeset_id);",
    ),
]


# All LIKE patterns use this escape character so that user-supplied input
# containing '_' or '%' isn't interpreted as a wildcard. Without this,
# `search("user_id")` would match `userXid`, `userYid`, etc., and any
# entity name containing an underscore (which is most of them) would
# return false positives.
_LIKE_ESCAPE = "\\"


def _escape_like(s: str) -> str:
    """Escape LIKE wildcards in user input. Pair with ``ESCAPE '\\'`` in SQL."""
    return (
        s.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
         .replace("_", _LIKE_ESCAPE + "_")
         .replace("%", _LIKE_ESCAPE + "%")
    )


class SelvedgeStorage:
    """Thread-safe SQLite-backed event store."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except sqlite3.OperationalError:
            # WAL mode unsupported on some filesystems (e.g. network mounts)
            # Fall back to default DELETE journal mode — still fully functional
            pass
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(CREATE_TABLE_SQL)
            conn.execute(CREATE_TOOL_CALLS_SQL)
            for idx_sql in CREATE_INDEXES_SQL:
                conn.execute(idx_sql)
        self._migrate_db()

    def _migrate_db(self) -> None:
        """Apply additive schema migrations for columns added after initial release."""
        with self._connect() as conn:
            for table, column, col_def, post_index_sql in MIGRATION_COLUMNS:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
                except sqlite3.OperationalError:
                    # Column already exists — expected on fresh DBs or re-runs
                    pass
                if post_index_sql:
                    conn.execute(post_index_sql)

    # ------------------------------------------------------------------
    # Write — change events
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_for_storage(event: ChangeEvent) -> ChangeEvent:
        """Defense-in-depth: normalize the timestamp before insertion."""
        try:
            event.timestamp = normalize_timestamp(event.timestamp)
        except (ValueError, TypeError):
            event.timestamp = utc_now_iso()
        return event

    @staticmethod
    def _event_row(event: ChangeEvent) -> tuple:
        return (
            event.id, event.timestamp, event.entity_type,
            event.entity_path, event.change_type, event.diff,
            event.reasoning, event.agent, event.session_id,
            event.git_commit, event.project, event.changeset_id,
            event.metadata,
        )

    def log_event(self, event: ChangeEvent) -> ChangeEvent:
        """Persist a ChangeEvent and return it (with id/timestamp set)."""
        self._normalize_for_storage(event)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events
                    (id, timestamp, entity_type, entity_path, change_type,
                     diff, reasoning, agent, session_id, git_commit, project,
                     changeset_id, metadata)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                self._event_row(event),
            )
        return event

    def log_event_batch(self, events: Iterable[ChangeEvent]) -> list[ChangeEvent]:
        """
        Persist multiple ChangeEvents in a single transaction.

        Significantly faster than calling :meth:`log_event` in a loop
        when importing large migration histories — one connection, one
        commit, one fsync. Also makes the import atomic: either all
        events land or none do.
        """
        events = list(events)
        if not events:
            return events
        rows = [self._event_row(self._normalize_for_storage(e)) for e in events]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO events
                    (id, timestamp, entity_type, entity_path, change_type,
                     diff, reasoning, agent, session_id, git_commit, project,
                     changeset_id, metadata)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                rows,
            )
        return events

    # ------------------------------------------------------------------
    # Write — tool call telemetry (local only, never networked)
    # ------------------------------------------------------------------

    def backfill_git_commit(self, commit_hash: str, window_minutes: int = 60) -> int:
        """
        Backfill ``git_commit`` on recent events that don't have one yet.

        Finds events logged within the last ``window_minutes`` minutes whose
        ``git_commit`` field is empty and sets it to ``commit_hash``.

        Default window widened to 60 minutes (was 10) so longer-running
        agent sessions still get their events stamped after a commit lands.

        Called automatically by the post-commit git hook installed via
        ``selvedge install-hook``.

        Returns:
            Number of events updated.
        """
        cutoff = normalize_timestamp(
            (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat()
        )
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE events SET git_commit = ? WHERE git_commit = '' AND timestamp >= ?",
                (commit_hash, cutoff),
            )
            return cursor.rowcount

    def count_missing_git_commit(self, since: str = "") -> int:
        """
        Count events with no ``git_commit`` set.

        Used by ``selvedge status`` to nudge users toward installing the
        post-commit hook when events are piling up unstamped — events
        without a commit hash are hard to correlate with code later.
        """
        clauses = ["git_commit = ''"]
        params: list = []
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        sql = f"SELECT COUNT(*) FROM events WHERE {' AND '.join(clauses)}"
        with self._connect() as conn:
            return conn.execute(sql, params).fetchone()[0]

    def record_tool_call(
        self,
        tool_name: str,
        entity_path: str = "",
        success: bool = True,
        error_msg: str = "",
    ) -> None:
        """
        Record a single MCP tool invocation for coverage analysis.

        This is local-only telemetry — nothing leaves the machine.
        Use ``get_tool_stats()`` or ``selvedge stats`` to view coverage.
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO tool_calls
                        (id, timestamp, tool_name, entity_path, success, error_msg)
                    VALUES (?,?,?,?,?,?)
                    """,
                    (
                        str(uuid.uuid4()),
                        utc_now_iso(),
                        tool_name,
                        entity_path,
                        int(success),
                        error_msg,
                    ),
                )
        except Exception:
            # Telemetry must never crash the tool that called it
            pass

    # ------------------------------------------------------------------
    # Read — change events
    # ------------------------------------------------------------------

    def get_entity_history(
        self, entity_path: str, limit: int = 20
    ) -> list[dict]:
        """
        Return change history for an entity or entity prefix.
        e.g. "users" matches "users", "users.email", "users.created_at".
        """
        prefix_pattern = f"{_escape_like(entity_path)}.%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE entity_path = ? OR entity_path LIKE ? ESCAPE '\\'
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (entity_path, prefix_pattern, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_blame(self, entity_path: str) -> Optional[dict]:
        """Return the most recent event for an exact entity path."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM events
                WHERE entity_path = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (entity_path,),
            ).fetchone()
        return dict(row) if row else None

    def get_history(
        self,
        since: str = "",
        entity_path: str = "",
        project: str = "",
        changeset_id: str = "",
        limit: int = 50,
    ) -> list[dict]:
        """Return filtered history across all entities."""
        clauses = ["1=1"]
        params: list = []

        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if entity_path:
            clauses.append("(entity_path = ? OR entity_path LIKE ? ESCAPE '\\')")
            params.extend([entity_path, f"{_escape_like(entity_path)}.%"])
        if project:
            clauses.append("project = ?")
            params.append(project)
        if changeset_id:
            clauses.append("changeset_id = ?")
            params.append(changeset_id)

        params.append(limit)
        sql = f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY timestamp DESC LIMIT ?"

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_changeset(self, changeset_id: str) -> list[dict]:
        """
        Return all events belonging to a changeset, oldest first.

        A changeset groups related changes made as part of a single feature
        or task (e.g. "add stripe billing" touching multiple entities).
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE changeset_id = ? ORDER BY timestamp ASC",
                (changeset_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_changesets(self, project: str = "", since: str = "") -> list[dict]:
        """
        Return a summary of all changesets: id, event count, agent, time range.

        Each entry has: changeset_id, event_count, agent (most common),
        first_event, last_event, project.
        """
        clauses = ["changeset_id != ''"]
        params: list = []
        if project:
            clauses.append("project = ?")
            params.append(project)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)

        where = " AND ".join(clauses)
        sql = f"""
            SELECT
                changeset_id,
                COUNT(*)          AS event_count,
                MIN(timestamp)    AS first_event,
                MAX(timestamp)    AS last_event,
                project
            FROM events
            WHERE {where}
            GROUP BY changeset_id
            ORDER BY last_event DESC
        """
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search across entity_path, diff, and reasoning."""
        pattern = f"%{_escape_like(query)}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE entity_path LIKE ? ESCAPE '\\'
                   OR diff        LIKE ? ESCAPE '\\'
                   OR reasoning   LIKE ? ESCAPE '\\'
                   OR change_type LIKE ? ESCAPE '\\'
                   OR agent       LIKE ? ESCAPE '\\'
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (pattern, pattern, pattern, pattern, pattern, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        """Total number of change events logged."""
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    # ------------------------------------------------------------------
    # Read — tool call telemetry
    # ------------------------------------------------------------------

    def get_tool_stats(self, since: str = "") -> dict:
        """
        Return tool call statistics for coverage analysis.

        Returns a dict with:
          - by_tool:           call count per tool name
          - total_calls:       total MCP tool invocations recorded
          - log_change_calls:  how many of those were log_change
          - log_change_ratio:  log_change / total (0.0–1.0)
          - recent:            10 most recent tool call records
        """
        clause = "WHERE timestamp >= ?" if since else ""
        params = [since] if since else []

        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT tool_name, COUNT(*) as cnt FROM tool_calls {clause} "
                "GROUP BY tool_name ORDER BY cnt DESC",
                params,
            ).fetchall()
            by_tool = {r["tool_name"]: r["cnt"] for r in rows}

            recent_rows = conn.execute(
                f"SELECT timestamp, tool_name, entity_path, success, error_msg "
                f"FROM tool_calls {clause} ORDER BY timestamp DESC LIMIT 10",
                params,
            ).fetchall()
            recent = [dict(r) for r in recent_rows]

        total = sum(by_tool.values())
        log_calls = by_tool.get("log_change", 0)

        return {
            "by_tool": by_tool,
            "total_calls": total,
            "log_change_calls": log_calls,
            "log_change_ratio": round(log_calls / total, 3) if total > 0 else 0.0,
            "recent": recent,
        }
