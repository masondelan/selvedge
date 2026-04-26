"""Tests for the MCP server tools (exercised directly, not via MCP protocol)."""


import pytest


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
from selvedge.server import blame, changeset, diff, history, log_change, search  # noqa: E402

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


# ---------------------------------------------------------------------------
# changeset_id — log_change and changeset tool
# ---------------------------------------------------------------------------


def test_log_change_with_changeset_id():
    result = log_change(
        entity_path="payments.amount",
        change_type="add",
        reasoning="Adding payments table as part of Stripe billing feature",
        changeset_id="add-stripe-billing",
    )
    assert result["status"] == "logged"

    rows = changeset("add-stripe-billing")
    assert len(rows) == 1
    assert rows[0]["changeset_id"] == "add-stripe-billing"
    assert rows[0]["entity_path"] == "payments.amount"


def test_log_change_changeset_id_defaults_empty():
    result = log_change(entity_path="users.email", change_type="add",
                        reasoning="Added email for auth")
    assert result["status"] == "logged"
    rows = history()
    assert rows[0]["changeset_id"] == ""


def test_changeset_groups_multiple_events():
    cs = "add-stripe"
    log_change(entity_path="payments", change_type="create",
               reasoning="Create payments table for Stripe integration", changeset_id=cs)
    log_change(entity_path="payments.amount", change_type="add",
               reasoning="Amount field for payment value", changeset_id=cs)
    log_change(entity_path="payments.currency", change_type="add",
               reasoning="Currency ISO code field", changeset_id=cs)
    # Unrelated event
    log_change(entity_path="users.email", change_type="add",
               reasoning="Email for auth login flow")

    rows = changeset(cs)
    assert len(rows) == 3
    assert all(r["changeset_id"] == cs for r in rows)


def test_changeset_returns_error_for_unknown():
    rows = changeset("nonexistent-changeset")
    assert len(rows) == 1
    assert "error" in rows[0]


def test_history_changeset_filter():
    log_change(entity_path="a.x", change_type="add",
               reasoning="Part of feature A", changeset_id="cs-a")
    log_change(entity_path="b.y", change_type="add",
               reasoning="Part of feature B", changeset_id="cs-b")

    rows = history(changeset_id="cs-a")
    assert len(rows) == 1
    assert rows[0]["entity_path"] == "a.x"


# ---------------------------------------------------------------------------
# reasoning quality warnings
# ---------------------------------------------------------------------------


def test_log_change_no_warning_for_good_reasoning():
    result = log_change(
        entity_path="users.phone",
        change_type="add",
        reasoning="User asked to add phone number field to support SMS 2FA verification.",
    )
    # v0.3.3+: every LogChangeResult key is always populated; empty list
    # means "reasoning passed the quality validator."
    assert result["warnings"] == []


def test_log_change_warns_empty_reasoning():
    result = log_change(entity_path="users.email", change_type="add", reasoning="")
    assert "warnings" in result
    assert any("empty" in w for w in result["warnings"])


def test_log_change_warns_short_reasoning():
    result = log_change(entity_path="users.email", change_type="add", reasoning="For auth")
    assert "warnings" in result
    assert any("short" in w for w in result["warnings"])


def test_log_change_warns_generic_reasoning():
    for generic in ["user request", "as requested", "done", "n/a", "updated"]:
        result = log_change(entity_path="x.y", change_type="modify", reasoning=generic)
        assert "warnings" in result, f"expected warning for reasoning={generic!r}"
        assert any("generic" in w for w in result["warnings"])


def test_log_change_event_still_logged_even_with_warning():
    result = log_change(entity_path="a.b", change_type="add", reasoning="")
    assert result["status"] == "logged"
    assert "id" in result
    rows = history()
    assert len(rows) == 1
