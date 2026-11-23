"""Defensive recall checks with valid stores and mocked verifier outcomes only."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from selvedge import chain, recall_integrity
from selvedge.cli import cli
from selvedge.models import ChangeEvent
from selvedge.presenters import prior_attempts_payload, prior_attempts_report
from selvedge.storage import SelvedgeStorage

ENTITY = "users.expiry"
REASON = "Rolled back token expiry because the upstream issuer owns it."


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "recall.db"
    monkeypatch.setenv("SELVEDGE_DB", str(path))
    return SelvedgeStorage(path)


def _record(store):
    return store.log_event(ChangeEvent(entity_path=ENTITY, change_type="revert", reasoning=REASON))


def _check(store):
    with store._read_snapshot() as snapshot, snapshot._session() as conn:
        return recall_integrity.check_recall_integrity(conn)


def test_clean_recall_preserves_data_and_empty_shape(store):
    event = _record(store)
    report = prior_attempts_report(store, entity_path=ENTITY)
    rows, notices = report["attempts"], report["warnings"]
    assert notices == []
    assert len(rows) == 1
    assert rows[0]["id"] == event.id
    assert rows[0]["reasoning"] == rows[0]["outcome_reasoning"] == REASON
    assert rows[0]["confidence"] == "exact"
    assert report["integrity"] == {
        "status": "intact", "scope": "local_chain_snapshot", "reason": "checked",
    }
    assert rows[0]["note"] == ""
    assert prior_attempts_payload(store, entity_path="missing") == ([], [])
    # The lower-level API keeps its existing event shape.
    assert "integrity" not in store.get_prior_attempts(entity_path=ENTITY)[0]


@pytest.mark.parametrize("empty", [False, True])
def test_failed_check_surfaces_identically_in_cli_and_mcp(store, monkeypatch, empty):
    import selvedge.server as srv

    event = None if empty else _record(store)
    monkeypatch.setattr(srv, "_storage", store)
    monkeypatch.setattr(chain, "verify_chain", Mock(return_value={
        "table_present": True, "records": 2, "intact": False,
    }))
    rows = srv.prior_attempts(entity_path=ENTITY)
    result = CliRunner().invoke(cli, ["prior-attempts", ENTITY, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == ([] if empty else rows)
    report = srv.prior_attempts(entity_path=ENTITY, output_format="report-v1")
    assert "Integrity check failed" in result.stderr
    assert "fuzzy unavailable" not in result.stderr
    assert len(rows) == 1
    assert report["integrity"]["status"] == "failed"
    assert "Integrity check failed" in rows[0]["note"]
    assert rows[0]["id"] == (event.id if event else "")
    if empty:
        assert rows[0]["match_type"] == "note"
        assert rows[0]["entity_path"] == ""
        assert rows[0]["confidence"] == ""
    else:
        assert rows[0]["reasoning"] == REASON
        assert rows[0]["confidence"] == "exact"
    text = CliRunner().invoke(cli, ["prior-attempts", ENTITY])
    assert text.exit_code == 0
    assert "Integrity check failed" in text.stderr
    assert ("No prior attempts" if empty else "reverted:") in text.stdout


@pytest.mark.parametrize("exc", [sqlite3.OperationalError("busy"), ValueError("unavailable"),
                                 TypeError("unavailable"), RuntimeError("unavailable")])
def test_verifier_errors_are_unknown_and_preserve_evidence(store, monkeypatch, exc):
    event = _record(store)
    monkeypatch.setattr(chain, "verify_chain", Mock(side_effect=exc))
    report = prior_attempts_report(store, entity_path=ENTITY)
    rows, notices = report["attempts"], report["warnings"]
    assert rows[0]["id"] == event.id
    assert report["integrity"]["status"] == "unknown"
    assert report["integrity"]["reason"] == "check_error"
    assert "Integrity unknown" in notices[0]
    assert "unverified" in rows[0]["note"]


@pytest.mark.parametrize("result", [
    {"table_present": False, "records": 0, "intact": True},
    {"table_present": True, "records": 0, "intact": True},
])
def test_absent_chain_cannot_be_reported_as_intact(store, monkeypatch, result):
    monkeypatch.setattr(chain, "verify_chain", Mock(return_value=result))
    report = prior_attempts_report(store, entity_path="missing")
    assert report["attempts"] == []
    assert report["query_status"] == "no_match"
    assert report["integrity"]["reason"] == "chain_unavailable"
    assert report["warnings"] and report["integrity"]["status"] == "unknown"


@pytest.mark.parametrize("coverage", [
    {"genesis_present": False, "unchained": 0},
    {"genesis_present": True, "unchained": 1},
])
def test_partial_or_unestablished_coverage_is_unknown(store, monkeypatch, coverage):
    _record(store)
    monkeypatch.setattr(chain, "chain_coverage", Mock(return_value=coverage))
    assert _check(store)["reason"] == "incomplete_coverage"


@pytest.mark.parametrize("limit_name,value", [("MAX_RECORDS", 1), ("MAX_BYTES", 1)])
def test_input_caps_skip_full_verifier(store, monkeypatch, limit_name, value):
    _record(store)
    monkeypatch.setattr(recall_integrity, limit_name, value)
    verifier = Mock(side_effect=AssertionError("must not walk an over-budget store"))
    monkeypatch.setattr(chain, "verify_chain", verifier)
    assert _check(store)["reason"] == "budget_exceeded"
    verifier.assert_not_called()


def test_time_budget_and_sql_interrupt_restore_connection(store, monkeypatch):
    store.log_event_batch([ChangeEvent(entity_path=f"item.{i}", change_type="reject")
                           for i in range(50)])
    monkeypatch.setattr(recall_integrity, "MAX_SQL_STEPS", 0)
    with store._read_snapshot() as snapshot, snapshot._session() as conn:
        assert recall_integrity.check_recall_integrity(conn)["reason"] == "budget_exceeded"
        # The check's progress handler has been cleared, even on interruption.
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 50
    monkeypatch.setattr(recall_integrity, "MAX_SQL_STEPS", 2_000_000)
    monkeypatch.setattr(recall_integrity, "MAX_SECONDS", 0)
    assert _check(store)["reason"] == "budget_exceeded"


def test_real_legacy_schema_without_chain_is_unknown(tmp_path):
    # A valid predecessor schema, built without a chain from the outset.
    from selvedge.storage import CREATE_TABLE_SQL

    with sqlite3.connect(tmp_path / "legacy.db") as conn:
        conn.executescript(CREATE_TABLE_SQL)
        conn.execute("BEGIN")
        signal = recall_integrity.check_recall_integrity(conn)
    assert signal["status"] == "unknown"
    assert signal["reason"] == "chain_unavailable"


def test_connection_without_snapshot_never_claims_intact(store):
    with store._session() as conn:
        assert recall_integrity.check_recall_integrity(conn)["reason"] == "snapshot_required"


def test_concurrent_valid_append_is_excluded_from_checked_snapshot(store, monkeypatch):
    first = _record(store)
    original = chain.verify_chain
    appended = []

    def verify_then_append(conn):
        # The writer commits after recall materializes its rows. Verification
        # must still see those same rows, not the writer's newer snapshot.
        appended.append(_record(store))
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        return original(conn)

    monkeypatch.setattr(chain, "verify_chain", verify_then_append)
    report = prior_attempts_report(store, entity_path=ENTITY)
    rows, notices = report["attempts"], report["warnings"]
    assert [row["id"] for row in rows] == [first.id]
    assert report["integrity"]["status"] == "intact"
    assert notices == []
    assert store._snapshot_connection is None
    monkeypatch.setattr(chain, "verify_chain", original)
    later, _ = prior_attempts_payload(store, entity_path=ENTITY)
    assert {row["id"] for row in later} == {first.id, appended[0].id}


def test_fuzzy_and_exact_share_one_check_and_snapshot(store, monkeypatch):
    from selvedge import semantic

    _record(store)
    other = store.log_event(ChangeEvent(entity_path="other.token", change_type="reject"))
    monkeypatch.setattr(semantic, "fuzzy_events", lambda *args, **kwargs: [
        {"entity_path": other.entity_path, "similarity": 0.9},
    ])
    verifier = Mock(wraps=chain.verify_chain)
    monkeypatch.setattr(chain, "verify_chain", verifier)
    report = prior_attempts_report(store, entity_path=ENTITY, fuzzy="token")
    rows, notices = report["attempts"], report["warnings"]
    assert len(rows) == 2
    assert {row["match_type"] for row in rows} == {"exact", "fuzzy"}
    assert report["integrity"]["status"] == "intact"
    assert notices == []
    verifier.assert_called_once()


def test_read_only_snapshot_does_not_initialize_or_change_database(store, monkeypatch):
    _record(store)
    reader = SelvedgeStorage(store.db_path, read_only=True)
    before = store.db_path.read_bytes()
    monkeypatch.setattr(SelvedgeStorage, "_init_db", Mock(side_effect=AssertionError("no init")))
    report = prior_attempts_report(reader, entity_path=ENTITY)
    assert report["integrity"]["status"] == "intact"
    assert report["warnings"] == []
    assert store.db_path.read_bytes() == before
    with reader._read_snapshot() as snapshot:
        with pytest.raises(PermissionError):
            snapshot.log_event(ChangeEvent(entity_path="another", change_type="add"))
        with pytest.raises(PermissionError), snapshot._session(immediate=True):
            pass


def test_unsupported_format_check_uses_unknown_without_walking(store):
    with store._read_snapshot() as snapshot, snapshot._session() as conn:
        proxy = Mock(wraps=conn)
        proxy.in_transaction = True
        execute = conn.execute

        def structural_result(sql, *args):
            if "canon !=" in sql:
                return Mock(fetchone=lambda: (1,))
            return execute(sql, *args)

        proxy.execute.side_effect = structural_result
        assert recall_integrity.check_recall_integrity(proxy)["reason"] == "unsupported_chain_format"


@pytest.mark.parametrize("failed", [False, True])
def test_read_only_fuzzy_fallback_has_no_writes_and_keeps_integrity_notice(store, monkeypatch, failed):
    reader = SelvedgeStorage(store.db_path, read_only=True)
    before = store.db_path.read_bytes()
    if failed:
        monkeypatch.setattr(chain, "verify_chain", Mock(return_value={
            "table_present": True, "records": 1, "intact": False,
        }))
    report = prior_attempts_report(reader, fuzzy="no such record")
    rows, notices = prior_attempts_payload(reader, fuzzy="no such record")
    assert len(rows) == 1 and rows[0]["match_type"] == "note"
    assert "fuzzy matching unavailable" in rows[0]["note"]
    assert report["integrity"]["status"] == ("failed" if failed else "intact")
    if failed:
        assert "Integrity check failed" in rows[0]["note"]
        assert len(notices) == 2
    assert store.db_path.read_bytes() == before
    with reader._session() as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='embeddings'"
        ).fetchone() is None
