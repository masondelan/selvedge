"""Guard the publication-safe native Action fixture and its narrow execution scope."""

from __future__ import annotations

import hashlib
import json
import runpy
import sqlite3
from dataclasses import asdict
from pathlib import Path

import pytest

from selvedge.ledger import read_ledger
from selvedge.models import ChangeEvent

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/native-review-context"
GENERATOR = runpy.run_path(str(FIXTURE / "build_fixture.py"))["build_fixture"]
COMPOSE = runpy.run_path(str(ROOT / "scripts/review_context.py"))["compose"]
PROBE = "tests/fixtures/native-review-context/probe.py"
EMPTY = "tests/fixtures/native-review-context/empty.py"


def _records(db: Path) -> list[dict]:
    connection = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute("SELECT * FROM events ORDER BY id")]
    finally:
        connection.close()


def test_frozen_fixture_contains_only_the_explicit_synthetic_records():
    expected = json.loads((FIXTURE / "records.json").read_text())
    actual = _records(FIXTURE / "history.db")
    assert len(actual) == len(expected) == 3
    for row, seed in zip(actual, expected, strict=True):
        assert row == asdict(ChangeEvent(**seed))
        assert row["reasoning"].startswith("SYNTHETIC VALIDATION ONLY:")
        assert row["agent"] == "synthetic-fixture"
        assert row["git_commit"] == row["session_id"] == row["project"] == ""
    connection = sqlite3.connect((FIXTURE / "history.db").as_uri() + "?mode=ro", uri=True)
    try:
        assert connection.execute("SELECT count(*) FROM tool_calls").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM path_migrations").fetchone()[0] == 0
    finally:
        connection.close()


def test_generator_is_repeatable_and_refuses_existing_data(tmp_path):
    first, second = tmp_path / "first.db", tmp_path / "second.db"
    assert GENERATOR(first) == GENERATOR(second)
    assert first.read_bytes() == second.read_bytes()
    assert _records(first) == _records(FIXTURE / "history.db")
    before = first.read_bytes()
    with pytest.raises(FileExistsError):
        GENERATOR(first)
    assert first.read_bytes() == before
    blocked = tmp_path / "blocked.db"
    sidecar = Path(str(blocked) + "-wal")
    sidecar.write_bytes(b"unrelated synthetic sidecar")
    with pytest.raises(ValueError, match="sidecar"):
        GENERATOR(blocked)
    assert not blocked.exists()
    assert sidecar.read_bytes() == b"unrelated synthetic sidecar"


def test_reading_and_rendering_the_fixture_never_changes_it():
    db = FIXTURE / "history.db"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    report = read_ledger(db, [PROBE], 10)
    assert report["total_matching"] == 2
    assert report["chain"]["intact"]
    assert report["chain_coverage"]["unchained"] == 0
    assert read_ledger(db, [EMPTY], 10)["total_matching"] == 0
    body = COMPOSE(report, "a" * 40, "b" * 40, repo="masondelan/selvedge",
                   db_path="tests/fixtures/native-review-context/history.db", budget=2000)
    assert len(body) <= 2000
    assert "SYNTHETIC VALIDATION ONLY" in body
    assert "unrelated demonstration record" not in body
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert not any(Path(str(db) + suffix).exists() for suffix in ("-wal", "-shm"))


def test_native_workflow_preserves_base_and_fixture_boundaries():
    workflow = (ROOT / ".github/workflows/review-context-native-smoke.yml").read_text()
    assert "pull_request_target:" in workflow
    assert "workflow_dispatch" not in workflow and "schedule:" not in workflow
    assert "github.event.pull_request.head.repo.full_name == github.repository" in workflow
    for branch in ("release/native-review-context-probe", "release/native-review-context-empty"):
        assert f"github.event.pull_request.head.ref == '{branch}'" in workflow
    assert workflow.count("ref: ${{ github.event.pull_request.base.sha }}") == 2
    assert workflow.count("persist-credentials: false") == 2
    assert workflow.count("db-path: tests/fixtures/native-review-context/history.db") == 3
    assert ".selvedge/selvedge.db" not in workflow
    assert "head.sha" not in workflow and "run:" not in workflow.replace("dry-run:", "")
    assert "needs: dry-run" in workflow
    assert workflow.count("pull-requests: write") == 1
    assert "contents: write" not in workflow and "id-token:" not in workflow
    assert workflow.count("uses: ./actions/review-context") == 3
    action = (ROOT / "actions/review-context/action.yml").read_text()
    assert "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0" in action
