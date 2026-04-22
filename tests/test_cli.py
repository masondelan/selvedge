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
