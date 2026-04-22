"""SQLite storage layer for Selvedge."""

import sqlite3
from pathlib import Path
from typing import Optional
from .models import ChangeEvent


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    entity_type TEXT NOT NULL DEFAULT 'other',
    entity_path TEXT NOT NULL,
    change_type TEXT NOT NULL,
    diff        TEXT NOT NULL DEFAULT '',
    reasoning   TEXT NOT NULL DEFAULT '',
    agent       TEXT NOT NULL DEFAULT '',
    session_id  TEXT NOT NULL DEFAULT '',
    git_commit  TEXT NOT NULL DEFAULT '',
    project     TEXT NOT NULL DEFAULT '',
    metadata    TEXT NOT NULL DEFAULT '{}'
);
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_entity_path ON events(entity_path);",
    "CREATE INDEX IF NOT EXISTS idx_timestamp   ON events(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_project     ON events(project);",
    "CREATE INDEX IF NOT EXISTS idx_change_type ON events(change_type);",
]


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
            for idx_sql in CREATE_INDEXES_SQL:
                conn.execute(idx_sql)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def log_event(self, event: ChangeEvent) -> ChangeEvent:
        """Persist a ChangeEvent and return it (with id/timestamp set)."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events
                    (id, timestamp, entity_type, entity_path, change_type,
                     diff, reasoning, agent, session_id, git_commit, project, metadata)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.id, event.timestamp, event.entity_type,
                    event.entity_path, event.change_type, event.diff,
                    event.reasoning, event.agent, event.session_id,
                    event.git_commit, event.project, event.metadata,
                ),
            )
        return event

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_entity_history(
        self, entity_path: str, limit: int = 20
    ) -> list[dict]:
        """
        Return change history for an entity or entity prefix.
        e.g. "users" matches "users", "users.email", "users.created_at".
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE entity_path = ? OR entity_path LIKE ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (entity_path, f"{entity_path}.%", limit),
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
        limit: int = 50,
    ) -> list[dict]:
        """Return filtered history across all entities."""
        clauses = ["1=1"]
        params: list = []

        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if entity_path:
            clauses.append("(entity_path = ? OR entity_path LIKE ?)")
            params.extend([entity_path, f"{entity_path}.%"])
        if project:
            clauses.append("project = ?")
            params.append(project)

        params.append(limit)
        sql = f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY timestamp DESC LIMIT ?"

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search across entity_path, diff, and reasoning."""
        pattern = f"%{query}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE entity_path LIKE ?
                   OR diff        LIKE ?
                   OR reasoning   LIKE ?
                   OR change_type LIKE ?
                   OR agent       LIKE ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (pattern, pattern, pattern, pattern, pattern, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        """Total number of events logged."""
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
