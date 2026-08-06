"""Tests for ``selvedge prune`` — the v0.3.6 retention command."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from selvedge import prune as prune_mod
from selvedge.cli import cli
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    p = tmp_path / ".selvedge" / "selvedge.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SELVEDGE_DB", str(p))
    monkeypatch.chdir(tmp_path)
    return p


def _insert_tool_call(db: Path, timestamp: str, tool_name: str = "log_change") -> None:
    """Insert a tool_calls row at an arbitrary timestamp, bypassing utc_now_iso."""
    SelvedgeStorage(db)  # ensure schema exists
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO tool_calls (id, timestamp, tool_name, entity_path, "
            "success, error_msg, agent) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), timestamp, tool_name, "x", 1, "", ""),
        )
        conn.commit()
    finally:
        conn.close()


def test_prune_removes_old_tool_calls(db_path):
    """Rows older than the cutoff are deleted."""
    _insert_tool_call(db_path, "2020-01-01T00:00:00Z")
    _insert_tool_call(db_path, "2021-06-15T12:30:00Z")

    result = prune_mod.run_prune(db_path, days=90)

    assert result.pruned == 2
    assert SelvedgeStorage(db_path).count_tool_calls() == 0


def test_prune_preserves_recent_tool_calls(db_path):
    """Rows newer than the cutoff are left alone."""
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    stale = "2020-01-01T00:00:00Z"
    _insert_tool_call(db_path, fresh)
    _insert_tool_call(db_path, stale)

    result = prune_mod.run_prune(db_path, days=90)

    assert result.pruned == 1
    assert SelvedgeStorage(db_path).count_tool_calls() == 1


def test_prune_days_override(db_path):
    """A tight ``--days`` window prunes rows the default would keep."""
    now = datetime.now(timezone.utc)
    ten_days_ago = (now - timedelta(days=10)).isoformat().replace("+00:00", "Z")
    _insert_tool_call(db_path, ten_days_ago)

    # 90-day default keeps the row.
    default_keep = prune_mod.run_prune(db_path, days=90)
    assert default_keep.pruned == 0
    assert SelvedgeStorage(db_path).count_tool_calls() == 1

    # 7-day window deletes it.
    aggressive = prune_mod.run_prune(db_path, days=7)
    assert aggressive.pruned == 1
    assert SelvedgeStorage(db_path).count_tool_calls() == 0


def test_prune_writes_log_line(db_path):
    """Every run appends one tab-separated line to ``.selvedge/prune.log``."""
    _insert_tool_call(db_path, "2020-01-01T00:00:00Z")

    result = prune_mod.run_prune(db_path, days=90)
    assert result.log_path.is_file()
    line = result.log_path.read_text(encoding="utf-8").strip()
    parts = line.split("\t")
    assert len(parts) == 3
    # column 2: rows pruned; column 3: day threshold
    assert int(parts[1]) == 1
    assert int(parts[2]) == 90


def test_prune_empty_table_still_logs(db_path):
    """An empty tool_calls table prunes 0 but still writes a log line."""
    SelvedgeStorage(db_path)  # initialize the schema, no rows

    result = prune_mod.run_prune(db_path, days=90)

    assert result.pruned == 0
    assert result.log_path.is_file()
    line = result.log_path.read_text(encoding="utf-8").strip()
    assert line.endswith("\t0\t90")


def test_prune_appends_subsequent_runs(db_path):
    """Repeated runs append to the log rather than overwriting it."""
    SelvedgeStorage(db_path)
    prune_mod.run_prune(db_path, days=90)
    prune_mod.run_prune(db_path, days=30)

    log_path = prune_mod.prune_log_path(db_path)
    lines = [
        ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    assert len(lines) == 2
    assert lines[-1].endswith("\t0\t30")


def test_last_prune_line_parses_tail(db_path):
    """``last_prune_line`` returns the latest tuple, ignoring earlier rows."""
    SelvedgeStorage(db_path)
    prune_mod.run_prune(db_path, days=90)
    prune_mod.run_prune(db_path, days=14)

    parsed = prune_mod.last_prune_line(prune_mod.prune_log_path(db_path))
    assert parsed is not None
    _ts, count, threshold = parsed
    assert count == 0
    assert threshold == 14


def test_last_prune_line_handles_missing_log(tmp_path):
    """No log file → ``None``, not an exception."""
    assert prune_mod.last_prune_line(tmp_path / "absent.log") is None


def test_prune_cli_json_shape(runner, db_path):
    """``--json`` is :meth:`PruneResult.to_dict` plus an ``events`` block.

    ``events`` is always present and empty unless ``--include-events`` ran —
    the house "every field always populated, never null" convention, so a
    caller never has to branch on key presence.
    """
    SelvedgeStorage(db_path)
    result = runner.invoke(cli, ["prune", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert set(payload.keys()) == {
        "pruned", "days_threshold", "cutoff", "log_path", "events",
    }
    assert payload["events"] == {}
    assert payload["days_threshold"] == prune_mod.DEFAULT_DAYS
    assert Path(payload["log_path"]).is_file()


def test_prune_cli_human_output(runner, db_path):
    """The human path summarizes the result on stdout."""
    _insert_tool_call(db_path, "2020-01-01T00:00:00Z")
    result = runner.invoke(cli, ["prune", "--days", "30"])
    assert result.exit_code == 0, result.output
    assert "Pruned" in result.output
    assert "1" in result.output


# ---------------------------------------------------------------------------
# --include-events — the destructive path (v0.3.10)
#
# Two independent gates, because each has a known bypass on its own: a prompt
# is defeated by `--yes` in a cron entry, an env var by a shell profile that
# exports it and forgets.
# ---------------------------------------------------------------------------


def test_cron_footgun_yes_without_destructive_env_errors(runner, db_path, monkeypatch):
    """--yes must NOT be sufficient. Named in the phase doc on purpose.

    This is the exact shape of the accident the two-gate rule exists to
    prevent: a scheduled job that passes --yes to avoid hanging on a prompt,
    and thereby deletes captured reasoning unattended. Do not retire this test
    in a suite cleanup — the name is referenced from
    `docs/architecture.md` § Phase 2.16.
    """
    monkeypatch.delenv("SELVEDGE_DESTRUCTIVE", raising=False)
    storage = SelvedgeStorage(db_path)
    storage.log_event(ChangeEvent(
        entity_path="users.email", change_type="add",
        timestamp="2020-01-01T00:00:00Z", reasoning="ancient but precious"))

    result = runner.invoke(
        cli, ["prune", "--include-events", "--event-days", "1", "--yes"]
    )

    assert result.exit_code == 2, result.output
    assert "SELVEDGE_DESTRUCTIVE" in result.stderr
    assert storage.count() == 1, "events were deleted with only --yes"


def test_destructive_env_alone_still_prompts(runner, db_path, monkeypatch):
    """The other half: the env var alone must not delete without confirmation."""
    monkeypatch.setenv("SELVEDGE_DESTRUCTIVE", "1")
    storage = SelvedgeStorage(db_path)
    storage.log_event(ChangeEvent(
        entity_path="users.email", change_type="add",
        timestamp="2020-01-01T00:00:00Z", reasoning="ancient but precious"))

    # Answer "no" at the prompt.
    result = runner.invoke(
        cli, ["prune", "--include-events", "--event-days", "1"], input="n\n"
    )

    assert result.exit_code == 1
    assert storage.count() == 1, "events were deleted despite declining the prompt"


def test_both_gates_satisfied_deletes_old_events(runner, db_path, monkeypatch):
    monkeypatch.setenv("SELVEDGE_DESTRUCTIVE", "1")
    storage = SelvedgeStorage(db_path)
    storage.log_event(ChangeEvent(
        entity_path="users.old", change_type="add",
        timestamp="2020-01-01T00:00:00Z", reasoning="old enough to prune"))
    storage.log_event(ChangeEvent(
        entity_path="users.new", change_type="add",
        reasoning="logged just now, must survive"))

    result = runner.invoke(
        cli, ["prune", "--include-events", "--event-days", "1"], input="y\n"
    )

    assert result.exit_code == 0, result.output
    assert storage.count() == 1
    assert storage.get_history()[0]["entity_path"] == "users.new"


def test_events_prune_is_recorded_in_the_audit_log(runner, db_path, monkeypatch):
    monkeypatch.setenv("SELVEDGE_DESTRUCTIVE", "1")
    storage = SelvedgeStorage(db_path)
    storage.log_event(ChangeEvent(
        entity_path="users.old", change_type="add",
        timestamp="2020-01-01T00:00:00Z", reasoning="old"))

    runner.invoke(cli, ["prune", "--include-events", "--event-days", "1", "--yes"],
                  input="y\n")

    log = prune_mod.prune_log_path(db_path)
    lines = log.read_text().splitlines()
    events_lines = [ln for ln in lines if "\tevents\t" in ln]
    assert events_lines, f"no events line in prune.log: {lines}"
    stamp, marker, pruned, days = events_lines[-1].split("\t")
    assert marker == "events"
    assert pruned == "1"
    assert days == "1"


def test_default_events_retention_never_deletes(runner, db_path, monkeypatch):
    """`retention_days_events` defaults to infinity, and that must hold."""
    monkeypatch.setenv("SELVEDGE_DESTRUCTIVE", "1")
    storage = SelvedgeStorage(db_path)
    storage.log_event(ChangeEvent(
        entity_path="users.ancient", change_type="add",
        timestamp="2010-01-01T00:00:00Z", reasoning="a decade old"))

    result = runner.invoke(cli, ["prune", "--include-events", "--yes"])

    assert result.exit_code == 0, result.output
    assert storage.count() == 1, "the default retention deleted an event"
    assert "never delete" in result.stderr


def test_run_events_prune_rechecks_the_env_itself(db_path, monkeypatch):
    """The storage-adjacent entry point does not take the caller's word."""
    monkeypatch.delenv("SELVEDGE_DESTRUCTIVE", raising=False)
    SelvedgeStorage(db_path)
    with pytest.raises(prune_mod.DestructiveNotPermitted, match="SELVEDGE_DESTRUCTIVE"):
        prune_mod.run_events_prune(db_path, days=1)


def test_prune_events_storage_method_is_unguarded_by_design(db_path):
    """Policy lives in `prune`, not in storage — assert they stay separate.

    A storage method that also enforced policy would be two things that can
    disagree; this pins the split so a later "safety" patch in the wrong
    layer is a visible decision.
    """
    storage = SelvedgeStorage(db_path)
    storage.log_event(ChangeEvent(
        entity_path="users.old", change_type="add",
        timestamp="2020-01-01T00:00:00Z", reasoning="old"))
    assert storage.prune_events("2021-01-01T00:00:00Z") == 1
    assert storage.count() == 0
