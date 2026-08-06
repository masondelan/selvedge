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
# v0.3.2: per-agent breakdown, missing reasoning, last tool call
# ---------------------------------------------------------------------------


def test_record_tool_call_persists_agent(storage):
    storage.record_tool_call("log_change", entity_path="x", agent="claude-code")
    stats = storage.get_tool_stats()
    assert "claude-code" in stats["by_agent"]
    assert stats["by_agent"]["claude-code"]["total"] == 1
    assert stats["by_agent"]["claude-code"]["log_change"] == 1


def test_get_tool_stats_per_agent_ratio(storage):
    storage.record_tool_call("log_change", agent="claude-code")
    storage.record_tool_call("log_change", agent="claude-code")
    storage.record_tool_call("blame", agent="claude-code")
    storage.record_tool_call("history", agent="cursor")
    storage.record_tool_call("history", agent="cursor")

    stats = storage.get_tool_stats()
    assert stats["by_agent"]["claude-code"]["ratio"] > 0.6  # 2/3
    assert stats["by_agent"]["cursor"]["ratio"] == 0.0


def test_get_tool_stats_unknown_agent_bucket(storage):
    """Empty agent rolls up under '(unknown)'."""
    storage.record_tool_call("blame")
    stats = storage.get_tool_stats()
    assert stats["by_agent"]["(unknown)"]["total"] == 1


def test_get_tool_stats_per_agent_sorted_by_total(storage):
    """The by_agent dict is sorted by total calls descending."""
    storage.record_tool_call("log_change", agent="rare-agent")
    for _ in range(3):
        storage.record_tool_call("log_change", agent="busy-agent")

    stats = storage.get_tool_stats()
    keys = list(stats["by_agent"].keys())
    assert keys[0] == "busy-agent"


def test_get_tool_stats_missing_reasoning_counts_validator_failures(storage):
    """missing_reasoning equals the count of stored events that fail the validator."""
    storage.log_event(ChangeEvent(
        entity_path="a", change_type="add",
        reasoning="A long, real explanation of why we did this work — no placeholder.",
    ))
    storage.log_event(ChangeEvent(
        entity_path="b", change_type="add",
        reasoning="done",  # generic
    ))
    storage.log_event(ChangeEvent(
        entity_path="c", change_type="add",
        reasoning="",  # empty
    ))
    storage.log_event(ChangeEvent(
        entity_path="d", change_type="add",
        reasoning="too short",  # under 20 chars
    ))

    stats = storage.get_tool_stats()
    assert stats["missing_reasoning"] == 3


def test_get_last_tool_call_timestamp_returns_none_when_empty(storage):
    assert storage.get_last_tool_call_timestamp() is None


def test_get_last_tool_call_timestamp_returns_most_recent(storage):
    storage.record_tool_call("log_change")
    storage.record_tool_call("blame")
    ts = storage.get_last_tool_call_timestamp()
    assert ts is not None
    assert ts.endswith("Z") or "+" in ts  # UTC ISO


def test_record_tool_call_backward_compatible_without_agent(storage):
    """Old callers passing positional args (no agent) still work."""
    storage.record_tool_call("log_change", entity_path="x")
    stats = storage.get_tool_stats()
    assert stats["total_calls"] == 1
    assert "(unknown)" in stats["by_agent"]


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


# ---------------------------------------------------------------------------
# Entity-path canonicalization on the READ path
#
# Writes canonicalize via ``_normalize_for_storage``; ``get_prior_attempts``
# and ``get_decision_status`` canonicalize their query too. These three reads
# did not, so an agent that ran ``prior_attempts <path>`` and then
# ``blame <path>`` on the SAME string got a hit followed by a miss.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "queried",
    ["./src/auth.py", "src//auth.py", "src\\auth.py"],
)
def test_blame_finds_non_canonical_query_path(storage, queried):
    storage.log_event(ChangeEvent(entity_path="src/auth.py", change_type="add"))
    row = storage.get_blame(queried)
    assert row is not None
    assert row["entity_path"] == "src/auth.py"


@pytest.mark.parametrize(
    "queried",
    ["./src/auth.py", "src//auth.py", "src\\auth.py"],
)
def test_entity_history_finds_non_canonical_query_path(storage, queried):
    storage.log_event(ChangeEvent(entity_path="src/auth.py", change_type="add"))
    assert [r["entity_path"] for r in storage.get_entity_history(queried)] == [
        "src/auth.py"
    ]


@pytest.mark.parametrize(
    "queried",
    ["./src/auth.py", "src//auth.py", "src\\auth.py"],
)
def test_get_history_finds_non_canonical_query_path(storage, queried):
    storage.log_event(ChangeEvent(entity_path="src/auth.py", change_type="add"))
    assert [
        r["entity_path"] for r in storage.get_history(entity_path=queried)
    ] == ["src/auth.py"]


def test_canonical_read_still_matches_dotted_children(storage):
    """Canonicalizing the query must not narrow the dotted-prefix match."""
    storage.log_event(ChangeEvent(entity_path="users", change_type="add"))
    storage.log_event(ChangeEvent(entity_path="users.email", change_type="add"))
    paths = {r["entity_path"] for r in storage.get_entity_history("users")}
    assert paths == {"users", "users.email"}


# ---------------------------------------------------------------------------
# Index coverage for the exact-or-dotted-prefix read
#
# ``idx_entity_path`` is BINARY-collated but SQLite's default LIKE is
# case-insensitive, so the LIKE-prefix optimization cannot use it and the
# hottest read in the product degrades to a full scan. This is a structural
# guard (same spirit as test_migrations_perf's page_count assertion) — a
# wall-clock threshold would be unfalsifiable on fast hardware.
# ---------------------------------------------------------------------------


def test_entity_prefix_read_does_not_full_scan(storage):
    storage.log_event(ChangeEvent(entity_path="users.email", change_type="add"))
    with storage._session() as conn:
        plan = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT * FROM events
            WHERE entity_path = ? OR entity_path LIKE ? ESCAPE '\\'
            ORDER BY timestamp ASC
            """,
            ("users.email", "users.email.%"),
        ).fetchall()
    detail = " | ".join(r["detail"] for r in plan)
    assert "SCAN events" not in detail, f"entity-path read full-scans: {detail}"


def test_entity_prefix_is_dotted_not_raw_string(storage):
    """Pin the semantics the MCP descriptions now state.

    `entity_path` prefix matching is on DOTTED segments, not raw string
    prefixes — `diff`/`history` described it as a "path prefix", so an agent
    asking for the history of `src/` or `src/auth.py` got a confident empty
    answer instead of an error.
    """
    for path in ("src/auth.py", "src/auth.py::login", "users", "users.email"):
        storage.log_event(ChangeEvent(entity_path=path, change_type="add"))

    assert storage.get_entity_history("src/") == []
    assert [r["entity_path"] for r in storage.get_entity_history("src/auth.py")] == [
        "src/auth.py"
    ]
    assert {r["entity_path"] for r in storage.get_entity_history("users")} == {
        "users", "users.email",
    }


# ---------------------------------------------------------------------------
# log_rename / log_supersede — decision fields must survive the write
#
# `log_change` accepted, validated, and then dropped `revisit_after`,
# `constraint` and `stale_when` on the rename and supersede branches (and
# `diff` on supersede). Fixing it in storage rather than in each caller is
# what makes the MCP tool and the CLI inherit the same behaviour.
# ---------------------------------------------------------------------------


def test_log_rename_carries_decision_fields_on_the_surviving_path(storage):
    """The fields land on the create-on-new-path event, and only there.

    A rename is a natural moment to record "revisit this in 90 days" — it is
    usually part of a restructuring someone intends to revisit. Putting the
    fields on the surviving entity (not the old path's rename marker) means
    `stale` reports one row rather than two, and the revisit nudge reads a
    satisfied value.
    """
    rename_event, create_event = storage.log_rename(
        old_path="src/auth.py::login",
        new_path="src/auth/session.py::login",
        entity_type="function",
        revisit_after="90d",
        constraint="login must stay in one module",
        stale_when="package layout changed",
    )

    assert create_event.revisit_after == "90d"
    assert create_event.constraint == "login must stay in one module"
    assert create_event.stale_when == "package layout changed"

    # The old path is a tombstone; duplicating the decision there would make
    # every rename surface twice in `stale`.
    assert rename_event.revisit_after == ""
    assert rename_event.constraint == ""
    assert rename_event.stale_when == ""


def test_log_rename_decision_fields_round_trip_through_storage(storage):
    storage.log_rename(
        old_path="src/old.py::f",
        new_path="src/new.py::f",
        revisit_after="90d",
        constraint="must stay one module",
        stale_when="package layout changed",
    )
    row = storage.get_entity_history("src/new.py::f")[0]
    assert row["revisit_after"] == "90d"
    assert row["constraint"] == "must stay one module"
    assert row["stale_when"] == "package layout changed"


def test_log_supersede_carries_diff_and_revisit_after(storage):
    storage.log_event(ChangeEvent(entity_path="pay.token", change_type="add"))
    storage.log_event(ChangeEvent(entity_path="pay.token", change_type="remove"))

    stored = storage.log_supersede(
        "pay.token",
        diff="ALTER TABLE pay ADD COLUMN token TEXT;",
        reasoning="Re-opening: the PCI concern was resolved by tokenizing upstream.",
        revisit_after="180d",
    )

    assert stored.diff == "ALTER TABLE pay ADD COLUMN token TEXT;"
    assert stored.revisit_after == "180d"

    row = storage.get_entity_history("pay.token")[0]
    assert row["diff"] == "ALTER TABLE pay ADD COLUMN token TEXT;"
    assert row["revisit_after"] == "180d"


def test_renamed_entity_with_revisit_after_surfaces_in_stale(storage):
    """The point of recording the field: it has to reach `stale`.

    Uses the changeset-activity signal rather than a tool call, because a due
    date alone never surfaces — `get_stale_decisions` requires evidence the
    entity is still in play.
    """
    storage.log_rename(
        old_path="src/old.py::f",
        new_path="src/new.py::f",
        revisit_after="2020-01-01",
        changeset_id="restructure-auth",
        reasoning="Moved login into its own module during the auth restructure.",
    )
    # A later sibling in the same changeset is the "feature kept moving" signal.
    storage.log_event(ChangeEvent(
        entity_path="src/new.py::helper",
        change_type="add",
        changeset_id="restructure-auth",
    ))

    due = storage.get_stale_decisions()
    paths = [d["entity_path"] for d in due]
    assert "src/new.py::f" in paths, f"renamed decision never became due: {due}"
