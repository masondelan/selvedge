"""Versioned recall reports preserve the legacy rows contract and source metadata."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from selvedge import chain, recall_integrity, semantic
from selvedge.cli import cli
from selvedge.models import ChangeEvent
from selvedge.presenters import prior_attempts_report
from selvedge.storage import SelvedgeStorage

ENTITY = "report.auth_token"
REASON = "Rolled back local tokens because expiry belongs to the issuer."
SOURCE_METADATA = {
    "context": {"label": "original source"},
    "integrity": {"status": "source-owned metadata"},
}
# Freeze the v0.3.19 row shape rather than deriving it from the new presenter.
LEGACY_ROW_KEYS = frozenset({
    "id", "timestamp", "entity_type", "entity_path", "change_type", "diff",
    "reasoning", "agent", "session_id", "git_commit", "project", "changeset_id",
    "metadata", "revisit_after", "expires_when", "supersedes", "constraint", "stale_when",
    "outcome", "confidence", "outcome_reasoning", "superseded_by", "supersede_reasoning",
    "current_status", "match_type", "similarity", "note",
})
REPORT_KEYS = frozenset({"format", "attempts", "integrity", "query_status", "warnings"})


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    import selvedge.server as srv

    db_path = tmp_path / "report.db"
    monkeypatch.setenv("SELVEDGE_DB", str(db_path))
    monkeypatch.setenv("SELVEDGE_NO_TELEMETRY", "1")
    monkeypatch.setenv("SELVEDGE_TELEMETRY", "0")
    monkeypatch.setenv("SELVEDGE_NO_UPDATE_CHECK", "1")
    store = SelvedgeStorage(db_path)
    event = store.log_event(ChangeEvent(
        entity_path=ENTITY, change_type="revert", reasoning=REASON,
        metadata=json.dumps(SOURCE_METADATA),
    ))
    monkeypatch.setattr(srv, "_storage", store)
    return store, event


def _assert_source_row(row, event):
    assert set(row) == LEGACY_ROW_KEYS
    assert row["id"] == event.id
    assert row["reasoning"] == row["outcome_reasoning"] == REASON
    assert row["outcome"] == "reverted"
    assert row["confidence"] == "exact"
    assert row["metadata"] == SOURCE_METADATA


def _assert_report(report, *, matched, status, reason):
    assert set(report) == REPORT_KEYS
    assert report["format"] == "selvedge.prior-attempts/1"
    assert report["query_status"] == ("matched" if matched else "no_match")
    assert isinstance(report["attempts"], list)
    assert len(report["attempts"]) == (1 if matched else 0)
    assert all(row["match_type"] != "note" for row in report["attempts"])
    assert report["integrity"] == {
        "status": status, "scope": "local_chain_snapshot", "reason": reason,
    }
    assert isinstance(report["warnings"], list)
    assert all(isinstance(warning, str) for warning in report["warnings"])


def _invoke_json(entity, flag):
    result = CliRunner().invoke(cli, ["prior-attempts", entity, flag])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout), result


def _set_uncertain_check(monkeypatch, status):
    if status == "failed":
        # Only the verifier's return is mocked; the recorded database stays valid.
        monkeypatch.setattr(chain, "verify_chain", Mock(return_value={
            "table_present": True, "records": 2, "intact": False,
        }))
    else:
        # A valid store exceeding a deliberately small check budget is unknown.
        monkeypatch.setattr(recall_integrity, "MAX_RECORDS", 0)


def test_clean_legacy_rows_keep_exact_fields_and_source_metadata(recorded):
    import selvedge.server as srv

    _, event = recorded
    rows, output = _invoke_json(ENTITY, "--json")
    assert isinstance(rows, list)
    assert len(rows) == 1
    _assert_source_row(rows[0], event)
    assert rows[0]["note"] == ""
    assert output.stderr == ""
    assert rows == srv.prior_attempts(entity_path=ENTITY)
    assert rows == srv.prior_attempts(entity_path=ENTITY, output_format="rows")


@pytest.mark.parametrize("matched", [False, True])
def test_clean_report_matches_cli_and_mcp_with_explicit_query_status(recorded, matched):
    import selvedge.server as srv

    store, event = recorded
    entity = ENTITY if matched else "report.missing"
    report = prior_attempts_report(store, entity_path=entity)
    _assert_report(report, matched=matched, status="intact", reason="checked")
    assert report["warnings"] == []
    if matched:
        _assert_source_row(report["attempts"][0], event)
    cli_report, output = _invoke_json(entity, "--json-report")
    assert output.stderr == ""
    assert cli_report == report
    assert srv.prior_attempts(entity_path=entity, output_format="report-v1") == report
    legacy, _ = _invoke_json(entity, "--json")
    assert legacy == report["attempts"]
    assert srv.prior_attempts(entity_path=entity) == legacy


@pytest.mark.parametrize("matched", [False, True])
@pytest.mark.parametrize("status,reason,warning", [
    ("failed", "chain_check_failed", "Integrity check failed"),
    ("unknown", "budget_exceeded", "Integrity unknown"),
])
def test_uncertain_report_keeps_evidence_and_query_metadata_separate(
    recorded, monkeypatch, matched, status, reason, warning,
):
    import selvedge.server as srv

    store, event = recorded
    _set_uncertain_check(monkeypatch, status)
    entity = ENTITY if matched else "report.missing"
    report = prior_attempts_report(store, entity_path=entity)
    _assert_report(report, matched=matched, status=status, reason=reason)
    assert any(warning in notice for notice in report["warnings"])
    cli_report, _ = _invoke_json(entity, "--json-report")
    assert cli_report == report
    assert srv.prior_attempts(entity_path=entity, output_format="report-v1") == report
    if matched:
        _assert_source_row(report["attempts"][0], event)
        assert warning in report["attempts"][0]["note"]
    # Query diagnostics must not overwrite or persist in the event's own metadata.
    assert store.get_blame(ENTITY)["metadata"] == SOURCE_METADATA


@pytest.mark.parametrize("status,warning", [
    ("failed", "Integrity check failed"), ("unknown", "Integrity unknown"),
])
def test_uncertain_legacy_match_keeps_fields_ids_and_cli_mcp_parity(
    recorded, monkeypatch, status, warning,
):
    import selvedge.server as srv

    _, event = recorded
    _set_uncertain_check(monkeypatch, status)
    rows, output = _invoke_json(ENTITY, "--json")
    assert len(rows) == 1
    _assert_source_row(rows[0], event)
    assert warning in rows[0]["note"]
    assert warning in output.stderr
    assert rows == srv.prior_attempts(entity_path=ENTITY)


@pytest.mark.parametrize("status,warning", [
    ("failed", "Integrity check failed"), ("unknown", "Integrity unknown"),
])
def test_uncertain_empty_legacy_cli_stays_empty_while_mcp_keeps_diagnostic(
    recorded, monkeypatch, status, warning,
):
    import selvedge.server as srv

    _set_uncertain_check(monkeypatch, status)
    rows, output = _invoke_json("report.missing", "--json")
    assert rows == []
    assert warning in output.stderr
    # MCP has no CLI stderr channel: this is the documented no-match exception.
    mcp_rows = srv.prior_attempts(entity_path="report.missing")
    assert len(mcp_rows) == 1
    note = mcp_rows[0]
    assert set(note) == LEGACY_ROW_KEYS
    assert note["match_type"] == "note"
    assert note["id"] == note["entity_path"] == note["confidence"] == ""
    assert note["metadata"] == {}
    assert warning in note["note"]


@pytest.mark.parametrize("matched", [False, True])
def test_report_excludes_fuzzy_note_rows_but_retains_warning(recorded, monkeypatch, matched):
    store, event = recorded
    monkeypatch.setattr(semantic, "fuzzy_prior_attempts", Mock(
        side_effect=semantic.SemanticUnavailable("optional semantic model unavailable"),
    ))
    report = prior_attempts_report(
        store, entity_path=ENTITY if matched else "report.missing", fuzzy="not-in-fixture",
    )
    _assert_report(report, matched=matched, status="intact", reason="checked")
    assert any("fuzzy matching unavailable" in notice for notice in report["warnings"])
    if matched:
        _assert_source_row(report["attempts"][0], event)


def test_json_modes_are_mutually_exclusive(recorded):
    result = CliRunner().invoke(cli, ["prior-attempts", ENTITY, "--json", "--json-report"])
    assert result.exit_code == 2
    assert "--json" in result.output and "--json-report" in result.output


def test_mcp_invalid_report_format_is_an_explicit_error(recorded):
    import selvedge.server as srv

    result = srv.prior_attempts(entity_path=ENTITY, output_format="unsupported")
    assert isinstance(result, list) and len(result) == 1
    assert set(result[0]) == {"error"}
    assert "output_format" in result[0]["error"]
