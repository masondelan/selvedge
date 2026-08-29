"""
Tests for the ``expires_when`` closed grammar + evaluator (v0.3.11, Phase 2.17).

Covers the four moving parts:

  - the closed grammar in ``selvedge.expires_when.PATTERNS`` — each
    recognized pattern accepted, plus a rejection case per malformed shape
    (fragmentation defense: non-matching values are REJECTED at write time),
  - write-time enforcement at the single chokepoint (``ChangeEvent``) and
    at both user-facing write surfaces (MCP ``log_change``, CLI ``log``),
  - the local-only evaluator: ``date:`` against now, ``entity:`` against
    the event log, ``library:`` against installed dist metadata (degrading
    to manual review when unobservable), ``manual:`` never auto-firing,
  - the ``stale_decisions`` wiring — expired decisions surface with the
    pattern that fired.

Determinism throughout: ``now`` is injected, timestamps pinned, and the
library lookups are either injected or use dists guaranteed present/absent
in the test venv.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from selvedge.expires_when import (
    PATTERNS,
    ExpiresEvaluation,
    evaluate_expires_when,
    validate_expires_when,
)
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage


@pytest.fixture
def storage(tmp_path: Path) -> SelvedgeStorage:
    return SelvedgeStorage(tmp_path / "ew.db")


_NOW = datetime(2026, 12, 31, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# The closed grammar — accepted shapes
# ---------------------------------------------------------------------------


def test_patterns_is_the_v1_closed_grammar():
    """The grammar is exactly the four documented kinds — growth is a
    deliberate, versioned act, not an accident."""
    assert set(PATTERNS) == {"library", "entity", "date", "manual"}


def test_library_pattern_accepted():
    assert validate_expires_when("library:django>=5.0") == "library:django>=5.0"
    assert (
        validate_expires_when("library:zope.interface>=6") == "library:zope.interface>=6"
    )


def test_entity_pattern_accepted_including_symbol_paths():
    assert (
        validate_expires_when("entity:users.email:changes")
        == "entity:users.email:changes"
    )
    # Entity paths can carry '::' — the grammar keeps every colon but its own.
    assert (
        validate_expires_when("entity:src/auth.py::login:changes")
        == "entity:src/auth.py::login:changes"
    )


def test_date_pattern_accepted_and_canonicalized():
    """The date payload is canonicalized to UTC at write time, same posture
    as revisit_after normalization."""
    assert validate_expires_when("date:2027-01-01") == "date:2027-01-01T00:00:00.000000Z"


def test_manual_pattern_accepted():
    assert validate_expires_when("manual:security-review") == "manual:security-review"


def test_empty_is_empty_and_whitespace_is_stripped():
    assert validate_expires_when("") == ""
    assert validate_expires_when("   ") == ""
    assert validate_expires_when("  manual:x ") == "manual:x"


# ---------------------------------------------------------------------------
# The closed grammar — a rejection case per malformed shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malformed",
    [
        "when django updates",  # free-form prose — the fragmentation case
        "library:django",  # library without the >=VERSION comparator
        "library:django>=latest",  # non-numeric version
        "library:>=5.0",  # library with no name
        "entity:users.email",  # entity without the :changes suffix
        "entity::changes",  # entity with an empty path
        "date:tomorrow",  # non-ISO date
        "date:2027-99-99",  # shaped like a date but unparseable
        "manual:",  # manual with an empty label
        "expires:2027-01-01",  # unknown kind prefix
    ],
)
def test_malformed_values_are_rejected(malformed):
    with pytest.raises(ValueError, match="expires_when"):
        validate_expires_when(malformed)


# ---------------------------------------------------------------------------
# Write-time enforcement — every path routes through the chokepoint
# ---------------------------------------------------------------------------


def test_change_event_constructor_rejects_non_grammar_values():
    """ChangeEvent is the single write chokepoint (MCP, CLI, importers all
    build one) — a value outside the grammar can never be stored."""
    with pytest.raises(ValueError, match="closed grammar"):
        ChangeEvent(
            entity_path="users", change_type="add", expires_when="whenever it rots"
        )


def test_change_event_constructor_accepts_and_normalizes():
    ev = ChangeEvent(
        entity_path="users", change_type="add", expires_when="date:2027-01-01"
    )
    assert ev.expires_when == "date:2027-01-01T00:00:00.000000Z"


def test_mcp_log_change_rejects_bad_expires_when():
    """The MCP surface reports the grammar rejection as an error payload
    with no event written — same convention as a bad revisit_after."""
    import selvedge.server as srv

    srv._storage = None
    try:
        result = srv.log_change(
            entity_path="users.email",
            change_type="add",
            reasoning="Adding email for auth; expiry test.",
            expires_when="not-a-pattern",
        )
        assert result["status"] == "error"
        assert "closed grammar" in result["error"]
        assert result["id"] == ""
        assert srv.get_storage().count() == 0
    finally:
        srv._storage = None


def test_mcp_log_change_stores_valid_expires_when():
    import selvedge.server as srv

    srv._storage = None
    try:
        result = srv.log_change(
            entity_path="users.email",
            change_type="add",
            reasoning="Adding email for auth; expiry round-trip test.",
            expires_when="entity:deps/stripe:changes",
        )
        assert result["status"] == "logged"
        row = srv.get_storage().get_blame("users.email")
        assert row is not None
        assert row["expires_when"] == "entity:deps/stripe:changes"
    finally:
        srv._storage = None


def test_cli_log_rejects_bad_expires_when():
    from click.testing import CliRunner

    from selvedge.cli import cli

    result = CliRunner().invoke(
        cli,
        [
            "log", "users.email", "add",
            "--reasoning", "Testing the CLI grammar gate.",
            "--expires-when", "sometime later",
        ],
    )
    assert result.exit_code == 2
    assert "closed grammar" in result.output + str(result.stderr)


# ---------------------------------------------------------------------------
# Evaluator — date:
# ---------------------------------------------------------------------------


def test_date_in_the_past_is_expired():
    ev = evaluate_expires_when(
        "date:2026-06-01", decision_timestamp="2026-01-01T00:00:00Z", now=_NOW
    )
    assert ev == ExpiresEvaluation(
        status="expired",
        kind="date",
        detail="expiry date 2026-06-01T00:00:00.000000Z has passed",
    )


def test_date_in_the_future_is_pending():
    ev = evaluate_expires_when(
        "date:2099-01-01", decision_timestamp="2026-01-01T00:00:00Z", now=_NOW
    )
    assert ev.status == "pending"
    assert ev.kind == "date"


# ---------------------------------------------------------------------------
# Evaluator — entity:
# ---------------------------------------------------------------------------


def test_entity_fires_only_when_the_callable_reports_a_later_event():
    calls: list[tuple[str, str]] = []

    def changed(path: str, ts: str) -> bool:
        calls.append((path, ts))
        return True

    ev = evaluate_expires_when(
        "entity:deps/stripe:changes",
        decision_timestamp="2026-01-01T00:00:00.000000Z",
        now=_NOW,
        entity_changed_after=changed,
    )
    assert ev.status == "expired" and ev.kind == "entity"
    assert calls == [("deps/stripe", "2026-01-01T00:00:00.000000Z")]

    ev = evaluate_expires_when(
        "entity:deps/stripe:changes",
        decision_timestamp="2026-01-01T00:00:00.000000Z",
        now=_NOW,
        entity_changed_after=lambda path, ts: False,
    )
    assert ev.status == "pending"


def test_entity_without_a_log_presents_as_manual_review():
    """No event log to check against → manual review, never a guess."""
    ev = evaluate_expires_when(
        "entity:users:changes",
        decision_timestamp="2026-01-01T00:00:00Z",
        now=_NOW,
        entity_changed_after=None,
    )
    assert ev.status == "manual_review"


# ---------------------------------------------------------------------------
# Evaluator — library: (installed dist metadata is the cheap local source)
# ---------------------------------------------------------------------------


def test_library_expired_when_installed_version_reaches_required():
    ev = evaluate_expires_when(
        "library:example>=2.0",
        decision_timestamp="2026-01-01T00:00:00Z",
        now=_NOW,
        installed_version=lambda name: "2.31.0.post1",
    )
    assert ev.status == "expired" and ev.kind == "library"


def test_library_pending_below_required_version():
    ev = evaluate_expires_when(
        "library:example>=3",
        decision_timestamp="2026-01-01T00:00:00Z",
        now=_NOW,
        installed_version=lambda name: "2.9.9",
    )
    assert ev.status == "pending"


def test_library_not_installed_presents_as_manual_review():
    """Not cheaply determinable → manual review rather than guessing."""
    ev = evaluate_expires_when(
        "library:definitely-not-a-real-dist-xyz>=1",
        decision_timestamp="2026-01-01T00:00:00Z",
        now=_NOW,
    )
    assert ev.status == "manual_review"
    assert "not locally observable" in ev.detail


@pytest.mark.parametrize("installed", ["5.0rc1", "5.0a1", "5.0b2", "5.0.dev1", "5.0.post1"])
def test_library_prerelease_of_the_boundary_version_is_manual_review(installed):
    """An equal numeric prefix with a suffix is not cheaply determinable.

    PEP 440 orders 5.0rc1 (and a1/b2/dev1) strictly BEFORE 5.0, so treating
    the prefix as satisfying >=5.0 would fire the expiry the day someone
    installs a pre-release — the module contract degrades to manual review
    rather than guessing. (.post1 orders after, but the evaluator does not
    parse suffixes; equal-prefix-with-suffix is uniformly manual review.)
    """
    ev = evaluate_expires_when(
        "library:django>=5.0",
        decision_timestamp="2026-01-01T00:00:00Z",
        now=_NOW,
        installed_version=lambda name: installed,
    )
    assert ev.status == "manual_review" and ev.kind == "library"
    assert "not comparable" in ev.detail


def test_library_suffix_still_decides_when_the_prefix_differs():
    """Strictly greater / lesser numeric prefixes stay decided, suffix or not."""
    expired = evaluate_expires_when(
        "library:django>=5.0",
        decision_timestamp="2026-01-01T00:00:00Z",
        now=_NOW,
        installed_version=lambda name: "5.1rc1",
    )
    assert expired.status == "expired"

    pending = evaluate_expires_when(
        "library:django>=5.0",
        decision_timestamp="2026-01-01T00:00:00Z",
        now=_NOW,
        installed_version=lambda name: "4.9rc1",
    )
    assert pending.status == "pending"


def test_library_exact_suffix_free_boundary_version_is_expired():
    """The plain final release of the boundary version still fires."""
    ev = evaluate_expires_when(
        "library:django>=5.0",
        decision_timestamp="2026-01-01T00:00:00Z",
        now=_NOW,
        installed_version=lambda name: "5.0",
    )
    assert ev.status == "expired"


def test_library_reads_real_installed_dist_metadata():
    """Against a dist guaranteed present in the test venv (pytest itself),
    with a floor of 0 so the verdict is version-independent."""
    ev = evaluate_expires_when(
        "library:pytest>=0",
        decision_timestamp="2026-01-01T00:00:00Z",
        now=_NOW,
    )
    assert ev.status == "expired"


# ---------------------------------------------------------------------------
# Evaluator — manual: never auto-fires
# ---------------------------------------------------------------------------


def test_manual_never_auto_fires():
    ev = evaluate_expires_when(
        "manual:security-review",
        decision_timestamp="2026-01-01T00:00:00Z",
        now=_NOW,
    )
    assert ev.status == "manual_review"
    assert ev.kind == "manual"
    assert "never auto-fires" in ev.detail


# ---------------------------------------------------------------------------
# stale_decisions wiring — expired decisions surface with the fired pattern
# ---------------------------------------------------------------------------


def test_stale_surfaces_expired_date_with_pattern(storage):
    storage.log_event(ChangeEvent(
        entity_path="deps/legacy", change_type="add",
        timestamp="2026-01-01T00:00:00Z", expires_when="date:2026-06-01",
        reasoning="Pinned the legacy dep until mid-year.",
    ))
    rows = storage.get_stale_decisions(now="2026-12-31T00:00:00Z")
    assert len(rows) == 1
    r = rows[0]
    assert r["flag"] == "expired"
    assert r["expired_pattern"] == "date"
    assert r["expires_status"] == "expired"
    assert "expires_when_fired" in r["active_use_signals"]
    assert "expired" in r["stale_reason"]
    # Rule-1/2 keys stay always-present as explicit "absent" values.
    assert r["revisit_due"] == "" and r["days_overdue"] == 0
    assert r["matched_terms"] == [] and r["matched_event_id"] == ""


def test_stale_entity_pattern_fires_on_a_later_event_only(storage):
    storage.log_event(ChangeEvent(
        entity_path="payments", change_type="add",
        timestamp="2026-01-01T00:00:00Z",
        expires_when="entity:deps/stripe:changes",
        reasoning="Payments design assumes the current Stripe integration.",
    ))
    # No event on deps/stripe yet → nothing surfaces.
    assert storage.get_stale_decisions(now="2026-12-31T00:00:00Z") == []

    storage.log_event(ChangeEvent(
        entity_path="deps/stripe", change_type="modify",
        timestamp="2026-02-01T00:00:00Z", reasoning="Bumped stripe SDK.",
    ))
    rows = storage.get_stale_decisions(now="2026-12-31T00:00:00Z")
    assert len(rows) == 1
    assert rows[0]["flag"] == "expired"
    assert rows[0]["expired_pattern"] == "entity"


def test_stale_entity_pattern_covers_dotted_prefix_children(storage):
    """entity:users:changes fires when users.email changes — same prefix
    convention as every other read surface."""
    storage.log_event(ChangeEvent(
        entity_path="auth-design", change_type="add",
        timestamp="2026-01-01T00:00:00Z", expires_when="entity:users:changes",
        reasoning="Auth design assumes the users table stays as-is.",
    ))
    storage.log_event(ChangeEvent(
        entity_path="users.email", change_type="modify",
        timestamp="2026-02-01T00:00:00Z", reasoning="Widened email column.",
    ))
    rows = storage.get_stale_decisions(now="2026-12-31T00:00:00Z")
    assert [r["entity_path"] for r in rows] == ["auth-design"]
    assert rows[0]["expired_pattern"] == "entity"


def test_stale_unobservable_library_presents_as_manual_review(storage):
    storage.log_event(ChangeEvent(
        entity_path="deps/ghost", change_type="add",
        timestamp="2026-01-01T00:00:00Z",
        expires_when="library:definitely-not-a-real-dist-xyz>=1",
        reasoning="Blocked on a dependency this venv can't observe.",
    ))
    rows = storage.get_stale_decisions(now="2026-12-31T00:00:00Z")
    assert len(rows) == 1
    r = rows[0]
    assert r["flag"] == "manual_review"
    assert r["expires_status"] == "manual_review"
    assert r["expired_pattern"] == ""  # nothing fired — presented, not guessed
    assert "expires_manual_review" in r["active_use_signals"]


def test_stale_manual_pattern_never_auto_surfaces(storage):
    storage.log_event(ChangeEvent(
        entity_path="deps/manual", change_type="add",
        timestamp="2026-01-01T00:00:00Z", expires_when="manual:sec-review",
        reasoning="Awaiting a human security review.",
    ))
    assert storage.get_stale_decisions(now="2026-12-31T00:00:00Z") == []


def test_stale_pending_expires_when_annotates_rows_surfaced_by_other_rules(storage):
    """A row surfaced by rule 1 still reports its (pending) expires_when
    evaluation — every field always populated, never null."""
    storage.log_event(ChangeEvent(
        entity_path="users", change_type="add", entity_type="table",
        timestamp="2026-01-01T00:00:00Z", revisit_after="90d",
        expires_when="date:2099-01-01", reasoning="Architectural decision.",
    ))
    storage.record_tool_call("blame", entity_path="users")
    rows = storage.get_stale_decisions(now="2026-12-31T00:00:00Z")
    assert len(rows) == 1
    r = rows[0]
    assert r["flag"] == "revisit_due"
    assert r["expires_status"] == "pending"
    assert r["expired_pattern"] == ""
    assert r["expires_detail"] != ""


def test_stale_skips_ungrammatical_stored_value_not_the_row(storage):
    """A value written around the validator (raw INSERT) skips the expiry
    rule rather than crashing the query."""
    import sqlite3

    with sqlite3.connect(str(storage.db_path)) as conn:
        conn.execute(
            "INSERT INTO events (id, timestamp, entity_path, change_type, expires_when) "
            "VALUES ('raw1', '2026-01-01T00:00:00.000000Z', 'legacy', 'add', 'free prose')"
        )
        conn.commit()
    assert storage.get_stale_decisions(now="2026-12-31T00:00:00Z") == []


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
