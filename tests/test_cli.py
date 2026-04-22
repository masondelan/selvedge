"""Tests for the Selvedge CLI."""

import pytest
from click.testing import CliRunner
from pathlib import Path
from selvedge.cli import cli
from selvedge.storage import SelvedgeStorage
from selvedge.models import ChangeEvent


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets a fresh DB in a temp directory."""
    monkeypatch.setenv("SELVEDGE_DB", str(tmp_path / "selvedge.db"))
    import selvedge.server as srv
    srv._storage = None
    yield
    srv._storage = None


def seed(n=1, entity="users.email", change_type="add", reasoning=""):
    """Helper to seed events directly into storage."""
    from selvedge.config import get_db_path
    storage = SelvedgeStorage(get_db_path())
    for i in range(n):
        storage.log_event(ChangeEvent(
            entity_path=entity,
            change_type=change_type,
            reasoning=reasoning or f"event {i}",
        ))


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_creates_directory(runner, tmp_path):
    result = runner.invoke(cli, ["init", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert "initialized" in result.output.lower()
    assert (tmp_path / ".selvedge").exists()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_empty(runner):
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "0" in result.output


def test_status_shows_count(runner):
    seed(3)
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "3" in result.output


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def test_diff_no_history(runner):
    result = runner.invoke(cli, ["diff", "nonexistent.column"])
    assert result.exit_code == 0
    assert "No history" in result.output


def test_diff_shows_history(runner):
    seed(2, entity="users.email", change_type="add")
    result = runner.invoke(cli, ["diff", "users.email"])
    assert result.exit_code == 0
    assert "users.email" in result.output


def test_diff_json_output(runner):
    import json
    seed(1, entity="users.email")
    result = runner.invoke(cli, ["diff", "users.email", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1


# ---------------------------------------------------------------------------
# blame
# ---------------------------------------------------------------------------


def test_blame_no_history(runner):
    result = runner.invoke(cli, ["blame", "nonexistent"])
    assert result.exit_code != 0


def test_blame_shows_entity(runner):
    seed(1, entity="users.email", reasoning="Added for login flow")
    result = runner.invoke(cli, ["blame", "users.email"])
    assert result.exit_code == 0
    assert "users.email" in result.output
    assert "Added for login flow" in result.output


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


def test_history_empty(runner):
    result = runner.invoke(cli, ["history"])
    assert result.exit_code == 0
    assert "No events" in result.output


def test_history_shows_events(runner):
    seed(3)
    result = runner.invoke(cli, ["history"])
    assert result.exit_code == 0
    assert "users.email" in result.output


def test_history_since_flag(runner):
    seed(1)
    result = runner.invoke(cli, ["history", "--since", "7d"])
    assert result.exit_code == 0
    assert "users.email" in result.output


def test_history_json_flag(runner):
    import json
    seed(2)
    result = runner.invoke(cli, ["history", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 2


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_search_no_results(runner):
    result = runner.invoke(cli, ["search", "xyzzy_nothing"])
    assert result.exit_code == 0
    assert "No events" in result.output


def test_search_finds_match(runner):
    seed(1, reasoning="billing stripe integration")
    result = runner.invoke(cli, ["search", "billing"])
    assert result.exit_code == 0
    assert "users.email" in result.output


# ---------------------------------------------------------------------------
# log (manual)
# ---------------------------------------------------------------------------


def test_log_command(runner):
    result = runner.invoke(cli, [
        "log", "users.phone", "add",
        "--reasoning", "Added phone for 2FA",
        "--agent", "human",
    ])
    assert result.exit_code == 0
    assert "users.phone" in result.output
    assert "add" in result.output


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def test_stats_empty(runner):
    result = runner.invoke(cli, ["stats"])
    assert result.exit_code == 0
    assert "No tool calls" in result.output


def test_stats_shows_total_and_ratio(runner):
    from selvedge.config import get_db_path
    from selvedge.storage import SelvedgeStorage
    storage = SelvedgeStorage(get_db_path())
    storage.record_tool_call("log_change", entity_path="users.email")
    storage.record_tool_call("log_change", entity_path="users.name")
    storage.record_tool_call("blame", entity_path="payments.amount")

    result = runner.invoke(cli, ["stats"])
    assert result.exit_code == 0
    assert "log_change" in result.output
    assert "blame" in result.output
    assert "3" in result.output  # total calls visible somewhere


def test_stats_json_output(runner):
    import json
    from selvedge.config import get_db_path
    from selvedge.storage import SelvedgeStorage
    storage = SelvedgeStorage(get_db_path())
    storage.record_tool_call("log_change", entity_path="users.email")
    storage.record_tool_call("diff", entity_path="users")

    result = runner.invoke(cli, ["stats", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["total_calls"] == 2
    assert data["by_tool"]["log_change"] == 1
    assert data["by_tool"]["diff"] == 1
    assert "log_change_ratio" in data


def test_stats_since_flag(runner):
    import json
    from selvedge.config import get_db_path
    from selvedge.storage import SelvedgeStorage
    storage = SelvedgeStorage(get_db_path())

    # Seed one old call (simulate by inserting directly with old timestamp)
    import sqlite3, uuid
    db_path = get_db_path()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO tool_calls (id, timestamp, tool_name, entity_path, success, error_msg) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), "2020-01-01T00:00:00+00:00", "log_change", "old.entity", 1, ""),
    )
    conn.commit()
    conn.close()

    # And one recent call
    storage.record_tool_call("blame", entity_path="new.entity")

    # --since 7d should only see the recent one
    result = runner.invoke(cli, ["stats", "--since", "7d", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["total_calls"] == 1
    assert "blame" in data["by_tool"]


# ---------------------------------------------------------------------------
# install-hook
# ---------------------------------------------------------------------------


def test_install_hook_creates_hook_file(runner, tmp_path):
    git_dir = tmp_path / ".git" / "hooks"
    git_dir.mkdir(parents=True)
    # fake a minimal .git dir
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

    result = runner.invoke(cli, ["install-hook", "--path", str(tmp_path)])
    assert result.exit_code == 0
    hook_file = tmp_path / ".git" / "hooks" / "post-commit"
    assert hook_file.exists()
    assert "selvedge backfill-commit" in hook_file.read_text()
    assert oct(hook_file.stat().st_mode)[-3:] == "755"


def test_install_hook_appends_to_existing(runner, tmp_path):
    git_dir = tmp_path / ".git" / "hooks"
    git_dir.mkdir(parents=True)
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    hook = git_dir / "post-commit"
    hook.write_text("#!/bin/sh\necho 'existing hook'\n")
    hook.chmod(0o755)

    result = runner.invoke(cli, ["install-hook", "--path", str(tmp_path)])
    assert result.exit_code == 0
    content = hook.read_text()
    assert "existing hook" in content
    assert "selvedge backfill-commit" in content


def test_install_hook_idempotent(runner, tmp_path):
    git_dir = tmp_path / ".git" / "hooks"
    git_dir.mkdir(parents=True)
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

    runner.invoke(cli, ["install-hook", "--path", str(tmp_path)])
    runner.invoke(cli, ["install-hook", "--path", str(tmp_path)])

    hook = tmp_path / ".git" / "hooks" / "post-commit"
    # Should only appear once
    assert hook.read_text().count("selvedge backfill-commit") == 1


def test_install_hook_fails_outside_git_repo(runner, tmp_path):
    result = runner.invoke(cli, ["install-hook", "--path", str(tmp_path)])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# backfill-commit
# ---------------------------------------------------------------------------


def test_backfill_commit_updates_events(runner):
    seed(3, entity="users.email")
    result = runner.invoke(cli, ["backfill-commit", "--hash", "abc1234def56"])
    assert result.exit_code == 0
    assert "3" in result.output

    # Verify the events were actually updated
    import json
    result2 = runner.invoke(cli, ["diff", "users.email", "--json"])
    data = json.loads(result2.output)
    assert all(e["git_commit"] == "abc1234def56" for e in data)


def test_backfill_commit_quiet_flag(runner):
    seed(1)
    result = runner.invoke(cli, ["backfill-commit", "--hash", "abc123", "--quiet"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_backfill_commit_no_events(runner):
    result = runner.invoke(cli, ["backfill-commit", "--hash", "abc123"])
    assert result.exit_code == 0
    assert "No events" in result.output


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def test_export_json_stdout(runner):
    import json
    seed(3)
    result = runner.invoke(cli, ["export"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 3


def test_export_csv_stdout(runner):
    seed(2, entity="users.email")
    result = runner.invoke(cli, ["export", "--format", "csv"])
    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert lines[0].startswith("id,")      # header row
    assert len(lines) == 3                  # header + 2 data rows


def test_export_to_file(runner, tmp_path):
    seed(2)
    out = tmp_path / "history.json"
    result = runner.invoke(cli, ["export", "--output", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    import json
    data = json.loads(out.read_text())
    assert len(data) == 2


def test_export_since_filter(runner):
    import json
    seed(1)
    result = runner.invoke(cli, ["export", "--since", "7d"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1


def test_export_empty(runner):
    import json
    result = runner.invoke(cli, ["export"])
    assert result.exit_code == 0
    assert json.loads(result.output) == []


# ---------------------------------------------------------------------------
# import (migration files)
# ---------------------------------------------------------------------------


def test_import_sql_file(runner, tmp_path):
    import json
    f = tmp_path / "migration.sql"
    f.write_text("CREATE TABLE users (id INTEGER); ALTER TABLE users ADD COLUMN email TEXT;")

    result = runner.invoke(cli, ["import", str(f)])
    assert result.exit_code == 0
    assert "2" in result.output

    # Verify events were persisted
    result2 = runner.invoke(cli, ["diff", "users", "--json"])
    data = json.loads(result2.output)
    assert len(data) == 2


def test_import_dry_run(runner, tmp_path):
    import json
    f = tmp_path / "migration.sql"
    f.write_text("CREATE TABLE users (id INTEGER);")

    result = runner.invoke(cli, ["import", str(f), "--dry-run"])
    assert result.exit_code == 0
    assert "users" in result.output

    # Dry run must NOT persist events
    result2 = runner.invoke(cli, ["diff", "users", "--json"])
    data = json.loads(result2.output)
    assert len(data) == 0


def test_import_json_flag(runner, tmp_path):
    import json
    f = tmp_path / "migration.sql"
    f.write_text("CREATE TABLE payments (id INTEGER);")

    result = runner.invoke(cli, ["import", str(f), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["entity_path"] == "payments"


def test_import_empty_file(runner, tmp_path):
    f = tmp_path / "empty.sql"
    f.write_text("")
    result = runner.invoke(cli, ["import", str(f)])
    assert result.exit_code == 0
    assert "No importable" in result.output
