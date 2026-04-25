"""
Tests for the schema-migrations runner.

The runner replaces the earlier ``try: ALTER ... except: pass`` pattern with
an explicit ``schema_migrations`` table tracking applied versions. These
tests lock in:

  - Fresh DB → all known migrations recorded as applied
  - Bootstrap path: DB created before migrations existed (changeset_id
    column already present) is marked applied without re-running DDL
  - Re-init never duplicates a migration record
  - Failing a migration leaves no partial state in schema_migrations
  - Migrations are append-only — version numbers must be unique and
    monotonically increasing
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from selvedge import migrations as mig
from selvedge.migrations import (
    MIGRATIONS,
    Migration,
    apply_migrations,
    get_applied_versions,
)
from selvedge.storage import SelvedgeStorage

# ---------------------------------------------------------------------------
# Static structure of the MIGRATIONS list — append-only invariant
# ---------------------------------------------------------------------------


def test_migrations_have_unique_versions() -> None:
    versions = [m.version for m in MIGRATIONS]
    assert len(versions) == len(set(versions)), "duplicate migration version found"


def test_migrations_are_monotonically_increasing() -> None:
    versions = [m.version for m in MIGRATIONS]
    assert versions == sorted(versions), "MIGRATIONS list is not in version order"


def test_migrations_have_nonempty_statements() -> None:
    for m in MIGRATIONS:
        assert m.statements, f"migration v{m.version} ({m.name}) has no statements"


# ---------------------------------------------------------------------------
# Fresh DB — every shipped migration ends up recorded
# ---------------------------------------------------------------------------


def test_fresh_db_marks_all_migrations_applied(tmp_path: Path) -> None:
    SelvedgeStorage(tmp_path / "fresh.db")
    conn = sqlite3.connect(str(tmp_path / "fresh.db"))
    try:
        applied = get_applied_versions(conn)
        expected = {m.version for m in MIGRATIONS}
        assert applied == expected
    finally:
        conn.close()


def test_re_init_does_not_re_apply(tmp_path: Path) -> None:
    """Opening the same DB twice must not duplicate migration records."""
    SelvedgeStorage(tmp_path / "reinit.db")
    SelvedgeStorage(tmp_path / "reinit.db")  # second open = re-init

    conn = sqlite3.connect(str(tmp_path / "reinit.db"))
    try:
        rows = conn.execute(
            "SELECT version, COUNT(*) FROM schema_migrations GROUP BY version"
        ).fetchall()
        for version, count in rows:
            assert count == 1, f"migration v{version} recorded {count} times"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Bootstrap path — pre-versioning DB with changeset_id already present
# ---------------------------------------------------------------------------


def _make_legacy_db_with_changeset_id(db_path: Path) -> None:
    """Build a DB that has the v0.2.1 schema but no schema_migrations table."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE events (
                id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
                entity_type TEXT NOT NULL DEFAULT 'other',
                entity_path TEXT NOT NULL, change_type TEXT NOT NULL,
                diff TEXT NOT NULL DEFAULT '', reasoning TEXT NOT NULL DEFAULT '',
                agent TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '',
                git_commit TEXT NOT NULL DEFAULT '', project TEXT NOT NULL DEFAULT '',
                changeset_id TEXT NOT NULL DEFAULT '',
                metadata TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE tool_calls (
                id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
                tool_name TEXT NOT NULL, entity_path TEXT NOT NULL DEFAULT '',
                success INTEGER NOT NULL DEFAULT 1, error_msg TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_bootstrap_marks_changeset_id_applied_without_rerun(tmp_path: Path) -> None:
    """If changeset_id already exists, the migration is recorded as applied
    without trying to re-add the column (which would raise OperationalError)."""
    db_path = tmp_path / "legacy.db"
    _make_legacy_db_with_changeset_id(db_path)

    # Should not raise even though the ALTER would fail
    SelvedgeStorage(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        applied = get_applied_versions(conn)
        assert 1 in applied
    finally:
        conn.close()


def _make_pre_changeset_db(db_path: Path) -> None:
    """Build a DB with the v0.2.0 schema (no changeset_id, no migrations)."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE events (
                id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
                entity_type TEXT NOT NULL DEFAULT 'other',
                entity_path TEXT NOT NULL, change_type TEXT NOT NULL,
                diff TEXT NOT NULL DEFAULT '', reasoning TEXT NOT NULL DEFAULT '',
                agent TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '',
                git_commit TEXT NOT NULL DEFAULT '', project TEXT NOT NULL DEFAULT '',
                metadata TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_pre_changeset_db_runs_migration_then_records(tmp_path: Path) -> None:
    """A truly pre-changeset_id DB has the column added AND the migration recorded."""
    db_path = tmp_path / "pre_changeset.db"
    _make_pre_changeset_db(db_path)

    SelvedgeStorage(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        # changeset_id column now exists
        cols = [row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()]
        assert "changeset_id" in cols
        # ...and migration v1 is recorded
        applied = get_applied_versions(conn)
        assert 1 in applied
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Failure path — partial migration must roll back cleanly
# ---------------------------------------------------------------------------


def test_failed_migration_does_not_record_version(tmp_path: Path) -> None:
    """A migration that raises must NOT appear in schema_migrations."""
    db_path = tmp_path / "fail.db"
    SelvedgeStorage(db_path)  # baseline

    bad = Migration(
        version=999,
        name="intentionally_broken",
        statements=("CREATE TABLE this_will_succeed (x INT)",
                    "THIS IS NOT VALID SQL"),
    )

    conn = sqlite3.connect(str(db_path))
    try:
        # Patch MIGRATIONS for the duration of this call
        original = mig.MIGRATIONS
        mig.MIGRATIONS = original + (bad,)
        try:
            with pytest.raises(sqlite3.OperationalError):
                apply_migrations(conn)
        finally:
            mig.MIGRATIONS = original

        applied = get_applied_versions(conn)
        assert 999 not in applied
        # And the partial side-effect (the first statement's table) was rolled back
        tables = [
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "this_will_succeed" not in tables
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# v0.3.2 — agent column on tool_calls (migration v2)
# ---------------------------------------------------------------------------


def _make_pre_v2_db(db_path: Path) -> None:
    """Build a DB with the v0.3.1 schema (no `agent` column on tool_calls)."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE events (
                id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
                entity_type TEXT NOT NULL DEFAULT 'other',
                entity_path TEXT NOT NULL, change_type TEXT NOT NULL,
                diff TEXT NOT NULL DEFAULT '', reasoning TEXT NOT NULL DEFAULT '',
                agent TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '',
                git_commit TEXT NOT NULL DEFAULT '', project TEXT NOT NULL DEFAULT '',
                changeset_id TEXT NOT NULL DEFAULT '',
                metadata TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE tool_calls (
                id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
                tool_name TEXT NOT NULL, entity_path TEXT NOT NULL DEFAULT '',
                success INTEGER NOT NULL DEFAULT 1, error_msg TEXT NOT NULL DEFAULT ''
            )
            """
        )
        # Mark v1 as applied — this DB shipped after the changeset_id migration
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
            "name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) "
            "VALUES (1, 'add_changeset_id_to_events', '2026-01-01T00:00:00Z')"
        )
        conn.commit()
    finally:
        conn.close()


def test_v2_adds_agent_column_to_tool_calls(tmp_path: Path) -> None:
    """A v0.3.1 DB upgraded to v0.3.2 gets the agent column added."""
    db_path = tmp_path / "pre_v2.db"
    _make_pre_v2_db(db_path)

    SelvedgeStorage(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        cols = [
            row[1] for row in conn.execute("PRAGMA table_info(tool_calls)").fetchall()
        ]
        assert "agent" in cols, f"agent column missing from tool_calls: {cols}"
        applied = get_applied_versions(conn)
        assert 2 in applied
    finally:
        conn.close()


def test_v2_bootstrap_on_fresh_db(tmp_path: Path) -> None:
    """Fresh DBs already have the agent column from CREATE_TOOL_CALLS_SQL —
    migration v2 is recorded via bootstrap_check, not by re-running ALTER."""
    db_path = tmp_path / "fresh.db"
    SelvedgeStorage(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        applied = get_applied_versions(conn)
        assert 2 in applied
        cols = [
            row[1] for row in conn.execute("PRAGMA table_info(tool_calls)").fetchall()
        ]
        assert "agent" in cols
    finally:
        conn.close()


def test_latest_version_includes_v2() -> None:
    from selvedge.migrations import latest_version
    assert latest_version() >= 2
