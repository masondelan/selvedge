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
