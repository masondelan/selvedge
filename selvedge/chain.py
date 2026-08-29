"""Tamper-evident hash chain for the Selvedge event log (``selvedge-chain/1``).

Implements design (d) from ``docs/design/tamper-evidence-proposal.md``: a
SHA-256 hash chain over an asymmetric cut of the ``events`` columns, stored in
a sidecar ``event_chain`` table so deleting an events row can never delete its
chain record.

The protected core is **every events column except** ``git_commit`` — 17
fields. ``git_commit`` is late-bound by the post-commit hook
(:meth:`selvedge.storage.SelvedgeStorage.backfill_git_commit` issues an
``UPDATE`` after the fact), sits outside the protected form on purpose, and is
independently checkable against the git object database. ``entity_path`` IS
protected; the one legitimate rewriter (``selvedge migrate-paths --apply``)
appends an ``amend`` boundary record that re-binds the rewritten rows.
``prune_events`` appends a ``tombstone`` boundary record in the same
transaction as its ``DELETE``, so a gated prune verifies clean while a silent
``sqlite3`` deletion of a chained row fails verification.

The chain does **not** cover the ``tool_calls`` telemetry table, and it makes
no claim about pre-genesis rows — including whether they still exist. Both
omissions are declared in :func:`chain_manifest` rather than left to be
discovered.

Canonicalization contract (version ``selvedge-chain/1``, §4 of the proposal):

  - UTF-8; one JSON object; keys sorted lexicographically by Unicode code
    point; compact separators (no insignificant whitespace).
  - Every protected value is a JSON string. Absent is the empty string;
    ``null`` never appears — SQL ``NULL`` (pre-migration rows) is coalesced
    to ``""`` **before** hashing.
  - ``metadata`` is hashed as the opaque stored string, never parsed and
    re-serialized. Callers must read the **raw column**, not the dict that
    ``_coalesce_event_nullables`` decodes on the read paths.
  - Escaping is exactly CPython's ``json.dumps(..., ensure_ascii=False)``:
    ``"`` and ``\\`` escaped, the C0 short forms (``\\b \\f \\n \\r \\t``),
    remaining C0 controls as lowercase ``\\u00xx``, and everything else —
    including all non-ASCII — emitted raw.
  - Control characters are preserved, not rejected; no Unicode normalization
    and no trimming happen at hash time. The digest covers the stored bytes.

Threat model, stated plainly: this detects casual and accidental modification
(a stray ``UPDATE``, a buggy code path, a silent row deletion, reordering or
insertion) and supports an independently verifiable export. It is **not**
proof against a motivated local attacker, who controls the file and can
recompute every digest — there is no key.

Future work (deliberately not in this release): a ``selvedge chain seal``
command that appends labelled ``chained_at_seal`` records for pre-genesis
rows so retroactive coverage is opt-in and never conflated with live
chaining, and a gap-acknowledge boundary record for downgrade-then-prune
recovery. See §5.2 and §11 of the proposal.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping

from .models import ChangeEvent

#: Version string of the canonicalization contract. Inside every link digest.
CANON_VERSION = "selvedge-chain/1"

#: The 17 protected columns — every ``events`` column except ``git_commit`` —
#: already in the required order (lexicographic by Unicode code point).
PROTECTED_FIELDS: tuple[str, ...] = (
    "agent",
    "change_type",
    "changeset_id",
    "constraint",
    "diff",
    "entity_path",
    "entity_type",
    "expires_when",
    "id",
    "metadata",
    "project",
    "reasoning",
    "revisit_after",
    "session_id",
    "stale_when",
    "supersedes",
    "timestamp",
)

# Sidecar table, deliberately NOT a versioned MIGRATIONS entry — a brand-new
# table added as ``CREATE TABLE IF NOT EXISTS`` (the ``path_migrations`` /
# ``embeddings`` precedent) so opening this DB with an older Selvedge doesn't
# trip verify's downgrade detector. AUTOINCREMENT on purpose: ``seq`` is
# inside every digest, so a reused rowid would be a correctness bug — the
# guarantee belongs in the schema, not in a comment.
CREATE_EVENT_CHAIN_SQL = """
CREATE TABLE IF NOT EXISTS event_chain (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,              -- event | genesis | amend | tombstone
    event_id   TEXT NOT NULL DEFAULT '',   -- '' for genesis/amend/tombstone
    core_hash  TEXT NOT NULL,
    prev_hash  TEXT NOT NULL DEFAULT '',
    chain_hash TEXT NOT NULL,
    canon      TEXT NOT NULL DEFAULT 'selvedge-chain/1',
    detail     TEXT NOT NULL DEFAULT '{}'  -- JSON; boundary-record payload
);
"""

CREATE_EVENT_CHAIN_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_chain_event_id
    ON event_chain(event_id) WHERE event_id != '';
"""

#: Verification page size. Read at call time (not bound as a default) so
#: tests can monkeypatch it to prove the pagination path is exercised.
VERIFY_PAGE_SIZE = 1000

#: Cap on individual failure messages carried back from a verification walk.
_MAX_FAILURE_DETAILS = 5

# The 17 protected columns spelled out for SQL, in PROTECTED_FIELDS order, so
# a fetched tuple zips positionally with the field list regardless of the
# connection's row factory. ``constraint`` is a SQL reserved word — quoted.
_PROTECTED_COLUMNS_SQL = (
    'agent, change_type, changeset_id, "constraint", diff, entity_path, '
    "entity_type, expires_when, id, metadata, project, reasoning, "
    "revisit_after, session_id, stale_when, supersedes, timestamp"
)

_INSERT_CHAIN_SQL = """
    INSERT INTO event_chain
        (seq, kind, event_id, core_hash, prev_hash, chain_hash, canon, detail)
    VALUES (?,?,?,?,?,?,?,?)
"""


# ---------------------------------------------------------------------------
# Canonicalization + digests
# ---------------------------------------------------------------------------


def canonical_json(obj: object) -> str:
    """Serialize ``obj`` to the ``selvedge-chain/1`` canonical JSON form.

    Exactly CPython's ``json.dumps`` with ``ensure_ascii=False``, sorted keys,
    and compact separators — which is the escaping table in §4.2 rule 6 of the
    proposal by construction: non-ASCII emitted raw, C0 controls escaped
    (short forms where they exist, lowercase ``\\u00xx`` otherwise), and no
    insignificant whitespace anywhere.
    """
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_hex(text: str) -> str:
    """Lowercase hex SHA-256 of ``text`` encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def core_hash_from_row(row: Mapping[str, object]) -> str:
    """Digest the 17-field protected core of one events row.

    ``row`` maps column names to stored values; any key outside
    :data:`PROTECTED_FIELDS` (notably ``git_commit``) is ignored, so the same
    call works on a full ``SELECT *`` row and on a narrowed one.

    Two hazards from §4.2 are handled here, on purpose:

      - **Rule 4:** SQL ``NULL`` (pre-migration rows) is coalesced to ``""``
        before hashing, matching the ``_coalesce_event_nullables`` read
        convention — a second implementation reading raw SQL without this
        step diverges on pre-v3 rows only.
      - **Rule 5:** ``metadata`` must be the raw stored TEXT. The caller is
        responsible for reading the raw column (never the decoded dict the
        read paths hand out); this function hashes whatever string it is
        given without parsing it.
    """
    core = {
        field: "" if row.get(field) is None else str(row.get(field))
        for field in PROTECTED_FIELDS
    }
    return _sha256_hex(canonical_json(core))


def core_hash_from_event(event: ChangeEvent) -> str:
    """Digest the protected core of a :class:`ChangeEvent` about to be stored.

    Field values are read straight off the (already normalized) dataclass —
    the same values ``SelvedgeStorage._event_row`` binds into the INSERT — so
    the digest covers exactly the bytes that land in the columns.
    ``event.git_commit`` is deliberately not read: the asymmetric cut.
    """
    return core_hash_from_row(
        {
            "agent": event.agent,
            "change_type": event.change_type,
            "changeset_id": event.changeset_id,
            "constraint": event.constraint,
            "diff": event.diff,
            "entity_path": event.entity_path,
            "entity_type": event.entity_type,
            "expires_when": event.expires_when,
            "id": event.id,
            "metadata": event.metadata,
            "project": event.project,
            "reasoning": event.reasoning,
            "revisit_after": event.revisit_after,
            "session_id": event.session_id,
            "stale_when": event.stale_when,
            "supersedes": event.supersedes,
            "timestamp": event.timestamp,
        }
    )


def core_hash_for_event_id(conn: sqlite3.Connection, event_id: str) -> str:
    """Digest the protected core of the stored events row ``event_id``.

    Reads the raw columns directly (never through the coalescing/decoding
    read paths — §4.2 rules 4 and 5). Returns ``""`` when no such row exists,
    so callers can treat absence without a second query.
    """
    row = conn.execute(
        f"SELECT {_PROTECTED_COLUMNS_SQL} FROM events WHERE id = ?",
        (event_id,),
    ).fetchone()
    if row is None:
        return ""
    return core_hash_from_row(dict(zip(PROTECTED_FIELDS, tuple(row), strict=True)))


def detail_core_hash(detail: Mapping[str, object]) -> str:
    """Digest a boundary record's ``detail`` payload — the record-kind digest.

    Used as the ``core`` half of the link digest for ``genesis`` / ``amend`` /
    ``tombstone`` records, which have no events row to hash.
    """
    return _sha256_hex(canonical_json(detail))


def link_hash(*, core: str, kind: str, prev: str, seq: int, canon: str = CANON_VERSION) -> str:
    """Digest one chain link per §4.3 of the proposal.

    ``seq`` is inside the digest deliberately: a record binds its own
    position, not only the prefix it extends, so a record cannot be lifted
    intact from one position to another.
    """
    payload = {
        "canon": canon,
        "core": core,
        "kind": kind,
        "prev": prev,
        "seq": str(seq),
    }
    return _sha256_hex(canonical_json(payload))


# ---------------------------------------------------------------------------
# Appending — always inside the caller's (BEGIN IMMEDIATE) transaction
# ---------------------------------------------------------------------------


def read_head(conn: sqlite3.Connection) -> tuple[int, str]:
    """Return ``(seq, chain_hash)`` of the newest chain record, or ``(0, "")``.

    A single ``ORDER BY seq DESC LIMIT 1`` on the AUTOINCREMENT primary key —
    O(log n). Callers appending must hold the write lock (``BEGIN IMMEDIATE``)
    before reading the head, or two writers could fork the chain (§7.1).
    """
    row = conn.execute(
        "SELECT seq, chain_hash FROM event_chain ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return 0, ""
    return int(row[0]), str(row[1])


def _append_record(
    conn: sqlite3.Connection,
    *,
    kind: str,
    core_hash: str,
    event_id: str = "",
    detail: str = "{}",
) -> int:
    """Append one chain record after the current head; return its ``seq``.

    ``seq`` is bound explicitly (head + 1) rather than left to AUTOINCREMENT
    so the value inside the digest is guaranteed to be the value stored — if
    a concurrent writer somehow slipped past the write lock, the PRIMARY KEY
    constraint fails loudly instead of silently forking the chain.
    """
    head_seq, head_hash = read_head(conn)
    seq = head_seq + 1
    digest = link_hash(core=core_hash, kind=kind, prev=head_hash, seq=seq)
    conn.execute(
        _INSERT_CHAIN_SQL,
        (seq, kind, event_id, core_hash, head_hash, digest, CANON_VERSION, detail),
    )
    return seq


def _already_chained_ids(conn: sqlite3.Connection, event_ids: list[str]) -> set[str]:
    """The subset of ``event_ids`` that already have an ``event`` chain record.

    Chunked ``IN`` lookups against the ``idx_chain_event_id`` partial index,
    staying under SQLite's bound-parameter limit for large importer batches.
    """
    found: set[str] = set()
    for start in range(0, len(event_ids), 500):
        batch = event_ids[start : start + 500]
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"SELECT event_id FROM event_chain "
            f"WHERE kind = 'event' AND event_id IN ({placeholders})",
            batch,
        ).fetchall()
        found.update(str(r[0]) for r in rows)
    return found


def append_event_records(conn: sqlite3.Connection, events: Iterable[ChangeEvent]) -> None:
    """Chain freshly inserted events, threading ``prev_hash`` in memory.

    ONE head read for the whole batch (§7.1) — the importer batch path gets
    cheaper relative to a per-event design. Must run inside the same
    ``BEGIN IMMEDIATE`` transaction as the events INSERT so the rows and
    their chain records land or roll back together.

    An event id that ALREADY has an ``event`` chain record is not re-chained.
    That happens legitimately: a gated prune deletes the events row but (by
    design) keeps its chain record, and a deterministic importer — the
    agent-trace path derives uuid5 ids — can later re-insert the same id.
    The original record remains the standing commitment for that id, and
    verification recomputes the re-inserted row against it, so re-importing
    identical content verifies clean while re-inserting DIFFERENT content
    under a committed id fails ``chain_intact`` — which is the honest
    outcome. (A second record would also violate the ``idx_chain_event_id``
    UNIQUE guarantee and abort the whole insert transaction.)
    """
    events = list(events)
    if not events:
        return
    already = _already_chained_ids(conn, [event.id for event in events])
    fresh = [event for event in events if event.id not in already]
    if not fresh:
        return
    head_seq, prev = read_head(conn)
    seq = head_seq
    rows: list[tuple[int, str, str, str, str, str, str, str]] = []
    for event in fresh:
        core = core_hash_from_event(event)
        seq += 1
        digest = link_hash(core=core, kind="event", prev=prev, seq=seq)
        rows.append((seq, "event", event.id, core, prev, digest, CANON_VERSION, "{}"))
        prev = digest
    conn.executemany(_INSERT_CHAIN_SQL, rows)


def append_genesis(
    conn: sqlite3.Connection,
    *,
    enabled_at: str,
    pre_chain_count: int,
    pre_chain_max_timestamp: str,
    selvedge_version: str,
) -> int:
    """Append the genesis record marking the moment the chain was enabled.

    Rows that predate genesis are *unchained, not invalid* — reported by the
    ``chain_coverage`` should-warn check, never fatal. Genesis refuses to
    make a claim it can't support: retroactively chaining pre-existing rows
    would look identical to a chain built live while proving something much
    weaker (§5.2).
    """
    detail = {
        "enabled_at": enabled_at,
        "pre_chain_count": pre_chain_count,
        "pre_chain_max_timestamp": pre_chain_max_timestamp,
        "selvedge_version": selvedge_version,
    }
    return _append_record(
        conn,
        kind="genesis",
        core_hash=detail_core_hash(detail),
        detail=canonical_json(detail),
    )


def append_amend(
    conn: sqlite3.Connection,
    *,
    field: str,
    rows: list[dict[str, str]],
    reason: str,
) -> int:
    """Append one covering ``amend`` record re-binding legitimately rewritten rows.

    ``rows`` is the full list of ``{"event_id": ..., "new_core_hash": ...}``
    bindings for this run — one covering record per operation (per §5.3 and
    the open-question-4 decision), with the row list in ``detail``.
    Verification treats the newest amend for an event id as its expected
    ``core_hash``, so the chain stays intact across a ``migrate-paths
    --apply`` without any row's original chain record being touched.
    """
    detail: dict[str, object] = {"field": field, "reason": reason, "rows": rows}
    return _append_record(
        conn,
        kind="amend",
        core_hash=detail_core_hash(detail),
        detail=canonical_json(detail),
    )


def append_tombstone(conn: sqlite3.Connection, *, cutoff: str, count: int) -> int:
    """Append a count-only ``tombstone`` accounting for a gated events prune.

    ``detail`` carries ``{cutoff, count, cumulative_count}`` — count-only by
    decision (§11 open question 3): which rows were pruned is not recorded,
    only how many, so verification treats up to ``cumulative_count`` absent
    chained rows as accounted for. ``count`` must cover CHAINED deletions
    only — pre-genesis rows leave no gap and no tombstone (§5.2), and
    counting them would bank allowance that later masks a silent deletion
    of chained rows. Must run in the same transaction as the ``DELETE`` it
    describes.
    """
    row = conn.execute(
        "SELECT detail FROM event_chain WHERE kind = 'tombstone' "
        "ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    prior = 0
    if row is not None:
        try:
            prior = int(json.loads(row[0]).get("cumulative_count", 0))
        except (ValueError, TypeError, json.JSONDecodeError):
            prior = 0
    detail = {"cutoff": cutoff, "count": count, "cumulative_count": prior + count}
    return _append_record(
        conn,
        kind="tombstone",
        core_hash=detail_core_hash(detail),
        detail=canonical_json(detail),
    )


# ---------------------------------------------------------------------------
# Verification — streaming, paginated, constant memory over the chain walk
# ---------------------------------------------------------------------------


def _table_present(conn: sqlite3.Connection) -> bool:
    """True when the ``event_chain`` sidecar table exists in this DB."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'event_chain'"
    ).fetchone()
    return row is not None


def _load_amend_map(conn: sqlite3.Connection) -> dict[str, str]:
    """Map each amended event id to its newest ``new_core_hash``.

    Amend records are rare (one covering record per ``migrate-paths --apply``
    run), so loading them up front keeps the main walk single-pass without
    breaking the bounded-memory posture. Walked in ``seq`` order so a later
    amend overrides an earlier one.
    """
    amended: dict[str, str] = {}
    for row in conn.execute(
        "SELECT detail FROM event_chain WHERE kind = 'amend' ORDER BY seq ASC"
    ):
        try:
            parsed = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            continue  # the walk itself reports the unparseable detail
        for binding in parsed.get("rows", []):
            event_id = str(binding.get("event_id", ""))
            new_core = str(binding.get("new_core_hash", ""))
            if event_id and new_core:
                amended[event_id] = new_core
    return amended


def verify_chain(conn: sqlite3.Connection, page_size: int | None = None) -> dict:
    """Walk the whole chain and recompute every digest. Streaming, paginated.

    Pages through ``event_chain`` with ``WHERE seq > ? ORDER BY seq LIMIT ?``,
    carrying one 64-char ``prev_hash`` plus a handful of counters across
    pages — never ``fetchall()`` on the table (§7.3). Per record it checks:

      - ``prev_hash`` matches the prior record's ``chain_hash`` and ``seq``
        is contiguous (a chain-row deletion becomes a visible gap);
      - the stored ``chain_hash`` matches its recomputed value;
      - for ``kind='event'``: the events row's recomputed ``core_hash``
        matches the expected one (the newest ``amend`` binding for that id,
        else the stored ``core_hash``); an absent events row is counted;
      - for boundary kinds: ``core_hash`` matches the digest of ``detail``,
        and tombstone ``cumulative_count`` arithmetic is consistent.

    After the walk, absent chained rows beyond the final tombstone
    ``cumulative_count`` are reported as unaccounted.

    Returns a dict with every field always populated: ``table_present``,
    ``records``, ``pages``, ``chained_events``, ``absent_events``,
    ``accounted_deletions``, ``amended_events``, ``failure_count``,
    ``failures`` (capped at 5, each naming the offending ``seq``), and
    ``intact``.
    """
    result = {
        "table_present": False,
        "records": 0,
        "pages": 0,
        "chained_events": 0,
        "absent_events": 0,
        "accounted_deletions": 0,
        "amended_events": 0,
        "failure_count": 0,
        "failures": [],
        "intact": True,
    }
    if not _table_present(conn):
        return result
    result["table_present"] = True

    amend_map = _load_amend_map(conn)
    result["amended_events"] = len(amend_map)

    page = page_size if page_size is not None else VERIFY_PAGE_SIZE
    failures: list[str] = []
    failure_count = 0

    def record_failure(message: str) -> None:
        nonlocal failure_count
        failure_count += 1
        if len(failures) < _MAX_FAILURE_DETAILS:
            failures.append(message)

    prev_seq = 0
    prev_hash = ""
    records = 0
    pages = 0
    chained_events = 0
    absent_events = 0
    cumulative_tombstoned = 0

    while True:
        rows = conn.execute(
            "SELECT seq, kind, event_id, core_hash, prev_hash, chain_hash, canon, detail "
            "FROM event_chain WHERE seq > ? ORDER BY seq LIMIT ?",
            (prev_seq, page),
        ).fetchall()
        if not rows:
            break
        pages += 1
        for raw in rows:
            seq = int(raw[0])
            kind = str(raw[1])
            event_id = str(raw[2])
            stored_core = str(raw[3])
            stored_prev = str(raw[4])
            stored_chain = str(raw[5])
            canon = str(raw[6])
            detail_text = str(raw[7])
            records += 1

            if seq != prev_seq + 1:
                record_failure(
                    f"seq {seq}: chain sequence jumps from {prev_seq} — "
                    f"chain record(s) missing"
                )
            if stored_prev != prev_hash:
                record_failure(
                    f"seq {seq}: prev_hash does not match the chain_hash at seq {prev_seq}"
                )
            recomputed_chain = link_hash(
                core=stored_core, kind=kind, prev=stored_prev, seq=seq, canon=canon
            )
            if recomputed_chain != stored_chain:
                record_failure(
                    f"seq {seq}: stored chain_hash does not match its recomputed value"
                )

            if kind == "event":
                chained_events += 1
                recomputed_core = core_hash_for_event_id(conn, event_id)
                if recomputed_core == "":
                    absent_events += 1
                else:
                    expected_core = amend_map.get(event_id, stored_core)
                    if recomputed_core != expected_core:
                        record_failure(
                            f"seq {seq}: core_hash mismatch for event {event_id} — "
                            f"a protected field was modified after commitment"
                        )
            else:
                try:
                    parsed_detail = json.loads(detail_text)
                except (TypeError, json.JSONDecodeError):
                    parsed_detail = None
                if parsed_detail is None:
                    record_failure(f"seq {seq}: unparseable detail on {kind} record")
                else:
                    if detail_core_hash(parsed_detail) != stored_core:
                        record_failure(
                            f"seq {seq}: {kind} record detail does not match its core_hash"
                        )
                    if kind == "tombstone":
                        try:
                            count = int(parsed_detail.get("count", 0))
                            cumulative = int(parsed_detail.get("cumulative_count", 0))
                        except (TypeError, ValueError):
                            count, cumulative = 0, -1
                        if cumulative != cumulative_tombstoned + count:
                            record_failure(
                                f"seq {seq}: tombstone cumulative_count {cumulative} "
                                f"does not equal prior cumulative "
                                f"{cumulative_tombstoned} + count {count}"
                            )
                        cumulative_tombstoned = max(cumulative, 0)

            prev_seq = seq
            prev_hash = stored_chain
        if len(rows) < page:
            break

    if absent_events > cumulative_tombstoned:
        unaccounted = absent_events - cumulative_tombstoned
        record_failure(
            f"{unaccounted} chained event row(s) absent from events and not "
            f"accounted for by tombstone cumulative_count "
            f"({absent_events} absent, {cumulative_tombstoned} tombstoned)"
        )

    result["records"] = records
    result["pages"] = pages
    result["chained_events"] = chained_events
    result["absent_events"] = absent_events
    result["accounted_deletions"] = cumulative_tombstoned
    result["failure_count"] = failure_count
    result["failures"] = failures
    result["intact"] = failure_count == 0
    return result


def chain_coverage(conn: sqlite3.Connection) -> dict:
    """Count events rows with no chain record — pre-genesis or downgrade-era rows.

    Returns a dict with every field always populated: ``table_present``,
    ``total_events``, ``unchained``, ``genesis_present``, and
    ``pre_chain_count`` (from the genesis detail, ``0`` when absent).
    Unchained rows are *expected* on every upgraded install — the
    ``chain_coverage`` check warns, never fails, and the chain makes no
    claim about pre-genesis rows, including whether they still exist.
    """
    total = int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    if not _table_present(conn):
        return {
            "table_present": False,
            "total_events": total,
            "unchained": total,
            "genesis_present": False,
            "pre_chain_count": 0,
        }
    unchained = int(
        conn.execute(
            "SELECT COUNT(*) FROM events e WHERE NOT EXISTS ("
            "  SELECT 1 FROM event_chain c"
            "  WHERE c.event_id = e.id AND c.event_id != ''"
            ")"
        ).fetchone()[0]
    )
    genesis_row = conn.execute(
        "SELECT detail FROM event_chain WHERE kind = 'genesis' ORDER BY seq ASC LIMIT 1"
    ).fetchone()
    genesis_present = genesis_row is not None
    pre_chain_count = 0
    if genesis_row is not None:
        try:
            pre_chain_count = int(json.loads(genesis_row[0]).get("pre_chain_count", 0))
        except (TypeError, ValueError, json.JSONDecodeError):
            pre_chain_count = 0
    return {
        "table_present": True,
        "total_events": total,
        "unchained": unchained,
        "genesis_present": genesis_present,
        "pre_chain_count": pre_chain_count,
    }


# ---------------------------------------------------------------------------
# Attestation manifest (SEP-3004 §2.7)
# ---------------------------------------------------------------------------


def chain_manifest() -> dict[str, str]:
    """Return the attestation manifest surfaced by ``selvedge verify --json``.

    Carries the four SEP-3004 §2.7 fields plus a ``coverage`` declaration so
    the omissions are stated in the data rather than discovered by a reader.
    Every value is a string, never null.
    """
    return {
        "storage_mechanism": (
            "SQLite sidecar table `event_chain` inside the Selvedge store — "
            "chain records are separate rows from `events` rows, so deleting "
            "an event cannot delete its chain record"
        ),
        "chain_algorithm": "SHA-256",
        "canonical_form_version": CANON_VERSION,
        "verification_procedure_ref": (
            "selvedge verify (selvedge.verify.run_checks) — the verifier "
            "ships in the selvedge package"
        ),
        "coverage": (
            "Protected core: every events column except git_commit, which is "
            "late-bound by the post-commit hook and independently checkable "
            "against git. The chain does NOT cover the tool_calls telemetry "
            "table, and makes no claim about pre-genesis rows, including "
            "whether they still exist."
        ),
    }
