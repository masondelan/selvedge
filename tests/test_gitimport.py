"""
Tests for ``selvedge import --from-git`` (v0.3.9.1).

Builds real throwaway git repositories in ``tmp_path`` (the importer shells
out to git, so anything less would test a mock, not the feature) and locks
in:

  - the two sweeps (revert-message commits, file-deletion commits),
  - dropped table/column extraction from removed SQL lines,
  - reasoning = subject + body, agent = "git-import", commit stamping,
  - --since with both a ref and a date,
  - idempotency on (commit, entity) across re-runs,
  - integration: imported reverts close prior attempts and gate
    get_decision_status (what the enforcement hook reads),
  - the Agent Trace round-trip: exported imported events re-import cleanly.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from selvedge.gitimport import GIT_IMPORT_AGENT, GitImportError, collect_revert_events
from selvedge.storage import SelvedgeStorage

# ---------------------------------------------------------------------------
# Fixture repo
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str, env_date: str = "") -> None:
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "HOME": str(repo),  # keep user gitconfig out of the picture
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    if env_date:
        env["GIT_AUTHOR_DATE"] = env_date
        env["GIT_COMMITTER_DATE"] = env_date
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env=env,
    )


def _commit_all(repo: Path, message: str, date: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message, env_date=date)
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo with: an add, a revert (message sweep), and a deletion (diff sweep)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")

    (repo / "migrations").mkdir()
    (repo / "migrations" / "0001_add_tokens.sql").write_text(
        "ALTER TABLE users ADD COLUMN auth_token TEXT;\n"
    )
    _commit_all(repo, "Add per-user auth token column", "2026-01-01T10:00:00+00:00")

    # The revert: message says revert, and the diff drops the column.
    (repo / "migrations" / "0002_drop_tokens.sql").write_text(
        "ALTER TABLE users DROP COLUMN auth_token;\n"
    )
    _commit_all(
        repo,
        "Revert per-user auth tokens\n\nTokens must be short-lived JWTs, not stored.",
        "2026-01-02T10:00:00+00:00",
    )

    # A plain deletion with no "revert" in the message (diff-filter sweep).
    (repo / "legacy_export.py").write_text("print('old')\n")
    _commit_all(repo, "Add legacy export script", "2026-01-03T10:00:00+00:00")
    (repo / "legacy_export.py").unlink()
    _commit_all(repo, "Drop unused legacy export", "2026-01-04T10:00:00+00:00")

    return repo


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def test_collects_message_and_deletion_sweeps(repo):
    events = collect_revert_events(repo)
    by_path = {e.entity_path for e in events}
    # Message sweep: the touched migration file + the dropped column.
    assert "migrations/0002_drop_tokens.sql" in by_path
    assert "users.auth_token" in by_path
    # Deletion sweep: the deleted file, despite no "revert" in the message.
    assert "legacy_export.py" in by_path


def test_event_shape(repo):
    events = collect_revert_events(repo)
    column_event = next(e for e in events if e.entity_path == "users.auth_token")
    assert column_event.change_type == "revert"
    assert column_event.entity_type == "column"
    assert column_event.agent == GIT_IMPORT_AGENT
    assert len(column_event.git_commit) == 40
    # Reasoning is subject + body.
    assert column_event.reasoning.startswith("Revert per-user auth tokens")
    assert "short-lived JWTs" in column_event.reasoning
    # Author date, normalized to canonical UTC.
    assert column_event.timestamp.startswith("2026-01-02T10:00:00")

    deletion_event = next(e for e in events if e.entity_path == "legacy_export.py")
    assert deletion_event.entity_type == "file"
    assert deletion_event.reasoning == "Drop unused legacy export"


def test_events_sorted_oldest_first(repo):
    events = collect_revert_events(repo)
    timestamps = [e.timestamp for e in events]
    assert timestamps == sorted(timestamps)


def test_since_ref_limits_range(repo):
    # Tag the revert commit; --since TAG must exclude it and earlier.
    _git(repo, "tag", "cutoff", "HEAD~1")
    events = collect_revert_events(repo, since="cutoff")
    by_path = {e.entity_path for e in events}
    assert "users.auth_token" not in by_path  # before the cutoff
    assert "legacy_export.py" in by_path  # after it


def test_since_date_limits_range(repo):
    events = collect_revert_events(repo, since="2026-01-03")
    by_path = {e.entity_path for e in events}
    assert "users.auth_token" not in by_path
    assert "legacy_export.py" in by_path


def test_git_revert_shape_extracts_removed_add_column(tmp_path):
    """A true `git revert` REMOVES the original ADD COLUMN lines — the other
    diff shape from the down-migration in the main fixture."""
    repo = tmp_path / "revert-shape"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    migration = repo / "schema.sql"
    migration.write_text("ALTER TABLE payments ADD COLUMN card_number TEXT;\n")
    _commit_all(repo, "Add card_number to payments", "2026-02-01T10:00:00+00:00")
    _git(repo, "revert", "--no-edit", "HEAD", env_date="2026-02-02T10:00:00+00:00")

    events = collect_revert_events(repo)
    by_path = {e.entity_path: e for e in events}
    assert "payments.card_number" in by_path
    assert by_path["payments.card_number"].entity_type == "column"
    assert by_path["payments.card_number"].reasoning.startswith("Revert")


def test_pure_rename_is_not_a_false_revert(tmp_path):
    """Regression: a rename must report as R (new path), never as a delete —
    so it never lands in the deletion sweep as a phantom revert. Forced with
    -M independent of the user's diff.renames config."""
    repo = tmp_path / "rename-repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    # Turn rename detection OFF in config to prove we override it with -M.
    _git(repo, "config", "diff.renames", "false")
    (repo / "old_name.py").write_text("print('hi')\n" * 5)
    _commit_all(repo, "Add module", "2026-03-01T10:00:00+00:00")
    _git(repo, "mv", "old_name.py", "new_name.py")
    _commit_all(repo, "Rename module for clarity", "2026-03-02T10:00:00+00:00")

    events = collect_revert_events(repo)
    by_path = {e.entity_path for e in events}
    # The rename commit had no "revert" in the message and deleted nothing
    # (it's an R), so neither path should surface as a revert.
    assert "old_name.py" not in by_path
    assert "new_name.py" not in by_path


def test_since_typo_ref_warns_on_stderr(tmp_path, capsys):
    """A typo'd --since ref falls through to git's date parser; the importer
    must say so on stderr rather than silently walking the whole history."""
    repo = tmp_path / "warn-repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("x\n")
    _commit_all(repo, "init", "2026-03-01T10:00:00+00:00")
    collect_revert_events(repo, since="v9.9.9-nope")
    assert "does not resolve as a git ref" in capsys.readouterr().err


def test_non_repo_raises(tmp_path):
    bare = tmp_path / "not-a-repo"
    bare.mkdir()
    with pytest.raises(GitImportError):
        collect_revert_events(bare)


def test_project_tag_applied(repo):
    events = collect_revert_events(repo, project="my-api")
    assert all(e.project == "my-api" for e in events)


# ---------------------------------------------------------------------------
# CLI + idempotency
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SELVEDGE_DB", str(tmp_path / "gitimport.db"))
    import selvedge.server as srv
    srv._storage = None
    yield
    srv._storage = None


def _db_storage():
    from selvedge.config import get_db_path

    return SelvedgeStorage(get_db_path())


def test_cli_import_from_git(runner, repo):
    from selvedge.cli import cli

    result = runner.invoke(cli, ["import", str(repo), "--from-git"])
    assert result.exit_code == 0, result.output
    assert "Imported" in result.output

    storage = _db_storage()
    row = storage.get_blame("users.auth_token")
    assert row["change_type"] == "revert"
    assert row["agent"] == GIT_IMPORT_AGENT


def test_cli_import_from_git_is_idempotent(runner, repo):
    from selvedge.cli import cli

    first = runner.invoke(cli, ["import", str(repo), "--from-git"])
    assert first.exit_code == 0, first.output
    count_after_first = _db_storage().count()

    second = runner.invoke(cli, ["import", str(repo), "--from-git"])
    assert second.exit_code == 0, second.output
    assert "Nothing new to import" in second.output
    assert _db_storage().count() == count_after_first


def test_cli_dry_run_writes_nothing(runner, repo):
    from selvedge.cli import cli

    result = runner.invoke(cli, ["import", str(repo), "--from-git", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert _db_storage().count() == 0


def test_cli_json_output(runner, repo):
    from selvedge.cli import cli

    result = runner.invoke(cli, ["import", str(repo), "--from-git", "--json"])
    assert result.exit_code == 0, result.output
    events = json.loads(result.output)
    assert all(e["change_type"] == "revert" for e in events)
    assert _db_storage().count() == 0  # --json implies dry-run


def test_cli_path_still_required_without_from_git(runner):
    from selvedge.cli import cli

    result = runner.invoke(cli, ["import"])
    assert result.exit_code == 2
    assert "PATH is required" in result.output


def test_cli_since_requires_from_git(runner, tmp_path):
    from selvedge.cli import cli

    target = tmp_path / "m.sql"
    target.write_text("CREATE TABLE t (id INT);")
    result = runner.invoke(cli, ["import", str(target), "--since", "v1.0"])
    assert result.exit_code == 2
    assert "--since" in result.output


def test_cli_missing_path_errors(runner, tmp_path):
    from selvedge.cli import cli

    result = runner.invoke(cli, ["import", str(tmp_path / "missing.sql")])
    assert result.exit_code == 2
    assert "does not exist" in result.output


# ---------------------------------------------------------------------------
# Integration — imported reverts feed the decision surfaces
# ---------------------------------------------------------------------------


def test_imported_revert_gates_decision_status(runner, repo):
    """The whole point: a pre-Selvedge revert becomes a standing verdict."""
    from selvedge.cli import cli

    runner.invoke(cli, ["import", str(repo), "--from-git"])
    storage = _db_storage()
    decision = storage.get_decision_status("users.auth_token")
    assert decision["status"] == "reverted"
    # ...which the supersede flow can then re-open, append-only.
    storage.log_supersede(
        "users.auth_token", reasoning="IdP change made stored tokens acceptable."
    )
    assert storage.get_decision_status("users.auth_token")["status"] == "reopened"


def test_imported_revert_closes_prior_attempts(runner, repo):
    from selvedge.cli import cli
    from selvedge.models import ChangeEvent

    runner.invoke(cli, ["import", str(repo), "--from-git"])
    storage = _db_storage()
    # A pre-existing attempt on the same entity, older than the revert.
    storage.log_event(ChangeEvent(
        entity_path="users.auth_token",
        change_type="add",
        timestamp="2026-01-01T09:00:00Z",
        reasoning="Original attempt at storing tokens.",
    ))
    results = storage.get_prior_attempts(entity_path="users.auth_token")
    attempt = next(r for r in results if r["change_type"] == "add")
    assert attempt["outcome"] == "reverted"
    assert "short-lived JWTs" in attempt["outcome_reasoning"]


# ---------------------------------------------------------------------------
# Agent Trace round-trip
# ---------------------------------------------------------------------------


def test_agent_trace_round_trip(runner, repo, tmp_path):
    """Exported imported events must re-import cleanly with fidelity."""
    from selvedge.cli import cli

    runner.invoke(cli, ["import", str(repo), "--from-git"])
    original = {
        (e["git_commit"], e["entity_path"], e["change_type"])
        for e in _db_storage().get_history(limit=1000)
    }

    trace_path = tmp_path / "trace.json"
    export = runner.invoke(
        cli, ["export", "--format", "agent-trace", "-o", str(trace_path)]
    )
    assert export.exit_code == 0, export.output

    # Re-import into a second, fresh DB.
    import selvedge.server as srv

    fresh_db = tmp_path / "fresh.db"
    import os
    os.environ["SELVEDGE_DB"] = str(fresh_db)
    srv._storage = None
    try:
        reimport = runner.invoke(
            cli, ["import", str(trace_path), "--format", "agent-trace"]
        )
        assert reimport.exit_code == 0, reimport.output
        rows = SelvedgeStorage(fresh_db).get_history(limit=1000)
        round_tripped = {
            (e["git_commit"], e["entity_path"], e["change_type"]) for e in rows
        }
        assert round_tripped == original
        assert all(e["change_type"] == "revert" for e in rows)
    finally:
        srv._storage = None


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])


# ---------------------------------------------------------------------------
# Revert detection must be anchored, not a substring sweep
#
# `_REVERT_RE` matched "revert" anywhere in a subject or body, so an ordinary
# commit that merely mentions the word seeded `revert` events on every file it
# touched. A seeded false "reverted" verdict gates the enforcement hook on
# innocent entities — the exact outcome the module docstring says this parser
# exists to avoid.
# ---------------------------------------------------------------------------


def _repo_with(tmp_path: Path, commits: list[tuple[str, str, str]]) -> Path:
    """Build a repo from (filename, contents, message) triples."""
    repo = tmp_path / "anchored"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for i, (name, contents, message) in enumerate(commits):
        (repo / name).write_text(contents)
        _commit_all(repo, message, f"2026-02-0{i + 1}T10:00:00+00:00")
    return repo


def _reverted_paths(repo: Path) -> set[str]:
    return {
        e.entity_path
        for e in collect_revert_events(repo)
        if e.change_type == "revert"
    }


def test_commit_merely_mentioning_revert_is_not_a_rollback(tmp_path: Path) -> None:
    repo = _repo_with(tmp_path, [
        ("app.py", "x = 1\n", "feat(ui): add a revert button to the toolbar"),
        ("README.md", "docs\n", "docs: explain how to revert a migration"),
    ])
    assert _reverted_paths(repo) == set()


def test_git_revert_style_subject_is_a_rollback(tmp_path: Path) -> None:
    repo = _repo_with(tmp_path, [
        ("app.py", "x = 1\n", "feat: add sso tokens"),
        ("app.py", "", 'Revert "feat: add sso tokens"'),
    ])
    assert "app.py" in _reverted_paths(repo)


def test_this_reverts_commit_body_is_a_rollback(tmp_path: Path) -> None:
    repo = _repo_with(tmp_path, [
        ("app.py", "x = 1\n", "feat: add sso tokens"),
        ("app.py", "", "undo the token work\n\nThis reverts commit "
                       "0123456789abcdef0123456789abcdef01234567."),
    ])
    assert "app.py" in _reverted_paths(repo)


def test_conventional_prefix_before_revert_still_counts(tmp_path: Path) -> None:
    repo = _repo_with(tmp_path, [
        ("app.py", "x = 1\n", "feat: add sso tokens"),
        ("app.py", "", "fix: revert the sso token rollout"),
    ])
    assert "app.py" in _reverted_paths(repo)
