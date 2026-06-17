"""
Tests for active memory v1 / date-based (v0.3.8, Phase 2.14).

Covers the four moving parts of the date-based half:

  - schema migration v3 backfill (existing rows NULL, new writes store ""),
  - ``revisit_after`` normalization + due-date resolution,
  - the ``stale_decisions`` query, especially the active-use weighting that
    keeps pure age from surfacing,
  - the reasoning-quality revisit-date nudge.

Timestamps are pinned explicitly and ``now`` is injected so every assertion is
deterministic.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage
from selvedge.timeutil import normalize_revisit_after, resolve_revisit_due
from selvedge.validation import check_revisit_nudge


@pytest.fixture
def storage(tmp_path: Path) -> SelvedgeStorage:
    return SelvedgeStorage(tmp_path / "am.db")


# ---------------------------------------------------------------------------
# ChangeEvent surface + schema migration v3 backfill
# ---------------------------------------------------------------------------


def test_change_event_has_revisit_fields_defaulting_empty():
    ev = ChangeEvent(entity_path="users", change_type="add")
    assert ev.revisit_after == ""
    assert ev.expires_when == ""
    d = ev.to_dict()
    assert d["revisit_after"] == ""
    assert d["expires_when"] == ""


def _make_pre_v3_db(db_path: Path) -> None:
    """Build a v0.3.7-schema DB (no revisit columns), v1/v2 recorded, one row."""
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
            "INSERT INTO events (id, timestamp, entity_path, change_type) "
            "VALUES ('old1', '2026-01-01T00:00:00Z', 'legacy.col', 'add')"
        )
        conn.commit()
    finally:
        conn.close()


def test_migration_v3_adds_columns_and_backfills_existing_rows_null(tmp_path):
    db = tmp_path / "pre_v3.db"
    _make_pre_v3_db(db)

    SelvedgeStorage(db)  # runs migration v3

    conn = sqlite3.connect(str(db))
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()]
        assert "revisit_after" in cols
        assert "expires_when" in cols
        # The pre-existing row reads back NULL on both (nullable, no default).
        row = conn.execute(
            "SELECT revisit_after, expires_when FROM events WHERE id = 'old1'"
        ).fetchone()
        assert row == (None, None)
        # ...and migration v3 is recorded.
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        assert 3 in applied
    finally:
        conn.close()


def test_pre_v3_null_columns_read_back_as_empty_string(tmp_path):
    """Pre-v3 rows (NULL on the new columns) must read back as '' across every
    read path — never JSON null — matching the never-null convention."""
    db = tmp_path / "pre_v3.db"
    _make_pre_v3_db(db)  # seeds row 'old1' at entity_path 'legacy.col'
    st = SelvedgeStorage(db)  # migrates (existing row gets NULL on both cols)

    # blame, diff (entity history), history, search, changeset all agree.
    assert st.get_blame("legacy.col")["revisit_after"] == ""
    assert st.get_entity_history("legacy.col")[0]["expires_when"] == ""
    assert st.get_history(entity_path="legacy.col")[0]["revisit_after"] == ""
    assert st.search("legacy.col")[0]["expires_when"] == ""


def test_new_writes_store_empty_string_not_null(storage):
    """A fresh write with no revisit_after stores '' (the never-null convention)."""
    storage.log_event(ChangeEvent(entity_path="users.email", change_type="add"))
    with sqlite3.connect(str(storage.db_path)) as conn:
        row = conn.execute(
            "SELECT revisit_after, expires_when FROM events WHERE entity_path='users.email'"
        ).fetchone()
    assert row == ("", "")


def test_revisit_after_round_trips_through_storage(storage):
    storage.log_event(
        ChangeEvent(
            entity_path="users", change_type="add", entity_type="table",
            timestamp="2026-01-01T00:00:00Z", revisit_after="90d",
        )
    )
    row = storage.get_blame("users")
    assert row is not None
    assert row["revisit_after"] == "90d"


# ---------------------------------------------------------------------------
# normalize_revisit_after + resolve_revisit_due
# ---------------------------------------------------------------------------


def test_normalize_revisit_after_preserves_relative_offset():
    assert normalize_revisit_after("90D") == "90d"
    assert normalize_revisit_after("  6mo ") == "6mo"


def test_normalize_revisit_after_canonicalizes_absolute_date():
    assert normalize_revisit_after("2026-09-01") == "2026-09-01T00:00:00Z"


def test_normalize_revisit_after_empty_is_empty():
    assert normalize_revisit_after("") == ""
    assert normalize_revisit_after("   ") == ""


def test_normalize_revisit_after_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_revisit_after("next tuesday")


def test_resolve_revisit_due_relative_is_offset_from_timestamp():
    due = resolve_revisit_due("2026-01-01T00:00:00Z", "90d")
    assert due.isoformat().startswith("2026-04-01")


def test_resolve_revisit_due_absolute_ignores_timestamp():
    due = resolve_revisit_due("2026-01-01T00:00:00Z", "2027-05-05")
    assert due.isoformat().startswith("2027-05-05")


# ---------------------------------------------------------------------------
# stale_decisions — active-use weighting
# ---------------------------------------------------------------------------

# A fixed "now" well past any 90d revisit date set on a 2026-01-01 event.
_NOW = "2026-12-31T00:00:00Z"


def _decision(storage, path="users", *, revisit="90d", ts="2026-01-01T00:00:00Z",
              entity_type="table", changeset_id="", project="", agent=""):
    storage.log_event(ChangeEvent(
        entity_path=path, change_type="add", entity_type=entity_type,
        timestamp=ts, revisit_after=revisit, changeset_id=changeset_id,
        project=project, agent=agent, reasoning="Architectural decision.",
    ))


def test_pure_age_does_not_surface(storage):
    """Past the revisit date but nobody's touched it → not stale."""
    _decision(storage)
    assert storage.get_stale_decisions(now=_NOW) == []


def test_queried_entity_surfaces(storage):
    _decision(storage)
    # A blame/diff/prior_attempts lookup AFTER the decision is the signal.
    storage.record_tool_call("blame", entity_path="users")
    rows = storage.get_stale_decisions(now=_NOW)
    assert len(rows) == 1
    assert rows[0]["entity_path"] == "users"
    assert "queried" in rows[0]["active_use_signals"]
    assert rows[0]["revisit_due"].startswith("2026-04-01")
    assert rows[0]["days_overdue"] > 0
    assert rows[0]["stale_reason"]
    # expires_when (NULL/absent) is coalesced to "".
    assert rows[0]["expires_when"] == ""


def test_changeset_activity_surfaces(storage):
    _decision(storage, changeset_id="auth-rewrite")
    # A later sibling in the same changeset = continued activity.
    storage.log_event(ChangeEvent(
        entity_path="sessions", change_type="add",
        timestamp="2026-03-01T00:00:00Z", changeset_id="auth-rewrite",
    ))
    rows = storage.get_stale_decisions(now=_NOW)
    assert len(rows) == 1
    assert "changeset_activity" in rows[0]["active_use_signals"]


def test_not_yet_due_is_excluded(storage):
    """revisit_after still in the future → never stale, even if queried."""
    _decision(storage, revisit="90d", ts="2026-12-15T00:00:00Z")  # due ~2027-03
    storage.record_tool_call("blame", entity_path="users")
    assert storage.get_stale_decisions(now=_NOW) == []


def test_query_before_decision_is_not_a_signal(storage):
    """A lookup that predates the decision doesn't count as active use."""
    storage.record_tool_call("blame", entity_path="users")  # uses now(), but...
    # Log the decision with a FUTURE timestamp so the tool_call predates it.
    _decision(storage, ts="2099-01-01T00:00:00Z", revisit="1d")
    # Even with a far-future now, the only tool_call predates the decision.
    assert storage.get_stale_decisions(now="2099-06-01T00:00:00Z") == []


def test_filters_by_entity_project_agent(storage):
    _decision(storage, path="users", project="api", agent="claude-code")
    _decision(storage, path="orders", project="web", agent="cursor")
    storage.record_tool_call("blame", entity_path="users")
    storage.record_tool_call("blame", entity_path="orders")

    assert {r["entity_path"] for r in storage.get_stale_decisions(now=_NOW)} == {
        "users", "orders",
    }
    assert {r["entity_path"]
            for r in storage.get_stale_decisions(now=_NOW, entity_path="users")} == {"users"}
    assert {r["entity_path"]
            for r in storage.get_stale_decisions(now=_NOW, project="web")} == {"orders"}
    assert {r["entity_path"]
            for r in storage.get_stale_decisions(now=_NOW, agent="claude-code")} == {"users"}


def test_events_without_revisit_after_never_surface(storage):
    """A normal event (no revisit date) is invisible to stale_decisions."""
    storage.log_event(ChangeEvent(entity_path="users.email", change_type="add"))
    storage.record_tool_call("blame", entity_path="users.email")
    assert storage.get_stale_decisions(now=_NOW) == []


def test_results_are_most_overdue_first(storage):
    """Ordering contract: earliest due date (most overdue) comes first."""
    # Same logged date, different offsets → different due dates.
    _decision(storage, path="late", revisit="60d")    # due 2026-03-02
    _decision(storage, path="early", revisit="10d")   # due 2026-01-11
    _decision(storage, path="mid", revisit="30d")     # due 2026-01-31
    for p in ("late", "early", "mid"):
        storage.record_tool_call("blame", entity_path=p)

    rows = storage.get_stale_decisions(now=_NOW)
    assert [r["entity_path"] for r in rows] == ["early", "mid", "late"]


def test_limit_truncates_to_most_overdue(storage):
    _decision(storage, path="late", revisit="60d")
    _decision(storage, path="early", revisit="10d")
    _decision(storage, path="mid", revisit="30d")
    for p in ("late", "early", "mid"):
        storage.record_tool_call("blame", entity_path=p)

    rows = storage.get_stale_decisions(now=_NOW, limit=2)
    assert [r["entity_path"] for r in rows] == ["early", "mid"]


def test_unparseable_stored_revisit_after_is_skipped(storage):
    """A row whose revisit_after bypassed the write-path validator (raw INSERT
    or pre-v3 migration) is skipped, not crashed on."""
    import sqlite3

    # A valid due-and-active decision.
    _decision(storage, path="good")
    storage.record_tool_call("blame", entity_path="good")
    # A garbage revisit_after inserted directly, also "queried".
    with sqlite3.connect(str(storage.db_path)) as conn:
        conn.execute(
            "INSERT INTO events (id, timestamp, entity_path, change_type, revisit_after) "
            "VALUES ('bad1', '2026-01-01T00:00:00Z', 'bad', 'add', 'not-a-date')"
        )
        conn.commit()
    storage.record_tool_call("blame", entity_path="bad")

    rows = storage.get_stale_decisions(now=_NOW)  # must not raise
    assert {r["entity_path"] for r in rows} == {"good"}


# ---------------------------------------------------------------------------
# Reasoning-quality revisit-date nudge
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("change_type", ["add", "modify", "create", "migrate"])
@pytest.mark.parametrize("entity_type", ["table", "schema", "dependency", "config"])
def test_nudge_fires_for_architectural_change_without_revisit(change_type, entity_type):
    warnings = check_revisit_nudge(change_type, entity_type, "")
    assert len(warnings) == 1
    assert "revisit_after" in warnings[0]


def test_nudge_silent_when_revisit_already_set():
    assert check_revisit_nudge("add", "table", "90d") == []


def test_nudge_silent_for_non_architectural_entity():
    assert check_revisit_nudge("add", "column", "") == []
    assert check_revisit_nudge("add", "function", "") == []


def test_nudge_silent_for_non_architectural_change_type():
    # remove/delete/rename aren't "decisions worth a revisit date."
    assert check_revisit_nudge("remove", "table", "") == []
    assert check_revisit_nudge("delete", "schema", "") == []
