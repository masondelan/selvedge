"""
Tests for selvedge.watch — live tail of newly-logged events.

Coverage focus:

  - **Filter semantics match selvedge history.** ``entity_path`` is
    prefix-aware, ``project`` and ``agent`` are exact-match.
  - **Cursor advancement.** Each poll only emits events strictly after
    the cursor — no re-emission across polls.
  - **Interval clamping.** Out-of-range ``interval`` values raise so
    misuse is loud, not silent.
  - **Catch-up on --since.** The catch-up window emits in chronological
    order before the loop starts, mirroring how new events stream in.
  - **--json mode** emits one compact JSON object per line.
  - **max_iterations seam** lets the loop exit deterministically without
    needing to send SIGINT into the test process.

The watch loop reaches into the storage layer, so tests use a real
``SelvedgeStorage`` against a tmp_path SQLite file and inject events via
``log_event_batch`` for speed.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage
from selvedge.watch import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    MAX_POLL_INTERVAL_SECONDS,
    MIN_POLL_INTERVAL_SECONDS,
    _matches_filters,
    _poll_once,
    watch,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def storage(tmp_path: Path) -> SelvedgeStorage:
    return SelvedgeStorage(tmp_path / "watch-test.db")


@pytest.fixture
def captured() -> tuple[Console, StringIO]:
    """A Rich console that writes to a StringIO so tests can read its output."""
    buf = StringIO()
    console = Console(file=buf, width=200, color_system=None, force_terminal=False)
    return console, buf


def _seed(storage: SelvedgeStorage, *events: ChangeEvent) -> list[ChangeEvent]:
    """Insert events one-by-one so each gets a distinct timestamp."""
    stored = []
    for ev in events:
        stored.append(storage.log_event(ev))
    return stored


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------


def test_matches_filters_no_filters_passes_everything():
    assert _matches_filters({"entity_path": "users.email"})


def test_matches_filters_entity_exact_match():
    row = {"entity_path": "users.email"}
    assert _matches_filters(row, entity_path="users.email")


def test_matches_filters_entity_prefix_match():
    """`users` should match `users.email` (the prefix-aware case)."""
    row = {"entity_path": "users.email"}
    assert _matches_filters(row, entity_path="users")


def test_matches_filters_entity_prefix_no_false_positive():
    """`user` should NOT match `users.email` — prefix is dot-aware."""
    row = {"entity_path": "users.email"}
    assert not _matches_filters(row, entity_path="user")


def test_matches_filters_project_is_exact_match_only():
    row = {"project": "my-api", "entity_path": "x"}
    assert _matches_filters(row, project="my-api")
    assert not _matches_filters(row, project="my")


def test_matches_filters_agent_is_exact_match_only():
    row = {"agent": "claude-code", "entity_path": "x"}
    assert _matches_filters(row, agent="claude-code")
    assert not _matches_filters(row, agent="claude")


# ---------------------------------------------------------------------------
# _poll_once — cursor advancement
# ---------------------------------------------------------------------------


def test_poll_once_returns_events_strictly_after_cursor(storage: SelvedgeStorage):
    e1, e2, e3 = _seed(
        storage,
        ChangeEvent(entity_path="users.email", change_type="add", reasoning="seed 1"),
        ChangeEvent(entity_path="users.phone", change_type="add", reasoning="seed 2"),
        ChangeEvent(entity_path="users.name",  change_type="add", reasoning="seed 3"),
    )

    # Cursor at the timestamp of e2 — should only return e3
    fresh = _poll_once(
        storage,
        cursor=e2.timestamp,
        entity_path="",
        project="",
        agent="",
    )
    paths = [r["entity_path"] for r in fresh]
    assert paths == ["users.name"]


def test_poll_once_oldest_first_within_returned_batch(storage: SelvedgeStorage):
    e1, e2, e3 = _seed(
        storage,
        ChangeEvent(entity_path="a", change_type="add", reasoning="first one"),
        ChangeEvent(entity_path="b", change_type="add", reasoning="second one"),
        ChangeEvent(entity_path="c", change_type="add", reasoning="third one"),
    )

    fresh = _poll_once(
        storage,
        cursor="",  # everything from the dawn of time
        entity_path="",
        project="",
        agent="",
    )

    timestamps = [r["timestamp"] for r in fresh]
    assert timestamps == sorted(timestamps), "watch must emit oldest-first"


def test_poll_once_filters_by_agent_post_query(storage: SelvedgeStorage):
    """Storage doesn't accept agent in get_history; watch filters in Python."""
    _seed(
        storage,
        ChangeEvent(entity_path="x", change_type="add", agent="claude-code", reasoning="from claude"),
        ChangeEvent(entity_path="y", change_type="add", agent="cursor",      reasoning="from cursor"),
    )

    fresh = _poll_once(
        storage,
        cursor="",
        entity_path="",
        project="",
        agent="claude-code",
    )

    assert len(fresh) == 1
    assert fresh[0]["agent"] == "claude-code"


# ---------------------------------------------------------------------------
# watch loop — using max_iterations seam to exit deterministically
# ---------------------------------------------------------------------------


def test_watch_emits_each_event_exactly_once(
    storage: SelvedgeStorage, captured
):
    """Run the loop a few iterations, seeding new events between polls."""
    console, buf = captured

    # Pre-existing event — should NOT show up because cursor starts
    # at "now" by default. (We test the catch-up path separately.)
    _seed(storage, ChangeEvent(
        entity_path="pre", change_type="add", reasoning="present at startup"
    ))

    # Iteration counter we'll use to inject one new event per cycle.
    poll_calls = {"count": 0}

    def sleep_then_seed(_secs: float) -> None:
        poll_calls["count"] += 1
        if poll_calls["count"] == 1:
            storage.log_event(ChangeEvent(
                entity_path="users.email", change_type="add",
                reasoning="injected during first sleep",
            ))
        elif poll_calls["count"] == 2:
            storage.log_event(ChangeEvent(
                entity_path="users.name", change_type="add",
                reasoning="injected during second sleep",
            ))

    watch(
        console=console,
        storage=storage,
        sleep=sleep_then_seed,
        max_iterations=3,
        interval=0.5,  # within the allowed range; sleep is stubbed anyway
    )

    output = buf.getvalue()
    # The pre-existing event should not appear — cursor started at "now"
    assert "pre " not in output and "present at startup" not in output
    # The two new events should each appear exactly once
    assert output.count("users.email") == 1
    assert output.count("users.name")  == 1


def test_watch_catches_up_on_since(storage: SelvedgeStorage, captured):
    """`--since` emits past events in chronological order before tailing."""
    console, buf = captured

    e1, e2 = _seed(
        storage,
        ChangeEvent(entity_path="x", change_type="add", reasoning="reasoning text 1"),
        ChangeEvent(entity_path="y", change_type="add", reasoning="reasoning text 2"),
    )

    watch(
        console=console,
        storage=storage,
        sleep=lambda _: None,
        max_iterations=1,
        interval=0.5,
        since=e1.timestamp,  # include e1 and after
    )

    out = buf.getvalue()
    # Both events should show up in the catch-up window, oldest first
    idx_x = out.find(" x ")
    idx_y = out.find(" y ")
    assert idx_x != -1 and idx_y != -1
    assert idx_x < idx_y, "catch-up output must be chronological"


def test_watch_json_mode_emits_one_object_per_line(
    storage: SelvedgeStorage, captured
):
    console, buf = captured

    e1, _ = _seed(
        storage,
        ChangeEvent(entity_path="alpha", change_type="add", reasoning="json render check"),
        ChangeEvent(entity_path="beta",  change_type="add", reasoning="json render check"),
    )

    watch(
        console=console,
        storage=storage,
        sleep=lambda _: None,
        max_iterations=1,
        interval=0.5,
        since=e1.timestamp,
        as_json=True,
    )

    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    # Two events emitted in catch-up window, one JSON line each
    assert len(lines) == 2
    for line in lines:
        parsed = json.loads(line)
        assert "entity_path" in parsed
        assert "change_type" in parsed


# ---------------------------------------------------------------------------
# Interval clamping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_interval",
    [0.0, 0.05, MIN_POLL_INTERVAL_SECONDS - 0.01, MAX_POLL_INTERVAL_SECONDS + 0.01, 600.0],
)
def test_watch_rejects_out_of_range_interval(bad_interval: float, captured):
    console, _ = captured
    with pytest.raises(ValueError, match="--interval"):
        watch(
            console=console,
            interval=bad_interval,
            max_iterations=0,
            sleep=lambda _: None,
        )


def test_watch_accepts_in_range_interval(storage: SelvedgeStorage, captured):
    console, _ = captured
    # Should not raise
    watch(
        console=console,
        storage=storage,
        sleep=lambda _: None,
        max_iterations=0,
        interval=DEFAULT_POLL_INTERVAL_SECONDS,
    )


# ---------------------------------------------------------------------------
# Filter integration
# ---------------------------------------------------------------------------


def test_watch_respects_entity_filter_in_catchup_and_tail(
    storage: SelvedgeStorage, captured
):
    console, buf = captured

    e1, _, _ = _seed(
        storage,
        ChangeEvent(entity_path="users.email", change_type="add", reasoning="r1"),
        ChangeEvent(entity_path="orders.id",   change_type="add", reasoning="r2"),
        ChangeEvent(entity_path="users.phone", change_type="add", reasoning="r3"),
    )

    watch(
        console=console,
        storage=storage,
        sleep=lambda _: None,
        max_iterations=1,
        since=e1.timestamp,
        entity_path="users",  # prefix
    )

    out = buf.getvalue()
    assert "users.email" in out
    assert "users.phone" in out
    assert "orders.id"   not in out
