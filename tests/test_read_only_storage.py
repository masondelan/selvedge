import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from selvedge import ChangeEvent, SelvedgeStorage


class ReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "synthetic.db"
        self.writer = SelvedgeStorage(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, kind="reject", **kw):
        return self.writer.log_event(
            ChangeEvent(entity_path="schema.sql", change_type=kind, reasoning="SYNTHETIC", **kw)
        )

    def test_default_compatible_and_public_mode(self):
        self.event()
        r = SelvedgeStorage(self.db, read_only=True)
        self.assertEqual(r.get_history(), self.writer.get_history())
        self.assertEqual(
            r.get_prior_attempts(entity_path="schema.sql"),
            self.writer.get_prior_attempts(entity_path="schema.sql"),
        )

    def test_no_init_or_migration(self):
        self.event()
        before = self.db.read_bytes()
        with (
            patch.object(SelvedgeStorage, "_init_db", side_effect=AssertionError("init forbidden")),
            patch(
                "selvedge.storage.apply_migrations",
                side_effect=AssertionError("migration forbidden"),
            ),
        ):
            r = SelvedgeStorage(self.db, read_only=True)
            self.assertEqual(r.count(), 1)
        self.assertEqual(before, self.db.read_bytes())

    def test_missing_file_and_parent_not_created(self):
        p = self.root / "absent" / "db"
        with self.assertRaises(FileNotFoundError):
            SelvedgeStorage(p, read_only=True)
        self.assertFalse(p.parent.exists())

    def test_schema_legacy_and_future_rejected(self):
        for version in ("legacy", "future"):
            p = self.root / (version + ".db")
            SelvedgeStorage(p)
            with sqlite3.connect(p) as c:
                if version == "legacy":
                    c.execute(
                        "DELETE FROM schema_migrations WHERE version=(SELECT MAX(version) FROM schema_migrations)"
                    )
                else:
                    c.execute("INSERT INTO schema_migrations VALUES(999,'future','now')")
            before = p.read_bytes()
            with self.assertRaises(ValueError):
                SelvedgeStorage(p, read_only=True)
            self.assertEqual(before, p.read_bytes())

    def test_schema_missing_columns_and_corruption_rejected(self):
        p = self.root / "empty.db"
        with sqlite3.connect(p) as c:
            c.execute("CREATE TABLE nope(x)")
        with self.assertRaises(ValueError):
            SelvedgeStorage(p, read_only=True)
        with sqlite3.connect(self.db) as c:
            c.execute("ALTER TABLE tool_calls DROP COLUMN error_msg")
        with self.assertRaises(ValueError):
            SelvedgeStorage(self.db, read_only=True)
        p = self.root / "bad.db"
        p.write_bytes(b"not sqlite")
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            SelvedgeStorage(p, read_only=True)

    def test_public_writes_rejected_even_noops(self):
        r = SelvedgeStorage(self.db, read_only=True)
        e = ChangeEvent(entity_path="schema.sql", change_type="reject")
        calls = [
            lambda: r.log_event(e),
            lambda: r.log_event_batch([]),
            lambda: r.log_supersede("missing", reasoning="x"),
            lambda: r.log_rename("a", "b"),
            lambda: r.backfill_git_commit("x"),
            lambda: r.record_tool_call("tool"),
            lambda: r.prune_events("2000"),
            lambda: r.prune_tool_calls("2000"),
            lambda: r.recanonicalize_paths(apply=True),
        ]
        before = self.db.read_bytes()
        for call in calls:
            with self.assertRaises(PermissionError):
                call()
        self.assertEqual(before, self.db.read_bytes())

    def test_sql_layer_write_denied(self):
        r = SelvedgeStorage(self.db, read_only=True)
        with r._session() as c:
            self.assertIsInstance(c.execute("SELECT 1 AS value").fetchone(), sqlite3.Row)
            with self.assertRaises(sqlite3.OperationalError):
                c.execute("CREATE TABLE forbidden(x)")

    def test_live_wal_committed_fresh_and_uncommitted_hidden(self):
        keeper = sqlite3.connect(self.db)
        keeper.execute("PRAGMA journal_mode=WAL")
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        try:
            self.event()
            wal = Path(str(self.db) + "-wal")
            self.assertGreater(wal.stat().st_size, 0)
            before = self.db.read_bytes()
            r = SelvedgeStorage(self.db, read_only=True)
            self.assertEqual(r.count(), 1)
            self.assertEqual(before, self.db.read_bytes())
            keeper.execute(
                "INSERT INTO events(id,timestamp,entity_path,change_type,reasoning) VALUES('uncommitted','2026-11-17T00:00:00Z','schema.sql','reject','SYNTHETIC')"
            )
            self.assertEqual(r.count(), 1)
            keeper.commit()
            before = self.db.read_bytes()
            self.assertEqual(r.count(), 2)
            self.assertEqual(before, self.db.read_bytes())
        finally:
            keeper.close()

    def test_single_session_snapshot_and_next_call_fresh(self):
        self.event()
        r = SelvedgeStorage(self.db, read_only=True)
        with r._session() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            self.event()
            self.assertEqual(c.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        self.assertEqual(r.count(), 2)

    def test_connections_closed_success_and_failure(self):
        opened = []
        original = sqlite3.connect

        class Tracked(sqlite3.Connection):
            def close(self):
                self.was_closed = True
                super().close()

        def connect(*a, **kw):
            c = original(*a, **kw, factory=Tracked)
            c.was_closed = False
            opened.append(c)
            return c

        with patch("selvedge.storage.sqlite3.connect", side_effect=connect):
            r = SelvedgeStorage(self.db, read_only=True)
            r.count()
            with self.assertRaises(RuntimeError):
                with r._session():
                    raise RuntimeError("synthetic")
        self.assertTrue(opened)
        self.assertTrue(all(c.was_closed for c in opened))

    def test_spoofed_shape_and_duplicate_ledger_rejected(self):
        with sqlite3.connect(self.db) as c:
            c.execute("ALTER TABLE schema_migrations RENAME TO old_ledger")
            c.execute("CREATE TABLE schema_migrations(version INTEGER,name TEXT,applied_at TEXT)")
            c.execute("INSERT INTO schema_migrations SELECT * FROM old_ledger")
            c.execute("INSERT INTO schema_migrations SELECT * FROM old_ledger LIMIT 1")
        with self.assertRaises(ValueError):
            SelvedgeStorage(self.db, read_only=True)
        p = self.root / "wrongtype.db"
        SelvedgeStorage(p)
        with sqlite3.connect(p) as c:
            c.execute("ALTER TABLE tool_calls RENAME TO old_tools")
            c.execute(
                "CREATE TABLE tool_calls(id INTEGER PRIMARY KEY,timestamp TEXT,tool_name TEXT,entity_path TEXT,success INTEGER,error_msg TEXT,agent TEXT)"
            )
        with self.assertRaises(ValueError):
            SelvedgeStorage(p, read_only=True)

    def test_type_strict(self):
        with self.assertRaises(TypeError):
            SelvedgeStorage(self.db, read_only="yes")


if __name__ == "__main__":
    unittest.main()


def test_schema_contract_matches_authoritative_initialization(tmp_path):
    """Schema snapshot must track the actual initializer and all migrations."""
    from selvedge._readonly_schema import REQUIRED_COLUMNS
    from selvedge.migrations import MIGRATIONS

    db = tmp_path / "schema.db"
    SelvedgeStorage(db)
    with sqlite3.connect(db) as conn:
        actual = {
            name: list(conn.execute(f'PRAGMA table_info("{name}")')) for name in REQUIRED_COLUMNS
        }
        assert actual == REQUIRED_COLUMNS
        assert set(conn.execute("SELECT version, name FROM schema_migrations")) == {
            (m.version, m.name) for m in MIGRATIONS
        }
    assert SelvedgeStorage(db, read_only=True).count() == 0


def test_upgraded_pre_changeset_store_is_readable(tmp_path):
    """A real migration sequence puts changeset_id after metadata."""
    from tests.test_migrations import _make_pre_changeset_db

    db = tmp_path / "pre-changeset.db"
    _make_pre_changeset_db(db)
    writer = SelvedgeStorage(db)
    event = writer.log_event(
        ChangeEvent(
            entity_path="schema.sql",
            change_type="reject",
            changeset_id="historical",
            reasoning="Keep the original schema.",
        )
    )
    with sqlite3.connect(db) as conn:
        names = [row[1] for row in conn.execute("PRAGMA table_info(events)")]
        assert names.index("metadata") < names.index("changeset_id")
    reader = SelvedgeStorage(db, read_only=True)
    assert reader.get_changeset("historical") == writer.get_changeset("historical")
    assert reader.get_history()[0]["id"] == event.id
    assert reader.get_prior_attempts(entity_path="schema.sql") == writer.get_prior_attempts(
        entity_path="schema.sql"
    )
