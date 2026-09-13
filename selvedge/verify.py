"""
``selvedge verify`` — DB-correctness checks split into two tiers.

Layout mirrors ``selvedge doctor`` (a list of check dicts), but verify
operates on *data correctness* rather than *ambient state*:

  - corruption (``PRAGMA integrity_check``)
  - schema migrations match what the running version declares
  - per-row invariants on ``events`` and ``tool_calls``
  - the tamper-evidence hash chain recomputes end to end (v0.3.11 —
    ``chain_intact`` / ``chain_coverage``, backed by :mod:`selvedge.chain`)

Each check declares a tier in :data:`CHECK_TIERS`:

  - ``must_fail`` — non-zero exit when the check fails.
  - ``should_warn`` — print a warning, but exit 0 by default. Pass
    ``--strict`` to escalate. The intent is that ``verify`` can be wired
    into CI on day one without users having to set ``|| true`` the first
    time a recoverable warning fires.

The split is locked in by ``tests/test_verify.py`` — adding a new check
without updating that file (and its assertion that every check has a
tier) will trip CI.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import chain
from .migrations import MIGRATIONS, get_applied_versions, latest_version
from .models import VALID_CHANGE_TYPES

# The two tiers. Adding a new check id means adding it here too — the
# ``run_checks`` function asserts every emitted check id is categorized,
# so omissions surface immediately rather than as a silent "always warn".
CHECK_TIERS: dict[str, str] = {
    "integrity_check": "must_fail",
    "schema_migrations": "must_fail",
    "entity_path_non_empty": "must_fail",
    "change_type_valid": "must_fail",
    "timestamp_parseable": "must_fail",
    "tool_calls_integrity": "must_fail",
    "orphan_changeset_id": "should_warn",
    "missing_git_commit": "should_warn",
    # Tamper evidence (v0.3.11). The split is deliberate: nothing legitimate
    # produces a chain_intact failure — every legitimate mutation either
    # stays outside the protected core (git_commit) or appends a boundary
    # record (migrate-paths amend, prune tombstone) — so must_fail is
    # defensible. Unchained rows, by contrast, are EXPECTED on every
    # upgraded install, so chain_coverage warns and never breaks CI.
    "chain_intact": "must_fail",
    "chain_coverage": "should_warn",
}

# How far back ``git_commit`` is allowed to be empty before we warn. Mirrors
# the post-commit hook's backfill window in :meth:`SelvedgeStorage.backfill_git_commit`.
# Events newer than this are expected to still be unstamped — the hook
# hasn't fired yet.
GIT_COMMIT_BACKFILL_WINDOW = timedelta(minutes=60)


@dataclass(frozen=True)
class CheckResult:
    """One verify check's outcome."""

    id: str
    status: str  # PASS / WARN / FAIL
    detail: str

    @property
    def tier(self) -> str:
        return CHECK_TIERS[self.id]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tier": self.tier,
            "status": self.status,
            "detail": self.detail,
        }


def _ok(check_id: str, detail: str = "") -> CheckResult:
    return CheckResult(id=check_id, status="PASS", detail=detail)


def _fail(check_id: str, detail: str) -> CheckResult:
    # ``status`` is FAIL for must_fail checks and WARN for should_warn — that
    # mapping is applied here so callers don't have to know each tier.
    status = "FAIL" if CHECK_TIERS[check_id] == "must_fail" else "WARN"
    return CheckResult(id=check_id, status=status, detail=detail)


def _check_integrity(conn: sqlite3.Connection) -> CheckResult:
    """SQLite's own corruption check."""
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    # PRAGMA integrity_check returns a single "ok" row when the DB is clean.
    values = [r[0] for r in rows]
    if values == ["ok"]:
        return _ok("integrity_check", "PRAGMA integrity_check returned ok")
    return _fail(
        "integrity_check",
        f"PRAGMA integrity_check returned: {'; '.join(values)}",
    )


def _check_schema(conn: sqlite3.Connection) -> CheckResult:
    """``schema_migrations`` matches the ``MIGRATIONS`` tuple shipped here."""
    declared = {m.version for m in MIGRATIONS}
    try:
        applied = get_applied_versions(conn)
    except sqlite3.OperationalError as exc:
        return _fail("schema_migrations", f"could not read schema_migrations: {exc}")

    missing = sorted(declared - applied)
    extra = sorted(applied - declared)

    if missing and extra:
        return _fail(
            "schema_migrations",
            f"missing migration(s) {missing} AND unknown applied version(s) {extra} "
            f"(downgrade or fork)",
        )
    if extra:
        return _fail(
            "schema_migrations",
            f"applied set has unknown version(s) {extra} — this DB was last "
            f"opened by a newer Selvedge; downgrading is not supported",
        )
    if missing:
        return _fail(
            "schema_migrations",
            f"missing migration(s) {missing} — run any selvedge command to "
            f"trigger apply",
        )
    return _ok("schema_migrations", f"at v{latest_version()} (all {len(declared)} applied)")


def _check_entity_paths(conn: sqlite3.Connection) -> CheckResult:
    """Every event has a non-empty ``entity_path``."""
    row = conn.execute(
        "SELECT COUNT(*) FROM events WHERE entity_path IS NULL OR entity_path = ''"
    ).fetchone()
    bad = row[0]
    if bad == 0:
        return _ok("entity_path_non_empty", "no empty entity_path rows")
    return _fail(
        "entity_path_non_empty",
        f"{bad} event(s) have empty entity_path — storage invariant violated",
    )


def _check_change_types(conn: sqlite3.Connection) -> CheckResult:
    """Every ``events.change_type`` is in the declared enum."""
    rows = conn.execute("SELECT DISTINCT change_type FROM events").fetchall()
    present = {r[0] for r in rows}
    unknown = sorted(present - VALID_CHANGE_TYPES)
    if not unknown:
        return _ok("change_type_valid", f"{len(present)} distinct change_type(s), all valid")
    return _fail(
        "change_type_valid",
        f"unknown change_type value(s): {unknown} — must be one of {sorted(VALID_CHANGE_TYPES)}",
    )


def _check_timestamps(conn: sqlite3.Connection) -> CheckResult:
    """Every ``events.timestamp`` parses as ISO-8601 UTC."""
    bad: list[str] = []
    for row in conn.execute("SELECT id, timestamp FROM events"):
        ts = row[1]
        try:
            # ``utc_now_iso`` writes a trailing Z; tolerate both forms here.
            datetime.fromisoformat(ts.replace("Z", "+00:00") if ts else "")
        except (ValueError, AttributeError):
            bad.append(f"{row[0]}={ts!r}")
            if len(bad) >= 5:
                break
    if not bad:
        return _ok("timestamp_parseable", "all event timestamps parse")
    return _fail(
        "timestamp_parseable",
        f"unparseable timestamp(s) (showing up to 5): {', '.join(bad)}",
    )


def _check_tool_calls(conn: sqlite3.Connection) -> CheckResult:
    """``tool_calls`` rows have non-empty ``tool_name`` and a parseable timestamp."""
    bad_name = conn.execute(
        "SELECT COUNT(*) FROM tool_calls WHERE tool_name IS NULL OR tool_name = ''"
    ).fetchone()[0]
    bad_ts: list[str] = []
    for row in conn.execute("SELECT id, timestamp FROM tool_calls"):
        ts = row[1]
        try:
            datetime.fromisoformat(ts.replace("Z", "+00:00") if ts else "")
        except (ValueError, AttributeError):
            bad_ts.append(row[0])
            if len(bad_ts) >= 5:
                break
    problems = []
    if bad_name:
        problems.append(f"{bad_name} row(s) missing tool_name")
    if bad_ts:
        problems.append(f"{len(bad_ts)} row(s) with unparseable timestamp")
    if not problems:
        return _ok("tool_calls_integrity", "tool_calls table is well-formed")
    return _fail("tool_calls_integrity", "; ".join(problems))


def _check_orphan_changesets(conn: sqlite3.Connection) -> CheckResult:
    """
    ``changeset_id`` values that appear in only one event.

    Multi-event changesets are the expected shape — a singleton is either a
    one-off (fine) or a partial write that lost its siblings (suspicious).
    Warn-only; we cannot tell which from the store alone.
    """
    row = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT changeset_id
            FROM events
            WHERE changeset_id != ''
            GROUP BY changeset_id
            HAVING COUNT(*) = 1
        )
        """
    ).fetchone()
    singletons = row[0]
    if singletons == 0:
        return _ok("orphan_changeset_id", "no singleton changesets")
    return _fail(
        "orphan_changeset_id",
        f"{singletons} changeset_id value(s) with only one event — could be "
        f"partial writes or one-off events",
    )


def _check_missing_git_commit(conn: sqlite3.Connection) -> CheckResult:
    """Events past the backfill window still missing ``git_commit``."""
    cutoff = (datetime.now(timezone.utc) - GIT_COMMIT_BACKFILL_WINDOW).isoformat()
    # Normalize cutoff to the same canonical form used by writes (``...Z``).
    cutoff_z = cutoff.replace("+00:00", "Z")
    row = conn.execute(
        "SELECT COUNT(*) FROM events WHERE git_commit = '' AND timestamp < ?",
        (cutoff_z,),
    ).fetchone()
    unstamped = row[0]
    if unstamped == 0:
        return _ok("missing_git_commit", "all events older than the backfill window are stamped")
    return _fail(
        "missing_git_commit",
        f"{unstamped} event(s) older than the {GIT_COMMIT_BACKFILL_WINDOW} backfill "
        f"window have no git_commit — install the post-commit hook with "
        f"`selvedge install-hook`",
    )


def _check_chain_intact(conn: sqlite3.Connection) -> CheckResult:
    """Recompute the tamper-evidence chain end to end (v0.3.11).

    Fails when a chained row's recomputed ``core_hash`` differs from the
    committed one, when ``prev_hash`` threading or ``seq`` contiguity breaks,
    or when a chained events row is absent without tombstone accounting.
    Streaming and paginated — see :func:`selvedge.chain.verify_chain`.
    """
    res = chain.verify_chain(conn)
    if not res["table_present"] or res["records"] == 0:
        return _ok(
            "chain_intact",
            "no chain records yet — chain not enabled on this store",
        )
    if res["intact"]:
        detail = (
            f"{res['records']} chain record(s) verified, "
            f"{res['chained_events']} chained event(s)"
        )
        if res["absent_events"]:
            detail += (
                f"; {res['absent_events']} pruned row(s) accounted for by tombstones"
            )
        return _ok("chain_intact", detail)
    shown = "; ".join(res["failures"])
    extra = res["failure_count"] - len(res["failures"])
    if extra > 0:
        shown += f" (+{extra} more)"
    return _fail("chain_intact", shown)


def _check_chain_coverage(conn: sqlite3.Connection) -> CheckResult:
    """Events rows with no chain record — unchained, not invalid (v0.3.11).

    Expected on every install that predates the chain: pre-genesis rows and
    rows written by a downgraded Selvedge have no chain record. Warn-only so
    upgraders' CI doesn't break on day one.
    """
    cov = chain.chain_coverage(conn)
    if cov["unchained"] == 0:
        return _ok(
            "chain_coverage",
            f"all {cov['total_events']} event row(s) have chain records",
        )
    return _fail(
        "chain_coverage",
        f"{cov['unchained']} of {cov['total_events']} event row(s) have no chain "
        f"record (genesis pre_chain_count={cov['pre_chain_count']}) — unchained, "
        f"not invalid: written before the chain was enabled or by an older "
        f"Selvedge. The chain makes no claim about pre-genesis rows, including "
        f"whether they still exist.",
    )


def run_checks(db_path: Path) -> list[CheckResult]:
    """
    Run every verify check and return them in display order.

    A check failure inside the runner (e.g. SQL error) is captured as a FAIL
    on that check so the rest still run — verify's job is to give the user
    a full picture in one shot, like ``doctor``.
    """
    results: list[CheckResult] = []

    if not db_path.is_file():
        # No DB file means nothing to verify. Treat as a single must-fail row
        # so CI gates trip rather than silently passing on a non-existent DB.
        results.append(
            _fail("integrity_check", f"DB file does not exist at {db_path}")
        )
        return results

    conn = sqlite3.connect(str(db_path))
    try:
        for check_fn in (
            _check_integrity,
            _check_schema,
            _check_entity_paths,
            _check_change_types,
            _check_timestamps,
            _check_tool_calls,
            _check_orphan_changesets,
            _check_missing_git_commit,
            _check_chain_intact,
            _check_chain_coverage,
        ):
            try:
                results.append(check_fn(conn))
            except sqlite3.Error as exc:
                # An individual check blowing up shouldn't kill the rest.
                # We synthesize a FAIL on the integrity tier for whichever
                # check id the function would have emitted — but since we
                # don't know that id here, fall back to a generic must_fail
                # row tied to integrity_check (the tier matters; the label
                # is informational).
                results.append(
                    CheckResult(
                        id="integrity_check",
                        status="FAIL",
                        detail=f"{check_fn.__name__} raised sqlite error: {exc}",
                    )
                )
    finally:
        conn.close()

    # Lock in the invariant that every emitted check has a known tier.
    for r in results:
        assert r.id in CHECK_TIERS, f"check {r.id!r} has no tier in CHECK_TIERS"

    return results


def exit_code(results: list[CheckResult], strict: bool = False) -> int:
    """
    Translate check results into the verify command's exit code.

    Default mode: must_fail FAILs trip the gate; should_warn WARNs do not.
    ``strict=True`` escalates any WARN to a failure too.
    """
    for r in results:
        if r.status == "FAIL":
            return 1
        if strict and r.status == "WARN":
            return 1
    return 0
