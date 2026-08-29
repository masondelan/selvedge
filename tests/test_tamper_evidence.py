"""
Tests for the tamper-evident event chain (v0.3.11, ``selvedge.chain``).

Follows the ~20-test plan in ``docs/design/tamper-evidence-proposal.md`` §10:
canonicalization (with the §4.4 known-answer vector frozen), chain
construction, the actual attack cases, legitimate mutations with boundary
records, and genesis/coverage semantics. All tests use ``tmp_path`` +
``SELVEDGE_DB``; none touch the network or the real store.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest
from click.testing import CliRunner

from selvedge import chain
from selvedge import storage as storage_mod
from selvedge import verify as verify_mod
from selvedge.cli import cli
from selvedge.models import ChangeEvent
from selvedge.prune import run_events_prune
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


def _by_id(results):
    return {r.id: r for r in results}


def _chain_rows(db_path: Path) -> list[tuple]:
    with sqlite3.connect(str(db_path)) as con:
        return con.execute(
            "SELECT seq, kind, event_id, core_hash, prev_hash, chain_hash, detail "
            "FROM event_chain ORDER BY seq ASC"
        ).fetchall()


# ---------------------------------------------------------------------------
# Canonicalization — the §4.4 known-answer vector, frozen
# ---------------------------------------------------------------------------

# The 413-byte core preimage from §4.4, byte for byte. A refactor that changes
# the canonical bytes fails here first.
_KAT_CORE_PREIMAGE = (
    '{"agent":"claude","change_type":"add","changeset_id":"cs-1","constraint":"",'
    '"diff":"+ email TEXT NOT NULL\\n","entity_path":"users.email",'
    '"entity_type":"column","expires_when":"",'
    '"id":"0f8fad5b-d9cb-469f-a165-70867728950e","metadata":"{}",'
    '"project":"selvedge","reasoning":"Signups need a contact address.",'
    '"revisit_after":"","session_id":"sess-1","stale_when":"","supersedes":"",'
    '"timestamp":"2026-08-10T12:00:00Z"}'
)
_KAT_CORE_HASH = "e45b2fcd0e026f5f97310431d8e5788bea4f6be2c77143f906c6c54827a20881"
_KAT_LINK_PREIMAGE = (
    '{"canon":"selvedge-chain/1",'
    '"core":"e45b2fcd0e026f5f97310431d8e5788bea4f6be2c77143f906c6c54827a20881",'
    '"kind":"event","prev":"","seq":"1"}'
)
_KAT_CHAIN_HASH = "eeca977a914561bc85d299d7aed05c5bf208fccac26db7e59af8aa16e531f5f2"

_KAT_ROW = {
    "agent": "claude",
    "change_type": "add",
    "changeset_id": "cs-1",
    "constraint": "",
    "diff": "+ email TEXT NOT NULL\n",
    "entity_path": "users.email",
    "entity_type": "column",
    "expires_when": "",
    "id": "0f8fad5b-d9cb-469f-a165-70867728950e",
    "metadata": "{}",
    "project": "selvedge",
    "reasoning": "Signups need a contact address.",
    "revisit_after": "",
    "session_id": "sess-1",
    "stale_when": "",
    "supersedes": "",
    "timestamp": "2026-08-10T12:00:00Z",
}


def test_known_answer_vector_frozen():
    """§4.4 worked vector: preimage bytes and both digests, frozen."""
    canonical = chain.canonical_json(
        {f: _KAT_ROW[f] for f in chain.PROTECTED_FIELDS}
    )
    assert canonical == _KAT_CORE_PREIMAGE
    assert len(canonical.encode("utf-8")) == 413
    assert chain.core_hash_from_row(_KAT_ROW) == _KAT_CORE_HASH

    link_preimage = chain.canonical_json(
        {
            "canon": "selvedge-chain/1",
            "core": _KAT_CORE_HASH,
            "kind": "event",
            "prev": "",
            "seq": "1",
        }
    )
    assert link_preimage == _KAT_LINK_PREIMAGE
    assert len(link_preimage.encode("utf-8")) == 137
    assert (
        chain.link_hash(core=_KAT_CORE_HASH, kind="event", prev="", seq=1)
        == _KAT_CHAIN_HASH
    )


def test_key_order_is_derived_not_chosen():
    """Digest is independent of dict insertion order — keys sort lexicographically."""
    scrambled = dict(reversed(list(_KAT_ROW.items())))
    scrambled["git_commit"] = "abc123"  # ignored: outside the protected core
    assert chain.core_hash_from_row(scrambled) == _KAT_CORE_HASH


def test_escaping_is_pinned_to_the_rule6_table():
    """`"`, `\\`, newline, tab, U+0001, and non-ASCII escape exactly per §4.2 rule 6."""
    value = 'caf\u00e9\n\t"x"\\y\u0001'
    got = chain.canonical_json({"diff": value})
    assert got == '{"diff":"café\\n\\t\\"x\\"\\\\y\\u0001"}'
    # Non-ASCII emitted raw — never \uXXXX-escaped.
    assert "\\u00e9" not in got and "café" in got
    # Remaining C0 controls use lowercase hex.
    assert "\\u0001" in got
    # The lowercase rule needs LETTER-bearing code points to be non-vacuous:
    # U+0001's hex digits are all numeric, so an uppercase (%04X) emitter
    # produces the identical string. U+000B / U+001A / U+001F pin the case.
    controls = "\x0b\x1a\x1f"
    got_controls = chain.canonical_json({"diff": controls})
    assert got_controls == '{"diff":"\\u000b\\u001a\\u001f"}'
    assert "\\u000B" not in got_controls and "\\u001F" not in got_controls


def test_non_ascii_vector_frozen():
    """A second known-answer vector with non-ASCII text, locking rule 6 end to end."""
    row = {
        "agent": "ansel",
        "change_type": "modify",
        "changeset_id": "",
        "constraint": "",
        "diff": "- caf\u00e9\n+ caf\u00e9 au lait\n",
        "entity_path": "menu.caf\u00e9",
        "entity_type": "column",
        "expires_when": "",
        "id": "11111111-2222-3333-4444-555555555555",
        "metadata": "{}",
        "project": "bistro",
        "reasoning": "R\u00e9nommage \u2014 accents preserved.\n",
        "revisit_after": "",
        "session_id": "s-2",
        "stale_when": "",
        "supersedes": "",
        "timestamp": "2026-08-10T13:00:00Z",
    }
    assert (
        chain.core_hash_from_row(row)
        == "ca9cf6eb58bcd721d7492721b57696578bb7272983f076f4939572ccb7ce033f"
    )


def test_sql_null_and_empty_string_hash_identically(db_path):
    """§4.2 rule 4 — the pre-v3-row hazard, pinned: NULL coalesces to "" pre-hash."""
    with_nulls = dict(_KAT_ROW)
    for col in ("revisit_after", "expires_when", "supersedes", "constraint", "stale_when"):
        with_nulls[col] = None
    assert chain.core_hash_from_row(with_nulls) == _KAT_CORE_HASH

    # And at the DB level: a raw row whose nullable columns are SQL NULL must
    # digest the same before and after those columns are set to ''.
    SelvedgeStorage(db_path)
    with sqlite3.connect(str(db_path)) as con:
        con.execute(
            "INSERT INTO events (id, timestamp, entity_type, entity_path, change_type) "
            "VALUES (?, ?, ?, ?, ?)",
            ("pre-v3", "2026-01-01T00:00:00Z", "column", "users.email", "add"),
        )
        con.commit()
        h_null = chain.core_hash_for_event_id(con, "pre-v3")
        con.execute(
            "UPDATE events SET revisit_after = '', expires_when = '', "
            "supersedes = '', \"constraint\" = '', stale_when = '' WHERE id = ?",
            ("pre-v3",),
        )
        con.commit()
        h_empty = chain.core_hash_for_event_id(con, "pre-v3")
    assert h_null == h_empty != ""


def test_metadata_is_hashed_as_the_raw_stored_string(db_path):
    """§4.2 rule 5 — the decoded-dict hazard, pinned: hash the raw column bytes."""
    s = SelvedgeStorage(db_path)
    raw_metadata = '{"a": 1, "b": "x"}'  # non-canonical spacing, on purpose
    event = s.log_event(
        ChangeEvent(entity_path="users.email", change_type="add", metadata=raw_metadata)
    )
    with sqlite3.connect(str(db_path)) as con:
        stored_core = con.execute(
            "SELECT core_hash FROM event_chain WHERE event_id = ?", (event.id,)
        ).fetchone()[0]
        recomputed = chain.core_hash_for_event_id(con, event.id)
    assert recomputed == stored_core

    # Re-serializing the decoded dict (what _coalesce_event_nullables hands
    # the read paths) produces DIFFERENT bytes and a different digest — a
    # canonicalizer reading the decoded dict would diverge exactly here.
    row = {f: getattr(event, f) for f in chain.PROTECTED_FIELDS}
    assert chain.core_hash_from_row(row) == stored_core
    row["metadata"] = chain.canonical_json(json.loads(raw_metadata))
    assert row["metadata"] != raw_metadata  # decode+re-serialize changed the bytes
    assert chain.core_hash_from_row(row) != stored_core


def test_git_commit_is_outside_the_protected_core(db_path):
    """§4.2 — the asymmetric cut, asserted: changing git_commit changes nothing."""
    event = ChangeEvent(entity_path="users.email", change_type="add")
    before = chain.core_hash_from_event(event)
    event.git_commit = "deadbeef"
    assert chain.core_hash_from_event(event) == before

    s = SelvedgeStorage(db_path)
    stored = s.log_event(ChangeEvent(entity_path="users.name", change_type="add"))
    with sqlite3.connect(str(db_path)) as con:
        h1 = chain.core_hash_for_event_id(con, stored.id)
        con.execute(
            "UPDATE events SET git_commit = 'cafe123' WHERE id = ?", (stored.id,)
        )
        con.commit()
        h2 = chain.core_hash_for_event_id(con, stored.id)
    assert h1 == h2


# ---------------------------------------------------------------------------
# Chain construction
# ---------------------------------------------------------------------------


def test_sequential_log_events_thread_the_chain(db_path):
    s = SelvedgeStorage(db_path)
    events = [
        s.log_event(ChangeEvent(entity_path=f"users.col{i}", change_type="add"))
        for i in range(5)
    ]
    rows = _chain_rows(db_path)
    assert [r[0] for r in rows] == [1, 2, 3, 4, 5, 6]
    assert rows[0][1] == "genesis" and rows[0][4] == ""  # seq 1, prev ""
    assert [r[1] for r in rows[1:]] == ["event"] * 5
    assert [r[2] for r in rows[1:]] == [e.id for e in events]
    for prior, current in zip(rows, rows[1:], strict=False):
        assert current[4] == prior[5]  # prev_hash == prior chain_hash
    with sqlite3.connect(str(db_path)) as con:
        assert chain.verify_chain(con)["intact"] is True


def test_batch_chains_in_insertion_order_one_transaction(db_path):
    s = SelvedgeStorage(db_path)
    events = s.log_event_batch(
        [ChangeEvent(entity_path=f"orders.c{i}", change_type="add") for i in range(4)]
    )
    rows = _chain_rows(db_path)
    assert [r[2] for r in rows[1:]] == [e.id for e in events]
    assert [r[0] for r in rows] == list(range(1, 6))
    with sqlite3.connect(str(db_path)) as con:
        res = chain.verify_chain(con)
    assert res["intact"] is True and res["chained_events"] == 4


def test_chain_failure_rolls_back_the_events_insert_too(db_path, monkeypatch):
    """§10 item 8, the 'one transaction' half, asserted structurally.

    ``append_event_records``' docstring requires it to run inside the same
    ``BEGIN IMMEDIATE`` transaction as the events INSERT. If chaining raises,
    the events rows must roll back with it — a refactor that commits events
    first and chains in a second transaction would leave committed rows with
    no chain record (a silent coverage WARN instead of a loud failure), and
    this test is what goes red.
    """
    s = SelvedgeStorage(db_path)

    def boom(conn, events):
        raise RuntimeError("chain append failed mid-batch")

    monkeypatch.setattr(chain, "append_event_records", boom)

    with pytest.raises(RuntimeError, match="mid-batch"):
        s.log_event_batch(
            [ChangeEvent(entity_path=f"x.col{i}", change_type="add") for i in range(3)]
        )
    with pytest.raises(RuntimeError, match="mid-batch"):
        s.log_event(ChangeEvent(entity_path="y.col", change_type="add"))

    with sqlite3.connect(str(db_path)) as con:
        assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        # Only genesis remains — no half-landed batch on either write path.
        kinds = [r[0] for r in con.execute("SELECT kind FROM event_chain")]
    assert kinds == ["genesis"]


def test_supersede_and_rename_append_and_never_touch_prior_rows(db_path):
    s = SelvedgeStorage(db_path)
    s.log_event(ChangeEvent(entity_path="users.email", change_type="add"))
    s.log_event(ChangeEvent(entity_path="users.email", change_type="remove"))
    before = _chain_rows(db_path)

    s.log_supersede("users.email", reasoning="the constraint no longer applies")
    s.log_rename("users.email", "users.contact_email")

    after = _chain_rows(db_path)
    # supersede appends 1, rename appends 2 (rename + create) — nothing edited.
    assert len(after) == len(before) + 3
    assert after[: len(before)] == before
    with sqlite3.connect(str(db_path)) as con:
        assert chain.verify_chain(con)["intact"] is True


def test_concurrent_writers_never_fork_the_chain(db_path):
    """The §7.1 gate: BEGIN IMMEDIATE serializes head reads across writers."""
    SelvedgeStorage(db_path)
    n_threads, per_thread = 8, 10
    errors: list[BaseException] = []

    def writer(thread_id: int) -> None:
        local = SelvedgeStorage(db_path)
        try:
            for i in range(per_thread):
                local.log_event(ChangeEvent(
                    entity_path=f"t{thread_id}.col{i}", change_type="add",
                ))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"writer errors: {errors!r}"
    rows = _chain_rows(db_path)
    seqs = [r[0] for r in rows]
    # Distinct, contiguous seq values: genesis + every event, no forks.
    assert seqs == list(range(1, n_threads * per_thread + 2))
    with sqlite3.connect(str(db_path)) as con:
        res = chain.verify_chain(con)
    assert res["intact"] is True
    assert res["chained_events"] == n_threads * per_thread


# ---------------------------------------------------------------------------
# Tamper detection — the actual attack cases
# ---------------------------------------------------------------------------


def test_reasoning_tamper_is_detected_and_names_the_seq(db_path):
    """The core case: a direct UPDATE of a protected field fails verify, by seq."""
    s = SelvedgeStorage(db_path)
    events = [
        s.log_event(ChangeEvent(
            entity_path=f"users.col{i}", change_type="add", reasoning=f"reason {i}",
        ))
        for i in range(5)
    ]
    third = events[2]
    with sqlite3.connect(str(db_path)) as con:
        con.execute(
            "UPDATE events SET reasoning = 'rewritten' WHERE id = ?", (third.id,)
        )
        con.commit()
        seq = con.execute(
            "SELECT seq FROM event_chain WHERE event_id = ?", (third.id,)
        ).fetchone()[0]

    results = verify_mod.run_checks(db_path)
    by_id = _by_id(results)
    assert by_id["chain_intact"].status == "FAIL"
    assert f"seq {seq}" in by_id["chain_intact"].detail
    assert verify_mod.exit_code(results) == 1


def test_silent_delete_without_tombstone_is_detected(db_path):
    s = SelvedgeStorage(db_path)
    events = [
        s.log_event(ChangeEvent(entity_path=f"users.col{i}", change_type="add"))
        for i in range(3)
    ]
    with sqlite3.connect(str(db_path)) as con:
        con.execute("DELETE FROM events WHERE id = ?", (events[1].id,))
        con.commit()

    by_id = _by_id(verify_mod.run_checks(db_path))
    assert by_id["chain_intact"].status == "FAIL"
    assert "not accounted" in by_id["chain_intact"].detail


def test_entity_path_repointing_attack_is_detected(db_path):
    """The §2a repointing attack — entity_path is INSIDE the protected core."""
    s = SelvedgeStorage(db_path)
    stored = s.log_event(ChangeEvent(
        entity_path="payments.card_number", change_type="remove",
        reasoning="rejected: PCI scope",
    ))
    with sqlite3.connect(str(db_path)) as con:
        con.execute(
            "UPDATE events SET entity_path = 'harmless.column' WHERE id = ?",
            (stored.id,),
        )
        con.commit()

    by_id = _by_id(verify_mod.run_checks(db_path))
    assert by_id["chain_intact"].status == "FAIL"


def test_editing_the_chain_table_does_not_defeat_verification(db_path):
    """Doctoring core_hash to match a doctored row still breaks the link digests."""
    s = SelvedgeStorage(db_path)
    stored = s.log_event(ChangeEvent(
        entity_path="users.email", change_type="add", reasoning="original",
    ))
    with sqlite3.connect(str(db_path)) as con:
        con.execute(
            "UPDATE events SET reasoning = 'doctored' WHERE id = ?", (stored.id,)
        )
        con.commit()
        doctored_core = chain.core_hash_for_event_id(con, stored.id)
        con.execute(
            "UPDATE event_chain SET core_hash = ? WHERE event_id = ?",
            (doctored_core, stored.id),
        )
        con.commit()

    by_id = _by_id(verify_mod.run_checks(db_path))
    assert by_id["chain_intact"].status == "FAIL"
    assert "chain_hash" in by_id["chain_intact"].detail


def test_hiding_an_event_wholesale_breaks_seq_contiguity(db_path):
    """§10 item 14, second prong: delete a mid-chain event AND its chain row.

    The per-record digests all still verify (nothing that remains was
    edited), so only the threading checks — seq contiguity and prev_hash —
    can catch this. This is the mutation-killer for removing either one.
    """
    s = SelvedgeStorage(db_path)
    events = [
        s.log_event(ChangeEvent(entity_path=f"users.col{i}", change_type="add"))
        for i in range(5)
    ]
    victim = events[2]
    with sqlite3.connect(str(db_path)) as con:
        seq = con.execute(
            "SELECT seq FROM event_chain WHERE event_id = ?", (victim.id,)
        ).fetchone()[0]
        con.execute("DELETE FROM events WHERE id = ?", (victim.id,))
        con.execute("DELETE FROM event_chain WHERE event_id = ?", (victim.id,))
        con.commit()

    results = verify_mod.run_checks(db_path)
    by_id = _by_id(results)
    assert by_id["chain_intact"].status == "FAIL"
    # The gap is named by its position, not just "chain broken".
    assert f"jumps from {seq - 1}" in by_id["chain_intact"].detail
    assert "missing" in by_id["chain_intact"].detail
    assert verify_mod.exit_code(results) == 1


def test_consistently_rechained_record_breaks_the_next_link(db_path):
    """§10 item 14: doctor a mid-chain events row and recompute BOTH of its
    record's digests consistently — the seq+1 record's ``prev_hash`` still
    exposes the edit. Verifies the threading check on its own: the doctored
    record's core_hash matches its row and its chain_hash recomputes clean.
    """
    s = SelvedgeStorage(db_path)
    events = [
        s.log_event(ChangeEvent(
            entity_path=f"users.col{i}", change_type="add", reasoning=f"reason {i}",
        ))
        for i in range(3)
    ]
    victim = events[1]
    with sqlite3.connect(str(db_path)) as con:
        con.execute(
            "UPDATE events SET reasoning = 'doctored' WHERE id = ?", (victim.id,)
        )
        seq, stored_prev = con.execute(
            "SELECT seq, prev_hash FROM event_chain WHERE event_id = ?",
            (victim.id,),
        ).fetchone()
        doctored_core = chain.core_hash_for_event_id(con, victim.id)
        doctored_chain = chain.link_hash(
            core=doctored_core, kind="event", prev=stored_prev, seq=seq
        )
        con.execute(
            "UPDATE event_chain SET core_hash = ?, chain_hash = ? WHERE seq = ?",
            (doctored_core, doctored_chain, seq),
        )
        con.commit()

    results = verify_mod.run_checks(db_path)
    by_id = _by_id(results)
    assert by_id["chain_intact"].status == "FAIL"
    assert (
        f"prev_hash does not match the chain_hash at seq {seq}"
        in by_id["chain_intact"].detail
    )
    assert verify_mod.exit_code(results) == 1


def test_backfill_git_commit_run_leaves_chain_intact(db_path):
    """NEGATIVE CONTROL — do not retire this test.

    ``backfill_git_commit`` UPDATEs rows after insert on every git commit;
    the asymmetric cut exists so that this exact, constant, legitimate
    mutation is NOT tampering. If this test fails, the design stopped
    solving the problem it was built for. (Named per the same reasoning as
    ``test_cron_footgun_yes_without_destructive_env_errors``.)
    """
    s = SelvedgeStorage(db_path)
    for i in range(3):
        s.log_event(ChangeEvent(entity_path=f"users.col{i}", change_type="add"))
    assert s.backfill_git_commit("abc123def") == 3

    by_id = _by_id(verify_mod.run_checks(db_path))
    assert by_id["chain_intact"].status == "PASS"
    assert by_id["chain_coverage"].status == "PASS"


# ---------------------------------------------------------------------------
# Legitimate mutation and boundary records
# ---------------------------------------------------------------------------


def test_migrate_paths_apply_appends_one_covering_amend(db_path):
    """Legacy (unchained) rows: amend + audit row appended, chain stays intact."""
    SelvedgeStorage(db_path)
    with sqlite3.connect(str(db_path)) as con:
        for i, p in enumerate(["./src/auth.py::login", "src//auth.py::login"]):
            con.execute(
                "INSERT INTO events (id, timestamp, entity_type, entity_path, change_type) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"legacy-{i}", f"2026-01-0{i + 1}T00:00:00Z", "function", p, "add"),
            )
        con.commit()

    s = SelvedgeStorage(db_path)
    report = s.recanonicalize_paths(apply=True)
    assert report["rows_rewritten"] == 2
    assert s.get_last_path_migration() is not None  # audit row still written

    amends = [r for r in _chain_rows(db_path) if r[1] == "amend"]
    assert len(amends) == 1  # ONE covering record per run
    detail = json.loads(amends[0][6])
    assert detail["field"] == "entity_path"
    assert {b["event_id"] for b in detail["rows"]} == {"legacy-0", "legacy-1"}

    by_id = _by_id(verify_mod.run_checks(db_path))
    assert by_id["chain_intact"].status == "PASS"


def test_amend_rebinds_chained_rows_after_a_canonicalization_change(db_path, monkeypatch):
    """A future canonicalization rule rewriting CHAINED rows must not read as tampering."""
    s = SelvedgeStorage(db_path)
    stored = s.log_event(ChangeEvent(entity_path="Users.Email", change_type="add"))

    # Simulate a canonicalization rule change: lowercase everything.
    monkeypatch.setattr(storage_mod, "canonicalize_entity_path", lambda p: p.lower())
    report = s.recanonicalize_paths(apply=True)
    assert report["rows_rewritten"] == 1

    with sqlite3.connect(str(db_path)) as con:
        new_path = con.execute(
            "SELECT entity_path FROM events WHERE id = ?", (stored.id,)
        ).fetchone()[0]
    assert new_path == "users.email"

    by_id = _by_id(verify_mod.run_checks(db_path))
    assert by_id["chain_intact"].status == "PASS"
    assert by_id["chain_coverage"].status == "PASS"


def test_gated_prune_appends_tombstone_and_still_verifies(db_path):
    s = SelvedgeStorage(db_path)
    for i in range(3):
        s.log_event(ChangeEvent(
            entity_path=f"old.col{i}", change_type="add",
            timestamp="2020-01-01T00:00:00Z",
        ))
    s.log_event(ChangeEvent(entity_path="fresh.col", change_type="add"))

    result = run_events_prune(db_path, days=30, env={"SELVEDGE_DESTRUCTIVE": "1"})
    assert result.pruned == 3

    rows = _chain_rows(db_path)
    tombstones = [r for r in rows if r[1] == "tombstone"]
    assert len(tombstones) == 1
    detail = json.loads(tombstones[0][6])
    assert detail["count"] == 3 and detail["cumulative_count"] == 3
    # Chain rows for the pruned events are retained — the sidecar point.
    assert len([r for r in rows if r[1] == "event"]) == 4

    by_id = _by_id(verify_mod.run_checks(db_path))
    assert by_id["chain_intact"].status == "PASS"
    assert "accounted" in by_id["chain_intact"].detail


def test_two_prunes_accumulate_cumulative_count(db_path):
    s = SelvedgeStorage(db_path)
    for i in range(2):
        s.log_event(ChangeEvent(
            entity_path=f"a.col{i}", change_type="add",
            timestamp="2019-06-01T00:00:00Z",
        ))
    for i in range(3):
        s.log_event(ChangeEvent(
            entity_path=f"b.col{i}", change_type="add",
            timestamp="2021-06-01T00:00:00Z",
        ))

    assert s.prune_events("2020-01-01T00:00:00Z") == 2
    assert s.prune_events("2022-01-01T00:00:00Z") == 3

    tombstones = [r for r in _chain_rows(db_path) if r[1] == "tombstone"]
    assert [json.loads(t[6])["cumulative_count"] for t in tombstones] == [2, 5]

    by_id = _by_id(verify_mod.run_checks(db_path))
    assert by_id["chain_intact"].status == "PASS"
    with sqlite3.connect(str(db_path)) as con:
        assert chain.verify_chain(con)["accounted_deletions"] == 5


def _seed_pre_chain_rows(db_path: Path, n: int = 3) -> None:
    """Simulate an upgraded store: ``n`` events written by a chain-less Selvedge."""
    SelvedgeStorage(db_path)
    with sqlite3.connect(str(db_path)) as con:
        con.execute("DROP TABLE event_chain")
        for i in range(n):
            con.execute(
                "INSERT INTO events (id, timestamp, entity_type, entity_path, change_type) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"legacy-{i}", f"2020-01-0{i + 1}T00:00:00Z", "column", f"old.c{i}", "add"),
            )
        con.commit()


def test_pruning_only_unchained_rows_appends_no_tombstone(db_path):
    """§5.2: pre-chain deletions leave "no gap and no tombstone" — and
    therefore bank NO allowance. A later silent deletion of a CHAINED row
    must still fail verify (the §10 test-12 attack on the exact population
    chain_coverage says is expected everywhere: an upgraded store that has
    pruned its pre-chain history)."""
    _seed_pre_chain_rows(db_path, n=3)
    s = SelvedgeStorage(db_path)  # the upgrade moment — genesis pre_chain_count=3
    chained = [
        s.log_event(ChangeEvent(entity_path=f"users.c{i}", change_type="add"))
        for i in range(2)
    ]

    # The gated prune removes only the 3 unchained legacy rows.
    assert s.prune_events("2021-01-01T00:00:00Z") == 3
    assert [r for r in _chain_rows(db_path) if r[1] == "tombstone"] == []

    by_id = _by_id(verify_mod.run_checks(db_path))
    assert by_id["chain_intact"].status == "PASS"

    # The attack: a bare sqlite3 deletion of a chained row, no tombstone.
    with sqlite3.connect(str(db_path)) as con:
        con.execute("DELETE FROM events WHERE id = ?", (chained[0].id,))
        con.commit()

    results = verify_mod.run_checks(db_path)
    by_id = _by_id(results)
    assert by_id["chain_intact"].status == "FAIL"
    assert "not accounted" in by_id["chain_intact"].detail
    assert verify_mod.exit_code(results) == 1


def test_mixed_prune_tombstones_only_the_chained_deletions(db_path):
    """A prune spanning chained AND unchained rows counts only the chained
    ones into the tombstone, so the allowance matches what actually left the
    chain's coverage — no permanent headroom for later silent deletions."""
    _seed_pre_chain_rows(db_path, n=3)
    s = SelvedgeStorage(db_path)
    for i in range(2):
        s.log_event(ChangeEvent(
            entity_path=f"a.c{i}", change_type="add",
            timestamp="2020-06-01T00:00:00Z",
        ))
    fresh = s.log_event(ChangeEvent(entity_path="fresh.col", change_type="add"))

    # 5 rows deleted (3 unchained legacy + 2 chained old); tombstone counts 2.
    assert s.prune_events("2021-01-01T00:00:00Z") == 5
    tombstones = [r for r in _chain_rows(db_path) if r[1] == "tombstone"]
    assert len(tombstones) == 1
    detail = json.loads(tombstones[0][6])
    assert detail["count"] == 2 and detail["cumulative_count"] == 2

    by_id = _by_id(verify_mod.run_checks(db_path))
    assert by_id["chain_intact"].status == "PASS"

    # Every unit of allowance is already consumed by the pruned chained rows:
    # silently deleting the remaining chained row must fail.
    with sqlite3.connect(str(db_path)) as con:
        con.execute("DELETE FROM events WHERE id = ?", (fresh.id,))
        con.commit()
    by_id = _by_id(verify_mod.run_checks(db_path))
    assert by_id["chain_intact"].status == "FAIL"
    assert "not accounted" in by_id["chain_intact"].detail


def _deterministic_event() -> ChangeEvent:
    """The same event twice — a deterministic importer's re-derived id."""
    return ChangeEvent(
        id="7f000001-aaaa-bbbb-cccc-000000000001",
        entity_path="users.email",
        change_type="add",
        entity_type="column",
        reasoning="Deterministic import: same id, same content.",
        timestamp="2020-01-01T00:00:00Z",
    )


def test_relogging_a_pruned_event_id_neither_crashes_nor_rechains(db_path):
    """A gated prune frees the events PK but the chain record (by design)
    outlives it. Re-importing the same deterministic id — the agent-trace
    path derives uuid5 ids, so prune-then-reimport of an overlapping trace
    does exactly this — must not abort on ``idx_chain_event_id``, must not
    append a second chain record, and must verify clean when the content is
    identical: the original record stays the standing commitment.
    """
    s = SelvedgeStorage(db_path)
    first = s.log_event(_deterministic_event())
    assert s.prune_events("2021-01-01T00:00:00Z") == 1

    relogged = s.log_event(_deterministic_event())  # would IntegrityError before
    assert relogged.id == first.id
    event_records = [r for r in _chain_rows(db_path) if r[1] == "event"]
    assert len(event_records) == 1  # not re-chained

    by_id = _by_id(verify_mod.run_checks(db_path))
    assert by_id["chain_intact"].status == "PASS"
    assert by_id["chain_coverage"].status == "PASS"  # the row is still covered


def test_batch_with_a_pruned_id_lands_atomically(db_path):
    """The importer batch case: one re-derived (pruned) id plus one brand-new
    id in a single ``log_event_batch`` — the whole batch lands, the fresh
    event is chained, the re-imported one keeps its original record."""
    s = SelvedgeStorage(db_path)
    s.log_event(_deterministic_event())
    assert s.prune_events("2021-01-01T00:00:00Z") == 1

    fresh = ChangeEvent(entity_path="users.name", change_type="add")
    s.log_event_batch([_deterministic_event(), fresh])

    with sqlite3.connect(str(db_path)) as con:
        assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
    event_records = [r for r in _chain_rows(db_path) if r[1] == "event"]
    assert len(event_records) == 2
    assert {r[2] for r in event_records} == {_deterministic_event().id, fresh.id}

    by_id = _by_id(verify_mod.run_checks(db_path))
    assert by_id["chain_intact"].status == "PASS"


def test_reinserting_different_content_under_a_committed_id_fails_verify(db_path):
    """The honest outcome of not re-chaining: same id, DIFFERENT content
    conflicts with the standing commitment and reads as tampering."""
    s = SelvedgeStorage(db_path)
    s.log_event(_deterministic_event())
    assert s.prune_events("2021-01-01T00:00:00Z") == 1

    doctored = _deterministic_event()
    doctored.reasoning = "Different content under the committed id."
    s.log_event(doctored)

    by_id = _by_id(verify_mod.run_checks(db_path))
    assert by_id["chain_intact"].status == "FAIL"
    assert "core_hash mismatch" in by_id["chain_intact"].detail


# ---------------------------------------------------------------------------
# Genesis and coverage
# ---------------------------------------------------------------------------


def test_genesis_is_appended_once_at_seq_one(db_path):
    SelvedgeStorage(db_path)
    SelvedgeStorage(db_path)  # a second construction must not add another
    rows = _chain_rows(db_path)
    assert len(rows) == 1
    assert rows[0][0] == 1 and rows[0][1] == "genesis"
    detail = json.loads(rows[0][6])
    assert detail["pre_chain_count"] == 0
    assert set(detail) == {
        "enabled_at", "pre_chain_count", "pre_chain_max_timestamp", "selvedge_version",
    }


def test_pre_chain_rows_are_unchained_not_invalid(db_path):
    """§5.2: rows predating the chain WARN on coverage, never fail intactness."""
    SelvedgeStorage(db_path)
    with sqlite3.connect(str(db_path)) as con:
        # Simulate a store written by a chain-less Selvedge: rows exist,
        # sidecar table doesn't.
        con.execute("DROP TABLE event_chain")
        for i in range(3):
            con.execute(
                "INSERT INTO events (id, timestamp, entity_type, entity_path, change_type) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"old-{i}", f"2026-02-0{i + 1}T00:00:00Z", "column", f"users.c{i}", "add"),
            )
        con.commit()

    SelvedgeStorage(db_path)  # the upgrade moment — chain enabled now
    rows = _chain_rows(db_path)
    assert rows[0][1] == "genesis"
    assert json.loads(rows[0][6])["pre_chain_count"] == 3

    results = verify_mod.run_checks(db_path)
    by_id = _by_id(results)
    assert by_id["chain_intact"].status == "PASS"
    assert by_id["chain_coverage"].status == "WARN"
    assert "3 of 3" in by_id["chain_coverage"].detail
    assert "pre_chain_count=3" in by_id["chain_coverage"].detail
    # The honest cost of "unchained, not invalid" is stated in the output.
    assert "no claim about pre-genesis rows" in by_id["chain_coverage"].detail
    assert verify_mod.exit_code(results) == 0
    assert verify_mod.exit_code(results, strict=True) == 1


def test_verification_paginates_with_bounded_memory(db_path, monkeypatch):
    """§7.3: the walk pages the chain instead of fetchall-ing the table.

    Asserted structurally — the §10 item-20 posture — by counting the
    paginated SELECTs the connection actually executes, not (only) the
    implementation's self-reported ``pages`` counter: a rewrite that
    fetchall()s the whole table and chunks the in-memory list could keep
    the counter honest-looking while losing the bounded-memory property.
    """
    s = SelvedgeStorage(db_path)
    s.log_event_batch(
        [ChangeEvent(entity_path=f"bulk.col{i}", change_type="add") for i in range(300)]
    )
    monkeypatch.setattr(chain, "VERIFY_PAGE_SIZE", 25)
    page_queries: list[str] = []

    def trace(sql: str) -> None:
        if "FROM event_chain WHERE seq >" in sql:
            page_queries.append(sql)

    with sqlite3.connect(str(db_path)) as con:
        con.set_trace_callback(trace)
        res = chain.verify_chain(con)
    assert res["intact"] is True
    assert res["chained_events"] == 300
    # 301 records at page size 25 → 13 paginated SELECTs actually executed,
    # observed from the connection's own trace...
    assert len(page_queries) == 13
    for sql in page_queries:
        assert "LIMIT" in sql  # every page query is bounded, never unbounded
    # ...and the self-reported counter agrees with the observed truth.
    assert res["pages"] == 13


# ---------------------------------------------------------------------------
# Manifest surface (SEP-3004 §2.7)
# ---------------------------------------------------------------------------


def test_chain_manifest_declares_the_contract_and_the_omissions():
    manifest = chain.chain_manifest()
    for field in (
        "storage_mechanism",
        "chain_algorithm",
        "canonical_form_version",
        "verification_procedure_ref",
    ):
        assert isinstance(manifest[field], str) and manifest[field]
    assert manifest["chain_algorithm"] == "SHA-256"
    assert manifest["canonical_form_version"] == "selvedge-chain/1"
    assert "selvedge package" in manifest["verification_procedure_ref"]
    # The declared omissions: git_commit outside the core, tool_calls uncovered.
    assert "git_commit" in manifest["coverage"]
    assert "tool_calls" in manifest["coverage"]
    # Every field a string, never null.
    assert all(isinstance(v, str) and v for v in manifest.values())


def test_verify_json_carries_the_chain_manifest(runner, db_path):
    s = SelvedgeStorage(db_path)
    s.log_event(ChangeEvent(
        entity_path="users.email", change_type="add",
        changeset_id="cs-1", git_commit="abc",
    ))
    s.log_event(ChangeEvent(
        entity_path="users.name", change_type="add",
        changeset_id="cs-1", git_commit="abc",
    ))
    result = runner.invoke(cli, ["verify", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    manifest = payload["chain_manifest"]
    assert manifest["canonical_form_version"] == "selvedge-chain/1"
    assert manifest["chain_algorithm"] == "SHA-256"
    checks = {c["id"]: c for c in payload["checks"]}
    assert checks["chain_intact"]["status"] == "PASS"
    assert checks["chain_coverage"]["status"] == "PASS"
