"""Tests for the SelvedgeStorage layer."""

from pathlib import Path

import pytest

from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage


@pytest.fixture
def storage(tmp_path: Path) -> SelvedgeStorage:
    return SelvedgeStorage(tmp_path / "test.db")


# ---------------------------------------------------------------------------
# log_event
# ---------------------------------------------------------------------------


def test_log_event_returns_event_with_id(storage):
    event = ChangeEvent(entity_path="users.email", change_type="add")
    stored = storage.log_event(event)
    assert stored.id
    assert stored.timestamp


def test_log_event_persists(storage):
    storage.log_event(ChangeEvent(entity_path="users.email", change_type="add"))
    assert storage.count() == 1


def test_log_multiple_events(storage):
    storage.log_event(ChangeEvent(entity_path="users.email", change_type="add"))
    storage.log_event(ChangeEvent(entity_path="users.name", change_type="add"))
    storage.log_event(ChangeEvent(entity_path="payments.amount", change_type="add"))
    assert storage.count() == 3


# ---------------------------------------------------------------------------
# get_entity_history
# ---------------------------------------------------------------------------


def test_entity_history_exact_match(storage):
    storage.log_event(ChangeEvent(entity_path="users.email", change_type="add"))
    storage.log_event(ChangeEvent(entity_path="users.name", change_type="add"))

    rows = storage.get_entity_history("users.email")
    assert len(rows) == 1
    assert rows[0]["entity_path"] == "users.email"


def test_entity_history_prefix_match(storage):
    storage.log_event(ChangeEvent(entity_path="users.email", change_type="add"))
    storage.log_event(ChangeEvent(entity_path="users.name", change_type="add"))
    storage.log_event(ChangeEvent(entity_path="payments.amount", change_type="add"))

    rows = storage.get_entity_history("users")
    assert len(rows) == 2
    paths = {r["entity_path"] for r in rows}
    assert paths == {"users.email", "users.name"}


def test_entity_history_ordered_newest_first(storage):
    e1 = ChangeEvent(entity_path="users.email", change_type="add")
    e1.timestamp = "2024-01-01T00:00:00+00:00"
    storage.log_event(e1)

    e2 = ChangeEvent(entity_path="users.email", change_type="modify")
    e2.timestamp = "2025-01-01T00:00:00+00:00"
    storage.log_event(e2)

    rows = storage.get_entity_history("users.email")
    assert rows[0]["change_type"] == "modify"
    assert rows[1]["change_type"] == "add"


def test_entity_history_limit(storage):
    for _i in range(10):
        storage.log_event(ChangeEvent(entity_path="users.email", change_type="modify"))
    rows = storage.get_entity_history("users.email", limit=3)
    assert len(rows) == 3


def test_entity_history_empty(storage):
    assert storage.get_entity_history("nonexistent.column") == []


# ---------------------------------------------------------------------------
# get_blame
# ---------------------------------------------------------------------------


def test_blame_returns_most_recent(storage):
    e1 = ChangeEvent(entity_path="users.email", change_type="add", reasoning="initial")
    e1.timestamp = "2024-01-01T00:00:00+00:00"
    storage.log_event(e1)

    e2 = ChangeEvent(entity_path="users.email", change_type="modify", reasoning="updated")
    e2.timestamp = "2025-06-01T00:00:00+00:00"
    storage.log_event(e2)

    blame = storage.get_blame("users.email")
    assert blame["reasoning"] == "updated"
    assert blame["change_type"] == "modify"


def test_blame_returns_none_for_unknown(storage):
    assert storage.get_blame("nonexistent.column") is None


# ---------------------------------------------------------------------------
# get_history (filtered)
# ---------------------------------------------------------------------------


def test_history_since_filter(storage):
    e_old = ChangeEvent(entity_path="users.email", change_type="add")
    e_old.timestamp = "2023-01-01T00:00:00+00:00"
    storage.log_event(e_old)

    e_new = ChangeEvent(entity_path="users.name", change_type="add")
    e_new.timestamp = "2025-06-01T00:00:00+00:00"
    storage.log_event(e_new)

    rows = storage.get_history(since="2024-01-01T00:00:00+00:00")
    assert len(rows) == 1
    assert rows[0]["entity_path"] == "users.name"


def test_history_entity_filter(storage):
    storage.log_event(ChangeEvent(entity_path="users.email", change_type="add"))
    storage.log_event(ChangeEvent(entity_path="payments.amount", change_type="add"))

    rows = storage.get_history(entity_path="users")
    assert len(rows) == 1
    assert rows[0]["entity_path"] == "users.email"


def test_history_project_filter(storage):
    storage.log_event(ChangeEvent(entity_path="users.email", change_type="add", project="api"))
    storage.log_event(ChangeEvent(entity_path="orders.total", change_type="add", project="shop"))

    rows = storage.get_history(project="api")
    assert len(rows) == 1
    assert rows[0]["project"] == "api"


def test_history_no_filter_returns_all(storage):
    for path in ["a.x", "b.y", "c.z"]:
        storage.log_event(ChangeEvent(entity_path=path, change_type="add"))
    assert len(storage.get_history()) == 3


def test_history_limit(storage):
    for i in range(20):
        storage.log_event(ChangeEvent(entity_path=f"t.col{i}", change_type="add"))
    assert len(storage.get_history(limit=5)) == 5


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_search_by_reasoning(storage):
    storage.log_event(ChangeEvent(
        entity_path="payments.amount", change_type="add", reasoning="billing feature for stripe"
    ))
    storage.log_event(ChangeEvent(
        entity_path="users.email", change_type="add", reasoning="auth feature"
    ))

    rows = storage.search("billing")
    assert len(rows) == 1
    assert rows[0]["entity_path"] == "payments.amount"


def test_search_by_entity_path(storage):
    storage.log_event(ChangeEvent(entity_path="payments.amount", change_type="add"))
    storage.log_event(ChangeEvent(entity_path="users.email", change_type="add"))

    rows = storage.search("payments")
    assert len(rows) == 1
    assert rows[0]["entity_path"] == "payments.amount"


def test_search_by_diff(storage):
    storage.log_event(ChangeEvent(
        entity_path="users.email", change_type="add",
        diff="+ email VARCHAR(255) NOT NULL"
    ))
    rows = storage.search("VARCHAR")
    assert len(rows) == 1


def test_search_no_results(storage):
    storage.log_event(ChangeEvent(entity_path="users.email", change_type="add"))
    assert storage.search("xyzzy_no_match") == []


def test_search_case_insensitive(storage):
    storage.log_event(ChangeEvent(
        entity_path="users.email", change_type="add", reasoning="Added for BILLING"
    ))
    rows = storage.search("billing")
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# record_tool_call / get_tool_stats
# ---------------------------------------------------------------------------


def test_record_tool_call_persists(storage):
    storage.record_tool_call("log_change", entity_path="users.email")
    stats = storage.get_tool_stats()
    assert stats["total_calls"] == 1
    assert stats["by_tool"]["log_change"] == 1


def test_record_tool_call_multiple_tools(storage):
    storage.record_tool_call("log_change", entity_path="users.email")
    storage.record_tool_call("log_change", entity_path="users.name")
    storage.record_tool_call("blame", entity_path="payments.amount")
    storage.record_tool_call("search")

    stats = storage.get_tool_stats()
    assert stats["total_calls"] == 4
    assert stats["by_tool"]["log_change"] == 2
    assert stats["by_tool"]["blame"] == 1
    assert stats["by_tool"]["search"] == 1


def test_get_tool_stats_log_change_ratio(storage):
    storage.record_tool_call("log_change")
    storage.record_tool_call("log_change")
    storage.record_tool_call("diff")
    storage.record_tool_call("blame")

    stats = storage.get_tool_stats()
    assert stats["log_change_calls"] == 2
    assert stats["log_change_ratio"] == 0.5


def test_get_tool_stats_empty(storage):
    stats = storage.get_tool_stats()
    assert stats["total_calls"] == 0
    assert stats["log_change_calls"] == 0
    assert stats["log_change_ratio"] == 0.0
    assert stats["by_tool"] == {}


def test_get_tool_stats_recent_list(storage):
    storage.record_tool_call("log_change", entity_path="users.email")
    storage.record_tool_call("blame", entity_path="payments.amount")

    stats = storage.get_tool_stats()
    assert len(stats["recent"]) == 2
    # newest first
    assert stats["recent"][0]["tool_name"] == "blame"
    assert stats["recent"][1]["tool_name"] == "log_change"


def test_record_tool_call_never_raises_on_bad_input(storage):
    # Should never throw — telemetry must be fire-and-forget
    storage.record_tool_call("log_change", entity_path="x" * 10_000)
    assert storage.get_tool_stats()["total_calls"] == 1


def test_tool_calls_independent_of_change_events(storage):
    # Tool call count and event count are tracked in separate tables
    storage.log_event(ChangeEvent(entity_path="users.email", change_type="add"))
    storage.record_tool_call("log_change", entity_path="users.email")

    assert storage.count() == 1                        # events table
    assert storage.get_tool_stats()["total_calls"] == 1  # tool_calls table


# ---------------------------------------------------------------------------
# backfill_git_commit
# ---------------------------------------------------------------------------


def test_backfill_git_commit_updates_events(storage):
    storage.log_event(ChangeEvent(entity_path="users.email", change_type="add"))
    storage.log_event(ChangeEvent(entity_path="users.name", change_type="add"))

    updated = storage.backfill_git_commit("abc1234", window_minutes=10)
    assert updated == 2

    rows = storage.get_entity_history("users")
    assert all(r["git_commit"] == "abc1234" for r in rows)


def test_backfill_git_commit_skips_already_set(storage):
    e = ChangeEvent(entity_path="users.email", change_type="add", git_commit="existing_hash")
    storage.log_event(e)

    updated = storage.backfill_git_commit("new_hash", window_minutes=10)
    assert updated == 0  # already has a commit hash — should not be overwritten

    row = storage.get_blame("users.email")
    assert row["git_commit"] == "existing_hash"


def test_backfill_git_commit_returns_zero_when_nothing_to_update(storage):
    updated = storage.backfill_git_commit("abc1234", window_minutes=10)
    assert updated == 0


def test_backfill_git_commit_respects_window(storage):
    from datetime import datetime, timedelta, timezone

    # Insert an event with an old timestamp (outside the window)
    old_event = ChangeEvent(entity_path="old.col", change_type="add")
    old_event.timestamp = (
        datetime.now(timezone.utc) - timedelta(minutes=30)
    ).isoformat()
    storage.log_event(old_event)

    # Insert a recent event (inside the window)
    new_event = ChangeEvent(entity_path="new.col", change_type="add")
    storage.log_event(new_event)

    updated = storage.backfill_git_commit("abc1234", window_minutes=10)
    assert updated == 1  # only the recent event

    old_row = storage.get_blame("old.col")
    new_row = storage.get_blame("new.col")
    assert old_row["git_commit"] == ""
    assert new_row["git_commit"] == "abc1234"


# ---------------------------------------------------------------------------
# changeset_id — log, retrieve, group
# ---------------------------------------------------------------------------


def test_log_event_with_changeset_id(storage):
    e = ChangeEvent(
        entity_path="payments.amount", change_type="add",
        changeset_id="add-stripe-billing"
    )
    stored = storage.log_event(e)
    assert stored.changeset_id == "add-stripe-billing"

    row = storage.get_blame("payments.amount")
    assert row["changeset_id"] == "add-stripe-billing"


def test_log_event_changeset_id_defaults_empty(storage):
    e = ChangeEvent(entity_path="users.email", change_type="add")
    storage.log_event(e)
    row = storage.get_blame("users.email")
    assert row["changeset_id"] == ""


def test_get_changeset_returns_events(storage):
    cs = "add-payments"
    storage.log_event(ChangeEvent(entity_path="payments", change_type="create", changeset_id=cs))
    storage.log_event(ChangeEvent(entity_path="payments.amount", change_type="add", changeset_id=cs))
    storage.log_event(ChangeEvent(entity_path="payments.currency", change_type="add", changeset_id=cs))
    # An unrelated event
    storage.log_event(ChangeEvent(entity_path="users.email", change_type="add"))

    rows = storage.get_changeset(cs)
    assert len(rows) == 3
    assert all(r["changeset_id"] == cs for r in rows)


def test_get_changeset_ordered_oldest_first(storage):
    cs = "my-changeset"
    e1 = ChangeEvent(entity_path="a.x", change_type="add", changeset_id=cs)
    e1.timestamp = "2025-01-01T00:00:00+00:00"
    e2 = ChangeEvent(entity_path="b.y", change_type="add", changeset_id=cs)
    e2.timestamp = "2025-06-01T00:00:00+00:00"
    storage.log_event(e1)
    storage.log_event(e2)

    rows = storage.get_changeset(cs)
    assert rows[0]["entity_path"] == "a.x"
    assert rows[1]["entity_path"] == "b.y"


def test_get_changeset_empty_for_unknown(storage):
    assert storage.get_changeset("nonexistent-changeset") == []


def test_get_history_changeset_filter(storage):
    storage.log_event(ChangeEvent(entity_path="a.x", change_type="add", changeset_id="cs-1"))
    storage.log_event(ChangeEvent(entity_path="b.y", change_type="add", changeset_id="cs-2"))
    storage.log_event(ChangeEvent(entity_path="c.z", change_type="add"))  # no changeset

    rows = storage.get_history(changeset_id="cs-1")
    assert len(rows) == 1
    assert rows[0]["entity_path"] == "a.x"


def test_list_changesets(storage):
    storage.log_event(ChangeEvent(entity_path="a.x", change_type="add", changeset_id="cs-1", project="api"))
    storage.log_event(ChangeEvent(entity_path="a.y", change_type="add", changeset_id="cs-1", project="api"))
    storage.log_event(ChangeEvent(entity_path="b.z", change_type="add", changeset_id="cs-2", project="api"))
    storage.log_event(ChangeEvent(entity_path="c.w", change_type="add"))  # no changeset — excluded

    rows = storage.list_changesets()
    assert len(rows) == 2
    cs1 = next(r for r in rows if r["changeset_id"] == "cs-1")
    assert cs1["event_count"] == 2


def test_list_changesets_project_filter(storage):
    storage.log_event(ChangeEvent(entity_path="a.x", change_type="add", changeset_id="cs-1", project="api"))
    storage.log_event(ChangeEvent(entity_path="b.y", change_type="add", changeset_id="cs-2", project="shop"))

    rows = storage.list_changesets(project="api")
    assert len(rows) == 1
    assert rows[0]["changeset_id"] == "cs-1"


def test_migration_adds_changeset_id_column(tmp_path):
    """Existing DBs without changeset_id get the column added on first open."""
    import sqlite3

    db_path = tmp_path / "legacy.db"

    # Create a DB without the changeset_id column (simulating a pre-migration DB)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE events (
            id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
            entity_type TEXT NOT NULL DEFAULT 'other',
            entity_path TEXT NOT NULL, change_type TEXT NOT NULL,
            diff TEXT NOT NULL DEFAULT '', reasoning TEXT NOT NULL DEFAULT '',
            agent TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '',
            git_commit TEXT NOT NULL DEFAULT '', project TEXT NOT NULL DEFAULT '',
            metadata TEXT NOT NULL DEFAULT '{}'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tool_calls (
            id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
            tool_name TEXT NOT NULL, entity_path TEXT NOT NULL DEFAULT '',
            success INTEGER NOT NULL DEFAULT 1, error_msg TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()

    # Opening with SelvedgeStorage should apply the migration
    storage = SelvedgeStorage(db_path)
    # Should be able to log an event with changeset_id without error
    e = ChangeEvent(entity_path="users.email", change_type="add", changeset_id="test-cs")
    stored = storage.log_event(e)
    assert stored.changeset_id == "test-cs"
