"""
Tests for the decision-states + supersede flow (v0.3.9.1).

Covers, in layer order:

  - schema migration v4 (three nullable columns, metadata-only ALTER,
    bootstrap on fresh DBs, coalesce-to-"" on pre-v4 rows),
  - the ``log_supersede`` writer (auto-link, explicit link, error cases),
  - the derived decision status (``get_decision_status`` + the
    tried → reverted → re-opened trail),
  - ``prior_attempts`` outcome upgrades (``reopened`` + the supersede
    annotation fields),
  - the ``stale_when`` keyword-overlap rule in ``get_stale_decisions``,
  - the MCP ``log_change`` extension (supersedes / constraint / stale_when),
  - the ``selvedge supersede`` CLI command.

The feature was publicly promised in the dev.to launch-post thread; records
stay append-only throughout — a re-open is a new fact, never an edit.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from selvedge.migrations import get_applied_versions
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage


@pytest.fixture
def storage(tmp_path: Path) -> SelvedgeStorage:
    return SelvedgeStorage(tmp_path / "supersede.db")


def _ev(storage, path, change_type, ts, reasoning="", **kwargs):
    return storage.log_event(
        ChangeEvent(
            entity_path=path,
            change_type=change_type,
            timestamp=ts,
            reasoning=reasoning,
            **kwargs,
        )
    )


def _seed_tried_and_reverted(storage, path="payments.card_token"):
    """The canonical arc: tried, then reverted with a constraint-shaped reason."""
    tried = _ev(
        storage, path, "add", "2026-01-01T00:00:00Z",
        "Store card tokens locally for faster checkout.",
    )
    reverted = _ev(
        storage, path, "remove", "2026-01-02T00:00:00Z",
        "Reverted: card data in our own DB puts us in PCI scope.",
    )
    return tried, reverted


# ---------------------------------------------------------------------------
# Migration v4 — schema
# ---------------------------------------------------------------------------


def test_fresh_db_has_v4_applied_and_columns(tmp_path):
    db_path = tmp_path / "fresh.db"
    SelvedgeStorage(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        assert 4 in get_applied_versions(conn)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()]
        for col in ("supersedes", "constraint", "stale_when"):
            assert col in cols, f"{col} missing from events: {cols}"
    finally:
        conn.close()


def _make_pre_v4_db(db_path: Path) -> None:
    """Build a v0.3.8/0.3.9 DB: v1–v3 applied, no supersede columns."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE events (
                id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
                entity_type TEXT NOT NULL DEFAULT 'other',
                entity_path TEXT NOT NULL, change_type TEXT NOT NULL,
                diff TEXT NOT NULL DEFAULT '', reasoning TEXT NOT NULL DEFAULT '',
                agent TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '',
                git_commit TEXT NOT NULL DEFAULT '', project TEXT NOT NULL DEFAULT '',
                changeset_id TEXT NOT NULL DEFAULT '',
                metadata TEXT NOT NULL DEFAULT '{}',
                revisit_after TEXT, expires_when TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE tool_calls (
                id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
                tool_name TEXT NOT NULL, entity_path TEXT NOT NULL DEFAULT '',
                success INTEGER NOT NULL DEFAULT 1, error_msg TEXT NOT NULL DEFAULT '',
                agent TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
            "name TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        for version, name in (
            (1, "add_changeset_id_to_events"),
            (2, "add_agent_to_tool_calls"),
            (3, "add_revisit_columns_to_events"),
        ):
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) "
                "VALUES (?, ?, '2026-06-01T00:00:00Z')",
                (version, name),
            )
        # A pre-v4 row that must keep reading back cleanly after the upgrade.
        conn.execute(
            "INSERT INTO events (id, timestamp, entity_path, change_type, reasoning) "
            "VALUES ('pre-v4-row', '2026-06-01T00:00:00Z', 'users.email', 'add', "
            "'Pre-upgrade event')"
        )
        conn.commit()
    finally:
        conn.close()


def test_pre_v4_db_upgrades_and_coalesces(tmp_path):
    db_path = tmp_path / "pre_v4.db"
    _make_pre_v4_db(db_path)

    storage = SelvedgeStorage(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        assert 4 in get_applied_versions(conn)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()]
        assert "supersedes" in cols and "constraint" in cols and "stale_when" in cols
    finally:
        conn.close()

    # The pre-v4 row reads back with "" (never null) on every read surface.
    row = storage.get_blame("users.email")
    assert row["supersedes"] == ""
    assert row["constraint"] == ""
    assert row["stale_when"] == ""
    history = storage.get_entity_history("users.email")
    assert history[0]["constraint"] == ""
    assert history[0]["superseded_by"] == ""


def test_old_style_log_change_keeps_working(storage):
    """Backward compat: an event with only free-text reasoning round-trips."""
    stored = _ev(storage, "users.email", "add", "2026-01-01T00:00:00Z", "Plain event.")
    row = storage.get_blame("users.email")
    assert row["id"] == stored.id
    assert row["supersedes"] == ""
    assert row["constraint"] == ""
    assert row["stale_when"] == ""


def test_constraint_and_stale_when_round_trip(storage):
    _ev(
        storage, "payments", "add", "2026-01-01T00:00:00Z", "Own billing tables.",
        constraint="card data in our own DB puts us in PCI scope",
        stale_when="payment provider changed",
    )
    row = storage.get_blame("payments")
    assert row["constraint"] == "card data in our own DB puts us in PCI scope"
    assert row["stale_when"] == "payment provider changed"


# ---------------------------------------------------------------------------
# log_supersede — the append-only re-open writer
# ---------------------------------------------------------------------------


def test_supersede_auto_links_latest_removal(storage):
    _, reverted = _seed_tried_and_reverted(storage)
    stored = storage.log_supersede(
        "payments.card_token",
        reasoning="Provider now vaults card data — PCI constraint gone.",
    )
    assert stored.change_type == "supersede"
    assert stored.supersedes == reverted.id


def test_supersede_explicit_id(storage):
    tried, _ = _seed_tried_and_reverted(storage)
    stored = storage.log_supersede(
        "payments.card_token",
        reasoning="Overriding the original attempt event explicitly.",
        supersedes=tried.id,
    )
    assert stored.supersedes == tried.id


def test_supersede_inherits_entity_type(storage):
    _ev(
        storage, "users.token", "add", "2026-01-01T00:00:00Z", "Tried token.",
        entity_type="column",
    )
    _ev(
        storage, "users.token", "remove", "2026-01-02T00:00:00Z", "Reverted token.",
        entity_type="column",
    )
    stored = storage.log_supersede("users.token", reasoning="World changed; re-opening.")
    assert stored.entity_type == "column"


def test_supersede_with_nothing_to_reopen_raises(storage):
    _ev(storage, "users.email", "add", "2026-01-01T00:00:00Z", "Never reverted.")
    with pytest.raises(ValueError, match="no reverted decision"):
        storage.log_supersede("users.email", reasoning="Nothing was reverted here.")


def test_supersede_with_unknown_id_raises(storage):
    _seed_tried_and_reverted(storage)
    with pytest.raises(ValueError, match="does not match any event"):
        storage.log_supersede(
            "payments.card_token",
            reasoning="Bad link target.",
            supersedes="not-a-real-id",
        )


def test_supersede_never_mutates_history(storage):
    """Append-only invariant: the reverted event is untouched after a supersede."""
    _, reverted = _seed_tried_and_reverted(storage)
    storage.log_supersede("payments.card_token", reasoning="Re-opening the decision.")
    conn = sqlite3.connect(str(storage.db_path))
    try:
        row = conn.execute(
            "SELECT change_type, reasoning FROM events WHERE id = ?", (reverted.id,)
        ).fetchone()
        assert row == ("remove", "Reverted: card data in our own DB puts us in PCI scope.")
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert count == 3  # tried + reverted + supersede; nothing deleted
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# get_decision_status — derived status + trail
# ---------------------------------------------------------------------------


def test_status_no_history(storage):
    result = storage.get_decision_status("ghost.entity")
    assert result["status"] == "no_history"
    assert result["trail"] == []
    assert result["status_line"]


def test_status_active(storage):
    _ev(storage, "users.email", "add", "2026-01-01T00:00:00Z", "Added email.")
    result = storage.get_decision_status("users.email")
    assert result["status"] == "active"


def test_status_reverted(storage):
    _seed_tried_and_reverted(storage)
    result = storage.get_decision_status("payments.card_token")
    assert result["status"] == "reverted"
    assert "PCI" in result["status_line"]


def test_status_reopened_with_full_trail(storage):
    _, reverted = _seed_tried_and_reverted(storage)
    storage.log_supersede(
        "payments.card_token",
        reasoning="Provider now vaults card data — PCI constraint gone.",
    )
    result = storage.get_decision_status("payments.card_token")
    assert result["status"] == "reopened"
    phases = [e["phase"] for e in result["trail"]]
    assert phases == ["tried", "reverted", "reopened"]
    # The reverted event is annotated with the supersede that overrode it.
    assert result["trail"][1]["id"] == reverted.id
    assert result["trail"][1]["superseded_by"] == result["trail"][2]["id"]


# ---------------------------------------------------------------------------
# prior_attempts — the reopened outcome
# ---------------------------------------------------------------------------


def test_prior_attempts_reports_reopened(storage):
    _seed_tried_and_reverted(storage)
    superseding = storage.log_supersede(
        "payments.card_token",
        reasoning="Provider now vaults card data — PCI constraint gone.",
    )
    results = storage.get_prior_attempts(entity_path="payments.card_token")
    attempt = next(r for r in results if r["change_type"] == "add")
    assert attempt["outcome"] == "reopened"
    assert attempt["outcome_reasoning"].startswith("Reverted:")
    assert attempt["superseded_by"] == superseding.id
    assert "PCI constraint gone" in attempt["supersede_reasoning"]
    assert attempt["current_status"] == "reopened"


def test_prior_attempts_reverted_keeps_reverted_without_supersede(storage):
    _seed_tried_and_reverted(storage)
    results = storage.get_prior_attempts(entity_path="payments.card_token")
    assert len(results) == 1
    r = results[0]
    assert r["outcome"] == "reverted"
    assert r["superseded_by"] == ""
    assert r["supersede_reasoning"] == ""
    assert r["current_status"] == "reverted"


def test_prior_attempts_active_rows_carry_new_keys(storage):
    _ev(storage, "users.email", "add", "2026-01-01T00:00:00Z", "Still active.")
    results = storage.get_prior_attempts(
        entity_path="users.email", min_confidence="proximity_low"
    )
    r = results[0]
    assert r["outcome"] == "active"
    assert r["superseded_by"] == ""
    assert r["supersede_reasoning"] == ""
    assert r["current_status"] == "active"


def test_entity_history_annotates_superseded_by(storage):
    _, reverted = _seed_tried_and_reverted(storage)
    superseding = storage.log_supersede(
        "payments.card_token", reasoning="Re-opening after provider change."
    )
    rows = storage.get_entity_history("payments.card_token")
    by_id = {r["id"]: r for r in rows}
    assert by_id[reverted.id]["superseded_by"] == superseding.id
    assert by_id[superseding.id]["superseded_by"] == ""


# ---------------------------------------------------------------------------
# stale_decisions — the stale_when keyword-overlap rule
# ---------------------------------------------------------------------------


def test_stale_when_match_flags_review_suggested(storage):
    decision = _ev(
        storage, "payments", "add", "2026-01-01T00:00:00Z",
        "Keep card tokens out of our DB.",
        stale_when="payment provider changed",
    )
    trigger = _ev(
        storage, "deps/adyen", "add", "2026-02-01T00:00:00Z",
        "Switched payment provider to Adyen for EU coverage.",
    )
    results = storage.get_stale_decisions()
    assert len(results) == 1
    r = results[0]
    assert r["id"] == decision.id
    assert r["flag"] == "review_suggested"
    assert r["matched_event_id"] == trigger.id
    assert set(r["matched_terms"]) == {"payment", "provider"}
    assert "stale_when_match" in r["active_use_signals"]
    assert "review suggested" in r["stale_reason"]
    assert "not un-retired automatically" in r["stale_reason"]
    # Condition-only rows carry the date fields as explicit "absent" values.
    assert r["revisit_due"] == ""
    assert r["days_overdue"] == 0


def test_stale_when_requires_two_overlapping_tokens(storage):
    _ev(
        storage, "payments", "add", "2026-01-01T00:00:00Z",
        "Keep card tokens out of our DB.",
        stale_when="payment provider changed",
    )
    # Only one meaningful token ("payment") overlaps — precision floor drops it.
    _ev(
        storage, "billing", "modify", "2026-02-01T00:00:00Z",
        "Tweaked the payment retry backoff.",
    )
    assert storage.get_stale_decisions() == []


def test_stale_when_ignores_earlier_events(storage):
    # The only keyword-overlapping event predates the decision — no flag.
    _ev(
        storage, "deps/stripe", "add", "2026-01-01T00:00:00Z",
        "Adopted Stripe as the payment provider.",
    )
    _ev(
        storage, "payments", "add", "2026-02-01T00:00:00Z",
        "Keep card tokens out of our DB.",
        stale_when="payment provider changed",
    )
    assert storage.get_stale_decisions() == []


def test_revisit_due_rows_keep_their_flag_and_order(storage):
    # A due, actively-used decision (date rule)...
    due = _ev(
        storage, "users", "add", "2026-01-01T00:00:00Z", "Users table decision.",
        revisit_after="2026-01-10",
    )
    storage.record_tool_call("blame", entity_path="users")
    # ...plus a condition-matched decision (stale_when rule).
    _ev(
        storage, "payments", "add", "2026-01-01T00:00:00Z", "Payments decision.",
        stale_when="payment provider changed",
    )
    _ev(
        storage, "deps/adyen", "add", "2026-02-01T00:00:00Z",
        "Switched payment provider to Adyen.",
    )
    results = storage.get_stale_decisions(now="2026-03-01T00:00:00Z")
    assert [r["flag"] for r in results] == ["revisit_due", "review_suggested"]
    assert results[0]["id"] == due.id
    assert results[0]["matched_terms"] == []
    assert results[0]["matched_event_id"] == ""


def test_both_rules_on_one_decision_flags_revisit_due(storage):
    _ev(
        storage, "payments", "add", "2026-01-01T00:00:00Z", "Payments decision.",
        revisit_after="2026-01-10",
        stale_when="payment provider changed",
    )
    storage.record_tool_call("blame", entity_path="payments")
    _ev(
        storage, "deps/adyen", "add", "2026-02-01T00:00:00Z",
        "Switched payment provider to Adyen.",
    )
    results = storage.get_stale_decisions(now="2026-03-01T00:00:00Z")
    assert len(results) == 1
    r = results[0]
    assert r["flag"] == "revisit_due"
    assert "queried" in r["active_use_signals"]
    assert "stale_when_match" in r["active_use_signals"]
    assert r["matched_terms"] == ["payment", "provider"]


# ---------------------------------------------------------------------------
# MCP server — log_change extension + blame decision state
# ---------------------------------------------------------------------------


@pytest.fixture
def server_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SELVEDGE_DB", str(tmp_path / "server.db"))
    import selvedge.server as srv
    srv._storage = None
    yield
    srv._storage = None


def test_log_change_supersedes_requires_supersede_type(server_env):
    from selvedge.server import log_change

    result = log_change(
        entity_path="users.email",
        change_type="add",
        supersedes="some-id",
        reasoning="Mismatched change type for the supersedes param.",
    )
    assert result["status"] == "error"
    assert "supersede" in result["error"]


def test_log_change_supersede_round_trip(server_env):
    from selvedge.server import blame, log_change, prior_attempts

    log_change(
        entity_path="users.sso_token",
        change_type="add",
        reasoning="Tried a per-user SSO token column.",
    )
    log_change(
        entity_path="users.sso_token",
        change_type="remove",
        reasoning="Reverted: SSO moved to short-lived JWTs.",
    )
    result = log_change(
        entity_path="users.sso_token",
        change_type="supersede",
        reasoning="IdP now requires long-lived tokens — JWT constraint gone.",
        constraint="tokens must be revocable",
    )
    assert result["status"] == "logged"
    assert result["supersedes"] != ""

    attempts = prior_attempts(entity_path="users.sso_token")
    assert attempts[0]["current_status"] == "reopened"

    b = blame("users.sso_token")
    assert b["status"] == "reopened"
    assert b["change_type"] == "supersede"
    assert b["constraint"] == "tokens must be revocable"


def test_log_change_supersede_with_nothing_to_reopen_errors(server_env):
    from selvedge.server import log_change

    result = log_change(
        entity_path="users.fresh",
        change_type="supersede",
        reasoning="There is nothing reverted on this path.",
    )
    assert result["status"] == "error"
    assert "no reverted decision" in result["error"]


def test_blame_reports_superseded_by_on_reverted_event(server_env):
    from selvedge.server import blame, log_change

    log_change(
        entity_path="users.sso_token",
        change_type="add",
        reasoning="Tried a per-user SSO token column.",
    )
    removed = log_change(
        entity_path="users.sso_token",
        change_type="remove",
        reasoning="Reverted: SSO moved to short-lived JWTs.",
    )
    # Before the supersede, blame is the removal with reverted status.
    before = blame("users.sso_token")
    assert before["id"] == removed["id"]
    assert before["status"] == "reverted"
    assert before["superseded_by"] == ""

    reopened = log_change(
        entity_path="users.sso_token",
        change_type="supersede",
        reasoning="IdP requirements changed — re-opening the token column.",
    )
    after = blame("users.sso_token")
    assert after["id"] == reopened["id"]
    assert after["status"] == "reopened"


# ---------------------------------------------------------------------------
# CLI — `selvedge supersede` + trail rendering
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SELVEDGE_DB", str(tmp_path / "cli.db"))
    import selvedge.server as srv
    srv._storage = None
    yield
    srv._storage = None


def _cli_storage():
    from selvedge.config import get_db_path

    return SelvedgeStorage(get_db_path())


def test_cli_supersede_reopens(runner):
    storage = _cli_storage()
    _seed_tried_and_reverted(storage)
    from selvedge.cli import cli

    result = runner.invoke(
        cli,
        [
            "supersede", "payments.card_token",
            "--reasoning", "Provider now vaults card data — PCI constraint gone.",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "re-opened" in result.output

    status = storage.get_decision_status("payments.card_token")
    assert status["status"] == "reopened"


def test_cli_supersede_json_output(runner):
    storage = _cli_storage()
    _seed_tried_and_reverted(storage)
    from selvedge.cli import cli

    result = runner.invoke(
        cli,
        [
            "supersede", "payments.card_token",
            "--reasoning", "Provider now vaults card data — PCI constraint gone.",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["change_type"] == "supersede"
    assert payload["supersedes"] != ""
    assert payload["status"] == "reopened"
    assert payload["status_line"]


def test_cli_supersede_errors_without_removal(runner):
    from selvedge.cli import cli

    result = runner.invoke(
        cli,
        ["supersede", "ghost.entity", "--reasoning", "There is nothing here at all."],
    )
    assert result.exit_code == 2
    assert "no reverted decision" in result.output


def test_cli_log_supersede_type_routes_to_writer(runner):
    storage = _cli_storage()
    _, reverted = _seed_tried_and_reverted(storage)
    from selvedge.cli import cli

    result = runner.invoke(
        cli,
        [
            "log", "payments.card_token", "supersede",
            "--reasoning", "Provider vaults card data now — re-opening.",
        ],
    )
    assert result.exit_code == 0, result.output
    row = storage.get_blame("payments.card_token")
    assert row["change_type"] == "supersede"
    assert row["supersedes"] == reverted.id


def test_cli_log_supersedes_flag_requires_supersede_type(runner):
    from selvedge.cli import cli

    result = runner.invoke(
        cli,
        ["log", "users.email", "add", "--supersedes", "some-id"],
    )
    assert result.exit_code == 2


def test_cli_log_constraint_and_stale_when_persist(runner):
    from selvedge.cli import cli

    result = runner.invoke(
        cli,
        [
            "log", "payments", "add",
            "--reasoning", "Own billing tables, tokens stay at the provider.",
            "--constraint", "card data in our own DB = PCI scope",
            "--stale-when", "payment provider changed",
        ],
    )
    assert result.exit_code == 0, result.output
    row = _cli_storage().get_blame("payments")
    assert row["constraint"] == "card data in our own DB = PCI scope"
    assert row["stale_when"] == "payment provider changed"


def test_cli_prior_attempts_renders_trail_and_status(runner):
    storage = _cli_storage()
    _seed_tried_and_reverted(storage)
    storage.log_supersede(
        "payments.card_token",
        reasoning="Provider now vaults card data.",
    )
    from selvedge.cli import cli

    result = runner.invoke(cli, ["prior-attempts", "payments.card_token"])
    assert result.exit_code == 0, result.output
    assert "reopened" in result.output
    assert "re-opened:" in result.output
    assert "current status" in result.output


def test_cli_blame_shows_status_line(runner):
    storage = _cli_storage()
    _seed_tried_and_reverted(storage)
    from selvedge.cli import cli

    result = runner.invoke(cli, ["blame", "payments.card_token"])
    assert result.exit_code == 0, result.output
    assert "reverted" in result.output


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
