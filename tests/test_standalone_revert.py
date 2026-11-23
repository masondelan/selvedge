"""Explicit reverts remain recallable when their original attempt was never recorded."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from selvedge.cli import cli
from selvedge.gitimport import GIT_IMPORT_AGENT
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage

ENTITY = "users.auth_token"
RATIONALE = "Revert per-user auth tokens\n\nShort-lived JWTs replace stored tokens."


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SelvedgeStorage:
    db_path = tmp_path / "recall.db"
    monkeypatch.setenv("SELVEDGE_DB", str(db_path))
    monkeypatch.chdir(tmp_path)
    return SelvedgeStorage(db_path)


def _git(repo: Path, *args: str, date: str = "2026-01-02T10:00:00+00:00") -> str:
    env = {
        "PATH": os.defpath,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        env=env, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _import_revert(tmp_path: Path) -> str:
    """Import a real throwaway Git timeline through the public CLI."""
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "schema.sql").write_text("ALTER TABLE users ADD COLUMN auth_token TEXT;\n")
    _git(repo, "add", "schema.sql")
    _git(repo, "commit", "-m", "Add auth token", date="2026-01-01T10:00:00+00:00")
    (repo / "schema.sql").write_text("ALTER TABLE users DROP COLUMN auth_token;\n")
    _git(repo, "add", "schema.sql")
    _git(repo, "commit", "-m", RATIONALE)
    commit = _git(repo, "rev-parse", "HEAD")
    imported = CliRunner().invoke(cli, ["import", str(repo), "--from-git"])
    assert imported.exit_code == 0, imported.output
    return commit


@pytest.mark.parametrize("paired", [False, True])
def test_git_import_recall_keeps_rationale_and_pairing(storage, tmp_path, paired):
    """An importer-only history surfaces once; a recorded attempt still owns the pair."""
    attempt = None
    if paired:
        attempt = storage.log_event(ChangeEvent(
            entity_path=ENTITY, change_type="modify",
            timestamp="2026-01-01T10:00:00Z", reasoning="Try storing auth tokens locally.",
        ))
    commit = _import_revert(tmp_path)
    closing = next(row for row in storage.get_entity_history(ENTITY)
                   if row["change_type"] == "revert")
    assert closing["git_commit"] == commit
    assert closing["agent"] == GIT_IMPORT_AGENT
    assert closing["reasoning"] == RATIONALE
    expected_id = attempt.id if attempt is not None else closing["id"]

    for lookup in ({"entity_path": ENTITY}, {"entity_path": "users"},
                   {"query": "Short-lived JWTs"}):
        for confidence in ("proximity_high", "proximity_low"):
            rows = [row for row in storage.get_prior_attempts(
                **lookup, min_confidence=confidence, window_minutes=1,
            ) if row["entity_path"] == ENTITY]
            assert len(rows) == 1
            row = rows[0]
            assert row["id"] == expected_id
            assert row["outcome"] == "reverted"
            assert row["confidence"] == "exact"
            assert row["outcome_reasoning"] == RATIONALE
            assert row["match_type"] == ("substring" if "query" in lookup else "exact")
            if attempt is None:
                assert row["reasoning"] == RATIONALE
                assert row["git_commit"] == commit
                assert row["agent"] == GIT_IMPORT_AGENT


@pytest.mark.parametrize("change_type,outcome", [("revert", "reverted"), ("reject", "rejected")])
def test_direct_standalone_verdict_is_exact(storage, change_type, outcome):
    event = storage.log_event(ChangeEvent(
        entity_path=ENTITY, change_type=change_type,
        reasoning=RATIONALE, timestamp="2026-01-02T10:00:00Z",
    ))
    for kwargs in ({"entity_path": ENTITY}, {"query": "Short-lived JWTs"}):
        rows = storage.get_prior_attempts(**kwargs)
        assert len(rows) == 1
        assert rows[0]["id"] == event.id
        assert rows[0]["outcome"] == outcome
        assert rows[0]["confidence"] == "exact"
        assert rows[0]["reasoning"] == rows[0]["outcome_reasoning"] == RATIONALE


@pytest.mark.parametrize("change_type", ["remove", "delete", "index_remove"])
def test_standalone_implicit_removal_does_not_claim_a_prior_attempt(storage, change_type):
    storage.log_event(ChangeEvent(entity_path=ENTITY, change_type=change_type))
    assert storage.get_prior_attempts(entity_path=ENTITY, min_confidence="proximity_low") == []


def test_targeted_supersede_reopens_only_its_standalone_revert(storage):
    target = storage.log_event(ChangeEvent(
        entity_path=ENTITY, change_type="revert", timestamp="2026-01-01T10:00:00Z",
        reasoning="First rollback retains its own verdict.",
    ))
    sibling = storage.log_event(ChangeEvent(
        entity_path=ENTITY, change_type="revert", timestamp="2026-01-02T10:00:00Z",
        reasoning="A separate rollback on the same entity.",
    ))
    other_path = storage.log_event(ChangeEvent(
        entity_path="users.refresh_token", change_type="revert",
        timestamp="2026-01-03T10:00:00Z", reasoning="Independent refresh-token rollback.",
    ))
    superseding = storage.log_supersede(
        ENTITY, supersedes=target.id, reasoning="Only the first rollback constraint changed.",
    )
    rows = {row["id"]: row for row in storage.get_prior_attempts(entity_path="users")}
    assert set(rows) == {target.id, sibling.id, other_path.id}
    assert rows[target.id]["outcome"] == "reopened"
    assert rows[target.id]["superseded_by"] == superseding.id
    assert rows[target.id]["supersede_reasoning"] == superseding.reasoning
    for event in (sibling, other_path):
        assert rows[event.id]["outcome"] == "reverted"
        assert rows[event.id]["superseded_by"] == ""
        assert rows[event.id]["confidence"] == "exact"
        assert rows[event.id]["outcome_reasoning"] == event.reasoning


@pytest.mark.parametrize("imported", [False, True])
def test_standalone_revert_cli_json_text_and_mcp(storage, tmp_path, monkeypatch, imported):
    """Both user surfaces retain the source ID and rationale, including --all recall."""
    import selvedge.server as srv

    if imported:
        _import_revert(tmp_path)
    else:
        storage.log_event(ChangeEvent(entity_path=ENTITY, change_type="revert", reasoning=RATIONALE))
    monkeypatch.setattr(srv, "_storage", storage)
    event = storage.get_entity_history(ENTITY)[0]
    runner = CliRunner()
    for show_all in (False, True):
        args = ["prior-attempts", ENTITY, "--json"] + (["--all"] if show_all else [])
        response = runner.invoke(cli, args)
        assert response.exit_code == 0, response.output
        rows = json.loads(response.stdout)
        mcp_rows = srv.prior_attempts(
            entity_path=ENTITY,
            min_confidence="proximity_low" if show_all else "proximity_high",
        )
        assert rows == mcp_rows
        assert len(rows) == 1
        assert rows[0]["id"] == event["id"]
        assert rows[0]["reasoning"] == rows[0]["outcome_reasoning"] == RATIONALE
        assert rows[0]["outcome"] == "reverted"
        assert rows[0]["confidence"] == "exact"
    text = runner.invoke(cli, ["prior-attempts", ENTITY])
    assert text.exit_code == 0, text.output
    assert "reverted:" in text.stdout
    assert "tried:" not in text.stdout
    assert text.stdout.count("Short-lived JWTs replace stored tokens.") == 1
