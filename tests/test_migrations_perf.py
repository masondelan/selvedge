"""
Performance gates for schema migration v3 (v0.3.8, Phase 2.14).

The v3 migration adds two TEXT columns to ``events`` via ``ALTER TABLE ADD
COLUMN``. In SQLite that's a metadata-only edit — the table is NOT rewritten —
so the migration stays fast no matter how many rows exist. These tests lock
that property in at 10k / 100k / 1M synthetic events.

The load-bearing guard is **structural and version-independent**: we assert the
events table's page allocation barely changes across ``apply_migrations``
(``PRAGMA page_count`` delta ≈ 0, only a page or two of migration bookkeeping).
A regression that turned the migration into a row-rewriting operation — a
data-backfilling ``UPDATE`` across every row, or a non-constant ``DEFAULT`` that
forces SQLite to rematerialize the table — would balloon page_count by
thousands of pages at 1M rows and fail here. (Note: a constant ``DEFAULT`` or
``NOT NULL`` ``ADD COLUMN`` is ALSO metadata-only on modern SQLite, so a
wall-clock gate alone could not catch that class of change — hence the page_count
assertion.) The time gates remain as a coarse secondary smoke.

Synthetic rows are generated with a recursive CTE (pure SQL, no Python-side
row loop) so building the fixture doesn't dominate the run. Only the
``apply_migrations`` call is timed — fixture build time is excluded.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from selvedge.migrations import apply_migrations, get_applied_versions

# Generous wall-clock ceilings. The real migration is sub-second even at 1M
# rows; these bounds only need to fail if it regresses into an O(rows) rewrite,
# while staying comfortable on a loaded CI runner.
_GATES = [
    (10_000, 5.0),
    (100_000, 8.0),
    (1_000_000, 20.0),
]


def _build_pre_v3_db(db_path: Path, n_events: int) -> None:
    """Create a v0.3.7-schema DB (no revisit columns) with ``n_events`` rows.

    v1 and v2 are pre-recorded so ``apply_migrations`` only has v3 left to do.
    """
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
            "CREATE TABLE tool_calls (id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, "
            "tool_name TEXT NOT NULL, entity_path TEXT NOT NULL DEFAULT '', "
            "success INTEGER NOT NULL DEFAULT 1, error_msg TEXT NOT NULL DEFAULT '', "
            "agent TEXT NOT NULL DEFAULT '')"
        )
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
            "name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?,?,?)",
            [
                (1, "add_changeset_id_to_events", "2026-01-01T00:00:00Z"),
                (2, "add_agent_to_tool_calls", "2026-01-01T00:00:00Z"),
            ],
        )
        conn.execute(
            """
            INSERT INTO events (id, timestamp, entity_type, entity_path, change_type)
            WITH RECURSIVE seq(n) AS (
                SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < ?
            )
            SELECT 'id' || n, '2026-01-01T00:00:00Z', 'other', 'e' || n, 'add'
            FROM seq
            """,
            (n_events,),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("n_events,max_seconds", _GATES)
def test_v3_migration_time_is_bounded(tmp_path, n_events, max_seconds):
    db = tmp_path / f"perf_{n_events}.db"
    _build_pre_v3_db(db, n_events)

    conn = sqlite3.connect(str(db))
    try:
        pages_before = conn.execute("PRAGMA page_count").fetchone()[0]

        start = time.perf_counter()
        applied = apply_migrations(conn)
        elapsed = time.perf_counter() - start

        pages_after = conn.execute("PRAGMA page_count").fetchone()[0]

        assert 3 in applied, "v3 should have been applied"
        # Structural guard: a metadata-only ADD COLUMN doesn't touch row pages.
        # Allow a tiny slack for the schema_migrations bookkeeping INSERT; a
        # real row-rewrite at this size would add thousands of pages.
        assert pages_after - pages_before <= 16, (
            f"v3 migration grew page_count by {pages_after - pages_before} pages "
            f"for {n_events:,} events — it rewrote/backfilled rows instead of "
            f"doing a metadata-only ADD COLUMN."
        )
        # Coarse secondary smoke: should be near-instant regardless of N.
        assert elapsed < max_seconds, (
            f"v3 migration took {elapsed:.3f}s for {n_events:,} events "
            f"(gate {max_seconds}s)."
        )
    finally:
        conn.close()


def test_v3_migration_backfills_null_without_rewriting_rows(tmp_path):
    """Every pre-existing row reads back NULL on both new columns, untouched."""
    db = tmp_path / "backfill.db"
    n = 100_000
    _build_pre_v3_db(db, n)

    conn = sqlite3.connect(str(db))
    try:
        apply_migrations(conn)
        assert 3 in get_applied_versions(conn)
        total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        nulls = conn.execute(
            "SELECT COUNT(*) FROM events "
            "WHERE revisit_after IS NULL AND expires_when IS NULL"
        ).fetchone()[0]
        assert total == n
        assert nulls == n  # all existing rows backfilled to NULL, none rewritten
    finally:
        conn.close()
