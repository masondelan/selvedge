"""Bounded local-chain checks for a pinned prior-attempts read snapshot.

This reuses the standalone verifier's chain logic, not its unbounded full
SQLite/schema/telemetry audit. A successful check says only that the protected
event contents and chain are consistent in this snapshot. It cannot establish
truth, authorship, completeness or an external trust anchor. git_commit and
semantic similarity are outside the protected core.
"""

from __future__ import annotations

import sqlite3
from time import monotonic

from . import chain

# Hard input caps before loading chain details or hashing event contents.
MAX_RECORDS = 1000
MAX_BYTES = 8 * 1024 * 1024
# Best-effort CPU/SQL budget, not a wall-clock deadline: Python hashing and
# JSON parsing cannot be preempted. The row/byte caps bound that work instead.
MAX_SECONDS = 0.1
MAX_SQL_STEPS = 2_000_000


def _signal(status: str, reason: str) -> dict[str, str]:
    return {"status": status, "scope": "local_chain_snapshot", "reason": reason}


def _within_input_bounds(conn: sqlite3.Connection) -> bool:
    for table in ("events", "event_chain"):
        count = conn.execute(
            f"SELECT COUNT(*) FROM (SELECT 1 FROM {table} LIMIT ?)",
            (MAX_RECORDS + 1,),
        ).fetchone()[0]
        if count > MAX_RECORDS:
            return False
    size = 0
    for table, fields in (
        ("events", chain.PROTECTED_FIELDS),
        ("event_chain", ("kind", "event_id", "core_hash", "prev_hash",
                         "chain_hash", "canon", "detail")),
    ):
        lengths = " + ".join(
            f'length(CAST(COALESCE("{field}", \'\') AS BLOB))'
            for field in fields
        )
        size += conn.execute(f"SELECT COALESCE(SUM({lengths}), 0) FROM {table}").fetchone()[0]
        if size > MAX_BYTES:
            return False
    return True


def check_recall_integrity(conn: sqlite3.Connection) -> dict[str, str]:
    """Check a caller-owned read transaction, or report why trust is unknown.

    The caller must materialize recalled events and all their outcome evidence
    through this same transaction. No data or schema is written, no results
    are cached, and any interrupted/unavailable check remains unknown.
    """
    if not conn.in_transaction:
        return _signal("unknown", "snapshot_required")

    deadline = monotonic() + MAX_SECONDS
    steps = 0
    exhausted = False

    def stop() -> int:
        nonlocal steps, exhausted
        steps += 1000
        exhausted = steps > MAX_SQL_STEPS or monotonic() >= deadline
        return int(exhausted)

    conn.set_progress_handler(stop, 1000)
    try:
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_chain'"
        ).fetchone()
        if not present:
            return _signal("unknown", "chain_unavailable")
        if not _within_input_bounds(conn):
            return _signal("unknown", "budget_exceeded")
        # Unsupported formats must not inherit a positive result from the
        # current verifier merely because their digests are self-consistent.
        unsupported = conn.execute(
            "SELECT 1 FROM event_chain WHERE canon != ? OR canon IS NULL "
            "OR kind NOT IN ('event', 'genesis', 'amend', 'tombstone') "
            "OR kind IS NULL LIMIT 1", (chain.CANON_VERSION,),
        ).fetchone()
        if unsupported:
            return _signal("unknown", "unsupported_chain_format")
        result = chain.verify_chain(conn)
        if not result["table_present"] or not result["records"]:
            return _signal("unknown", "chain_unavailable")
        if not result["intact"]:
            return _signal("failed", "chain_check_failed")
        coverage = chain.chain_coverage(conn)
        if not coverage["genesis_present"] or coverage["unchained"]:
            return _signal("unknown", "incomplete_coverage")
        if monotonic() >= deadline:
            return _signal("unknown", "budget_exceeded")
        return _signal("intact", "checked")
    except Exception:
        # Verification is a defensive adjunct to recall. Never turn an
        # unavailable check into either an integrity pass or lost history.
        # Do not include raw database/exception text in agent-facing notices.
        return _signal("unknown", "budget_exceeded" if exhausted else "check_error")
    finally:
        conn.set_progress_handler(None, 0)


def integrity_notice(signal: dict[str, str]) -> str:
    """Explain a failed/unknown check without overstating recorded evidence."""
    if signal["status"] == "intact":
        return ""
    if signal["status"] == "failed":
        return (
            "Integrity check failed: recalled evidence may be unreliable. "
            "Review the local store with `selvedge verify`; exact confidence "
            "describes recorded provenance, not correctness."
        )
    reasons = {
        "budget_exceeded": "the bounded check reached its work limit",
        "chain_unavailable": "chain evidence is unavailable",
        "incomplete_coverage": "chain evidence does not cover the full store",
        "unsupported_chain_format": "the chain format is unsupported",
        "snapshot_required": "no pinned read snapshot is available",
        "check_error": "the check could not complete",
    }
    reason = reasons.get(signal["reason"], "the check could not complete")
    return (
        f"Integrity unknown: {reason}. Recalled evidence is unverified; "
        "run `selvedge verify` for the full local audit. Missing history is not approval."
    )
