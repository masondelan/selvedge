"""
Tests for ``selvedge verify`` — the v0.3.5 correctness gate.

Exercises the underlying ``run_checks`` collector and the Click entry point
together — the JSON output, two-tier exit codes, and ``--strict`` are part
of the contract callers will rely on.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from selvedge import verify as verify_mod
from selvedge.cli import cli
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    p = tmp_path / ".selvedge" / "selvedge.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SELVEDGE_DB", str(p))
    monkeypatch.chdir(tmp_path)
    return p


# ---------------------------------------------------------------------------
# Tier locking — adding an unknown id without updating CHECK_TIERS is loud
# ---------------------------------------------------------------------------


def test_every_check_id_is_categorized(db_path):
    """``run_checks`` asserts the tier is known; every emitted id must be in CHECK_TIERS."""
    SelvedgeStorage(db_path)  # create empty DB
    results = verify_mod.run_checks(db_path)
    for r in results:
        assert r.id in verify_mod.CHECK_TIERS, (
            f"check {r.id!r} has no tier — add it to CHECK_TIERS"
        )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_clean_db_passes_every_check(db_path):
    s = SelvedgeStorage(db_path)
    # Use a multi-event changeset so we don't trip the singleton-warning check.
    s.log_event(ChangeEvent(
        entity_path="users.email", change_type="add",
        changeset_id="cs-1", git_commit="abc123",
    ))
    s.log_event(ChangeEvent(
        entity_path="users.name", change_type="add",
        changeset_id="cs-1", git_commit="abc123",
    ))
    results = verify_mod.run_checks(db_path)
    for r in results:
        assert r.status == "PASS", f"{r.id}: {r.detail}"
    assert verify_mod.exit_code(results) == 0
    assert verify_mod.exit_code(results, strict=True) == 0


# ---------------------------------------------------------------------------
# must_fail tier
# ---------------------------------------------------------------------------


def test_unknown_change_type_in_store_fails(db_path):
    """Direct INSERT of a bogus change_type — ChangeEvent guards in normal writes."""
    SelvedgeStorage(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO events (id, timestamp, entity_type, entity_path, change_type) "
            "VALUES (?, ?, ?, ?, ?)",
            ("evt1", "2026-05-11T00:00:00Z", "column", "users.email", "explode"),
        )
        conn.commit()

    results = verify_mod.run_checks(db_path)
    by_id = {r.id: r for r in results}
    assert by_id["change_type_valid"].status == "FAIL"
    assert "explode" in by_id["change_type_valid"].detail
    assert verify_mod.exit_code(results) == 1


def test_empty_entity_path_fails(db_path):
    SelvedgeStorage(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO events (id, timestamp, entity_type, entity_path, change_type) "
            "VALUES (?, ?, ?, ?, ?)",
            ("evt1", "2026-05-11T00:00:00Z", "column", "", "add"),
        )
        conn.commit()

    by_id = {r.id: r for r in verify_mod.run_checks(db_path)}
    assert by_id["entity_path_non_empty"].status == "FAIL"


def test_unparseable_timestamp_fails(db_path):
    SelvedgeStorage(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO events (id, timestamp, entity_type, entity_path, change_type) "
            "VALUES (?, ?, ?, ?, ?)",
            ("evt1", "not-a-timestamp", "column", "users.email", "add"),
        )
        conn.commit()

    by_id = {r.id: r for r in verify_mod.run_checks(db_path)}
    assert by_id["timestamp_parseable"].status == "FAIL"


def test_schema_extra_version_fails_as_downgrade(db_path):
    """schema_migrations row from a future version → must-fail (downgrade signal)."""
    SelvedgeStorage(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            (9999, "future_migration", "2030-01-01T00:00:00Z"),
        )
        conn.commit()

    by_id = {r.id: r for r in verify_mod.run_checks(db_path)}
    assert by_id["schema_migrations"].status == "FAIL"
    assert "9999" in by_id["schema_migrations"].detail


def test_missing_db_file_fails(tmp_path):
    """No DB at all → single must-fail row rather than silent pass."""
    results = verify_mod.run_checks(tmp_path / "does-not-exist.db")
    assert verify_mod.exit_code(results) == 1
    assert any(r.status == "FAIL" for r in results)


# ---------------------------------------------------------------------------
# should_warn tier
# ---------------------------------------------------------------------------


def test_singleton_changeset_is_warn_not_fail(db_path):
    s = SelvedgeStorage(db_path)
    # Single event with a changeset_id — orphan from this release's POV.
    s.log_event(ChangeEvent(
        entity_path="users.email", change_type="add", changeset_id="lonely",
    ))
    results = verify_mod.run_checks(db_path)
    by_id = {r.id: r for r in results}
    assert by_id["orphan_changeset_id"].status == "WARN"
    # No must_fail FAILs → default exit code is 0
    assert verify_mod.exit_code(results) == 0
    # --strict escalates → 1
    assert verify_mod.exit_code(results, strict=True) == 1


def test_old_event_missing_git_commit_warns(db_path):
    """Event past the backfill window with empty git_commit → WARN, not FAIL."""
    SelvedgeStorage(db_path)
    old = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO events (id, timestamp, entity_type, entity_path, change_type, git_commit) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("evt1", old, "column", "users.email", "add", ""),
        )
        conn.commit()

    by_id = {r.id: r for r in verify_mod.run_checks(db_path)}
    assert by_id["missing_git_commit"].status == "WARN"


def test_recent_event_missing_git_commit_passes(db_path):
    """Event INSIDE the backfill window with empty git_commit must NOT warn."""
    s = SelvedgeStorage(db_path)
    # Default ChangeEvent stamps timestamp=now and git_commit="" — exactly the
    # "just logged, hook hasn't fired yet" case.
    s.log_event(ChangeEvent(entity_path="users.email", change_type="add"))
    s.log_event(ChangeEvent(entity_path="users.name", change_type="add"))
    by_id = {r.id: r for r in verify_mod.run_checks(db_path)}
    assert by_id["missing_git_commit"].status == "PASS"


# ---------------------------------------------------------------------------
# CLI surface — JSON + exit codes + --strict
# ---------------------------------------------------------------------------


def test_verify_cli_json_clean_exits_zero(runner, db_path):
    s = SelvedgeStorage(db_path)
    s.log_event(ChangeEvent(
        entity_path="users.email", change_type="add",
        changeset_id="cs-1", git_commit="abc",
    ))
    s.log_event(ChangeEvent(
        entity_path="users.name", change_type="add",
        changeset_id="cs-1", git_commit="abc",
    ))
    result = runner.invoke(cli, ["verify", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["exit_code"] == 0
    assert all(c["status"] == "PASS" for c in payload["checks"])


def test_verify_cli_warn_only_exits_zero_strict_escalates(runner, db_path):
    s = SelvedgeStorage(db_path)
    s.log_event(ChangeEvent(
        entity_path="users.email", change_type="add", changeset_id="lonely",
    ))
    # Default: WARN-only → exit 0
    result = runner.invoke(cli, ["verify", "--json"])
    assert result.exit_code == 0
    # --strict: WARN promoted → exit 1
    result_strict = runner.invoke(cli, ["verify", "--strict", "--json"])
    assert result_strict.exit_code == 1


def test_verify_cli_fail_exits_one(runner, db_path):
    SelvedgeStorage(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO events (id, timestamp, entity_type, entity_path, change_type) "
            "VALUES (?, ?, ?, ?, ?)",
            ("evt1", "2026-05-11T00:00:00Z", "column", "users.email", "explode"),
        )
        conn.commit()

    result = runner.invoke(cli, ["verify", "--json"])
    assert result.exit_code == 1
