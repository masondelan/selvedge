"""
Tests for the `prior_attempts` wedge (v0.3.7) at the storage layer.

Focus: the confidence tiers (proximity_high vs proximity_low), the
configurable add→remove proximity window, and the conservative-recall
default (an empty result is preferred over a false positive). Timestamps are
set explicitly so the proximity math is deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage


@pytest.fixture
def storage(tmp_path: Path) -> SelvedgeStorage:
    return SelvedgeStorage(tmp_path / "pa.db")


def _ev(storage, path, change_type, ts, reasoning=""):
    storage.log_event(
        ChangeEvent(
            entity_path=path,
            change_type=change_type,
            timestamp=ts,
            reasoning=reasoning,
        )
    )


def test_tried_then_reverted_is_high_confidence(storage):
    _ev(storage, "users.token", "add", "2026-01-01T00:00:00Z", "Tried a per-user token column.")
    _ev(storage, "users.token", "remove", "2026-01-02T00:00:00Z", "Reverted: moved to JWTs.")

    results = storage.get_prior_attempts(entity_path="users.token")
    assert len(results) == 1
    r = results[0]
    assert r["change_type"] == "add"
    assert r["outcome"] == "reverted"
    assert r["confidence"] == "proximity_high"
    assert r["outcome_reasoning"] == "Reverted: moved to JWTs."


def test_default_excludes_active_attempts(storage):
    # Only ever added, never removed — no clear "rejected" signal.
    _ev(storage, "users.email", "add", "2026-01-01T00:00:00Z", "Email for auth, still in use.")
    assert storage.get_prior_attempts(entity_path="users.email") == []


def test_low_confidence_tail_includes_active_attempts(storage):
    _ev(storage, "users.email", "add", "2026-01-01T00:00:00Z", "Email for auth, still in use.")
    results = storage.get_prior_attempts(
        entity_path="users.email", min_confidence="proximity_low"
    )
    assert len(results) == 1
    assert results[0]["outcome"] == "active"
    assert results[0]["confidence"] == "proximity_low"
    assert results[0]["outcome_reasoning"] == ""


def test_revert_outside_window_drops_to_low_confidence(storage):
    # 9-day gap exceeds the default 7-day (10080 min) window.
    _ev(storage, "api/v1/legacy", "add", "2026-01-01T00:00:00Z", "Added legacy endpoint.")
    _ev(storage, "api/v1/legacy", "remove", "2026-01-10T00:00:00Z", "Removed nine days later.")

    # Default high-confidence floor: the far-apart revert doesn't qualify.
    assert storage.get_prior_attempts(entity_path="api/v1/legacy") == []
    # But it surfaces in the low-confidence tail, still marked reverted.
    tail = storage.get_prior_attempts(
        entity_path="api/v1/legacy", min_confidence="proximity_low"
    )
    assert len(tail) == 1
    assert tail[0]["outcome"] == "reverted"
    assert tail[0]["confidence"] == "proximity_low"


def test_window_minutes_is_configurable(storage):
    # 1-day gap: high under the default window, low under a 10-minute window.
    _ev(storage, "x.y", "add", "2026-01-01T00:00:00Z", "Tried x.y.")
    _ev(storage, "x.y", "remove", "2026-01-02T00:00:00Z", "Reverted x.y.")

    assert storage.get_prior_attempts(entity_path="x.y")[0]["confidence"] == "proximity_high"
    tight = storage.get_prior_attempts(
        entity_path="x.y", window_minutes=10, min_confidence="proximity_low"
    )
    assert tight[0]["confidence"] == "proximity_low"


def test_removal_events_are_folded_not_returned(storage):
    _ev(storage, "users.token", "add", "2026-01-01T00:00:00Z", "Tried token.")
    _ev(storage, "users.token", "remove", "2026-01-02T00:00:00Z", "Reverted token.")
    results = storage.get_prior_attempts(
        entity_path="users.token", min_confidence="proximity_low"
    )
    # The removal surfaces only via outcome_reasoning on the attempt, never as
    # its own result row.
    assert all(r["change_type"] != "remove" for r in results)
    assert len(results) == 1


def test_query_mode_matches_description(storage):
    _ev(storage, "users.token", "add", "2026-01-01T00:00:00Z", "Tried storing an SSO token.")
    _ev(storage, "users.token", "remove", "2026-01-02T00:00:00Z", "Reverted the SSO token approach.")
    results = storage.get_prior_attempts(query="SSO token")
    assert len(results) == 1
    assert results[0]["entity_path"] == "users.token"
    assert results[0]["outcome"] == "reverted"


def test_prefix_match_covers_columns(storage):
    _ev(storage, "users.token", "add", "2026-01-01T00:00:00Z", "Tried token col.")
    _ev(storage, "users.token", "remove", "2026-01-02T00:00:00Z", "Reverted token col.")
    # Querying the table prefix surfaces the reverted column attempt.
    results = storage.get_prior_attempts(entity_path="users")
    assert len(results) == 1
    assert results[0]["entity_path"] == "users.token"


def test_neither_arg_returns_empty(storage):
    assert storage.get_prior_attempts() == []


# ---------------------------------------------------------------------------
# GitHub issue #30 — an id-less supersede must re-open at most the single
# removal it auto-links to (the most recent one at its write time, matching
# log_supersede's own auto-link rule), never every earlier revert on the path.
# ---------------------------------------------------------------------------


def test_issue_30_idless_supersede_reopens_only_the_latest_revert(storage):
    # Decision A: tried and reverted.
    _ev(storage, "users.auth_token", "add", "2026-01-01T00:00:00Z", "Attempt A.")
    _ev(storage, "users.auth_token", "remove", "2026-01-02T00:00:00Z", "Reverted A.")
    # Decision B: independent, also tried and reverted.
    _ev(storage, "users.auth_token", "add", "2026-02-01T00:00:00Z", "Attempt B.")
    _ev(storage, "users.auth_token", "remove", "2026-02-02T00:00:00Z", "Reverted B.")
    # A hand-logged supersede with NO id link (log_event bypasses the
    # auto-linking writer — the shape importers and pre-v0.3.9.1 rows have).
    storage.log_event(
        ChangeEvent(
            entity_path="users.auth_token",
            change_type="supersede",
            timestamp="2026-03-01T00:00:00Z",
            reasoning="Re-opening the auth token question.",
        )
    )

    rows = storage.get_prior_attempts(entity_path="users.auth_token")
    by_reason = {r["reasoning"]: r for r in rows if r["change_type"] == "add"}
    # Only B — the removal the supersede would have auto-linked — re-opens.
    assert by_reason["Attempt B."]["outcome"] == "reopened"
    # A's revert still stands: the whole point of the fix.
    assert by_reason["Attempt A."]["outcome"] == "reverted"
    assert by_reason["Attempt A."]["superseded_by"] == ""


def test_issue_30_explicit_link_still_reopens_exactly_its_removal(storage):
    """The explicit-id path was already correct — pin it alongside the fix."""
    _ev(storage, "users.auth_token", "add", "2026-01-01T00:00:00Z", "Attempt A.")
    _ev(storage, "users.auth_token", "remove", "2026-01-02T00:00:00Z", "Reverted A.")
    _ev(storage, "users.auth_token", "add", "2026-02-01T00:00:00Z", "Attempt B.")
    _ev(storage, "users.auth_token", "remove", "2026-02-02T00:00:00Z", "Reverted B.")
    reverted_a = next(
        r
        for r in storage.get_entity_history("users.auth_token")
        if r["reasoning"] == "Reverted A."
    )
    storage.log_supersede(
        "users.auth_token",
        supersedes=reverted_a["id"],
        reasoning="Re-opening A specifically.",
    )
    rows = storage.get_prior_attempts(entity_path="users.auth_token")
    by_reason = {r["reasoning"]: r for r in rows if r["change_type"] == "add"}
    assert by_reason["Attempt A."]["outcome"] == "reopened"
    assert by_reason["Attempt B."]["outcome"] == "reverted"


def test_idless_supersede_before_any_removal_reopens_nothing(storage):
    """A dangling id-less supersede (nothing precedes it) flips nothing."""
    storage.log_event(
        ChangeEvent(
            entity_path="users.token",
            change_type="supersede",
            timestamp="2026-01-01T00:00:00Z",
            reasoning="Dangling re-open marker.",
        )
    )
    _ev(storage, "users.token", "add", "2026-02-01T00:00:00Z", "Attempt.")
    _ev(storage, "users.token", "remove", "2026-02-02T00:00:00Z", "Reverted.")
    rows = storage.get_prior_attempts(entity_path="users.token")
    attempt = next(r for r in rows if r["change_type"] == "add")
    assert attempt["outcome"] == "reverted"
    assert attempt["superseded_by"] == ""


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
