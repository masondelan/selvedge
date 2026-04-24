"""
Schema migration runner for Selvedge.

Replaces the earlier ``try: ALTER TABLE ... except OperationalError: pass``
pattern with an explicit, versioned migrations table. Each migration runs
exactly once per database, recorded by version number with timestamp, and
each runs in its own transaction so a partial failure leaves a clean state.

Adding a new migration:

  1. Append a new :class:`Migration` to :data:`MIGRATIONS` with the next
     unused version number. **Never edit or reorder existing entries** —
     they're the historical record applied to every Selvedge DB in the wild.
  2. Provide a ``bootstrap_check`` only when the schema change has already
     shipped under the old try/except scheme; the check lets pre-versioning
     databases be marked applied without re-running DDL that would error.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from .timeutil import utc_now_iso

logger = logging.getLogger(__name__)


SCHEMA_MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Migration:
    """A single, immutable schema migration."""

    version: int
    name: str
    statements: tuple[str, ...]
    # If supplied and returns True, the migration is recorded as applied
    # WITHOUT running ``statements``. Used to bootstrap the migrations
    # table on databases created before versioning existed.
    bootstrap_check: Callable[[sqlite3.Connection], bool] | None = None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Return True if ``table`` has a column named ``column``."""
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return False
    # PRAGMA table_info returns rows of (cid, name, type, notnull, dflt_value, pk)
    return any(row[1] == column for row in rows)


# Historical record of every schema change Selvedge has shipped. APPEND-ONLY.
# Edits to existing entries will not re-run on databases that have already
# recorded the version as applied — they will silently diverge.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="add_changeset_id_to_events",
        statements=(
            "ALTER TABLE events ADD COLUMN changeset_id TEXT NOT NULL DEFAULT ''",
            "CREATE INDEX IF NOT EXISTS idx_changeset_id ON events(changeset_id)",
        ),
        # v0.2.1 shipped this column under the old try/except scheme. Fresh
        # 0.3.1+ databases also have the column from CREATE_TABLE_SQL — both
        # cases are detected by checking for the column directly.
        bootstrap_check=lambda conn: _column_exists(conn, "events", "changeset_id"),
    ),
)


def get_applied_versions(conn: sqlite3.Connection) -> set[int]:
    """Return the set of migration versions already applied to this DB."""
    try:
        rows = conn.execute(
            "SELECT version FROM schema_migrations"
        ).fetchall()
    except sqlite3.OperationalError:
        # schema_migrations not yet created
        return set()
    return {row[0] for row in rows}


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """
    Apply all pending migrations against ``conn``.

    Creates the ``schema_migrations`` tracking table if missing. Each
    migration runs in its own transaction (commit on success, rollback on
    error) and is recorded atomically — the ``schema_migrations`` row is
    written in the same transaction as the DDL, so a partial failure
    leaves the database in a known state.

    Returns:
        The list of newly-applied migration version numbers, in order.
    """
    # Tracking table itself — idempotent, safe to run on every open.
    conn.execute(SCHEMA_MIGRATIONS_TABLE_SQL)
    conn.commit()

    applied = get_applied_versions(conn)
    newly_applied: list[int] = []

    for migration in MIGRATIONS:
        if migration.version in applied:
            continue

        # Bootstrap path: the schema change is already present (e.g. a
        # database created before schema_migrations existed, or a fresh
        # database where the change is baked into CREATE_TABLE_SQL).
        # Record it without re-running DDL that would error.
        if migration.bootstrap_check is not None and migration.bootstrap_check(conn):
            try:
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) "
                    "VALUES (?, ?, ?)",
                    (migration.version, migration.name, utc_now_iso()),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                # Another process raced us to the bootstrap. The version is
                # now applied either way, so this is fine.
                conn.rollback()
            newly_applied.append(migration.version)
            logger.info(
                "selvedge.migrations: marked v%d (%s) as applied (bootstrap)",
                migration.version,
                migration.name,
            )
            continue

        # Normal apply path. Wrap explicitly in BEGIN/COMMIT — Python 3.10
        # and 3.11's sqlite3 module auto-commits DDL outside DML
        # transactions, so a partial failure of a multi-statement migration
        # could leave half-applied DDL behind without an explicit transaction.
        try:
            # Make sure no implicit transaction is open before we BEGIN.
            if conn.in_transaction:
                conn.commit()
            conn.execute("BEGIN")
            for stmt in migration.statements:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) "
                "VALUES (?, ?, ?)",
                (migration.version, migration.name, utc_now_iso()),
            )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except sqlite3.Error:
                # Already rolled back / no active transaction — fine
                logger.debug(
                    "selvedge.migrations: rollback after v%d failure was a no-op",
                    migration.version,
                )
            logger.exception(
                "selvedge.migrations: failed to apply v%d (%s)",
                migration.version,
                migration.name,
            )
            raise

        newly_applied.append(migration.version)
        logger.info(
            "selvedge.migrations: applied v%d (%s)",
            migration.version,
            migration.name,
        )

    return newly_applied
