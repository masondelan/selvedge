"""
Tests for ``selvedge backup`` — the v0.3.5 snapshot command.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from selvedge import backup as backup_mod
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


def test_create_backup_writes_valid_sqlite(db_path):
    s = SelvedgeStorage(db_path)
    s.log_event(ChangeEvent(entity_path="users.email", change_type="add"))
    result = backup_mod.create_backup(db_path)

    assert result.output_path.is_file()
    assert result.bytes_written > 0
    # Snapshot must be a well-formed SQLite DB readable on its own.
    with sqlite3.connect(str(result.output_path)) as conn:
        rows = conn.execute("SELECT entity_path FROM events").fetchall()
    assert rows == [("users.email",)]


def test_rotation_keeps_only_keep_last(db_path):
    """create_backup prunes oldest snapshots beyond keep_last."""
    SelvedgeStorage(db_path)
    backups_dir = backup_mod.default_backups_dir(db_path)
    backups_dir.mkdir(parents=True, exist_ok=True)

    # Drop 5 pre-existing fake snapshots with mtimes 5,4,3,2,1 seconds ago so
    # sort order is deterministic.
    now = time.time()
    for i in range(5):
        f = backups_dir / f"selvedge-2026010{i}-000000.db"
        f.write_bytes(b"")
        # Older files first so the rotation prunes them.
        import os
        os.utime(f, (now - (10 - i), now - (10 - i)))

    # Rotation runs AFTER the new backup lands. 5 old + 1 new = 6, keep_last=2
    # prunes the 4 oldest. Two newest remain (including the new one).
    result = backup_mod.create_backup(db_path, keep_last=2)
    remaining = sorted(backups_dir.glob("selvedge-*.db"))
    assert len(remaining) == 2
    assert result.output_path in remaining
    assert len(result.pruned) == 4


def test_create_backup_collides_within_same_second(db_path):
    """Two backups within the same second don't clobber each other."""
    SelvedgeStorage(db_path)
    fixed_now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)
    a = backup_mod.create_backup(db_path, now=fixed_now)
    b = backup_mod.create_backup(db_path, now=fixed_now)
    assert a.output_path != b.output_path
    assert a.output_path.is_file()
    assert b.output_path.is_file()


def test_missing_db_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup_mod.create_backup(tmp_path / "nope.db")


def test_ensure_gitignore_creates_and_is_idempotent(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    gitignore = project / ".gitignore"

    # First call: creates a .gitignore with the line.
    assert backup_mod.ensure_gitignore_entry(project) is True
    assert backup_mod.GITIGNORE_LINE in gitignore.read_text()

    # Second call: no-op.
    assert backup_mod.ensure_gitignore_entry(project) is False
    assert gitignore.read_text().count(backup_mod.GITIGNORE_LINE) == 1


def test_ensure_gitignore_appends_to_existing(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".gitignore").write_text("node_modules/\n*.pyc\n")

    assert backup_mod.ensure_gitignore_entry(project) is True
    content = (project / ".gitignore").read_text()
    assert "node_modules/" in content  # original preserved
    assert backup_mod.GITIGNORE_LINE in content


def test_backup_cli_writes_and_emits_json(runner, db_path):
    SelvedgeStorage(db_path)
    result = runner.invoke(cli, ["backup", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    from pathlib import Path
    assert Path(payload["output_path"]).is_file()
    assert payload["bytes_written"] > 0


# ---------------------------------------------------------------------------
# Interrupted snapshots must not be trusted
#
# `VACUUM INTO` writes directly to the final `selvedge-*.db` name. If it is
# interrupted — OOM kill, SIGTERM from a CI timeout, laptop shutdown, disk
# full — the truncated file keeps that name, and every consumer trusts the
# name: `last_backup_time` (so `selvedge doctor` reports a green "0d ago"),
# `list_backups`, and `_rotate`, which sorts by mtime and will happily evict
# the last *valid* snapshot to make room for corrupt ones.
# ---------------------------------------------------------------------------


def _explode_on_vacuum(monkeypatch, real_connect=sqlite3.connect):
    """Make VACUUM INTO fail the way a kill mid-write does: partial file, raise."""

    class _Boom(sqlite3.Connection):
        def execute(self, sql, *a, **kw):  # type: ignore[override]
            if sql.strip().upper().startswith("VACUUM INTO"):
                # Simulate the partially-written destination the real failure
                # leaves behind, then fail.
                Path(a[0][0]).write_bytes(b"SQLite format 3\x00" + b"\x00" * 512)
                raise sqlite3.OperationalError("disk I/O error")
            return super().execute(sql, *a, **kw)

    monkeypatch.setattr(
        backup_mod.sqlite3,
        "connect",
        lambda *a, **kw: real_connect(*a, **{**kw, "factory": _Boom}),
    )


def test_interrupted_backup_leaves_no_trusted_snapshot(db_path, monkeypatch):
    s = SelvedgeStorage(db_path)
    s.log_event(ChangeEvent(entity_path="users.email", change_type="add"))
    backups_dir = backup_mod.default_backups_dir(db_path)

    _explode_on_vacuum(monkeypatch)
    with pytest.raises(sqlite3.OperationalError):
        backup_mod.create_backup(db_path)

    # Nothing carrying the trusted name, and no leftover temp file either.
    assert list(backups_dir.glob("selvedge-*.db")) == []
    assert list(backups_dir.glob("*.part")) == []
    assert backup_mod.last_backup_time(db_path) is None


def test_interrupted_backups_do_not_evict_the_last_good_one(db_path, monkeypatch):
    """Interrupted runs must not consume rotation slots.

    Rotation keeps the `keep_last` newest `selvedge-*.db` files by mtime. If
    interrupted vacuums leave truncated files under that name they are the
    newest, so the next successful backup's rotation evicts the older — and
    only valid — snapshot, leaving a directory of unrestorable files.
    """
    s = SelvedgeStorage(db_path)
    s.log_event(ChangeEvent(entity_path="users.email", change_type="add"))
    good = backup_mod.create_backup(db_path, keep_last=3).output_path
    assert good.is_file()

    _explode_on_vacuum(monkeypatch)
    for _ in range(8):
        with pytest.raises(sqlite3.OperationalError):
            backup_mod.create_backup(db_path, keep_last=3)
    monkeypatch.undo()

    # A later successful backup rotates; the valid history must survive it.
    time.sleep(0.01)
    backup_mod.create_backup(db_path, keep_last=3)

    survivors = list(backup_mod.default_backups_dir(db_path).glob("selvedge-*.db"))
    assert good in survivors, "rotation evicted the only valid snapshot"
    for snap in survivors:
        con = sqlite3.connect(snap)
        try:
            assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok", snap
        finally:
            con.close()
