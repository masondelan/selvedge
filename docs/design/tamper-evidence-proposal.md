# Tamper evidence for the Selvedge event log — design proposal

**Status:** proposal, not scheduled. No code, no migration, no tests written.
**Date:** 2026-08-10
**Scope:** make `selvedge verify` able to detect *retroactive modification* of stored
events, not only *corruption*.

---

## 0. Why this exists

`selvedge verify` today has eight checks (`selvedge/verify.py:37-46`). Every one of
them asserts **data correctness** — is the DB corrupt, are the migrations the ones we
declare, does every row have a parseable timestamp and a known `change_type`. Not one
of them asserts that a row is the row we wrote. There is no hash chain anywhere in the
repo; the only two `sha256` uses in `selvedge/` are the semantic cache key
(`semantic.py:135`) and the Agent Trace `content_hash` (`exporters/agent_trace.py:594`).

That gap matters because of what Selvedge claims. The retention argument — rejected and
superseded decisions stay on the record — is currently a promise about our own conduct.
MCP SEP-3004 ("Tamper-Evident Audit Record Contract",
<https://github.com/modelcontextprotocol/modelcontextprotocol/pull/3004>) specifies a
canonical byte form plus an append-only hash chain, and the shape of Selvedge's event
model is close to it. Close, with nothing to demonstrate, is a weaker position than
either conforming or explaining precisely why we don't.

---

## 1. Lead with the hard part: fields are written after insert

A naive "hash the whole row and chain it" design is broken by Selvedge's own legitimate
behavior, before any attacker shows up.

`storage.py:806`:

```sql
UPDATE events SET git_commit = ? WHERE git_commit = '' AND timestamp >= ?
```

`backfill_git_commit` (`storage.py:784-809`) runs from the post-commit hook installed by
`selvedge install-hook`. It stamps every event logged in the preceding **60 minutes**
(`window_minutes: int = 60`) with the commit hash. The commit SHA *does not exist* when
the event is logged. It cannot. The event describes work that produced the commit.

This is not a bug to be designed around quietly — it is the structural fact the design
has to answer, and it generalizes past Selvedge. Any audit contract for agent work meets
fields that resolve after the fact: commit SHA, review outcome, deploy id, incident
linkage. A whole-row chain says such a field is either impossible or a tamper event.

Note the distinction from two arguments already live in the SEP-3004 thread, because it
is not the same argument and should not be conflated with either:

- **Whose clock** (navigatorbuilds,
  [#issuecomment-5121450963](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/3004#issuecomment-5121450963)):
  `occurred_at` is the recorder's clock, so the core can attest when the host wrote an
  event down, never when the caller acted. That is about a fact known at emission time
  that the recorder cannot vouch for.
- **Rule migration** (Tetsurohhori,
  [#issuecomment-5191981169](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/3004#issuecomment-5191981169)):
  a canonicalization rule "cannot be applied retroactively. Recomputing digests over
  existing records to adopt a new rule is, from the outside, indistinguishable from
  tampering." That is about versioning the parameters of the stream.

Late binding is neither. It is a fact **not yet in existence** at emission time. SEP-3004
as drafted has no text on it at all — no amendment record, no correction record, no
supersession, no provisional field. §2.5 forbids the `UPDATE` outright, and §2.6 gives a
verifier exactly one reading of a changed field: "A mismatch indicates the record was
altered after commitment."

So the design question this proposal must settle first is: **which mutable fields go
inside the protected form, and what happens to the ones that stay outside.**

### 1.1 Complete mutation inventory (verified, not assumed)

Three sites in the entire package mutate or delete `events`. There are no others.

| # | Site | Statement | Frequency / trigger | Audited today? |
|---|------|-----------|---------------------|----------------|
| 1 | `backfill_git_commit`, `storage.py:806` | `UPDATE events SET git_commit = ?` over a 60-min window | Automatic, every `git commit`, via the post-commit hook | No |
| 2 | `recanonicalize_paths(apply=True)`, `storage.py:1038` | `UPDATE events SET entity_path = ? WHERE id = ?` | Rare, explicit — `selvedge migrate-paths --apply`; dry run is the default | Yes — one row per run into `path_migrations` (`storage.py:1041-1057`) |
| 3 | `prune_events`, `storage.py:960` | `DELETE FROM events WHERE timestamp < ?` | Rare, doubly gated: confirmation **and** `SELVEDGE_DESTRUCTIVE=1` (`prune.py:164-184`, re-checked in `run_events_prune` at `prune.py:225`) | Partially — one line to `.selvedge/prune.log`, a **separate file**, nothing in the DB |

**Redaction never writes.** `selvedge/redaction.py` is read-only end to end:
`scan_store_for_secrets` (`:126-155`) reads via `storage.scan_events_for_text` and returns
`{event_id, timestamp, entity_path, pattern}` rows, deliberately never the matched text.
The module docstring (`:25-27`) states the posture directly — an append-only store that
quietly alters content on the way in would be a worse lie than one that stores too much.
There is no redact-in-place path in `cli.py` or `server.py`. So redaction imposes **no
constraint** on the chain design today; §7.4 covers what a future redaction feature would
have to do.

`log_supersede` (`storage.py:619-711`) and `log_rename` (`:713-778`) are **not** mutations —
both construct new `ChangeEvent`s and return through `log_event` / `log_event_batch`
(`:711`, `:778`). The superseded row is untouched. That part of the append-only claim is
already literally true.

The honest one-line summary, which any outgoing writing should use verbatim: **the
event-logging API is append-only — no logging operation ever edits or deletes a prior
event. Three non-logging operations do mutate rows, and none of them is currently
detectable after the fact.**

---

## 2. Design space

Four options. (a), (b), (c) are the ones named in the brief; (d) is a hybrid of (a) and
(c) that falls out of the mutation inventory above and is what this proposal recommends.

### (a) Protected immutable core subset

Hash only the columns that are never mutated; leave the mutable ones outside the
protected form entirely.

From §1.1, the mutable columns are `git_commit` (site 1) and `entity_path` (site 2). So
the protected core would be 16 of 18 columns and the envelope would be
`{git_commit, entity_path}`.

- **Protects:** `reasoning`, `diff`, `change_type`, `timestamp`, `supersedes`,
  `constraint`, `stale_when`, `metadata`, `id`, and the rest — the decision content,
  which is the thing Selvedge exists to keep.
- **Does not protect:** `git_commit` *and* `entity_path`. The second omission is the
  killer. `entity_path` is the identity of what changed. An unprotected `entity_path`
  means a silent `UPDATE` can repoint a rejected decision from `payments.card_number` to
  a harmless entity and the chain reports green. That is a hole big enough to make the
  whole claim not worth making.
- **Migration cost:** lowest. No behavior change to any of the three mutation sites.
- **SEP-3004:** conforms structurally (§2.1's protected-field notion), and the SEP's own
  Security Implications section already concedes the shape — "The integrity guarantee is
  only as strong as the protected field set." But SEP-3004 makes that a *registration-time*
  choice about record shape, not a mechanism for handling timing.
- **Verdict:** right instinct, wrong cut. Excluding a field because it is *occasionally*
  mutated by a rare explicit command costs far more than it saves.

### (b) Backfill becomes an appended amendment event; whole-row chain

Make the store literally append-only. `backfill_git_commit` stops issuing `UPDATE` and
instead appends one amendment event per affected row; the effective `git_commit` is
derived at read time by folding amendments over the base event.

- **Protects:** everything. Strongest guarantee on offer, and the most SEP-3004-faithful
  reading of §2.5 ("The record store MUST reject in-place UPDATE and DELETE of committed
  records through its normal access paths").
- **Does not protect:** nothing structural. The weakness is elsewhere.
- **Migration cost:** very high, and concentrated in the worst place. A single commit
  after a long agent session backfills every event in a 60-minute window — that is now N
  new rows per commit, on the hot path, from a git hook. Worse, **every read surface has
  to learn to fold**: `blame`, `history`, `search`, `changeset`, `prior_attempts`, `diff`,
  the exporters, and `_check_missing_git_commit` (`verify.py:232-249`) all read
  `git_commit` directly. `_coalesce_event_nullables` (`storage.py:368-400`) exists
  precisely because a read-shape change that was *not* funnelled through one chokepoint
  leaked `null` onto five of six read surfaces; this is that problem an order of magnitude
  larger. `idx_project` / direct `git_commit` predicates stop being straightforward.
- **SEP-3004:** conforms best of the four.
- **Verdict:** correct and unaffordable. It buys protection on one field —
  `git_commit`, a value that is a public, independently checkable git object anyway — at
  the price of rewriting every read path in the project. If Selvedge were being written
  from scratch this would be the design. It is not.

### (c) Two-tier: inner immutable digest chained, outer envelope digest recomputed

Compute an inner digest over the immutable core and an outer digest over
core-plus-mutable-envelope. Chain **only** the inner one; recompute the outer on every
legitimate amendment.

- **Protects:** the core, with the chain; and it makes envelope drift *visible* (the outer
  digest changes) without making it fatal.
- **Does not protect:** the envelope's history — an outer digest that is recomputed on
  amendment tells you the current envelope matches the current row, and nothing about what
  the envelope held before. Without an amendment journal it is a checksum, not evidence.
  It also adds a second digest per row whose failure mode ("outer mismatch, inner fine")
  needs a defined verdict; SEP-3004 §2.6 admits only pass/fail, and the thread has already
  found that insufficient (Tetsurohhori's `VERIFY OK` printed alongside
  `attested_prefix_lines=0`).
- **Migration cost:** moderate. Two digest columns, and every mutation site has to
  remember to recompute the outer one — a rule enforced by convention, which is how it
  will eventually be forgotten.
- **SEP-3004:** the two-digest shape has no analogue in the spec.
- **Verdict:** the right *idea* — separate what is chained from what is merely
  checksummed — carrying an unnecessary second digest. Fold the useful half into (d).

### (d) **Recommended** — chained core over an asymmetric cut, plus a boundary-record journal

The insight the other three miss is in §1.1's table: the two mutable columns are not
alike, and treating them alike is what forces the bad trade.

- `git_commit` — mutated **automatically, constantly, on up to 60 minutes of rows, by a
  hook**. Late-bound by nature. Making this append-only costs design (b)'s whole bill.
- `entity_path` — mutated **rarely, only by an explicit human-invoked
  `migrate-paths --apply`, which already writes an audit row** (`storage.py:1041-1057`).
  A once-a-release operation.

So: **frequency and provenance decide which side of the line a mutable field goes on.**

1. `git_commit` sits **outside** the protected core. It is not chained, and the design
   says so out loud rather than implying otherwise. It is also the one field whose value
   is independently checkable against the git object database, which is what makes the
   omission tolerable.
2. `entity_path` sits **inside** the protected core, and `migrate-paths --apply` appends
   an **amendment record** to the chain that re-binds the rewritten rows. Design (b)'s
   mechanism, applied only where it is cheap.
3. The chain lives in a **sidecar table**, not in new columns on `events`. Prune then
   cannot silently remove chain history, because deleting an events row does not delete
   its chain row — the deletion becomes a visible gap instead of an invisible one.
4. Prune appends a **tombstone record**. Deleting events becomes the first destructive
   operation in Selvedge that leaves a trace *inside the database* rather than only in
   `.selvedge/prune.log`.

Amendment and tombstone records are the "boundary event" pattern the SEP-3004 thread
converged on independently across four separate builds, and they adopt navigatorbuilds'
normative formulation — a boundary record binds both the prefix it extends and its own
position ([#issuecomment-5227496013](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/3004#issuecomment-5227496013)).
Here every record binds its own `seq`, not just the boundaries (§4.3).

### Comparison

| | (a) core subset | (b) full append-only | (c) two-tier | **(d) recommended** |
|---|---|---|---|---|
| `reasoning` / `diff` / `change_type` protected | yes | yes | yes | **yes** |
| `entity_path` protected | **no** | yes | no | **yes** |
| `git_commit` protected | no | yes | envelope only | **no, stated** |
| Silent row deletion detected | no | no¹ | no¹ | **yes** |
| Reordering / insertion detected | no² | yes | no² | **yes** |
| Read paths change | none | **all of them** | none | none |
| Hot-path write cost | digest | digest + N rows/commit | 2 digests | digest + head read (§7.1) |
| Mutation sites needing changes | 0 | 1 (rewritten) | 3 | 2 (append a record) |
| SEP-3004 §2.5 conformance | no | **yes** | no | no, declared |

¹ Not without a sidecar table; both (b) and (c) as usually drawn put the digest on the
row, so deleting the row deletes the evidence.
² A per-row digest with no chain detects content mutation only.

---

## 3. Recommendation

**Adopt (d).** Reasons, in order of weight:

1. **It protects the fields whose integrity is the actual product claim.** Reasoning,
   constraint, stale_when, change_type, supersedes, entity_path — the decision record. It
   omits exactly one field, and that field is a pointer into git, which is itself a hash
   chain maintained by other software.
2. **It changes no read path.** The `_coalesce_event_nullables` history
   (`storage.py:379-387`) is the standing evidence in this codebase for how expensive a
   read-shape change is when it isn't funnelled through one chokepoint. (d) touches the
   write path and `verify.py`, nothing else.
3. **It makes prune honest.** Today a gated events prune leaves nothing in the DB. Under
   (d) it leaves a tombstone, and an *ungated* deletion — someone with `sqlite3` and a
   `DELETE` — fails verification. That is a strictly new capability, and it is the one
   directly relevant to the retention claim.
4. **The omission is declarable, not hidden.** SEP-3004 §2.7 requires a manifest with
   `canonical_form_version` for exactly this purpose. `git_commit`'s exclusion goes in the
   manifest, so a verifier is told what is and is not covered instead of inferring it.
5. **It can ship in two stages** (§7.2), which de-risks the one real performance question.

---

## 4. The canonicalization contract

Concrete enough to implement from, in either direction, without reading the Selvedge
source. Version string: **`selvedge-chain/1`**.

### 4.1 The protected core — exactly 17 fields

Every `events` column **except** `git_commit`:

```
agent, change_type, changeset_id, constraint, diff, entity_path, entity_type,
expires_when, id, metadata, project, reasoning, revisit_after, session_id,
stale_when, supersedes, timestamp
```

That list is already in the required order: **keys are sorted lexicographically by
Unicode code point**, so field order is derived, never chosen. (`change_type` precedes
`changeset_id` because `_` is U+005F and `s` is U+0073.)

### 4.2 Rules

1. **Encoding is UTF-8.** Stated because SEP-3004 does not state it — see §8.2.
2. **Serialization is JSON**, one object, no insignificant whitespace: `,` between
   members, `:` between key and value, no spaces anywhere outside string literals.
3. **Every protected value is a JSON string.** There are no numbers, booleans, nulls,
   arrays, or nested objects in the protected body. This is not a restriction we have to
   enforce — it is what the schema already is, since all 18 `events` columns are `TEXT`.
   It disposes of SEP-3004's `1` vs `1.0` vs `1e0` problem by construction.
4. **Absent is the empty string. `null` never appears.** This is the house convention
   (`CLAUDE.md:27`: "Every field always populated, never `null`") doing real work: it
   closes the absent-vs-present-and-null ambiguity that SEP-3004 leaves open (§8.2).
   **Implementation hazard:** the five nullable columns (`revisit_after`, `expires_when`,
   `supersedes`, `constraint`, `stale_when`) read back as SQL `NULL` on rows written
   before migrations v3/v4. A canonicalizer MUST apply the `_coalesce_event_nullables`
   mapping (`storage.py:389-391`) — `None` → `""` — **before** hashing. A second
   implementation reading raw SQL without this step will diverge on pre-v3 rows and only
   on pre-v3 rows, which is the worst kind of bug to find later.
5. **`metadata` is hashed as the opaque stored string**, not parsed and re-canonicalized.
   The column holds JSON TEXT; the digest covers the bytes on disk. This avoids nested
   canonicalization and the number-format question entirely, and no mutation site touches
   `metadata` (§1.1), so nothing legitimately re-serializes it.
   **Second implementation hazard:** `_coalesce_event_nullables` *decodes* `metadata` into
   a `dict` on every event read path (`storage.py:392-397`). A canonicalizer MUST read the
   **raw column**, not the decoded dict.
6. **String escaping is pinned to this table**, because "valid JSON" is not one byte
   sequence:
   - `"` → `\"`, `\` → `\\`
   - U+0008 → `\b`, U+000C → `\f`, U+000A → `\n`, U+000D → `\r`, U+0009 → `\t`
   - all other C0 controls (U+0000–U+001F) → `\u00xx`, **lowercase hex**
   - **everything else is emitted raw**, including all non-ASCII. No `\uXXXX` for
     non-ASCII. `/`, `<`, `>`, `&` are **not** escaped.

   This is exactly what CPython's `json.dumps(..., ensure_ascii=False)` emits, verified
   against `{"k":"café\n\t\"x\"\\y\u0001"}` while drafting. It is written out as a table
   rather than as "whatever Python does" because a second implementation cannot depend on
   a reference implementation's incidental behavior — the same gap this proposal charges
   SEP-3004 with in §8.2.
7. **Control characters are preserved, not rejected.** A `diff` without newlines is not a
   diff. This is a deliberate, named divergence from SEP-3004 §2.3 — see §8.1.
8. **No Unicode normalization pass, and no trimming, inside the canonicalizer.**
   `ChangeEvent.__post_init__` already strips `entity_path` (`models.py:129`) and
   `_normalize_for_storage` (`storage.py:555-569`) already canonicalizes the path and
   normalizes the timestamp. The canonicalizer hashes what is stored, byte for byte.
   Adding an NFC pass at hash time would mean the digest covers a value that is not in the
   column, and the verifier would silently mask a real difference. If NFC is wanted, it
   belongs at the write chokepoint, where it changes the stored value too — that is a
   separate proposal (§9).
9. **Length**: no cap in the canonicalizer. Field sizes are already bounded upstream by
   the `diff_bytes` / `reasoning_bytes` settings (`config.py:173,177`) applied in
   `validation.py:210-222`; the digest covers the post-truncation stored value. Divergence
   from SEP-3004's 8192-code-unit baseline is noted in §8.1.
10. **`timestamp`** is hashed in the exact stored form. Selvedge normalizes to a trailing
    `Z` at the write path; the canonicalizer does not re-derive it.

### 4.3 Digests

```
core_hash  = SHA256( canonical_json(<the 17-field object>) )        → lowercase hex
chain_hash = SHA256( canonical_json({
                 "canon": "selvedge-chain/1",
                 "core":  <core_hash, or the record-kind digest for non-event kinds>,
                 "kind":  "event" | "genesis" | "amend" | "tombstone",
                 "prev":  <previous record's chain_hash, "" for genesis>,
                 "seq":   "<decimal, as a string>"
             }) )                                                    → lowercase hex
```

`seq` is inside the digest deliberately. A record therefore binds its own position, not
only the prefix it extends, so a record cannot be lifted intact from one position to
another. That is the thread's boundary-event result generalized to every record.

### 4.4 Worked vector (known-answer test)

Freeze this as the first test. The 413-byte core preimage:

```
{"agent":"claude","change_type":"add","changeset_id":"cs-1","constraint":"","diff":"+ email TEXT NOT NULL\n","entity_path":"users.email","entity_type":"column","expires_when":"","id":"0f8fad5b-d9cb-469f-a165-70867728950e","metadata":"{}","project":"selvedge","reasoning":"Signups need a contact address.","revisit_after":"","session_id":"sess-1","stale_when":"","supersedes":"","timestamp":"2026-08-10T12:00:00Z"}
```

```
core_hash  = e45b2fcd0e026f5f97310431d8e5788bea4f6be2c77143f906c6c54827a20881
```

The 137-byte link preimage at `seq=1`, `prev=""`:

```
{"canon":"selvedge-chain/1","core":"e45b2fcd0e026f5f97310431d8e5788bea4f6be2c77143f906c6c54827a20881","kind":"event","prev":"","seq":"1"}
```

```
chain_hash = eeca977a914561bc85d299d7aed05c5bf208fccac26db7e59af8aa16e531f5f2
```

Both digests were computed while drafting this document, from the preimages exactly as
printed. The vector deliberately includes a trailing newline inside `diff` — the case
SEP-3004 §2.3 would reject (§8.1) — and an empty string in five fields, so the
absent-encoding rule is exercised. A second vector containing non-ASCII text should be
added at implementation time to lock rule 6.

### 4.5 Considered and rejected: length-prefixed concatenation

axcpeter's TTTPS takes the other road
([#issuecomment-5191670385](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/3004#issuecomment-5191670385)):
"The chain hash never touches JSON. It is computed over a fixed-order concatenation of
the record's identifying fields, so there is no serializer to disagree about." Applied
here it would dissolve rules 6 and 7 at a stroke — no escaping question, no
control-character question.

Rejected, narrowly, because convergence with SEP-3004 is the point of the exercise and a
second canonicalization is the thing the thread is actively arguing about (axcpeter's
proposal that a deployment may use one rule internally and another for export has drawn
no reply from the SEP author and no change to §2.3). Selvedge should land on the
one-rule side and say so. If §2.3 is later amended toward two layers, this decision
should be revisited — the length-prefixed form is strictly easier to implement correctly.

---

## 5. Storage and migration strategy

### 5.1 New table, not a migration

```sql
CREATE TABLE IF NOT EXISTS event_chain (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,              -- event | genesis | amend | tombstone
    event_id   TEXT NOT NULL DEFAULT '',   -- '' for genesis/tombstone
    core_hash  TEXT NOT NULL,
    prev_hash  TEXT NOT NULL DEFAULT '',
    chain_hash TEXT NOT NULL,
    canon      TEXT NOT NULL DEFAULT 'selvedge-chain/1',
    detail     TEXT NOT NULL DEFAULT '{}'  -- JSON; boundary-record payload
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_chain_event_id
    ON event_chain(event_id) WHERE event_id != '';
```

**Additive only, and deliberately not a `MIGRATIONS` entry.** This is a new table, not an
alteration of an existing one — the same call made for `path_migrations`, whose comment
(`storage.py:85-88`) gives the reason explicitly: adding it as a versioned migration
means "opening a v0.3.7 DB with an older Selvedge doesn't trip the doctor's downgrade
detector." That is not a stylistic preference. `_check_schema` (`verify.py:118-122`)
**hard-fails** on an applied version it doesn't recognize: "this DB was last opened by a
newer Selvedge; downgrading is not supported." Shipping the chain as migration v5 would
turn every older Selvedge into a `verify` failure on any DB that had been opened once by
a newer one. A `CREATE TABLE IF NOT EXISTS` in `_init_db` (alongside `storage.py:525-527`)
is invisible to older versions. `embeddings` (`semantic.py:68`) sets the same precedent.

Consequences of the sidecar choice, all intended:

- `events` is untouched. No `ALTER`, so no table rewrite, so the bound
  `tests/test_migrations_perf.py` locks in at 10k/100k/1M events is not at risk.
- Deleting an events row does not delete its chain row. That is what makes silent
  deletion detectable at all.
- `AUTOINCREMENT` rather than a bare `INTEGER PRIMARY KEY`: bare rowids are reused after
  the highest row is deleted. Chain rows are never deleted today, so the difference is
  latent — but `seq` is inside the digest (§4.3), so a reused `seq` would be a
  correctness bug, and the guarantee should be in the schema rather than in a comment.
  Cost: SQLite materializes `sqlite_sequence`.

### 5.2 Rows predating the chain — genesis

**Mason's instinct is right and I'd ship it, with one addition.**

A `genesis` record is appended at `seq=1` when the chain is first enabled, carrying
`{enabled_at, pre_chain_count, pre_chain_max_timestamp, selvedge_version}` in `detail`,
with `core_hash` computed over that object. Rows with no chain row are then **unchained,
not invalid**: reported and counted by a `should_warn` check, never fatal (§6).

Right because the alternative is worse in a specific way. Retroactively chaining existing
rows at upgrade time produces a chain that *looks* identical to one built live, while
proving something much weaker — that the rows were mutually consistent at seal time, not
that they were never edited before it. That is Tetsurohhori's point exactly, and shipping
it silently would make Selvedge's chain indistinguishable from a doctored one to any
outside verifier. A genesis marker refuses to make a claim it can't support.

The addition: **be explicit that the chain says nothing about pre-genesis rows,
including whether they still exist.** Pre-chain rows have no chain row, so deleting them
leaves no gap and no tombstone — invisible. That is the honest cost of "unchained, not
invalid," and it should be in the check's own output text, not only here.

For operators who want the weaker guarantee anyway, offer it **labelled**: an optional
`selvedge chain seal` that appends chain records for existing rows with
`kind="event"` and a `detail.sealed_at` marker, so verification reports two coverage
classes — `chained_live` versus `chained_at_seal` — and never conflates them. The
distinction lives in the data, where a downstream verifier can act on it, rather than in
documentation. This is opt-in and can land after the core feature; it is listed as an
open question in §9 rather than recommended outright.

### 5.3 What the three mutation sites do

| Site | Change |
|---|---|
| `backfill_git_commit` | **None.** `git_commit` is outside the protected core. The `UPDATE` stands as written. This is the whole point of the asymmetric cut. |
| `recanonicalize_paths(apply=True)` | After rewriting, append one **covering** `amend` record per run with the full row list in `detail`, each entry binding `{event_id, new_core_hash}` under `{field: "entity_path"}`. *(Amended 2026-08-29, resolving §11 open question 4: the shipped implementation and `test_migrate_paths_apply_appends_one_covering_amend` use ONE covering record per run, not one per row — the per-row variant described in the original draft was declined.)* It already writes a `path_migrations` audit row in the same transaction (`storage.py:1041-1057`) — this is a second write to the same transaction. |
| `prune_events` | Append one `tombstone` record in the same transaction as the `DELETE`, with `{cutoff, count, cumulative_count}` in `detail`. Note this puts a *write* inside a method whose docstring (`storage.py:950-957`) says it is "deliberately dumb — no gating, no prompting." The tombstone is not a gate, so the docstring's intent holds; it should be updated to say so. |

---

## 6. The new verify checks

Two ids, not one. The split matters more than the names.

| id | tier | fires when |
|---|---|---|
| `chain_intact` | **`must_fail`** | A chained row's recomputed `core_hash` differs from the stored one; or `prev_hash` does not match the prior record's `chain_hash`; or a chain row's events row is **absent and not accounted for** by tombstone `cumulative_count`. |
| `chain_coverage` | **`should_warn`** | Events rows exist with no chain row — pre-genesis rows, or rows written by a downgraded Selvedge. Reports the count and the genesis `pre_chain_count`. |

Why the split: nothing legitimate produces a `chain_intact` failure. Every legitimate
mutation either stays outside the protected core (`git_commit`), or appends a boundary
record (`migrate-paths`, `prune`). So `must_fail` is defensible and won't produce the
false positives that would teach users to add `|| true`. Conversely, unchained rows are
*expected* on every existing install on day one, and a `must_fail` there would break
every upgrader's CI at once — which is exactly the failure mode the two-tier design in
`verify.py:11-21` was built to avoid.

`CHECK_TIERS` (`verify.py:37-46`) goes from **8 entries to 10**. Both new ids must be
added there or `run_checks`' assertion at `verify.py:303` will raise.

### 6.1 Forced changes to `tests/test_verify.py`

The tier-locking test `test_every_check_id_is_categorized` (`:43-50`) iterates emitted
results and asserts membership in `CHECK_TIERS`; it passes automatically once the two ids
are added. There is **no** assertion anywhere in the repo that pins the check *count* — I
grepped; the only references to `CHECK_TIERS` are `verify.py` itself,
`tests/test_verify.py:39-49`, `CHANGELOG.md:970`, `docs/architecture.md:535`, and
`docs/outreach/sep-3004-comment.md:121`. That last file states `CHECK_TIERS` "has exactly
8 entries" and would need updating if both ship; it is outside this proposal's scope and
is flagged, not edited. (That file is another lane's live draft — re-locate the line
before editing.)

Test-by-test impact:

- `test_clean_db_passes_every_check` (`:58-73`) — asserts **every** result is `PASS`,
  including under `--strict`. A fresh DB under the new version has genesis at
  `pre_chain_count=0` and both events written through `log_event`, so coverage is 2/2 and
  both new checks PASS. **Holds, but it is the test that will break first** if genesis or
  coverage accounting is wrong, which makes it a useful canary rather than a nuisance.
- `test_unknown_change_type_in_store_fails` (`:81-96`),
  `test_empty_entity_path_fails` (`:99-110`),
  `test_unparseable_timestamp_fails` (`:113-124`),
  `test_old_event_missing_git_commit_warns` (`:169-182`),
  `test_verify_cli_fail_exits_one` (`:231-242`) — all insert events via **raw `sqlite3`**,
  bypassing `log_event`. Those rows will be unchained → a new `chain_coverage` **WARN**.
  Each of these asserts on a specific check id via `by_id[...]`, and the two that assert
  exit codes assert `1` (driven by an existing `must_fail`), so **all five still pass**.
  They should nonetheless get a comment noting the raw-insert rows are deliberately
  unchained, so the next reader doesn't "fix" it.
- `test_singleton_changeset_is_warn_not_fail` (`:154-166`) — asserts
  `exit_code(results) == 0` and `strict=True → 1`. Already 1 under strict from the orphan
  warning; an added coverage WARN changes neither. **Holds.**
- `test_verify_cli_warn_only_exits_zero_strict_escalates` (`:218-228`) — same shape.
  **Holds.**

So the forced diff is small: two `CHECK_TIERS` entries, and new tests. That is a
consequence of the tier design being right, not of the change being minor.

---

## 7. Implementation constraints

### 7.1 Concurrency — the one real risk

Appending a chain record requires reading the current head. `_session`
(`storage.py:496`) uses SQLite's default **deferred** transactions, which take no write
lock until the first write. Two processes could therefore both read the same head and
fork the chain.

The fix is `BEGIN IMMEDIATE`, and there is direct precedent with a written rationale:
`migrations.py:217` and its comment at `:209-216`, which describes exactly this class of
bug — N processes passing the same pre-lock check, and the PreToolUse hook being "a third
contender" on every gated tool call.

Honest cost: `log_event` goes from a lock-free deferred insert to one that serializes
writers. Mitigations, in order: the head read is a single `ORDER BY seq DESC LIMIT 1` on
the `AUTOINCREMENT` primary key (O(log n)); `_retry_on_locked` (`storage.py:411`) and the
`busy_timeout` PRAGMA (`storage.py:469`) already exist for exactly this; and
`log_event_batch` (`storage.py:601-617`) takes **one** head read for N events, so the
importers actually get cheaper relative to a per-event design.

This is the largest unknown in the proposal and it must be measured, not argued —
`tests/test_concurrency.py` already exists and should gate the change.

### 7.2 Staged rollout, if 7.1 disappoints

`core_hash` and the chain are separable, and the separation is useful:

- **Stage 1 — `core_hash` only.** No head read, no `BEGIN IMMEDIATE`, no serialization
  cost. This alone detects **content mutation of any protected field**, which is most of
  the value.
- **Stage 2 — chaining.** Adds detection of deletion, insertion, and reordering.

If the benchmark says stage 2 is too expensive on the hook path, stage 1 still ships and
still moves the claim from "promise" to "checked." Stage 1 alone is not tamper-evidence
in the SEP-3004 sense and must not be described as such.

### 7.3 Streaming verification — no full-table load

Constant memory, by construction:

- Paginate the chain with `WHERE seq > ? ORDER BY seq LIMIT 1000`, carrying exactly one
  64-char `prev_hash` string plus two integer counters across pages. Never `fetchall()`
  the table.
- Per page, fetch the corresponding events rows by `id` and recompute. Peak memory is one
  page, tunable.
- The link-only half (does `prev_hash` match the prior `chain_hash`) touches **only** the
  narrow `event_chain` table and needs no events rows at all — useful for a fast mode, but
  it does **not** detect content mutation, so it cannot be the default.

Cost is one full scan. That is already the shape of `_check_timestamps`
(`verify.py:159-176`), which iterates every events row today — so a full-scan check is
inside the existing verify budget rather than a new class of cost. Ordering by `seq`
means the scan is index-ordered.

### 7.4 If redaction ever writes

Redaction is read-only today (§1.1), so it constrains nothing. But it is the most likely
future source of a legitimate in-place rewrite — someone will eventually want
`selvedge redact --event-id X`. Recording the rule now, while it is free: a redaction MUST
NOT `UPDATE` a protected field silently. It appends an `amend` record binding
`{event_id, field, new_core_hash, reason: "redaction"}`, exactly as `migrate-paths` does.
A redaction that breaks the chain and a redaction that is recorded differ only in whether
someone remembered — which is why the rule belongs in the design doc that precedes the
feature.

### 7.5 Dependencies — zero new, confirmed

`hashlib` and `json` are stdlib; `json` is already imported in `storage.py`. Nothing in
this design needs `unicodedata` — rule 8 explicitly declines to normalize at hash time,
which was partly a correctness decision and conveniently also keeps the import list
unchanged. No `jsonschema`, no crypto library, no signing (§8.3), no network. This
satisfies the `CLAUDE.md` "no external dependencies beyond the declared ones" rule with
nothing to argue about, and it is the same posture as
`exporters/agent_trace.py:490-538`'s hand-rolled validator.

---

## 8. Threat model — stated plainly

### What the design would detect

Conditional throughout: none of this is implemented.

- Accidental modification: a `sqlite3` session, a script, a well-meant `UPDATE` on the
  wrong DB.
- Buggy or unaware code paths — including future Selvedge code that mutates a protected
  field without appending a boundary record. This is arguably the highest-value case,
  because it is the one that will actually happen.
- Silent deletion of chained rows, via the sidecar plus tombstone accounting.
- Reordering or insertion of rows, because `seq` is inside each digest.
- **A verifiable export.** A third party handed an exported record set can recompute
  independently, with no access to the producing machine — SEP-3004 §2.6's property that
  the procedure "runs identically whether executed by an adopter attesting to its own
  trail or by a third party with read access to an exported record set."

### What the design would NOT detect

**A motivated local attacker.** Selvedge is a local-first SQLite store. An attacker who
controls the file controls everything: edit any row, recompute every `core_hash`, rechain
every record from the edit point forward, rewrite the tombstone counters, and `verify`
reports green. There is no key, so there is nothing they cannot recompute. `hashlib`
alone cannot do better — that is not an implementation gap, it is what unkeyed hashing
over attacker-controlled storage means.

Also not covered, specifically:

- **`git_commit`.** Outside the protected core by design (§2d). It can be rewritten
  freely and the chain says nothing. Independently checkable against git, which is why
  the trade is acceptable — but it is a real hole and must be declared in the manifest,
  not discovered by a reader.
- **Pre-genesis rows** (§5.2): not protected, and their *deletion* is invisible.
- **Which** rows were pruned. Tombstone accounting is a count, so deleting one row and
  fabricating one tombstone increment keeps the books balanced. Strengthening this to a
  per-prune digest over the pruned `seq` list is possible and is listed in §9.
- Anything about the machine: no attestation that the recorder ran the code it claims.

### The honest claim

**Not yet true. Nothing in this section describes shipped behavior.** As of 2026-08-10
Selvedge has no hash chain, no `core_hash`, and no chain checks — `verify` has eight
checks and every one of them tests data correctness. The sentence below is drafted for
use *after* the design ships, and must not be published before then.

> Once implemented, Selvedge will detect casual and accidental modification of its event
> log, and will produce an independently verifiable export. It is not proof against a
> motivated local attacker.

That sentence, or something equivalent, should appear anywhere the feature is described
once it exists — README, release notes, and any SEP-3004 thread comment. Until it exists,
outgoing text should say Selvedge has *assessed* itself against the contract, not that it
conforms to any part of it. The thread has spent two weeks punishing unchecked claims,
including two participants publicly correcting their own prior statements (Tetsurohhori,
2026-08-05; wowlegend, 2026-08-09). Overstating this would be worse than not shipping it.

### What would raise the bar — future work

**Anchoring**, and the natural anchor is already installed. The post-commit hook runs on
every commit and already writes to the DB (`backfill_git_commit`). It could additionally
record the current chain head — `{seq, chain_hash}` — into the commit itself, via a git
note or a trailer. The git history then witnesses the chain: an attacker who rewrites
Selvedge history must also rewrite git history, which is pushed, mirrored, and observed
by other people. The witness is external, free, and already in the workflow.

Second option: an external timestamp authority. Stronger against a fully local attacker,
but it needs a network call, which collides with the zero-network line and the planned
`tests/test_no_network.py` (`docs/architecture.md:1316-1324`). Git anchoring does not.

This places Selvedge on a specific side of the live §2.8 argument in the SEP-3004 thread.
The SEP calls external anchoring "a distinct, weaker-priority threat — an *auditing
organization* rewriting its own history." wowlegend's objection
([#issuecomment-5227112418](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/3004#issuecomment-5227112418))
is that when the recorder is a party to what it records, that is precisely the case that
decides whether the record is usable *against* the recorder. For a local-first store
where the operator owns the file, the objection is straightforwardly correct: internal
chaining is the weaker half, and anchoring is the half that matters. Selvedge is a
concrete deployment class that demonstrates it. Q-B is still open at PR head, and the
author has not responded to this line.

---

## 9. SEP-3004 alignment

Assessed against the normative text at SEP-3004 head
`377f8d260ded5b6854871b2ce3c73621ffcaef1d`, branch
`sep-tamper-evident-audit-record-contract` in `scottrhodes/modelcontextprotocol`,
resolved on 2026-08-10 via:

```
gh api repos/modelcontextprotocol/modelcontextprotocol/pulls/3004 --jq '.head.sha'
```

The PR has had thread activity since (last recorded 2026-08-10T11:50Z), so re-run that
command and re-check the head before relying on any section number or quotation below in
outgoing text.

### Where design (d) would conform

- **§2.4 (chain construction).** SHA-256 baseline, `previous_hash` equal to the
  immediately preceding record's hash, `null`/`""` for the first record in a segment.
  Direct match.
- **§2.6 (verification).** Two steps, deterministic, same verdict for self-attester and
  third party. Direct match.
- **§2.7 (attestation manifest).** All four required fields are producible:
  `storage_mechanism` = SQLite sidecar table, `chain_algorithm` = SHA-256,
  `canonical_form_version` = `selvedge-chain/1`, `verification_procedure_ref` = the
  `selvedge verify` implementation. Notably, the "bare 'trust us' claim is not conformant"
  rule is satisfiable here precisely because the verifier ships in the package.
- **§2.3 structurally**: lexicographic key sort at every level, no insignificant
  whitespace, `null` encoded distinguishably from `""`. And Selvedge is *stricter* on the
  bare-number problem — all protected values are strings, so `1` vs `1.0` vs `1e0` cannot
  arise (§4.2 rule 3).

### Where it would NOT conform, and why

**1. §2.3 control characters — the substantive one.** The SEP's normalization rule for
protected free-text fields states that "Control characters — including the C0 set (tab
U+0009, LF U+000A, CR U+000D) — are **rejected**, not trimmed," and that "A protected
string failing normalization makes the record non-conforming." **A unified diff without
newlines is not a diff.** Selvedge's `diff` and
`reasoning` fields are multi-line by nature; this is not an edge case, it is the primary
content. As drafted, §2.3 makes it structurally impossible to carry a diff or multi-line
rationale in a protected field.

This appears to be unraised in the thread and it is not a Selvedge-specific problem — any
audit record carrying a code diff, a stack trace, a log excerpt, or a multi-line
justification hits it. It pairs with the late-binding gap in §1: both are cases where the
contract's rules were drawn for short, single-line, machine-generated attribute values and
have not been tested against records whose payload is human- or agent-written prose.

**2. §2.5 append-only.** Selvedge fails this on two counts, and neither is hidden:
`backfill_git_commit` issues an in-place `UPDATE` through a normal access path, and
`prune --include-events` issues a `DELETE`. §2.5's named conforming mechanisms —
permission revocation plus row-level policy, write-once media, an append-only log service,
a ledger — assume infrastructure a local SQLite file does not have and, in the prune case,
assume the operator should not be permitted to delete their own local data. Selvedge's
answer is that both operations append a boundary record, which is *not* what §2.5 asks
for.

**3. §2.1 protected core.** `git_commit` is excluded (§2d). SEP-3004's core has no
opt-out; the SEP's Security Implications concede that "fields an extension registration
chooses to leave out of its data object are unprotected," but that is a registration-time
shape choice, not a timing mechanism.

**4. §2.3 length cap.** Baseline 8192 code units; Selvedge diffs exceed it routinely,
bounded instead by `diff_bytes` / `reasoning_bytes`.

### What this is actually worth to the thread

Not another implementation. The thread already carries GIF plus at least four independent
implementations landing on the published digests, a two-language conformance suite at
`tersignhq/evidence-record-conformance`, and the SEP author's own position
([#issuecomment-4871440335](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/3004#issuecomment-4871440335))
that reproduction "needs no coordination at all." An n-th reproduction adds nothing.

What no one has filed is an **adoption report from a store that existed first**. The
deployments reported so far are evidence streams by construction — built as hash chains,
against the contract's own shape — rather than pre-existing stores with their own schema
retrofitted to it: navigatorbuilds' "small self-hosted testnet, not a production network";
Tetsurohhori's append-only JSONL chain (a real deployment, self-described as "a small
independent deployment (~3 weeks live, four weekly anchor batches, **16 append-only
lines**)", who notes he has "not experienced a chain break in production yet, so I have no
operational recovery story to offer"); Tersign's system, which is an evidence-record system
by construction. Selvedge is an agent-change
store with its own schema and its own event model that predates SEP-3004, measured against
the contract and found non-conformant in three specific, structural, generalizable places:
late-bound fields (§1), multi-line protected content (§9), and an operator's right to
delete their own local data (§2.5).

The thread's own stated currency is exactly this. Tetsurohhori: "A suite where each
deployment brings its own vectors measures self-consistency, not conformance." wowlegend:
"A vector only counts when the deployment it fails did not write it." And the Security IG
charter's Office Hours purpose list literally includes **deployment reports**
(`docs/community/interest-groups/security.mdx`), while SEP-3004 does not currently appear
on that charter's Discussion Topics table.

Two corrections that must not survive into any outgoing text, both currently wrong in
tracked files: `docs/architecture.md:1999-2000` claims the project lists "6 IGs, none
audit-related." The Security Interest Group exists, its charter contains the phrase
"tamper-evident," and its scope line reads: "**Auditability and observability**:
requirements for tamper-evident records of what a tool call did and under what authority,
for compliance and incident review." The same defect appears in the untracked
`RADAR-AMENDMENT-2026-08-06.md`. Fixing `architecture.md` is out of scope here and is
flagged, not edited.

---

## 10. Test plan

Target: ~20 tests in a new `tests/test_tamper_evidence.py`. All use `tmp_path` and
`SELVEDGE_DB` per `CLAUDE.md`; none touch the network.

**Canonicalization (6)**
1. The §4.4 worked vector: preimage bytes and both digests, frozen. A refactor that
   changes the bytes fails here first.
2. Key order is lexicographic and independent of dict insertion order.
3. Escaping: a value containing `"`, `\`, newline, tab, U+0001, and non-ASCII (`café`)
   produces the exact byte string from §4.2 rule 6 — no `\uXXXX` for non-ASCII, lowercase
   hex for the control.
4. `""` and SQL `NULL` in a nullable column produce the **same** digest (the coalesce rule,
   §4.2 rule 4) — the pre-v3-row hazard, pinned.
5. `metadata` is hashed as the raw stored string: a row read via the raw column and one
   read through `_coalesce_event_nullables`' decoded dict must canonicalize identically.
6. Changing `git_commit` does **not** change `core_hash`. The asymmetric cut, asserted.

**Chain construction (4)**

7. Five sequential `log_event` calls produce five chain rows with `seq` 1..5 (after
   genesis) and correctly threaded `prev_hash`.
8. `log_event_batch` of N events chains in insertion order, one transaction.
9. `log_supersede` and `log_rename` append chain rows for their **new** events and leave
   the superseded/renamed rows' chain rows untouched.
10. Concurrent writers (reuse `tests/test_concurrency.py` harness) never fork the chain:
    all `seq` values distinct, `prev_hash` threading unbroken. This is the §7.1 gate.

**Tamper detection — the actual attack cases (5)**

11. **The core case.** Write 5 events. Open the DB directly with `sqlite3` and
    `UPDATE events SET reasoning = 'rewritten' WHERE id = <third event>`. Assert
    `chain_intact` is `FAIL`, `exit_code(results) == 1`, and the detail **names the
    sequence number of the third record** — not just "chain broken." The seq is the whole
    diagnostic value.
12. Direct `DELETE FROM events WHERE id = ?` with no tombstone → `chain_intact` FAIL,
    detail reports an unaccounted absent row.
13. Direct `UPDATE events SET entity_path = ?` (the repointing attack from §2a) →
    `chain_intact` FAIL.
14. Tampering with the **chain table** — `UPDATE event_chain SET core_hash = ?` to match a
    doctored events row — still fails, because `chain_hash` no longer matches and the link
    to `seq+1` breaks. Confirms the chain is not defeated by editing one table.
15. **Negative control:** a normal `backfill_git_commit` run over a window containing
    several events leaves `chain_intact` at `PASS`. This is the test that proves the design
    solved the problem it was built for, and it should be named so it cannot be quietly
    retired — the same reasoning as
    `test_cron_footgun_yes_without_destructive_env_errors` (`prune.py:8-12`).

**Legitimate mutation and boundary records (3)**

16. `migrate-paths --apply` on rows with non-canonical paths: chain stays intact, one
    covering `amend` record per run with the rewritten-row list in `detail` *(amended
    2026-08-29 per the open-question-4 decision — originally "one record per rewritten
    row")*, and `path_migrations` still gets its audit row.
17. A gated `prune --include-events` (both gates satisfied): rows deleted, chain rows
    retained, one tombstone appended, `chain_intact` PASS.
18. Two prunes accumulate `cumulative_count` correctly and both still verify.

**Genesis and coverage (2)**

19. A DB with rows written before the chain existed (simulate by inserting via raw
    `sqlite3`, then constructing storage): `chain_coverage` is `WARN` with the right count,
    `chain_intact` is `PASS`, default exit code is 0, `--strict` is 1. "Unchained, not
    invalid," asserted.
20. Verification of a 50k-event store completes with bounded memory — assert the
    pagination path is used rather than a `fetchall`, e.g. by monkeypatching the page size
    to 10 and counting queries.

**Budget note.** ~20 tests. No phase currently carries this work. The natural home is
Phase 2.18 / v0.3.12, as a sibling to the already-planned `test_append_only.py`
(`docs/architecture.md:1357-1365`, unchecked, file does not exist) — same theme, the
positioning claim backed by CI. But 2.18's budget is "≤ 35 new tests"
(`docs/architecture.md:2080`) and already absorbs the git-import cluster plus context-cost
CI. Adding 20 more overruns it, so per `CLAUDE.md` the release notes must say why, or the
work gets its own phase. **That is a scheduling decision for Mason, not one this document
should make.**

---

## 11. Open questions

1. **Retroactive seal** (§5.2) — ship `selvedge chain seal` as labelled opt-in, or refuse
   it entirely? Refusing is more honest; labelling is more useful and keeps the honesty in
   the data. Leaning toward shipping it labelled, but after the core.
2. **Unaccounted-gap recovery.** Downgrade → prune with old code → upgrade produces
   absent rows with no tombstone and a hard `chain_intact` FAIL with no way out short of
   rebuilding. A `selvedge chain seal --acknowledge-gap` that appends a boundary record
   converting an unexplained absence into a signed-off one would fix it — same pattern,
   more surface. Deferred.
3. **Tombstone precision** (§8) — count-only, or a digest over the pruned `seq` list? The
   digest is strictly stronger and streamable with a running hash, but ordering gets
   awkward when an importer inserts an old-timestamped event at a high `seq` that a later
   prune removes. Count-only is simple and honest; revisit if it proves too weak.
4. **Per-row `amend` vs one covering record** for a large `migrate-paths` run (§5.3).
   **Resolved (2026-08-29): one covering record per run**, with the full row list in
   `detail` — recorded in `chain.append_amend`'s docstring and locked by
   `test_migrate_paths_apply_appends_one_covering_amend`. §5.3 and §10 item 16 are
   amended to match.
5. **Does the chain cover `tool_calls`?** This proposal says no — telemetry, ungated
   prune, not part of the decision record. Worth stating explicitly in the manifest so the
   omission is declared rather than discovered.
6. **Manifest surface.** SEP-3004 §2.7 wants four fields. Where does Selvedge emit them —
   `selvedge verify --json`, a new `selvedge manifest`, or the Agent Trace export bundle
   preamble? The export bundle is tempting, but note the cautionary case already in hand:
   `verification_procedure_ref` must be "a resolvable pointer," and Selvedge's own
   upstream spec repo `github.com/cursor/agent-trace` now returns **HTTP 404**. A manifest
   that points at a URL is a manifest that will eventually point at nothing. Pointing at
   the installed package is more durable than pointing at a host.
