"""
Concurrency safety tests for SelvedgeStorage.

These exercise the connection-with-retry path and the WAL-mode + busy_timeout
configuration: spawn N threads writing simultaneously into the same SQLite
file and assert that every event lands. Without WAL mode + busy_timeout +
the application-level retry decorator, multi-threaded writers will reliably
hit ``database is locked`` errors.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from selvedge import storage as storage_mod
from selvedge.models import ChangeEvent
from selvedge.storage import (
    SelvedgeStorage,
    _is_locked_error,
    _retry_on_locked,
)

# ---------------------------------------------------------------------------
# Threaded write tests — single-row and batch
# ---------------------------------------------------------------------------


def test_concurrent_log_event_all_persist(tmp_path: Path) -> None:
    """N threads each writing M events: all N*M land in the DB."""
    db_path = tmp_path / "concurrent.db"
    SelvedgeStorage(db_path)  # initialize schema once

    n_threads = 8
    events_per_thread = 25
    expected = n_threads * events_per_thread

    errors: list[BaseException] = []

    def writer(thread_id: int) -> None:
        # Each thread opens its own SelvedgeStorage — sqlite3 connections
        # are per-thread by default (check_same_thread=True).
        local = SelvedgeStorage(db_path)
        try:
            for i in range(events_per_thread):
                local.log_event(ChangeEvent(
                    entity_path=f"thread{thread_id}.col{i}",
                    change_type="add",
                    reasoning=f"thread {thread_id} event {i}",
                ))
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(i,), name=f"writer-{i}")
        for i in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"writer errors: {errors!r}"
    reader = SelvedgeStorage(db_path)
    assert reader.count() == expected


def test_concurrent_log_event_batch_all_persist(tmp_path: Path) -> None:
    """Batch writers must not corrupt the DB or lose events."""
    db_path = tmp_path / "concurrent_batch.db"
    SelvedgeStorage(db_path)

    n_threads = 4
    batch_size = 30
    expected = n_threads * batch_size

    errors: list[BaseException] = []

    def writer(thread_id: int) -> None:
        local = SelvedgeStorage(db_path)
        try:
            events = [
                ChangeEvent(
                    entity_path=f"thread{thread_id}.col{i}",
                    change_type="add",
                    reasoning=f"batch {thread_id}.{i}",
                )
                for i in range(batch_size)
            ]
            local.log_event_batch(events)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(i,), name=f"batch-{i}")
        for i in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"writer errors: {errors!r}"
    reader = SelvedgeStorage(db_path)
    assert reader.count() == expected


def test_concurrent_mixed_read_write(tmp_path: Path) -> None:
    """Readers and writers running in parallel must not deadlock or error."""
    db_path = tmp_path / "concurrent_mixed.db"
    storage = SelvedgeStorage(db_path)

    # Seed something so readers always have rows to scan
    storage.log_event(ChangeEvent(entity_path="seed.col", change_type="add"))

    n_writers = 4
    n_readers = 4
    writes_per_writer = 20
    reads_per_reader = 30

    errors: list[BaseException] = []

    def writer(thread_id: int) -> None:
        local = SelvedgeStorage(db_path)
        try:
            for i in range(writes_per_writer):
                local.log_event(ChangeEvent(
                    entity_path=f"w{thread_id}.col{i}",
                    change_type="add",
                ))
        except BaseException as exc:
            errors.append(exc)

    def reader(thread_id: int) -> None:
        local = SelvedgeStorage(db_path)
        try:
            for _ in range(reads_per_reader):
                local.get_history(limit=10)
                local.search("col")
        except BaseException as exc:
            errors.append(exc)

    threads = (
        [threading.Thread(target=writer, args=(i,)) for i in range(n_writers)]
        + [threading.Thread(target=reader, args=(i,)) for i in range(n_readers)]
    )
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"errors during mixed read/write: {errors!r}"
    # 1 seed + n_writers * writes_per_writer
    assert storage.count() == 1 + n_writers * writes_per_writer


# ---------------------------------------------------------------------------
# Retry decorator unit tests
# ---------------------------------------------------------------------------


def test_is_locked_error_recognizes_lock_messages() -> None:
    assert _is_locked_error(sqlite3.OperationalError("database is locked"))
    assert _is_locked_error(sqlite3.OperationalError("database is busy"))
    # Non-lock OperationalErrors should not be retried
    assert not _is_locked_error(sqlite3.OperationalError("no such table: foo"))
    # Other exception types are not retryable
    assert not _is_locked_error(ValueError("nope"))


def test_retry_on_locked_succeeds_after_transient_lock(monkeypatch) -> None:
    """A function that fails once with `database is locked` then succeeds is retried."""
    # Speed up the test by zeroing the backoff
    monkeypatch.setattr(storage_mod, "_RETRY_INITIAL_BACKOFF", 0.0)
    monkeypatch.setattr(storage_mod, "_RETRY_BACKOFF_MAX", 0.0)

    calls = {"n": 0}

    @_retry_on_locked
    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 2


def test_retry_on_locked_gives_up_after_max_attempts(monkeypatch) -> None:
    monkeypatch.setattr(storage_mod, "_RETRY_INITIAL_BACKOFF", 0.0)
    monkeypatch.setattr(storage_mod, "_RETRY_BACKOFF_MAX", 0.0)

    @_retry_on_locked
    def always_locked() -> None:
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        always_locked()


def test_retry_on_locked_does_not_swallow_other_errors() -> None:
    """Non-lock errors must propagate without being retried."""
    calls = {"n": 0}

    @_retry_on_locked
    def explodes() -> None:
        calls["n"] += 1
        raise sqlite3.OperationalError("no such table: foo")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        explodes()
    assert calls["n"] == 1  # no retry for non-lock errors


# ---------------------------------------------------------------------------
# _session context manager — connection lifecycle
# ---------------------------------------------------------------------------


def test_session_commits_on_success(tmp_path: Path) -> None:
    storage = SelvedgeStorage(tmp_path / "session.db")
    with storage._session() as conn:
        conn.execute(
            "INSERT INTO events (id, timestamp, entity_path, change_type) "
            "VALUES (?, ?, ?, ?)",
            ("x", "2025-01-01T00:00:00Z", "manual.entity", "add"),
        )
    assert storage.count() == 1


def test_session_rolls_back_on_error(tmp_path: Path) -> None:
    storage = SelvedgeStorage(tmp_path / "rollback.db")
    with pytest.raises(RuntimeError):
        with storage._session() as conn:
            conn.execute(
                "INSERT INTO events (id, timestamp, entity_path, change_type) "
                "VALUES (?, ?, ?, ?)",
                ("x", "2025-01-01T00:00:00Z", "manual.entity", "add"),
            )
            raise RuntimeError("boom")
    # The row should NOT have been committed
    assert storage.count() == 0


# ---------------------------------------------------------------------------
# Concurrent open of an UN-UPGRADED database
#
# Every test above pre-initializes the schema before spawning workers, so
# `apply_migrations` short-circuits and its BEGIN is never entered
# concurrently. The path that matters in production is the opposite one: a
# user upgrades the package, and the next N agent processes (CLI, MCP server,
# and the PreToolUse hook, which constructs storage on every gated tool call)
# all open a database with migrations still pending.
# ---------------------------------------------------------------------------


# The v0.3.7 `events` table, spelled out. Deliberately NOT derived from the
# current CREATE_TABLE_SQL by dropping columns: `ALTER TABLE ... DROP COLUMN`
# re-parses the table definition and its behavior varies across the SQLite
# versions in the support matrix, which made this fixture pass on one and fail
# on another. A literal legacy schema is both version-independent and a more
# faithful simulation of what a pre-upgrade store actually looks like.
_PRE_V3_EVENTS_SQL = """
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


def _build_pre_v3_db(db_path: Path) -> None:
    """Simulate a v0.3.7-shaped store: migrations {1,2} applied, v3/v4 pending."""
    from selvedge.migrations import SCHEMA_MIGRATIONS_TABLE_SQL

    con = sqlite3.connect(db_path)
    try:
        con.executescript(_PRE_V3_EVENTS_SQL)
        con.execute(SCHEMA_MIGRATIONS_TABLE_SQL)
        for version in (1, 2):
            con.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, name, applied_at) "
                "VALUES (?, ?, ?)",
                (version, f"v{version}", "2026-01-01T00:00:00Z"),
            )
        con.commit()
    finally:
        con.close()
    # Guard the fixture itself: if a future schema change makes these columns
    # present from the start, the test would silently stop exercising the race.
    con = sqlite3.connect(db_path)
    try:
        present = {r[1] for r in con.execute("PRAGMA table_info(events)")}
    finally:
        con.close()
    assert not {"revisit_after", "supersedes", "stale_when"} & present, (
        "fixture is not pre-v3; the migration race is no longer being exercised"
    )


def test_stale_pre_lock_read_does_not_rerun_the_migration(
    tmp_path: Path, monkeypatch
) -> None:
    """The race, made deterministic.

    The threaded test below reproduces this only sometimes — the window is
    genuinely narrow — so it is a smoke check, not the guard. This is the
    guard: it forces the exact interleaving the bug needs.

    `apply_migrations` reads "which versions are applied?" before taking any
    write lock. If another process applies the migration in between, that read
    is stale. Here the first read is pinned to a stale snapshot while the
    database really does get migrated underneath, which is precisely what a
    losing process saw. Correct behavior is to notice under the lock and skip;
    the bug re-ran the `ALTER TABLE` and died on `duplicate column name`.
    """
    from selvedge import migrations as migrations_mod

    db_path = tmp_path / "stale-read.db"
    _build_pre_v3_db(db_path)

    # Another process gets there first and fully migrates the store.
    SelvedgeStorage(db_path)

    # Pin BOTH pre-lock reads to the stale view a losing process had: the
    # applied-set, and the bootstrap probe that asks "is the column already
    # there?". Each returns its stale answer once — the pre-lock evaluation —
    # and the truth thereafter, which is exactly what the loser observed after
    # blocking on the writer lock.
    real_get_applied = migrations_mod.get_applied_versions
    real_column_exists = migrations_mod._column_exists
    applied_calls = {"n": 0}
    stale_columns: set[str] = set()

    def stale_first_applied(conn):
        applied_calls["n"] += 1
        if applied_calls["n"] == 1:
            return {1, 2}
        return real_get_applied(conn)

    def stale_first_column(conn, table, column):
        if column not in stale_columns:
            stale_columns.add(column)
            return False
        return real_column_exists(conn, table, column)

    monkeypatch.setattr(migrations_mod, "get_applied_versions", stale_first_applied)
    monkeypatch.setattr(migrations_mod, "_column_exists", stale_first_column)

    conn = sqlite3.connect(db_path)
    try:
        migrations_mod.apply_migrations(conn)   # must not raise
    finally:
        conn.close()

    assert applied_calls["n"] >= 2, "the applied-set was never re-read under the lock"

    con = sqlite3.connect(db_path)
    try:
        applied = {r[0] for r in con.execute("SELECT version FROM schema_migrations")}
        columns = {r[1] for r in con.execute("PRAGMA table_info(events)")}
    finally:
        con.close()
    assert applied == {1, 2, 3, 4}
    assert {"revisit_after", "supersedes", "stale_when"} <= columns


@pytest.mark.parametrize("trial", range(4))
def test_concurrent_open_of_pending_migration_db(tmp_path: Path, trial: int) -> None:
    """N processes opening a pending-migration DB must all succeed.

    Before the BEGIN IMMEDIATE fix this failed on roughly half of trials, with
    up to 7 of 8 threads raising ``duplicate column name: revisit_after`` —
    the losers evaluated "is this migration applied?" outside the write lock,
    then re-ran the ALTER TABLE after the winner had already committed it.
    The error is not a lock error, so the retry decorator never saw it and it
    propagated straight out of the SelvedgeStorage constructor.
    """
    db_path = tmp_path / f"pending-{trial}.db"
    _build_pre_v3_db(db_path)

    n_threads = 8
    errors: list[BaseException] = []
    barrier = threading.Barrier(n_threads)

    def worker() -> None:
        barrier.wait()
        try:
            SelvedgeStorage(db_path)
        except BaseException as exc:  # noqa: BLE001 - recording for the assert
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"{len(errors)}/{n_threads} opens failed: {errors[0]!r}"

    con = sqlite3.connect(db_path)
    try:
        applied = {r[0] for r in con.execute("SELECT version FROM schema_migrations")}
        columns = {r[1] for r in con.execute("PRAGMA table_info(events)")}
    finally:
        con.close()
    assert applied == {1, 2, 3, 4}
    assert {"revisit_after", "supersedes", "stale_when"} <= columns
