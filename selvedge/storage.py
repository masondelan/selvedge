"""SQLite storage layer for Selvedge."""

import functools
import json
import logging
import re
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypeVar

from .migrations import apply_migrations
from .models import ChangeEvent
from .timeutil import normalize_timestamp, resolve_revisit_due, utc_now_iso

logger = logging.getLogger(__name__)

# How long SQLite's internal busy handler will wait for a lock before giving up,
# in milliseconds. Set via PRAGMA on every connection. WAL mode handles most
# concurrent reads + a single writer cleanly; this timeout covers brief
# contention windows during write-write conflicts.
_BUSY_TIMEOUT_MS = 5_000

# Application-level retry on top of the C-level busy_timeout. Handles the
# rare cases where the busy handler can't engage (e.g. snapshot-isolation
# conflicts in WAL mode that surface as `database is locked` even after
# the busy_timeout expires).
_RETRY_MAX_ATTEMPTS = 5
_RETRY_INITIAL_BACKOFF = 0.05  # seconds
_RETRY_BACKOFF_MULTIPLIER = 2.0
_RETRY_BACKOFF_MAX = 1.0  # cap individual sleeps

T = TypeVar("T")


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id           TEXT PRIMARY KEY,
    timestamp    TEXT NOT NULL,
    entity_type  TEXT NOT NULL DEFAULT 'other',
    entity_path  TEXT NOT NULL,
    change_type  TEXT NOT NULL,
    diff         TEXT NOT NULL DEFAULT '',
    reasoning    TEXT NOT NULL DEFAULT '',
    agent        TEXT NOT NULL DEFAULT '',
    session_id   TEXT NOT NULL DEFAULT '',
    git_commit   TEXT NOT NULL DEFAULT '',
    project      TEXT NOT NULL DEFAULT '',
    changeset_id TEXT NOT NULL DEFAULT '',
    metadata     TEXT NOT NULL DEFAULT '{}',
    -- Active memory v1 (v0.3.8, migration v3). NULLABLE on purpose — pre-v3
    -- rows read back as NULL; new writes store '' for "absent". `expires_when`
    -- ships now but its evaluator is deferred to v0.3.11.
    revisit_after TEXT,
    expires_when  TEXT
);
"""

CREATE_TOOL_CALLS_SQL = """
CREATE TABLE IF NOT EXISTS tool_calls (
    id          TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    entity_path TEXT NOT NULL DEFAULT '',
    success     INTEGER NOT NULL DEFAULT 1,
    error_msg   TEXT NOT NULL DEFAULT '',
    agent       TEXT NOT NULL DEFAULT ''
);
"""

# One audit row per `selvedge migrate-paths --apply` run (v0.3.7). Makes the
# entity-path re-canonicalization operation visible to `selvedge doctor` and
# to future releases, and keeps it reversible by inspection — `detail` holds
# the JSON list of {from, to} rewrites the run performed. Introduced as a
# plain ``CREATE TABLE IF NOT EXISTS`` (the same way ``tool_calls`` was added)
# rather than a versioned migration, since it's a brand-new table, not an
# alteration of an existing one — so opening a v0.3.7 DB with an older
# Selvedge doesn't trip the doctor's downgrade detector.
CREATE_PATH_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS path_migrations (
    id             TEXT PRIMARY KEY,
    timestamp      TEXT NOT NULL,
    rows_scanned   INTEGER NOT NULL DEFAULT 0,
    rows_rewritten INTEGER NOT NULL DEFAULT 0,
    collisions     INTEGER NOT NULL DEFAULT 0,
    agent          TEXT NOT NULL DEFAULT '',
    detail         TEXT NOT NULL DEFAULT '{}'
);
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_entity_path   ON events(entity_path);",
    "CREATE INDEX IF NOT EXISTS idx_timestamp     ON events(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_project       ON events(project);",
    "CREATE INDEX IF NOT EXISTS idx_change_type   ON events(change_type);",
    "CREATE INDEX IF NOT EXISTS idx_tc_tool_name  ON tool_calls(tool_name);",
    "CREATE INDEX IF NOT EXISTS idx_tc_timestamp  ON tool_calls(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_pm_timestamp  ON path_migrations(timestamp);",
]

# Schema migrations are now declared in selvedge.migrations as an explicit,
# versioned list with bootstrap detection for pre-versioning databases. Each
# migration runs at most once per database, recorded in ``schema_migrations``.


# All LIKE patterns use this escape character so that user-supplied input
# containing '_' or '%' isn't interpreted as a wildcard. Without this,
# `search("user_id")` would match `userXid`, `userYid`, etc., and any
# entity name containing an underscore (which is most of them) would
# return false positives.
_LIKE_ESCAPE = "\\"


def _escape_like(s: str) -> str:
    """Escape LIKE wildcards in user input. Pair with ``ESCAPE '\\'`` in SQL."""
    return (
        s.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
         .replace("_", _LIKE_ESCAPE + "_")
         .replace("%", _LIKE_ESCAPE + "%")
    )


# Entity-path canonicalization (v0.3.7) -------------------------------------
#
# A single deterministic chokepoint so that ``src/auth.py::login`` and
# ``./src/auth.py::login`` resolve to the SAME entity instead of silently
# splitting history. Wired into ``_normalize_for_storage`` so EVERY write —
# the MCP ``log_change`` tool, the ``selvedge log`` CLI command, and the
# migration importers (which batch through ``log_event_batch``) — stores a
# canonical path. Nothing writes an un-canonicalized ``entity_path`` after
# this lands.
_LEADING_DOT_SLASH_RE = re.compile(r"^(?:\./)+")
_MULTI_SLASH_RE = re.compile(r"/{2,}")


def canonicalize_entity_path(entity_path: str) -> str:
    """Return the canonical form of an ``entity_path``.

    Deterministic and idempotent. Applies, in order:

      1. trim surrounding whitespace,
      2. normalize separators to ``/`` (Windows-style ``\\`` becomes ``/``),
      3. collapse runs of slashes (``a//b`` -> ``a/b``),
      4. strip any number of leading ``./`` segments (``././src`` -> ``src``).

    **Case is preserved on purpose.** Filesystems differ — macOS and Windows
    are case-insensitive by default, but most Linux filesystems are
    case-sensitive — so silently lowercasing would collapse genuinely
    distinct entities (``Users`` vs ``users``) on case-sensitive hosts.
    Case-collisions are surfaced as a ``selvedge doctor`` warning instead of
    being merged away. The ``::`` symbol separator and dotted column paths
    are left untouched (only slash separators are normalized).

    A non-empty input never canonicalizes to the empty string: if the rules
    above would strip everything (e.g. ``"./"``), the trimmed original is
    returned so a write can't end up with an empty ``entity_path``.
    """
    trimmed = entity_path.strip()
    canonical = trimmed.replace("\\", "/")
    canonical = _MULTI_SLASH_RE.sub("/", canonical)
    canonical = _LEADING_DOT_SLASH_RE.sub("", canonical)
    return canonical or trimmed


def _parse_iso(ts: str) -> datetime:
    """Parse a stored canonical UTC timestamp into an aware ``datetime``.

    Stored timestamps always carry the ``Z`` suffix (see ``timeutil``), so
    this is the inverse used for proximity-window math in
    :meth:`SelvedgeStorage.get_prior_attempts`.
    """
    s = ts[:-1] + "+00:00" if ts.endswith(("Z", "z")) else ts
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# Change types that represent the removal/teardown of an entity. An "attempt"
# (any non-removal event) is inferred as *reverted* when one of these follows
# it on the same path — the v0.3.7 add->remove proximity heuristic that stands
# in until explicit ``reject``/``revert`` change_types ship in v0.3.11.
_REMOVAL_CHANGE_TYPES: frozenset[str] = frozenset({"remove", "delete", "index_remove"})

# Read tools whose calls count as "this entity is in active use" for the
# v0.3.8 stale-decisions active-use weighting. A decision aging past its
# revisit_after only surfaces if the entity has actually been looked at (or
# its changeset kept moving) — pure age alone never surfaces. Recorded in the
# local-only ``tool_calls`` telemetry by both the MCP tools and the CLI.
_ACTIVE_USE_TOOLS: frozenset[str] = frozenset({"blame", "diff", "prior_attempts"})


def _paths_related(a: str, b: str) -> bool:
    """True if two entity paths are the same entity or a dotted-prefix pair.

    Mirrors the storage prefix convention (``X`` covers ``X.col``) in both
    directions, so a ``blame users`` tool-call counts as active use of a
    ``users.email`` decision and vice versa. Boundary is the literal ``.`` so
    ``users`` doesn't spuriously match ``users_audit``.
    """
    if a == b:
        return True
    return a.startswith(b + ".") or b.startswith(a + ".")


def _stale_reason_text(signals: list[str]) -> str:
    """Render the active-use signals into a one-line, deterministic summary.

    Pure templated assembly — the human-readable half of the ``stale_decisions``
    contract, with no LLM hop. Returns the empty string for no signals (which
    never happens on a surfaced row, but keeps the field always-present).
    """
    phrases = {
        "queried": "the entity was queried (blame/diff/prior_attempts) after the decision",
        "changeset_activity": "its changeset kept moving with later sibling changes",
    }
    parts = [phrases[s] for s in signals if s in phrases]
    if not parts:
        return ""
    return "past its revisit date and still active — " + "; ".join(parts) + "."


def _coalesce_event_nullables(row: dict) -> dict:
    """Coalesce the nullable active-memory columns (v0.3.8) to ``""`` in place.

    ``revisit_after`` / ``expires_when`` are NULLABLE, so rows written before
    migration v3 read back as ``NULL``. This keeps every read path on the
    "every field always populated, never null" convention — so
    diff / history / search / changeset / prior_attempts agree with
    blame / stale_decisions instead of leaking ``null`` for a pre-v3 row.
    """
    for col in ("revisit_after", "expires_when"):
        if row.get(col) is None:
            row[col] = ""
    return row


def _is_locked_error(exc: BaseException) -> bool:
    """Return True if ``exc`` represents a transient SQLite lock contention."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _retry_on_locked(fn: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator: retry on SQLite ``database is locked`` / ``busy`` errors
    with exponential backoff. Other exceptions propagate immediately.

    The PRAGMA ``busy_timeout`` set on each connection handles the common
    case at the C level; this decorator is defense in depth for snapshot
    conflicts in WAL mode that escape the busy handler.

    Safe under retry: SQLite returns ``locked``/``busy`` *before* the
    write is applied, so re-running the wrapped method does not produce
    duplicate inserts.
    """

    @functools.wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> T:
        backoff = _RETRY_INITIAL_BACKOFF
        for attempt in range(1, _RETRY_MAX_ATTEMPTS + 1):
            try:
                return fn(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                if not _is_locked_error(exc):
                    raise
                if attempt == _RETRY_MAX_ATTEMPTS:
                    logger.error(
                        "selvedge.storage: database still locked after %d attempts; giving up",
                        attempt,
                    )
                    raise
                logger.warning(
                    "selvedge.storage: database busy on attempt %d/%d, retrying in %.2fs",
                    attempt,
                    _RETRY_MAX_ATTEMPTS,
                    backoff,
                )
                time.sleep(backoff)
                backoff = min(backoff * _RETRY_BACKOFF_MULTIPLIER, _RETRY_BACKOFF_MAX)
        # Unreachable — the loop either returns or raises on the final attempt
        raise RuntimeError("retry loop exited without returning")

    return wrapper


def _open_connection(db_path: Path) -> sqlite3.Connection:
    """
    Open a SQLite connection tuned for Selvedge.

    Caller is responsible for closing — prefer :meth:`SelvedgeStorage._session`
    which handles commit/rollback/close as a context manager.
    """
    conn = sqlite3.connect(str(db_path), timeout=_BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except sqlite3.OperationalError:
        # WAL mode unsupported on some filesystems (e.g. network mounts).
        # Fall back to default DELETE journal mode — still fully functional.
        logger.debug("selvedge.storage: WAL mode unavailable; using default journal mode")
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS};")
    return conn


class SelvedgeStorage:
    """Thread-safe SQLite-backed event store."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """
        Return a raw connection. Prefer :meth:`_session` which manages the
        transaction and connection lifecycle correctly.

        Retained for backward compatibility — direct use leaks connections
        unless the caller explicitly closes them.
        """
        return _open_connection(self.db_path)

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        """
        Context manager that yields a connection and guarantees
        ``commit-on-success / rollback-on-error / always-close``.

        Use for every read or write — fixes the connection-leak that
        ``with self._connect() as conn`` had (sqlite3.Connection's own
        context manager handles the transaction but does NOT close the
        connection on exit).
        """
        conn = _open_connection(self.db_path)
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """
        Create the base tables and run any pending schema migrations.

        Base tables are idempotent (``CREATE TABLE IF NOT EXISTS``); the
        migration runner uses a separate ``schema_migrations`` ledger so
        every additive change after v0.1.0 runs exactly once per DB.
        """
        with self._session() as conn:
            conn.execute(CREATE_TABLE_SQL)
            conn.execute(CREATE_TOOL_CALLS_SQL)
            conn.execute(CREATE_PATH_MIGRATIONS_SQL)
            for idx_sql in CREATE_INDEXES_SQL:
                conn.execute(idx_sql)
        # Migrations open their own connection and manage transactions
        # per-migration so a partial failure leaves the DB in a known state.
        with self._session() as conn:
            apply_migrations(conn)
            # The revisit_after index is created here — AFTER migrations — so
            # the column is guaranteed to exist on both fresh DBs (added by
            # CREATE_TABLE_SQL) and upgraded ones (added by migration v3). It
            # can't live in CREATE_INDEXES_SQL, which runs before migrations.
            #
            # PARTIAL on purpose: it indexes only rows that actually carry a
            # revisit date, which is the exact predicate get_stale_decisions
            # filters on. That keeps the index tiny AND makes building it
            # instant on a freshly-migrated multi-million-event DB where every
            # existing row is NULL (a full index would scan all N rows).
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_revisit_after "
                "ON events(revisit_after) "
                "WHERE revisit_after IS NOT NULL AND revisit_after != ''"
            )

    # ------------------------------------------------------------------
    # Write — change events
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_for_storage(event: ChangeEvent) -> ChangeEvent:
        """Canonicalize the entity_path and normalize the timestamp pre-insert.

        This is the single write chokepoint for entity-path canonicalization
        (v0.3.7): both :meth:`log_event` and :meth:`log_event_batch` route
        through here, so the MCP tool, the CLI, and the importers all store
        canonical paths without each having to remember to call
        :func:`canonicalize_entity_path` themselves.
        """
        event.entity_path = canonicalize_entity_path(event.entity_path)
        try:
            event.timestamp = normalize_timestamp(event.timestamp)
        except (ValueError, TypeError):
            event.timestamp = utc_now_iso()
        return event

    @staticmethod
    def _event_row(event: ChangeEvent) -> tuple:
        return (
            event.id, event.timestamp, event.entity_type,
            event.entity_path, event.change_type, event.diff,
            event.reasoning, event.agent, event.session_id,
            event.git_commit, event.project, event.changeset_id,
            event.metadata, event.revisit_after, event.expires_when,
        )

    @_retry_on_locked
    def log_event(self, event: ChangeEvent) -> ChangeEvent:
        """Persist a ChangeEvent and return it (with id/timestamp set)."""
        self._normalize_for_storage(event)
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO events
                    (id, timestamp, entity_type, entity_path, change_type,
                     diff, reasoning, agent, session_id, git_commit, project,
                     changeset_id, metadata, revisit_after, expires_when)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                self._event_row(event),
            )
        return event

    @_retry_on_locked
    def log_event_batch(self, events: Iterable[ChangeEvent]) -> list[ChangeEvent]:
        """
        Persist multiple ChangeEvents in a single transaction.

        Significantly faster than calling :meth:`log_event` in a loop
        when importing large migration histories — one connection, one
        commit, one fsync. Also makes the import atomic: either all
        events land or none do.
        """
        events = list(events)
        if not events:
            return events
        rows = [self._event_row(self._normalize_for_storage(e)) for e in events]
        with self._session() as conn:
            conn.executemany(
                """
                INSERT INTO events
                    (id, timestamp, entity_type, entity_path, change_type,
                     diff, reasoning, agent, session_id, git_commit, project,
                     changeset_id, metadata, revisit_after, expires_when)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                rows,
            )
        return events

    @_retry_on_locked
    def log_rename(
        self,
        old_path: str,
        new_path: str,
        *,
        entity_type: str = "other",
        diff: str = "",
        reasoning: str = "",
        agent: str = "",
        session_id: str = "",
        git_commit: str = "",
        project: str = "",
        changeset_id: str = "",
    ) -> list[ChangeEvent]:
        """Record a rename as the dual-event pattern (v0.3.7).

        Emits a ``rename`` event on ``old_path`` and a ``create`` event on
        ``new_path`` whose ``metadata.renamed_from`` records the canonical old
        path. This is the same shape the SQL DDL / Alembic importers already
        emit internally, so a later ``blame`` / ``diff`` on the new path
        surfaces the rename history instead of returning empty. Both paths run
        through the shared write chokepoint, so both land canonicalized.

        Returns the two stored events ``[rename, create]``, oldest first.
        """
        canonical_old = canonicalize_entity_path(old_path)
        rename_event = ChangeEvent(
            entity_path=old_path,
            change_type="rename",
            entity_type=entity_type,
            diff=diff,
            reasoning=reasoning,
            agent=agent,
            session_id=session_id,
            git_commit=git_commit,
            project=project,
            changeset_id=changeset_id,
        )
        create_event = ChangeEvent(
            entity_path=new_path,
            change_type="create",
            entity_type=entity_type,
            diff=diff,
            reasoning=reasoning,
            agent=agent,
            session_id=session_id,
            git_commit=git_commit,
            project=project,
            changeset_id=changeset_id,
            metadata=json.dumps({"renamed_from": canonical_old}),
        )
        return self.log_event_batch([rename_event, create_event])

    # ------------------------------------------------------------------
    # Write — tool call telemetry (local only, never networked)
    # ------------------------------------------------------------------

    @_retry_on_locked
    def backfill_git_commit(self, commit_hash: str, window_minutes: int = 60) -> int:
        """
        Backfill ``git_commit`` on recent events that don't have one yet.

        Finds events logged within the last ``window_minutes`` minutes whose
        ``git_commit`` field is empty and sets it to ``commit_hash``.

        Default window widened to 60 minutes (was 10) so longer-running
        agent sessions still get their events stamped after a commit lands.

        Called automatically by the post-commit git hook installed via
        ``selvedge install-hook``.

        Returns:
            Number of events updated.
        """
        cutoff = normalize_timestamp(
            (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat()
        )
        with self._session() as conn:
            cursor = conn.execute(
                "UPDATE events SET git_commit = ? WHERE git_commit = '' AND timestamp >= ?",
                (commit_hash, cutoff),
            )
            return cursor.rowcount

    def count_missing_git_commit(self, since: str = "") -> int:
        """
        Count events with no ``git_commit`` set.

        Used by ``selvedge status`` to nudge users toward installing the
        post-commit hook when events are piling up unstamped — events
        without a commit hash are hard to correlate with code later.
        """
        clauses = ["git_commit = ''"]
        params: list = []
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        sql = f"SELECT COUNT(*) FROM events WHERE {' AND '.join(clauses)}"
        with self._session() as conn:
            return conn.execute(sql, params).fetchone()[0]

    @_retry_on_locked
    def record_tool_call(
        self,
        tool_name: str,
        entity_path: str = "",
        success: bool = True,
        error_msg: str = "",
        agent: str = "",
    ) -> None:
        """
        Record a single MCP tool invocation for coverage analysis.

        This is local-only telemetry — nothing leaves the machine.
        Use ``get_tool_stats()`` or ``selvedge stats`` to view coverage.

        ``agent`` enables the per-agent breakdown in ``selvedge stats`` —
        catches the case where one agent (e.g. claude-code) is well
        instrumented but another (e.g. cursor) isn't logging changes.
        Older callers passing only the original five positional args
        keep working: ``agent`` defaults to ''.
        """
        try:
            with self._session() as conn:
                conn.execute(
                    """
                    INSERT INTO tool_calls
                        (id, timestamp, tool_name, entity_path,
                         success, error_msg, agent)
                    VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        str(uuid.uuid4()),
                        utc_now_iso(),
                        tool_name,
                        entity_path,
                        int(success),
                        error_msg,
                        agent,
                    ),
                )
        except Exception:
            # Telemetry must never crash the tool that called it. Log so the
            # failure is visible if SELVEDGE_LOG_LEVEL=DEBUG, but swallow.
            logger.exception("selvedge.storage: failed to record tool call %r", tool_name)

    def get_last_tool_call_timestamp(self) -> str | None:
        """
        Return the most recent ``tool_calls`` timestamp, or None if empty.

        Used by ``selvedge doctor`` as a proxy for "is the agent actually
        wired up to this DB" — a long gap (or no entries at all) usually
        means the MCP server isn't connected to the right database.
        """
        with self._session() as conn:
            row = conn.execute(
                "SELECT timestamp FROM tool_calls ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else None

    def count_tool_calls(self) -> int:
        """Total rows in the ``tool_calls`` table.

        Used by ``selvedge doctor`` to drive the oversized-table WARN row
        introduced in v0.3.6. Exposed as its own method so doctor tests
        can stub the count via monkeypatch without inserting 100k rows.
        """
        with self._session() as conn:
            return conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]

    @_retry_on_locked
    def prune_tool_calls(self, cutoff: str) -> int:
        """Delete ``tool_calls`` rows older than ``cutoff`` (exclusive).

        ``cutoff`` is a UTC ISO timestamp; rows with ``timestamp < cutoff``
        are removed. Returns the number of rows deleted.

        Wraps with :func:`_retry_on_locked` so a concurrent ``log_change``
        insert can't lose the prune to a transient ``database is locked``
        in WAL mode.

        v0.3.6 only prunes ``tool_calls``; the events-table prune path
        lands in v0.3.10 alongside ``.selvedge/config.toml`` and an
        explicit ``SELVEDGE_DESTRUCTIVE=1`` gate.
        """
        with self._session() as conn:
            cursor = conn.execute(
                "DELETE FROM tool_calls WHERE timestamp < ?",
                (cutoff,),
            )
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Entity-path migration (v0.3.7 — `selvedge migrate-paths`)
    # ------------------------------------------------------------------

    @_retry_on_locked
    def recanonicalize_paths(self, *, apply: bool = False, agent: str = "") -> dict:
        """Re-canonicalize every stored ``entity_path`` (v0.3.7 backfill).

        Scans the events table, computes the canonical form of each path, and
        reports the rows whose stored value differs. With ``apply=False`` (the
        ``selvedge migrate-paths`` dry-run default) nothing is written — the
        caller gets the planned rewrites plus the *collisions report*:
        pre-canonicalization paths that converge to the same canonical value,
        i.e. histories that the migration would MERGE. With ``apply=True`` the
        rewrites land in one transaction and a single audit row is appended to
        ``path_migrations``.

        Idempotent on the event data: re-running after an ``apply`` rewrites
        zero rows because every path is already canonical. (Each ``apply`` run
        still records its own ``path_migrations`` audit row — the trail is the
        point.)

        Returns a report dict with ``applied``, ``rows_scanned``,
        ``rows_to_rewrite``, ``rows_rewritten`` (0 on a dry run), the
        ``rewrites`` list (``{id, from, to}``), and the ``collisions`` list
        (``{canonical, paths}``).
        """
        with self._session() as conn:
            rows = conn.execute("SELECT id, entity_path FROM events").fetchall()
            rows_scanned = len(rows)

            rewrites: list[dict] = []
            # canonical value -> set of distinct original paths feeding into it
            convergence: dict[str, set[str]] = {}
            for r in rows:
                original = r["entity_path"]
                canonical = canonicalize_entity_path(original)
                convergence.setdefault(canonical, set()).add(original)
                if canonical != original:
                    rewrites.append(
                        {"id": r["id"], "from": original, "to": canonical}
                    )

            # A collision is any canonical value reached from >1 DISTINCT
            # original path — applying the migration merges those histories.
            collisions = [
                {"canonical": canonical, "paths": sorted(paths)}
                for canonical, paths in sorted(convergence.items())
                if len(paths) > 1
            ]

            if apply:
                for rw in rewrites:
                    conn.execute(
                        "UPDATE events SET entity_path = ? WHERE id = ?",
                        (rw["to"], rw["id"]),
                    )
                conn.execute(
                    """
                    INSERT INTO path_migrations
                        (id, timestamp, rows_scanned, rows_rewritten,
                         collisions, agent, detail)
                    VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        str(uuid.uuid4()),
                        utc_now_iso(),
                        rows_scanned,
                        len(rewrites),
                        len(collisions),
                        agent,
                        json.dumps({"rewrites": rewrites, "collisions": collisions}),
                    ),
                )

        return {
            "applied": apply,
            "rows_scanned": rows_scanned,
            "rows_to_rewrite": len(rewrites),
            "rows_rewritten": len(rewrites) if apply else 0,
            "rewrites": rewrites,
            "collisions": collisions,
        }

    def get_last_path_migration(self) -> dict | None:
        """Return the most recent ``path_migrations`` audit row, or None.

        Used by ``selvedge doctor`` to surface when ``migrate-paths`` last ran
        and what it rewrote — keeping the entity-path migration visible.
        """
        with self._session() as conn:
            row = conn.execute(
                "SELECT id, timestamp, rows_scanned, rows_rewritten, collisions, "
                "agent FROM path_migrations ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Read — change events
    # ------------------------------------------------------------------

    def get_entity_history(
        self, entity_path: str, limit: int = 20
    ) -> list[dict]:
        """
        Return change history for an entity or entity prefix.
        e.g. "users" matches "users", "users.email", "users.created_at".
        """
        prefix_pattern = f"{_escape_like(entity_path)}.%"
        with self._session() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE entity_path = ? OR entity_path LIKE ? ESCAPE '\\'
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (entity_path, prefix_pattern, limit),
            ).fetchall()
        return [_coalesce_event_nullables(dict(r)) for r in rows]

    def get_blame(self, entity_path: str) -> dict | None:
        """Return the most recent event for an exact entity path."""
        with self._session() as conn:
            row = conn.execute(
                """
                SELECT * FROM events
                WHERE entity_path = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (entity_path,),
            ).fetchone()
        return _coalesce_event_nullables(dict(row)) if row else None

    def get_history(
        self,
        since: str = "",
        entity_path: str = "",
        project: str = "",
        changeset_id: str = "",
        limit: int = 50,
    ) -> list[dict]:
        """Return filtered history across all entities."""
        clauses = ["1=1"]
        params: list = []

        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if entity_path:
            clauses.append("(entity_path = ? OR entity_path LIKE ? ESCAPE '\\')")
            params.extend([entity_path, f"{_escape_like(entity_path)}.%"])
        if project:
            clauses.append("project = ?")
            params.append(project)
        if changeset_id:
            clauses.append("changeset_id = ?")
            params.append(changeset_id)

        params.append(limit)
        sql = f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY timestamp DESC LIMIT ?"

        with self._session() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_coalesce_event_nullables(dict(r)) for r in rows]

    def get_changeset(self, changeset_id: str) -> list[dict]:
        """
        Return all events belonging to a changeset, oldest first.

        A changeset groups related changes made as part of a single feature
        or task (e.g. "add stripe billing" touching multiple entities).
        """
        with self._session() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE changeset_id = ? ORDER BY timestamp ASC",
                (changeset_id,),
            ).fetchall()
        return [_coalesce_event_nullables(dict(r)) for r in rows]

    def list_changesets(self, project: str = "", since: str = "") -> list[dict]:
        """
        Return a summary of all changesets: id, event count, agent, time range.

        Each entry has: changeset_id, event_count, agent (most common),
        first_event, last_event, project.
        """
        clauses = ["changeset_id != ''"]
        params: list = []
        if project:
            clauses.append("project = ?")
            params.append(project)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)

        where = " AND ".join(clauses)
        sql = f"""
            SELECT
                changeset_id,
                COUNT(*)          AS event_count,
                MIN(timestamp)    AS first_event,
                MAX(timestamp)    AS last_event,
                project
            FROM events
            WHERE {where}
            GROUP BY changeset_id
            ORDER BY last_event DESC
        """
        with self._session() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search across entity_path, diff, and reasoning."""
        pattern = f"%{_escape_like(query)}%"
        with self._session() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE entity_path LIKE ? ESCAPE '\\'
                   OR diff        LIKE ? ESCAPE '\\'
                   OR reasoning   LIKE ? ESCAPE '\\'
                   OR change_type LIKE ? ESCAPE '\\'
                   OR agent       LIKE ? ESCAPE '\\'
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (pattern, pattern, pattern, pattern, pattern, limit),
            ).fetchall()
        return [_coalesce_event_nullables(dict(r)) for r in rows]

    def count(self) -> int:
        """Total number of change events logged."""
        with self._session() as conn:
            return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def find_case_collisions(self) -> list[dict]:
        """Return groups of distinct entity_paths that differ only by case.

        Powers the ``selvedge doctor`` case-collision watch (v0.3.7). Because
        canonicalization preserves case on purpose, sibling paths like
        ``Users.email`` and ``users.email`` coexist as separate entities —
        correct on case-sensitive Linux, but usually a typo on
        case-insensitive macOS/Windows. Surfacing them keeps the divergence
        visible without silently merging it. Each group is
        ``{"lower": <lowercased path>, "paths": [<distinct originals>]}``.
        """
        with self._session() as conn:
            rows = conn.execute(
                "SELECT DISTINCT entity_path FROM events"
            ).fetchall()
        groups: dict[str, set[str]] = {}
        for r in rows:
            path = r["entity_path"]
            groups.setdefault(path.lower(), set()).add(path)
        return [
            {"lower": lower, "paths": sorted(paths)}
            for lower, paths in sorted(groups.items())
            if len(paths) > 1
        ]

    def get_prior_attempts(
        self,
        *,
        entity_path: str = "",
        query: str = "",
        min_confidence: str = "proximity_high",
        window_minutes: int = 10080,
        limit: int = 20,
    ) -> list[dict]:
        """Return prior change attempts on an entity, each with inferred outcome.

        Storage layer for the v0.3.7 ``prior_attempts`` wedge. Two lookup
        modes (``entity_path`` takes precedence when both are given):

          - ``entity_path`` — events at that canonical path (exact + ``.``
            prefix, so a table query also sees its columns).
          - ``query`` — free-text substring across reasoning / diff /
            entity_path, to find candidate entities by description.

        For each candidate path the timeline is walked oldest-first and every
        non-removal event (the "attempt") is paired with the next removal
        event (``remove`` / ``delete`` / ``index_remove``) on the same path. A
        paired attempt reports ``outcome="reverted"``; an unpaired one
        ``outcome="active"``. ``confidence`` is ``proximity_high`` when the
        attempt->removal gap is within ``window_minutes``, else
        ``proximity_low`` (active attempts are always ``proximity_low``).

        Conservative-recall posture: ``min_confidence="proximity_high"`` (the
        default) returns only the high-confidence "tried then rejected"
        signal; pass ``"proximity_low"`` to include the noisy tail. An empty
        list is the preferred answer over a false positive.

        Each result is an event dict plus three always-present keys —
        ``outcome``, ``confidence``, ``outcome_reasoning`` (the reverting
        event's reasoning, or ``""`` while active). Removal events are folded
        into the attempt they close and never appear on their own. Newest
        first, capped at ``limit``.
        """
        with self._session() as conn:
            if entity_path:
                canonical = canonicalize_entity_path(entity_path)
                prefix = f"{_escape_like(canonical)}.%"
                rows = conn.execute(
                    """
                    SELECT * FROM events
                    WHERE entity_path = ? OR entity_path LIKE ? ESCAPE '\\'
                    ORDER BY entity_path ASC, timestamp ASC
                    """,
                    (canonical, prefix),
                ).fetchall()
            elif query:
                pattern = f"%{_escape_like(query)}%"
                path_rows = conn.execute(
                    """
                    SELECT DISTINCT entity_path FROM events
                    WHERE entity_path LIKE ? ESCAPE '\\'
                       OR diff        LIKE ? ESCAPE '\\'
                       OR reasoning   LIKE ? ESCAPE '\\'
                    """,
                    (pattern, pattern, pattern),
                ).fetchall()
                paths = [r["entity_path"] for r in path_rows]
                if not paths:
                    return []
                placeholders = ",".join("?" for _ in paths)
                rows = conn.execute(
                    f"SELECT * FROM events WHERE entity_path IN ({placeholders}) "
                    "ORDER BY entity_path ASC, timestamp ASC",
                    paths,
                ).fetchall()
            else:
                return []

        # Group the (already path/time-ordered) rows by entity_path.
        by_path: dict[str, list[dict]] = {}
        for r in rows:
            by_path.setdefault(r["entity_path"], []).append(dict(r))

        window = timedelta(minutes=window_minutes)
        results: list[dict] = []
        for events in by_path.values():
            removals = [e for e in events if e["change_type"] in _REMOVAL_CHANGE_TYPES]
            for e in events:
                if e["change_type"] in _REMOVAL_CHANGE_TYPES:
                    continue  # removals surface via the attempt they close
                attempt_ts = _parse_iso(e["timestamp"])
                closing = next(
                    (
                        rm
                        for rm in removals
                        if _parse_iso(rm["timestamp"]) >= attempt_ts
                    ),
                    None,
                )
                if closing is None:
                    outcome, confidence, outcome_reasoning = (
                        "active",
                        "proximity_low",
                        "",
                    )
                else:
                    gap = _parse_iso(closing["timestamp"]) - attempt_ts
                    confidence = (
                        "proximity_high" if gap <= window else "proximity_low"
                    )
                    outcome = "reverted"
                    outcome_reasoning = closing["reasoning"]
                annotated = _coalesce_event_nullables(dict(e))
                annotated["outcome"] = outcome
                annotated["confidence"] = confidence
                annotated["outcome_reasoning"] = outcome_reasoning
                results.append(annotated)

        if min_confidence == "proximity_high":
            results = [r for r in results if r["confidence"] == "proximity_high"]

        # Newest-first, then cap.
        results.sort(key=lambda r: r["timestamp"], reverse=True)
        return results[:limit]

    def get_stale_decisions(
        self,
        *,
        now: str = "",
        entity_path: str = "",
        project: str = "",
        agent: str = "",
        limit: int = 20,
    ) -> list[dict]:
        """Return decisions whose ``revisit_after`` has passed AND are in active use.

        Storage layer for the v0.3.8 ``stale_decisions`` surface (date-based
        active memory). A candidate is any event carrying a non-empty
        ``revisit_after`` whose resolved due date (see
        :func:`selvedge.timeutil.resolve_revisit_due`) is at or before ``now``
        (defaults to the current UTC time).

        **Active-use weighting — pure age does NOT surface.** A due decision is
        returned only if there is an additional signal that the entity is still
        live, exactly one of:

          - ``queried`` — a ``blame`` / ``diff`` / ``prior_attempts`` tool-call
            on the entity (exact or dotted-prefix, either direction) recorded
            at or after the decision was logged; or
          - ``changeset_activity`` — the decision carries a ``changeset_id`` and
            a *later* sibling event shares it (the feature kept moving).

        This is the noise defense: an old-but-correct decision nobody touches
        never nags. Filterable by ``entity_path`` (exact + ``.`` prefix),
        ``project``, and ``agent``. Output is templated and deterministic — no
        LLM call. ``expires_when`` is ignored here; its evaluator lands in
        v0.3.11.

        Each result is the event dict (NULL columns coalesced to ``""``) plus
        four always-present keys: ``revisit_due`` (canonical UTC ISO),
        ``days_overdue`` (int, ``0`` on the due day), ``active_use_signals``
        (list of the signal names above), and ``stale_reason`` (a one-line
        human-readable summary). Most-overdue first, capped at ``limit``.
        """
        now_dt = (
            _parse_iso(normalize_timestamp(now)) if now else datetime.now(timezone.utc)
        )

        clauses = ["revisit_after IS NOT NULL", "revisit_after != ''"]
        params: list = []
        if entity_path:
            canonical = canonicalize_entity_path(entity_path)
            clauses.append("(entity_path = ? OR entity_path LIKE ? ESCAPE '\\')")
            params.extend([canonical, f"{_escape_like(canonical)}.%"])
        if project:
            clauses.append("project = ?")
            params.append(project)
        if agent:
            clauses.append("agent = ?")
            params.append(agent)
        where = " AND ".join(clauses)

        results: list[dict] = []
        with self._session() as conn:
            candidates = conn.execute(
                f"SELECT * FROM events WHERE {where} ORDER BY timestamp ASC",
                params,
            ).fetchall()
            if not candidates:
                return []

            # Active-use telemetry, fetched once: all entity-scoped read-tool
            # calls. Small in practice; matched in Python so the dotted-prefix
            # logic isn't tangled up in LIKE-escaping both directions.
            placeholders = ",".join("?" for _ in _ACTIVE_USE_TOOLS)
            tool_rows = conn.execute(
                f"SELECT entity_path, timestamp FROM tool_calls "
                f"WHERE tool_name IN ({placeholders}) AND entity_path != ''",
                tuple(sorted(_ACTIVE_USE_TOOLS)),
            ).fetchall()
            # Pre-parse tool-call timestamps to aware datetimes so the
            # at-or-after comparison is chronological, not lexicographic —
            # mixed-precision strings ('...00Z' vs '...00.5Z') would sort
            # wrong ('.' < 'Z'). Same posture as get_prior_attempts. The
            # tool_calls table stores the RAW agent-supplied path (unlike the
            # events table, which canonicalizes on write), so canonicalize it
            # here too — otherwise a query of './users.email' would never match
            # the canonical 'users.email' decision and the signal would be lost.
            # Bad rows are skipped rather than crashing the query.
            tool_calls: list[tuple[str, datetime]] = []
            for r in tool_rows:
                try:
                    tool_calls.append(
                        (canonicalize_entity_path(r["entity_path"]), _parse_iso(r["timestamp"]))
                    )
                except (ValueError, TypeError):
                    continue

            for row in candidates:
                ev = dict(row)
                try:
                    due = resolve_revisit_due(ev["timestamp"], ev["revisit_after"])
                except ValueError:
                    # Unparseable revisit_after — skip rather than crash the query.
                    continue
                if due > now_dt:
                    continue  # not due yet

                ev_ts = _parse_iso(ev["timestamp"])
                signals: list[str] = []
                # Signal 1: the entity was queried at/after the decision.
                if any(
                    tc_dt >= ev_ts and _paths_related(tc_path, ev["entity_path"])
                    for tc_path, tc_dt in tool_calls
                ):
                    signals.append("queried")
                # Signal 2: a later sibling shares the decision's changeset.
                if ev.get("changeset_id"):
                    sibling = conn.execute(
                        "SELECT 1 FROM events WHERE changeset_id = ? "
                        "AND timestamp > ? AND id != ? LIMIT 1",
                        (ev["changeset_id"], ev["timestamp"], ev["id"]),
                    ).fetchone()
                    if sibling is not None:
                        signals.append("changeset_activity")

                if not signals:
                    continue  # pure age — active-use weighting drops it

                annotated = {k: ("" if v is None else v) for k, v in ev.items()}
                annotated["revisit_due"] = normalize_timestamp(due.isoformat())
                annotated["days_overdue"] = max(0, (now_dt - due).days)
                annotated["active_use_signals"] = signals
                annotated["stale_reason"] = _stale_reason_text(signals)
                results.append(annotated)

        # Most overdue first (earliest due date), then cap.
        results.sort(key=lambda r: r["revisit_due"])
        return results[:limit]

    def summarize(
        self, *, since: str = "", project: str = "", top_n: int = 10
    ) -> dict:
        """Return aggregate activity counts for ``selvedge.aggregates.summary()``.

        Pure read. Powers the schema-versioned digest dataclass that v0.3.9's
        ``selvedge audit`` / ``digest`` will consume. Returns the total event
        count, the distinct changesets and agents touched in the window, and
        the top entities by event count (capped at ``top_n``).
        """
        clauses = ["1=1"]
        params: list = []
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if project:
            clauses.append("project = ?")
            params.append(project)
        where = " AND ".join(clauses)
        with self._session() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM events WHERE {where}", params
            ).fetchone()[0]
            changesets = [
                r[0]
                for r in conn.execute(
                    f"SELECT DISTINCT changeset_id FROM events WHERE {where} "
                    "AND changeset_id != '' ORDER BY changeset_id",
                    params,
                ).fetchall()
            ]
            agents = [
                r[0]
                for r in conn.execute(
                    f"SELECT DISTINCT agent FROM events WHERE {where} "
                    "AND agent != '' ORDER BY agent",
                    params,
                ).fetchall()
            ]
            top_entities = [
                {"entity_path": r["entity_path"], "event_count": r["cnt"]}
                for r in conn.execute(
                    f"SELECT entity_path, COUNT(*) AS cnt FROM events WHERE {where} "
                    "GROUP BY entity_path ORDER BY cnt DESC, entity_path ASC LIMIT ?",
                    [*params, top_n],
                ).fetchall()
            ]
        return {
            "total_events": total,
            "changesets": changesets,
            "agents": agents,
            "top_entities": top_entities,
        }

    # ------------------------------------------------------------------
    # Read — tool call telemetry
    # ------------------------------------------------------------------

    def get_tool_stats(self, since: str = "") -> dict:
        """
        Return tool call statistics for coverage analysis.

        Returns a dict with:
          - by_tool:              call count per tool name
          - by_agent:             {agent_name: {"total": int, "log_change": int,
                                                "ratio": float}}, sorted by
                                  total calls desc. Catches under-instrumented
                                  agents — e.g. claude-code is logging changes
                                  but cursor is only querying history.
          - total_calls:          total MCP tool invocations recorded
          - log_change_calls:     how many of those were log_change
          - log_change_ratio:     log_change / total (0.0–1.0)
          - missing_reasoning:    count of log_change events in the same window
                                  whose reasoning fails the quality validator
                                  (empty, too short, or generic placeholder).
                                  An agent that ignores the warnings shows up
                                  here.
          - recent:               10 most recent tool call records
        """
        clause = "WHERE timestamp >= ?" if since else ""
        params: list = [since] if since else []

        with self._session() as conn:
            rows = conn.execute(
                f"SELECT tool_name, COUNT(*) as cnt FROM tool_calls {clause} "
                "GROUP BY tool_name ORDER BY cnt DESC",
                params,
            ).fetchall()
            by_tool = {r["tool_name"]: r["cnt"] for r in rows}

            recent_rows = conn.execute(
                f"SELECT timestamp, tool_name, entity_path, success, error_msg, agent "
                f"FROM tool_calls {clause} ORDER BY timestamp DESC LIMIT 10",
                params,
            ).fetchall()
            recent = [dict(r) for r in recent_rows]

            # Per-agent breakdown: total calls and log_change calls.
            # Agents are reported under the literal string they passed —
            # empty agent rolls up into "(unknown)" so the breakdown
            # always sums to total_calls without surprises.
            agent_rows = conn.execute(
                f"SELECT agent, tool_name, COUNT(*) as cnt FROM tool_calls "
                f"{clause} GROUP BY agent, tool_name",
                params,
            ).fetchall()

            # Missing-reasoning count: scan log_change events in the same
            # window through the validator, tally the ones that fail.
            # Doing this at query time (rather than persisting a flag at
            # insert time) means the count works retroactively for events
            # logged before the v0.3.1 validator regex was fixed.
            reasoning_clause = "WHERE timestamp >= ?" if since else ""
            reasoning_rows = conn.execute(
                f"SELECT reasoning FROM events {reasoning_clause}",
                params,
            ).fetchall()

        # Aggregate per-agent.
        from .validation import check_reasoning_quality

        per_agent: dict[str, dict[str, float]] = {}
        for r in agent_rows:
            agent_name = r["agent"] or "(unknown)"
            entry = per_agent.setdefault(
                agent_name, {"total": 0, "log_change": 0, "ratio": 0.0}
            )
            entry["total"] = int(entry["total"]) + int(r["cnt"])
            if r["tool_name"] == "log_change":
                entry["log_change"] = int(entry["log_change"]) + int(r["cnt"])
        for entry in per_agent.values():
            t = int(entry["total"])
            entry["ratio"] = (
                round(int(entry["log_change"]) / t, 3) if t > 0 else 0.0
            )
        # Sort by total desc for stable, useful display order.
        by_agent = dict(
            sorted(per_agent.items(), key=lambda kv: int(kv[1]["total"]), reverse=True)
        )

        missing_reasoning = sum(
            1 for r in reasoning_rows if check_reasoning_quality(r["reasoning"] or "")
        )

        total = sum(by_tool.values())
        log_calls = by_tool.get("log_change", 0)

        return {
            "by_tool": by_tool,
            "by_agent": by_agent,
            "total_calls": total,
            "log_change_calls": log_calls,
            "log_change_ratio": round(log_calls / total, 3) if total > 0 else 0.0,
            "missing_reasoning": missing_reasoning,
            "recent": recent,
        }
