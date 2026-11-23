"""Clean, dependency-free recall/report compatibility checks across runtimes.

Also runnable with ``python -m unittest tests.test_recall_core_compat -v``.
Historical fixtures are constructed in their original schema before opening
with current storage. No existing chain or protected content is modified.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from selvedge import chain
from selvedge.migrations import MIGRATIONS, SCHEMA_MIGRATIONS_TABLE_SQL
from selvedge.models import ChangeEvent
from selvedge.presenters import prior_attempts_payload, prior_attempts_report
from selvedge.storage import SelvedgeStorage

_BASE_SCHEMA = """
CREATE TABLE events (
    id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
    entity_type TEXT NOT NULL DEFAULT 'other',
    entity_path TEXT NOT NULL, change_type TEXT NOT NULL,
    diff TEXT NOT NULL DEFAULT '', reasoning TEXT NOT NULL DEFAULT '',
    agent TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '',
    git_commit TEXT NOT NULL DEFAULT '', project TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE tool_calls (
    id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
    tool_name TEXT NOT NULL, entity_path TEXT NOT NULL DEFAULT '',
    success INTEGER NOT NULL DEFAULT 1, error_msg TEXT NOT NULL DEFAULT ''
);
"""
_LEGACY_KEYS = {
    "id", "timestamp", "entity_type", "entity_path", "change_type", "diff", "reasoning",
    "agent", "session_id", "git_commit", "project", "changeset_id", "metadata",
    "revisit_after", "expires_when", "supersedes", "constraint", "stale_when",
    "outcome", "confidence", "outcome_reasoning", "superseded_by", "supersede_reasoning",
    "current_status", "match_type", "similarity", "note",
}


class RecallCoreCompatibility(unittest.TestCase):
    """Normal writes, valid historical schemas, and mocked status failures."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="selvedge-core-compat-")
        self.root = Path(self.tmp.name)
        self.store = SelvedgeStorage(self.root / "fresh.db")

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, kind="revert", entity="fixture.token", timestamp="2026-01-01T00:00:00Z"):
        return self.store.log_event(ChangeEvent(
            entity_path=entity, change_type=kind, timestamp=timestamp,
            reasoning="The issuer manages token expiry; retain its expiry policy.",
            metadata=json.dumps({"source": "benign fixture", "integrity": "source-owned value"}),
        ))

    def test_standalone_and_strict_json_keys(self):
        event = self.record()
        rows, warnings = prior_attempts_payload(self.store, entity_path=event.entity_path)
        row = json.loads(json.dumps(rows))[0]
        self.assertEqual(set(row), _LEGACY_KEYS)
        self.assertEqual(row["id"], event.id)
        self.assertEqual(row["outcome"], "reverted")
        self.assertEqual(row["confidence"], "exact")
        self.assertEqual(row["reasoning"], row["outcome_reasoning"])
        self.assertEqual(row["metadata"], json.loads(event.metadata))
        self.assertEqual(warnings, [])
        report = prior_attempts_report(self.store, entity_path=event.entity_path)
        self.assertEqual(report["attempts"], rows)
        self.assertEqual(report["integrity"]["status"], "intact")
        self.assertEqual(report["format"], "selvedge.prior-attempts/1")

    def test_paired_verdict_is_not_duplicated(self):
        attempt = self.record("modify")
        closing = self.record(timestamp="2026-01-02T00:00:00Z")
        report = prior_attempts_report(self.store, entity_path=attempt.entity_path)
        self.assertEqual([r["id"] for r in report["attempts"]], [attempt.id])
        self.assertNotEqual(report["attempts"][0]["id"], closing.id)
        self.assertEqual(report["attempts"][0]["outcome_reasoning"], closing.reasoning)

    def test_targeted_supersession_leaves_sibling_closed(self):
        target = self.record()
        sibling = self.record(timestamp="2026-01-02T00:00:00Z")
        revision = self.store.log_supersede(target.entity_path, supersedes=target.id,
            reasoning="The issuer now supplies the required expiry information.")
        report = prior_attempts_report(self.store, entity_path=target.entity_path)
        rows = {r["id"]: r for r in report["attempts"]}
        self.assertEqual(set(rows), {target.id, sibling.id})
        self.assertEqual(rows[target.id]["outcome"], "reopened")
        self.assertEqual(rows[target.id]["superseded_by"], revision.id)
        self.assertEqual(rows[sibling.id]["outcome"], "reverted")
        self.assertEqual(rows[sibling.id]["superseded_by"], "")

    def test_clean_no_match_keeps_explicit_report(self):
        self.assertEqual(prior_attempts_payload(self.store, entity_path="missing"), ([], []))
        report = prior_attempts_report(self.store, entity_path="missing")
        self.assertEqual(report["attempts"], [])
        self.assertEqual(report["query_status"], "no_match")
        self.assertEqual(report["integrity"]["status"], "intact")
        self.assertEqual(report["warnings"], [])

    def test_uncertain_no_match_metadata_is_separate_from_attempts(self):
        self.record()
        for status in ("failed", "unknown"):
            with self.subTest(status=status):
                outcome = {"table_present": True, "records": 2, "intact": False}
                with patch.object(chain, "verify_chain", return_value=outcome,
                                  side_effect=RuntimeError("mock unavailable") if status == "unknown" else None):
                    report = prior_attempts_report(self.store, entity_path="missing")
                    self.assertEqual(report["attempts"], [])
                    self.assertEqual(report["integrity"]["status"], status)
                    self.assertTrue(report["warnings"])
                    rows, warnings = prior_attempts_payload(self.store, entity_path="missing",
                                                           include_empty_notice=False)
                    self.assertEqual(rows, [])
                    self.assertTrue(warnings)
                    mcp_rows, _ = prior_attempts_payload(self.store, entity_path="missing")
                    self.assertEqual(len(mcp_rows), 1)
                    self.assertEqual(mcp_rows[0]["match_type"], "note")
                    self.assertEqual(set(mcp_rows[0]), _LEGACY_KEYS)

    def test_fuzzy_no_index_is_read_only_and_query_warning(self):
        self.record()
        reader = SelvedgeStorage(self.store.db_path, read_only=True)
        before = reader.db_path.read_bytes()
        report = prior_attempts_report(reader, fuzzy="no such wording")
        self.assertEqual(report["attempts"], [])
        self.assertTrue(report["warnings"])
        self.assertEqual(report["integrity"]["status"], "intact")
        self.assertEqual(reader.db_path.read_bytes(), before)
        with reader._session() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='embeddings'").fetchone())

    def test_valid_append_during_check_uses_same_snapshot(self):
        event = self.record()
        verify = chain.verify_chain
        later = []

        def check(conn):
            later.append(self.record(entity="fixture.other"))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            return verify(conn)

        with patch.object(chain, "verify_chain", side_effect=check):
            report = prior_attempts_report(self.store, entity_path="fixture")
        self.assertEqual([r["id"] for r in report["attempts"]], [event.id])
        self.assertEqual(report["integrity"]["status"], "intact")
        self.assertEqual(len(prior_attempts_report(self.store, entity_path="fixture")["attempts"]), 2)

    def test_all_historical_migration_versions_upgrade_honestly(self):
        for version in range(len(MIGRATIONS) + 1):
            for seeded in (False, True):
                with self.subTest(version=version, seeded=seeded):
                    db = self.root / f"legacy-{version}-{seeded}.db"
                    with closing(sqlite3.connect(db)) as conn, conn:
                        conn.executescript(_BASE_SCHEMA)
                        if version:
                            conn.executescript(SCHEMA_MIGRATIONS_TABLE_SQL)
                            for migration in MIGRATIONS[:version]:
                                for statement in migration.statements:
                                    conn.execute(statement)
                                conn.execute("INSERT INTO schema_migrations VALUES(?,?,?)",
                                    (migration.version, migration.name, "2026-01-01T00:00:00Z"))
                        if seeded:
                            conn.execute("INSERT INTO events(id,timestamp,entity_path,change_type,reasoning) VALUES(?,?,?,?,?)",
                                ("historical-revert", "2026-01-01T00:00:00Z", "fixture.old", "revert", "Historical rollback rationale."))
                    writer = SelvedgeStorage(db)
                    reader = SelvedgeStorage(db, read_only=True)
                    report = prior_attempts_report(reader, entity_path="fixture.old")
                    self.assertEqual(report["integrity"]["status"], "unknown" if seeded else "intact")
                    if seeded:
                        self.assertEqual(report["integrity"]["reason"], "incomplete_coverage")
                        self.assertEqual(report["attempts"][0]["id"], "historical-revert")
                        self.assertEqual(report["attempts"][0]["confidence"], "exact")
                        revision = writer.log_supersede("fixture.old", supersedes="historical-revert",
                            reasoning="The historical constraint changed for this one verdict.")
                        updated = prior_attempts_report(reader, entity_path="fixture.old")
                        self.assertEqual(updated["attempts"][0]["superseded_by"], revision.id)
                        self.assertEqual(updated["integrity"]["status"], "unknown")
                    else:
                        self.assertEqual(report["attempts"], [])
                        self.assertEqual(report["query_status"], "no_match")


if __name__ == "__main__":
    unittest.main()
