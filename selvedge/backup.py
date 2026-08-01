"""
``selvedge backup`` — snapshot the SQLite store and prune old snapshots.

Uses SQLite's online backup (``VACUUM INTO``) so a backup taken while
agents are writing yields a consistent, well-formed copy. The destination
default is ``.selvedge/backups/selvedge-YYYYMMDD-HHMMSS.db`` next to the
live DB; ``--output`` overrides for ad-hoc paths.

The ``keep_last`` value is hardcoded at :data:`KEEP_LAST` for this
release. v0.3.10 introduces ``.selvedge/config.toml`` and the setting
becomes ``backup_keep_last`` there.

``.selvedge/backups/`` is added to the project ``.gitignore`` so backups
never reach the remote — ``selvedge init`` writes it on fresh repos in
v0.3.5+, and the first ``selvedge backup`` run on an existing repo
appends it the same way.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Hardcoded for v0.3.5. Becomes ``backup_keep_last`` in .selvedge/config.toml
# when the config file lands in v0.3.10.
KEEP_LAST = 7

BACKUP_FILENAME_FMT = "selvedge-%Y%m%d-%H%M%S.db"

# Line written to the project ``.gitignore`` on init and on first backup.
GITIGNORE_LINE = ".selvedge/backups/"


@dataclass(frozen=True)
class BackupResult:
    """Outcome of one ``selvedge backup`` invocation."""

    output_path: Path
    bytes_written: int
    pruned: list[Path]

    def to_dict(self) -> dict:
        return {
            "output_path": str(self.output_path),
            "bytes_written": self.bytes_written,
            "pruned": [str(p) for p in self.pruned],
        }


def default_backups_dir(db_path: Path) -> Path:
    """The ``.selvedge/backups/`` directory next to the live DB."""
    return db_path.parent / "backups"


def default_backup_path(db_path: Path, now: datetime | None = None) -> Path:
    """Compose the default timestamped destination for a backup."""
    when = now or datetime.now(timezone.utc)
    return default_backups_dir(db_path) / when.strftime(BACKUP_FILENAME_FMT)


def create_backup(
    db_path: Path,
    output: Path | None = None,
    keep_last: int = KEEP_LAST,
    now: datetime | None = None,
) -> BackupResult:
    """
    Snapshot ``db_path`` to ``output`` and rotate old snapshots.

    If ``output`` is None, writes to
    ``<.selvedge>/backups/selvedge-YYYYMMDD-HHMMSS.db``.

    Returns a :class:`BackupResult` with the destination path, snapshot size,
    and any files pruned by the rotation.
    """
    if not db_path.is_file():
        raise FileNotFoundError(
            f"cannot back up: no database at {db_path} — run `selvedge init`"
        )

    dest = output if output is not None else default_backup_path(db_path, now=now)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # ``VACUUM INTO`` errors if the destination already exists — common on the
    # second backup within the same second. Bump the filename with a suffix
    # rather than overwriting (overwriting could clobber a known-good
    # snapshot in the same second).
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        n = 1
        while True:
            candidate = dest.with_name(f"{stem}-{n}{suffix}")
            if not candidate.exists():
                dest = candidate
                break
            n += 1

    # Vacuum to a temporary name and rename into place only on success. Every
    # consumer downstream trusts the `selvedge-*.db` name to mean "a complete
    # snapshot" — `_rotate` sorts by mtime and prunes, `list_backups` and
    # `last_backup_time` report it, and `selvedge doctor` turns the latter into
    # a green "last backup 0d ago". A vacuum killed partway (OOM, SIGTERM from
    # a CI timeout, shutdown, disk full) would otherwise leave a truncated file
    # holding that name: unopenable, newest by mtime, and therefore evicting
    # real snapshots on the next rotation.
    part = dest.with_name(dest.name + ".part")
    conn = sqlite3.connect(str(db_path))
    try:
        # VACUUM INTO is online — concurrent readers/writers are fine. It
        # also rewrites the file from scratch, so the snapshot has no
        # WAL/SHM sidecars to worry about.
        conn.execute("VACUUM INTO ?", (str(part),))
    except BaseException:
        # BaseException, not Exception: a KeyboardInterrupt mid-vacuum must
        # clean up too, since that is one of the ways this actually happens.
        part.unlink(missing_ok=True)
        part.with_name(part.name + "-journal").unlink(missing_ok=True)
        raise
    finally:
        conn.close()
    os.replace(part, dest)

    pruned = _rotate(default_backups_dir(db_path), keep_last=keep_last)

    return BackupResult(
        output_path=dest,
        bytes_written=dest.stat().st_size,
        pruned=pruned,
    )


def _rotate(backups_dir: Path, keep_last: int) -> list[Path]:
    """
    Delete oldest backups beyond ``keep_last``. Returns the deleted paths.

    Only looks at files matching the ``selvedge-*.db`` glob so ad-hoc
    ``--output`` destinations parked elsewhere aren't swept up. Custom
    ``--output`` paths outside ``.selvedge/backups/`` are never rotated.
    """
    if keep_last <= 0 or not backups_dir.is_dir():
        return []

    snapshots = sorted(
        backups_dir.glob("selvedge-*.db"),
        key=lambda p: p.stat().st_mtime,
    )
    excess = len(snapshots) - keep_last
    if excess <= 0:
        return []

    pruned: list[Path] = []
    for p in snapshots[:excess]:
        try:
            p.unlink()
            pruned.append(p)
        except OSError:
            # Best-effort prune: if a file can't be deleted (permissions,
            # AV scanner holding a handle), don't fail the backup.
            continue
    return pruned


def ensure_gitignore_entry(project_root: Path) -> bool:
    """
    Append the backups gitignore line to ``project_root/.gitignore`` if missing.

    Returns True when the file was modified (or created), False when the
    line was already present or no ``.gitignore`` existed and one couldn't
    be reasonably created (project_root missing).

    Idempotent: re-running on a repo that already has the line is a no-op.
    """
    if not project_root.is_dir():
        return False

    gitignore = project_root / ".gitignore"
    if gitignore.exists():
        try:
            existing = gitignore.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        # Match the bare line; tolerate surrounding whitespace.
        lines = {ln.strip() for ln in existing.splitlines()}
        if GITIGNORE_LINE in lines or f"/{GITIGNORE_LINE}" in lines:
            return False
        # Preserve trailing newline behavior — append on its own line.
        suffix = "" if existing.endswith("\n") else "\n"
        gitignore.write_text(
            f"{existing}{suffix}{GITIGNORE_LINE}\n", encoding="utf-8"
        )
        return True

    # No .gitignore — create a minimal one. Init also calls this on fresh
    # repos; first-backup-on-existing-repo paths also land here when the
    # repo never had a .gitignore.
    gitignore.write_text(f"{GITIGNORE_LINE}\n", encoding="utf-8")
    return True


def list_backups(backups_dir: Path) -> list[Path]:
    """All backup snapshots in the dir, oldest first."""
    if not backups_dir.is_dir():
        return []
    return sorted(
        backups_dir.glob("selvedge-*.db"),
        key=lambda p: p.stat().st_mtime,
    )


def last_backup_time(backups_dir: Path) -> datetime | None:
    """When the most recent backup was taken (UTC), or None if no backups exist."""
    snaps = list_backups(backups_dir)
    if not snaps:
        return None
    newest = snaps[-1]
    return datetime.fromtimestamp(newest.stat().st_mtime, tz=timezone.utc)
