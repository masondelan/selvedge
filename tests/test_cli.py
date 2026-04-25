"""Tests for the Selvedge CLI."""


import pytest
from click.testing import CliRunner

from selvedge.cli import cli
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage


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
    import sqlite3
    import uuid
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


def test_stats_per_agent_breakdown(runner):
    """v0.3.2: per-agent breakdown surfaces under-instrumented agents."""
    import json

    from selvedge.config import get_db_path
    from selvedge.storage import SelvedgeStorage
    storage = SelvedgeStorage(get_db_path())
    # claude-code logs and queries; cursor only queries
    storage.record_tool_call("log_change", entity_path="users.email", agent="claude-code")
    storage.record_tool_call("log_change", entity_path="users.name", agent="claude-code")
    storage.record_tool_call("blame", entity_path="users", agent="claude-code")
    storage.record_tool_call("history", entity_path="", agent="cursor")
    storage.record_tool_call("history", entity_path="", agent="cursor")

    result = runner.invoke(cli, ["stats", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "by_agent" in data
    # claude-code: 2/3 = 0.667
    assert data["by_agent"]["claude-code"]["total"] == 3
    assert data["by_agent"]["claude-code"]["log_change"] == 2
    assert data["by_agent"]["claude-code"]["ratio"] > 0.6
    # cursor: 0/2 = 0
    assert data["by_agent"]["cursor"]["total"] == 2
    assert data["by_agent"]["cursor"]["log_change"] == 0
    assert data["by_agent"]["cursor"]["ratio"] == 0.0


def test_stats_unknown_agent_rolls_up(runner):
    """Calls with empty agent show under '(unknown)' so totals add up."""
    import json

    from selvedge.config import get_db_path
    from selvedge.storage import SelvedgeStorage
    storage = SelvedgeStorage(get_db_path())
    storage.record_tool_call("log_change", entity_path="x")  # no agent
    storage.record_tool_call("blame", entity_path="x", agent="claude-code")

    result = runner.invoke(cli, ["stats", "--json"])
    data = json.loads(result.output)
    assert data["by_agent"]["(unknown)"]["total"] == 1
    assert data["by_agent"]["claude-code"]["total"] == 1
    summed = sum(int(v["total"]) for v in data["by_agent"].values())
    assert summed == data["total_calls"]


def test_stats_missing_reasoning_count(runner):
    """v0.3.2: missing_reasoning counts events whose reasoning fails the validator."""
    import json

    from selvedge.config import get_db_path
    from selvedge.storage import SelvedgeStorage
    storage = SelvedgeStorage(get_db_path())
    # Three events, two of which are flagged
    storage.log_event(ChangeEvent(
        entity_path="users.email", change_type="add",
        reasoning="Need to add email column for password reset flow",
    ))
    storage.log_event(ChangeEvent(
        entity_path="users.phone", change_type="add",
        reasoning="done",  # generic placeholder → flagged
    ))
    storage.log_event(ChangeEvent(
        entity_path="users.name", change_type="add",
        reasoning="",  # empty → flagged
    ))

    result = runner.invoke(cli, ["stats", "--json"])
    data = json.loads(result.output)
    assert data["missing_reasoning"] == 2


# ---------------------------------------------------------------------------
# status — hook failure surfacing (v0.3.2)
# ---------------------------------------------------------------------------


def test_status_surfaces_hook_failure(runner, tmp_path, monkeypatch):
    """A line in .selvedge/hook.log shows up under status."""
    # Override the DB to a path inside a real .selvedge/ dir we control
    sd = tmp_path / ".selvedge"
    sd.mkdir()
    monkeypatch.setenv("SELVEDGE_DB", str(sd / "selvedge.db"))
    (sd / "hook.log").write_text(
        "2026-04-25T05:30:00Z\tselvedge command not on PATH\n"
    )

    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "post-commit hook last failed" in result.output
    # Rich may insert line breaks inside the message — collapse whitespace
    # before checking content.
    flat = " ".join(result.output.split())
    assert "not on PATH" in flat


def test_status_no_hook_failure_when_log_clean(runner):
    """No hook.log → status doesn't mention failures."""
    result = runner.invoke(cli, ["status"])
    assert "post-commit hook last failed" not in result.output


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
    # CREATE TABLE → 2 events (table + id column); ALTER ADD email → 1
    assert "3" in result.output

    # Verify events were persisted
    result2 = runner.invoke(cli, ["diff", "users", "--json"])
    data = json.loads(result2.output)
    assert len(data) == 3


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


# ---------------------------------------------------------------------------
# changeset CLI command
# ---------------------------------------------------------------------------


def seed_changeset(changeset_id: str, n: int = 2) -> None:
    """Seed events with a shared changeset_id."""
    from selvedge.config import get_db_path
    storage = SelvedgeStorage(get_db_path())
    for i in range(n):
        storage.log_event(ChangeEvent(
            entity_path=f"payments.col{i}",
            change_type="add",
            reasoning=f"Adding payments column {i} for Stripe integration",
            changeset_id=changeset_id,
        ))


def test_changeset_list_shows_changesets(runner):
    seed_changeset("add-stripe", n=3)
    seed_changeset("add-auth", n=1)
    result = runner.invoke(cli, ["changeset", "--list"])
    assert result.exit_code == 0
    assert "add-stripe" in result.output
    assert "add-auth" in result.output


def test_changeset_list_json(runner):
    import json
    seed_changeset("my-cs", n=2)
    result = runner.invoke(cli, ["changeset", "--list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["changeset_id"] == "my-cs"
    assert data[0]["event_count"] == 2


def test_changeset_show_by_id(runner):
    seed_changeset("show-cs", n=2)
    result = runner.invoke(cli, ["changeset", "show-cs"])
    assert result.exit_code == 0
    assert "show-cs" in result.output
    assert "payments.col0" in result.output


def test_changeset_show_json(runner):
    import json
    seed_changeset("json-cs", n=2)
    result = runner.invoke(cli, ["changeset", "json-cs", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 2
    assert all(r["changeset_id"] == "json-cs" for r in data)


def test_changeset_show_unknown(runner):
    result = runner.invoke(cli, ["changeset", "nonexistent-cs"])
    assert result.exit_code == 0
    assert "No events" in result.output


def test_changeset_list_empty(runner):
    result = runner.invoke(cli, ["changeset", "--list"])
    assert result.exit_code == 0
    assert "No changesets" in result.output


# ---------------------------------------------------------------------------
# history --changeset filter
# ---------------------------------------------------------------------------


def test_history_changeset_filter(runner):
    seed_changeset("cs-a", n=2)
    seed_changeset("cs-b", n=3)
    result = runner.invoke(cli, ["history", "--changeset", "cs-a"])
    assert result.exit_code == 0
    # All shown events should be from cs-a — check entity paths
    assert "payments.col0" in result.output


def test_history_changeset_filter_json(runner):
    import json
    seed_changeset("cs-x", n=2)
    seed(1, entity="unrelated.col")
    result = runner.invoke(cli, ["history", "--changeset", "cs-x", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 2
    assert all(r["changeset_id"] == "cs-x" for r in data)


# ---------------------------------------------------------------------------
# history --summarize
# ---------------------------------------------------------------------------


def test_history_summarize_groups_by_session(runner):
    from selvedge.config import get_db_path
    storage = SelvedgeStorage(get_db_path())
    for i in range(3):
        storage.log_event(ChangeEvent(
            entity_path=f"users.col{i}", change_type="add",
            reasoning=f"Adding column {i} for the user profile feature",
            session_id="sess-abc",
        ))
    result = runner.invoke(cli, ["history", "--summarize"])
    assert result.exit_code == 0
    assert "Changelog" in result.output
    assert "users.col" in result.output


def test_history_summarize_groups_by_changeset(runner):
    seed_changeset("cs-summarize", n=3)
    result = runner.invoke(cli, ["history", "--summarize"])
    assert result.exit_code == 0
    assert "changeset" in result.output
    assert "cs-summarize" in result.output


def test_history_summarize_empty(runner):
    result = runner.invoke(cli, ["history", "--summarize"])
    assert result.exit_code == 0
    assert "No events" in result.output


# ---------------------------------------------------------------------------
# log --changeset option
# ---------------------------------------------------------------------------


def test_log_with_changeset(runner):
    result = runner.invoke(cli, [
        "log", "payments.amount", "add",
        "--reasoning", "Adding amount field for Stripe billing feature",
        "--changeset", "add-stripe",
    ])
    assert result.exit_code == 0
    assert "add-stripe" in result.output


def test_log_changeset_stored(runner):
    import json
    runner.invoke(cli, [
        "log", "payments.amount", "add",
        "--reasoning", "Adding amount field for Stripe billing feature",
        "--changeset", "my-feature",
    ])
    result = runner.invoke(cli, ["diff", "payments.amount", "--json"])
    data = json.loads(result.output)
    assert data[0]["changeset_id"] == "my-feature"
