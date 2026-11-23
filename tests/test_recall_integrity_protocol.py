"""Legacy rows and versioned recall reports over real MCP stdio."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from selvedge.cli import cli
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage
from tests.test_mcp_protocol import _payload

# Freeze the released row keys, independently of the report implementation.
LEGACY_ROW_KEYS = frozenset({
    "id", "timestamp", "entity_type", "entity_path", "change_type", "diff",
    "reasoning", "agent", "session_id", "git_commit", "project", "changeset_id",
    "metadata", "revisit_after", "expires_when", "supersedes", "constraint", "stale_when",
    "outcome", "confidence", "outcome_reasoning", "superseded_by", "supersede_reasoning",
    "current_status", "match_type", "similarity", "note",
})
REPORT_KEYS = frozenset({"format", "attempts", "integrity", "query_status", "warnings"})
SOURCE_METADATA = {
    "context": {"label": "original protocol fixture"},
    "integrity": {"status": "source-owned value"},
}


@pytest.fixture
def server_params(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StdioServerParameters:
    """Keep both entry points on a disposable store with network work disabled."""
    for key, value in {
        "SELVEDGE_DB": str(tmp_path / "protocol.db"),
        "SELVEDGE_QUIET": "1",
        "SELVEDGE_LOG_LEVEL": "ERROR",
        "SELVEDGE_NO_TELEMETRY": "1",
        "SELVEDGE_TELEMETRY": "0",
        "SELVEDGE_NO_UPDATE_CHECK": "1",
    }.items():
        monkeypatch.setenv(key, value)
    return StdioServerParameters(
        command=sys.executable, args=["-m", "selvedge.server"], env=dict(os.environ),
    )


def _cli_json(entity: str, flag: str):
    result = CliRunner().invoke(cli, ["prior-attempts", entity, flag])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout), result.stderr


def _legacy_rows(result) -> list[dict]:
    """Assert the actual wire wrapper as well as the legacy event key set."""
    assert not result.isError
    structured = result.structuredContent
    assert isinstance(structured, dict)
    assert set(structured) == {"result"}
    rows = structured["result"]
    assert isinstance(rows, list)
    assert _payload(result) == rows
    assert all(set(row) == LEGACY_ROW_KEYS for row in rows)
    return rows


def _report(result, *, matched: bool, status: str, reason: str) -> dict:
    assert not result.isError
    report = _payload(result)
    assert isinstance(report, dict)
    assert set(report) == REPORT_KEYS
    assert report["format"] == "selvedge.prior-attempts/1"
    assert report["query_status"] == ("matched" if matched else "no_match")
    assert report["integrity"] == {
        "status": status, "scope": "local_chain_snapshot", "reason": reason,
    }
    attempts = report["attempts"]
    assert isinstance(attempts, list)
    assert bool(attempts) == matched
    assert all(set(row) == LEGACY_ROW_KEYS for row in attempts)
    assert all(row["id"] and row["match_type"] != "note" for row in attempts)
    assert isinstance(report["warnings"], list)
    assert all(isinstance(warning, str) for warning in report["warnings"])
    return report


@pytest.mark.asyncio
async def test_standalone_revert_and_targeted_supersede_over_stdio(server_params):
    """Source IDs and targeted supersession survive legacy and report transport."""
    entity = "protocol.auth_token"
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            missed = await session.call_tool("prior_attempts", arguments={"entity_path": entity})
            assert _legacy_rows(missed) == []
            missed_report = _report(await session.call_tool("prior_attempts", arguments={
                "entity_path": entity, "output_format": "report-v1",
            }), matched=False, status="intact", reason="checked")
            assert missed_report["warnings"] == []
            assert _cli_json(entity, "--json-report") == (missed_report, "")
            assert _cli_json(entity, "--json") == ([], "")
            verdict_ids = []
            reasons = ["Rolled back local tokens because the issuer owns expiry.",
                       "Rolled back a separate token strategy because rotation was incomplete."]
            for reason in reasons:
                result = await session.call_tool("log_change", arguments={
                    "entity_path": entity, "change_type": "revert", "reasoning": reason,
                    "agent": "protocol-regression",
                })
                assert not result.isError
                verdict_ids.append(_payload(result)["id"])
            result = await session.call_tool("log_change", arguments={
                "entity_path": entity, "change_type": "supersede",
                "supersedes": verdict_ids[0],
                "reasoning": "The issuer now exposes the expiry needed by the first strategy.",
                "agent": "protocol-regression",
            })
            assert not result.isError
            superseding_id = _payload(result)["id"]
            rows = _legacy_rows(await session.call_tool("prior_attempts", arguments={
                "entity_path": entity,
            }))
            explicit_rows = _legacy_rows(await session.call_tool("prior_attempts", arguments={
                "entity_path": entity, "output_format": "rows",
            }))
            report = _report(await session.call_tool("prior_attempts", arguments={
                "entity_path": entity, "output_format": "report-v1",
            }), matched=True, status="intact", reason="checked")

    assert len(rows) == 2
    assert explicit_rows == report["attempts"] == rows
    assert report["warnings"] == []
    by_id = {row["id"]: row for row in rows}
    assert set(by_id) == set(verdict_ids)
    assert by_id[verdict_ids[0]]["outcome"] == "reopened"
    assert by_id[verdict_ids[0]]["superseded_by"] == superseding_id
    assert by_id[verdict_ids[1]]["outcome"] == "reverted"
    assert by_id[verdict_ids[1]]["superseded_by"] == ""
    for event_id, reason in zip(verdict_ids, reasons, strict=True):
        assert by_id[event_id]["reasoning"] == by_id[event_id]["outcome_reasoning"] == reason
        assert by_id[event_id]["confidence"] == "exact"
        assert by_id[event_id]["metadata"] == {}
    assert _cli_json(entity, "--json") == (rows, "")
    assert _cli_json(entity, "--json-report") == (report, "")


@pytest.mark.asyncio
@pytest.mark.parametrize("has_match", [False, True])
async def test_large_valid_store_reports_unknown_over_stdio(server_params, has_match):
    """Bounded-check metadata survives both matching and empty query reports."""
    storage = SelvedgeStorage(server_params.env["SELVEDGE_DB"])
    events = [ChangeEvent(
        entity_path=f"protocol.background_{index}", change_type="add",
        reasoning="Valid unrelated history for the bounded verification fixture.",
    ) for index in range(1000)]
    event = ChangeEvent(
        entity_path="protocol.retained_verdict", change_type="revert",
        reasoning="Retain this rollback.", metadata=json.dumps(SOURCE_METADATA),
    )
    storage.log_event_batch([*events, event])
    entity = event.entity_path if has_match else "protocol.no_match"
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            rows = _legacy_rows(await session.call_tool("prior_attempts", arguments={
                "entity_path": entity,
            }))
            report = _report(await session.call_tool("prior_attempts", arguments={
                "entity_path": entity, "output_format": "report-v1",
            }), matched=has_match, status="unknown", reason="budget_exceeded")
    assert len(rows) == 1
    assert "Integrity unknown" in rows[0]["note"]
    assert any("Integrity unknown" in warning for warning in report["warnings"])
    if has_match:
        assert report["attempts"] == rows
        assert rows[0]["id"] == event.id
        assert rows[0]["outcome"] == "reverted"
        assert rows[0]["reasoning"] == event.reasoning
        assert rows[0]["metadata"] == SOURCE_METADATA
    else:
        assert rows[0]["id"] == rows[0]["entity_path"] == ""
        assert rows[0]["match_type"] == "note"
        assert rows[0]["metadata"] == {}
        assert report["attempts"] == []
    cli_rows, stderr = _cli_json(entity, "--json")
    assert cli_rows == (rows if has_match else [])
    assert "Integrity unknown" in stderr
    cli_report, stderr = _cli_json(entity, "--json-report")
    assert cli_report == report
    assert "Integrity unknown" in stderr
    assert storage.get_blame(event.entity_path)["metadata"] == SOURCE_METADATA


@pytest.mark.asyncio
@pytest.mark.parametrize("has_match", [False, True])
async def test_mocked_verifier_failure_is_visible_over_stdio(server_params, monkeypatch, has_match):
    """Mocked failure retains evidence and never inserts note rows into reports."""
    from selvedge import chain

    storage = SelvedgeStorage(server_params.env["SELVEDGE_DB"])
    event = storage.log_event(ChangeEvent(
        entity_path="protocol.recorded", change_type="revert",
        reasoning="The recorded rollback remains visible alongside the warning.",
        metadata=json.dumps(SOURCE_METADATA),
    ))
    before = storage.get_history()
    # Mock only the verifier result. The fixture remains a valid chained store.
    failure = {"table_present": True, "records": 1, "intact": False}
    server_params.args = ["-c", (
        "from selvedge import chain\n"
        f"chain.verify_chain = lambda conn: {failure!r}\n"
        "from selvedge.server import main\nmain()\n"
    )]
    entity = event.entity_path if has_match else "protocol.no_match"
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            rows = _legacy_rows(await session.call_tool("prior_attempts", arguments={
                "entity_path": entity,
            }))
            report = _report(await session.call_tool("prior_attempts", arguments={
                "entity_path": entity, "output_format": "report-v1",
            }), matched=has_match, status="failed", reason="chain_check_failed")
    assert len(rows) == 1
    row = rows[0]
    assert "Integrity check failed" in row["note"]
    assert any("Integrity check failed" in warning for warning in report["warnings"])
    if has_match:
        assert report["attempts"] == rows
        assert row["id"] == event.id
        assert row["reasoning"] == event.reasoning
        assert row["outcome"] == "reverted"
        assert row["confidence"] == "exact"
        assert row["metadata"] == SOURCE_METADATA
    else:
        assert row["id"] == row["entity_path"] == row["confidence"] == ""
        assert row["match_type"] == "note"
        assert row["metadata"] == {}
        assert report["attempts"] == []
    monkeypatch.setattr(chain, "verify_chain", lambda conn: dict(failure))
    cli_rows, stderr = _cli_json(entity, "--json")
    assert cli_rows == (rows if has_match else [])
    assert "Integrity check failed" in stderr
    cli_report, stderr = _cli_json(entity, "--json-report")
    assert cli_report == report
    assert "Integrity check failed" in stderr
    assert storage.get_history() == before
