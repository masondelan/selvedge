"""
Tests for the `selvedge migrate-paths` backfill (v0.3.7).

Exercises :meth:`SelvedgeStorage.recanonicalize_paths` directly — the dry-run
default, the collisions report, the `--apply` write, the `path_migrations`
audit row, and idempotency. Legacy (un-canonical) rows are inserted raw
because the normal write path now canonicalizes, so a "pre-v0.3.7" database
has to be simulated at the SQL level.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from click.testing import CliRunner

from selvedge.cli import cli
from selvedge.storage import SelvedgeStorage


def _seed_legacy(db_path: Path, paths: list[str]) -> None:
    """Insert rows with un-canonical entity_paths, bypassing the write path."""
    SelvedgeStorage(db_path)  # create the schema
    con = sqlite3.connect(db_path)
    for i, p in enumerate(paths):
        con.execute(
            "INSERT INTO events (id, timestamp, entity_type, entity_path, "
            "change_type, diff, reasoning, agent, session_id, git_commit, "
            "project, changeset_id, metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                f"2026-01-0{i + 1}T00:00:00Z",
                "function",
                p,
                "add",
                "",
                "legacy",
                "",
                "",
                "",
                "",
                "",
                "{}",
            ),
        )
    con.commit()
    con.close()


@pytest.fixture
def storage(tmp_path: Path) -> SelvedgeStorage:
    db = tmp_path / "m.db"
    _seed_legacy(db, ["./src/auth.py::login", "src//auth.py::login", "users.email"])
    return SelvedgeStorage(db)


def test_dry_run_is_the_default_and_writes_nothing(storage):
    report = storage.recanonicalize_paths()  # apply defaults to False
    assert report["applied"] is False
    assert report["rows_to_rewrite"] == 2
    assert report["rows_rewritten"] == 0
    # Nothing changed on disk: the legacy spellings are still present.
    assert storage.get_blame("./src/auth.py::login") is not None
    assert storage.get_last_path_migration() is None


def test_dry_run_collisions_report(storage):
    report = storage.recanonicalize_paths()
    # Both legacy spellings converge to the same canonical path.
    assert len(report["collisions"]) == 1
    collision = report["collisions"][0]
    assert collision["canonical"] == "src/auth.py::login"
    assert collision["paths"] == ["./src/auth.py::login", "src//auth.py::login"]


def test_apply_rewrites_rows(storage):
    report = storage.recanonicalize_paths(apply=True)
    assert report["applied"] is True
    assert report["rows_rewritten"] == 2
    # Canonical path now holds both events; messy spellings are gone.
    assert storage.get_blame("./src/auth.py::login") is None
    assert len(storage.get_entity_history("src/auth.py::login")) == 2


def test_apply_writes_audit_row(storage):
    storage.recanonicalize_paths(apply=True, agent="claude-code")
    audit = storage.get_last_path_migration()
    assert audit is not None
    assert audit["rows_rewritten"] == 2
    assert audit["collisions"] == 1
    assert audit["agent"] == "claude-code"


def test_apply_is_idempotent_on_event_data(storage):
    storage.recanonicalize_paths(apply=True)
    second = storage.recanonicalize_paths(apply=True)
    # Re-running rewrites nothing — every path is already canonical.
    assert second["rows_to_rewrite"] == 0
    assert second["rows_rewritten"] == 0
    assert second["collisions"] == []


def test_apply_preserves_event_count_while_merging(storage):
    before = storage.count()
    storage.recanonicalize_paths(apply=True)
    # Histories merge (fewer distinct paths) but no events are lost.
    assert storage.count() == before


def test_cli_dry_run_apply_idempotent_and_json(tmp_path: Path):
    """End-to-end `selvedge migrate-paths` flow exercising the CLI rendering."""
    db = tmp_path / "cli.db"
    _seed_legacy(db, ["./src/auth.py::login", "src//auth.py::login", "users.email"])
    runner = CliRunner()
    env = {"SELVEDGE_DB": str(db), "SELVEDGE_QUIET": "1"}

    # Dry run (default) — reports rewrites + collisions, writes nothing.
    dry = runner.invoke(cli, ["migrate-paths"], env=env)
    assert dry.exit_code == 0
    assert "Would rewrite" in dry.output
    assert "collision" in dry.output.lower()
    assert "Dry run" in dry.output

    # Apply — writes and confirms.
    applied = runner.invoke(cli, ["migrate-paths", "--apply", "--agent", "claude-code"], env=env)
    assert applied.exit_code == 0
    assert "Applied" in applied.output
    # The audit row actually landed via the CLI path (not just the storage
    # layer), with the --agent value recorded.
    audit = SelvedgeStorage(db).get_last_path_migration()
    assert audit is not None
    assert audit["rows_rewritten"] == 2
    assert audit["agent"] == "claude-code"

    # Re-run is a no-op on the event data.
    again = runner.invoke(cli, ["migrate-paths"], env=env)
    assert "already canonical" in again.output

    # JSON output is machine-readable.
    as_json = runner.invoke(cli, ["migrate-paths", "--json"], env=env)
    payload = json.loads(as_json.output)
    assert payload["rows_to_rewrite"] == 0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
