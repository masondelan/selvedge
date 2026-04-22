"""Tests for the MCP server tools (exercised directly, not via MCP protocol)."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    """Point every test to a fresh temporary database."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("SELVEDGE_DB", str(db_path))

    # Reset the module-level _storage singleton between tests
    import selvedge.server as srv
    srv._storage = None
    yield
    srv._storage = None


# Import server tools after the fixture patches the env
from selvedge.server import log_change, diff, blame, history, search


# ---------------------------------------------------------------------------
# log_change
# ---------------------------------------------------------------------------


def test_log_change_returns_logged_status():
    result = log_change(
        entity_path="users.email",
        change_type="add",
        reasoning="Added email for auth",
    )
    assert result["status"] == "logged"
    assert "id" in result
    assert "timestamp" in result


def test_log_change_minimal_args():
    result = log_change(entity_path="users", change_type="create")
    assert result["status"] == "logged"


def test_log_change_all_fields():
    result = log_change(
        entity_path="payments.amount",
        change_type="add",
        diff="+ amount DECIMAL(10,2) NOT NULL",
        entity_type="column",
        reasoning="Needed for stripe billing",
        agent="claude-code",
        session_id="sess_123",
        git_commit="abc1234",
        project="my-api",
    )
    assert result["status"] == "logged"


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def test_diff_returns_history():
    log_change(entity_path="users.email", change_type="add")
    log_change(entity_path="users.email", change_type="modify")
    rows = diff("users.email")
    assert len(rows) == 2


def test_diff_prefix_match():
    log_change(entity_path="users.email", change_type="add")
    log_change(entity_path="users.name", change_type="add")
    log_change(entity_path="payments.amount", change_type="add")

    rows = diff("users")
    assert len(rows) == 2


def test_diff_empty_for_unknown():
    assert diff("nonexistent.column") == []


def test_diff_respects_limit():
    for _ in range(10):
        log_change(entity_path="users.email", change_type="modify")
    assert len(diff("users.email", limit=3)) == 3


# ---------------------------------------------------------------------------
# blame
# ---------------------------------------------------------------------------


def test_blame_returns_most_recent():
    log_change(entity_path="users.email", change_type="add", reasoning="first")
    log_change(entity_path="users.email", change_type="modify", reasoning="second")
    result = blame("users.email")
    assert result["reasoning"] == "second"


def test_blame_returns_error_for_unknown():
    result = blame("nonexistent.entity")
    assert "error" in result


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


def test_history_returns_all():
    log_change(entity_path="a.x", change_type="add")
    log_change(entity_path="b.y", change_type="add")
    rows = history()
    assert len(rows) == 2


def test_history_relative_time_7d():
    log_change(entity_path="users.email", change_type="add")
    rows = history(since="7d")
    assert len(rows) == 1


def test_history_entity_filter():
    log_change(entity_path="users.email", change_type="add")
    log_change(entity_path="payments.amount", change_type="add")
    rows = history(entity_path="users")
    assert len(rows) == 1
    assert rows[0]["entity_path"] == "users.email"


def test_history_project_filter():
    log_change(entity_path="users.email", change_type="add", project="api")
    log_change(entity_path="orders.total", change_type="add", project="shop")
    rows = history(project="shop")
    assert len(rows) == 1
    assert rows[0]["project"] == "shop"


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_search_finds_by_reasoning():
    log_change(entity_path="payments.amount", change_type="add", reasoning="stripe billing integration")
    log_change(entity_path="users.email", change_type="add", reasoning="auth setup")
    rows = search("stripe")
    assert len(rows) == 1
    assert rows[0]["entity_path"] == "payments.amount"


def test_search_no_results():
    log_change(entity_path="users.email", change_type="add")
    assert search("xyzzy_impossible") == []
