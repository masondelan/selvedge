# Prior-attempt recall output contract

This contract describes the v0.3.20 recall output. It keeps query diagnostics
separate from source event metadata and provides an explicit format for clients
that need status even when no events match.

## Choosing a format

| Surface | Successful result | Empty query |
| --- | --- | --- |
| CLI `prior-attempts ENTITY --json` | Legacy list and row keys | `[]`; integrity diagnostics on stderr |
| MCP `prior_attempts(entity_path=ENTITY)` | Legacy list and row keys | Clean: `[]`. Failed/unknown integrity: one shaped `match_type="note"` row |
| CLI `prior-attempts ENTITY --json-report` | Versioned report below | Report with `attempts: []` and explicit query integrity/warnings |
| MCP `prior_attempts(entity_path=ENTITY, output_format="report-v1")` | Identical versioned report | Identical versioned report |

`--json` and `--json-report` are mutually exclusive. MCP `output_format` defaults
to `rows`; unsupported format values return the existing one-element error list.
Ordinary CLI validation errors retain exit 2. A completed recall has exit 0,
including an unknown or failed integrity check; clients requiring intact evidence
must inspect report status rather than infer it from process success.

The existing opt-in fuzzy fallback note remains in both list forms. Reports put
that diagnostic in `warnings` and exclude note rows from `attempts`.

The default MCP uncertain-empty note is a deliberate compatibility exception.
MCP has no CLI stderr for the query, so silently returning `[]` would hide the
check failure. Clients must recognize `match_type="note"` or choose report-v1.
Default CLI stdout preserves its empty list, so CLI clients that discard stderr
should choose report-v1 to retain query diagnostics. Neither empty form proves
that no history exists outside the selected query, confidence floor or result
limit. Neither is approval to repeat an approach.

## Report version 1

A successful report has exactly these top-level fields:

```json
{
  "format": "selvedge.prior-attempts/1",
  "attempts": [],
  "integrity": {
    "status": "intact",
    "scope": "local_chain_snapshot",
    "reason": "checked"
  },
  "query_status": "no_match",
  "warnings": []
}
```

- `format`: the fixed version identifier above. A shape change requires another
  explicit format version, not silently changing report-v1.
- `attempts`: actual event rows only, newest first under the existing recall
  rules. Original IDs, source rationale, and event metadata are retained.
- `integrity`: exactly `status`, `scope`, and `reason`, all strings. Scope is
  `local_chain_snapshot`. Status is `intact`, `failed`, or `unknown`.
- `query_status`: `matched` when attempts are present, otherwise `no_match`.
  This describes the filtered result, not store completeness or confidence in
  an absence claim.
- `warnings`: human-readable strings. Clients must branch on structured status
  and reason, not parse this text.

The event rows retain the v0.3.19 fields:

```text
id timestamp entity_type entity_path change_type diff reasoning agent session_id
 git_commit project changeset_id metadata revisit_after expires_when supersedes
 constraint stale_when outcome confidence outcome_reasoning superseded_by
 supersede_reasoning current_status match_type similarity note
```

No `integrity` field is added to the event row. `metadata` is the original event
metadata object, including any source-owned key named `integrity`; query status
never overwrites it. `note` is the existing transient presentation field and can
carry an integrity warning. A note is not persisted to the store.

MCP keeps `structuredContent` wrapped as exactly `{"result": value}` for both
formats. `value` is the requested list or report. Its output schema advertises
both alternatives; the tool count remains eight. This wrapper was exercised over
real stdio in the validation artifact. The broader supported SDK/runtime matrix
must pass before release; one observed SDK is not a claim about every version.

## Integrity meaning and work limits

All event/trail reads, fuzzy record reads, and the bounded chain check use one
query-only SQLite transaction. There is no cross-request trust cache. A positive
result therefore refers to the same snapshot that produced the recalled records.
The existing chain verifier is reused, and full-store chain coverage must be
present for an intact signal.

| Status | Reason | Meaning |
| --- | --- | --- |
| `intact` | `checked` | Supported local chain and protected event contents were consistent in this snapshot |
| `failed` | `chain_check_failed` | The verifier reported a failure; evidence is retained with a warning |
| `unknown` | `chain_unavailable` | No usable chain evidence |
| `unknown` | `incomplete_coverage` | Coverage/genesis did not establish coverage of the full store |
| `unknown` | `unsupported_chain_format` | The bounded check does not support the observed format |
| `unknown` | `budget_exceeded` | A work bound was exceeded |
| `unknown` | `check_error` | Verification could not complete |
| `unknown` | `snapshot_required` | The low-level checker was called without a transaction |

Before walking the chain, the checker caps event rows and chain records at 1,000
each, and combined protected event/chain content at 8 MiB. It additionally caps
SQLite VM work at 2,000,000 steps and uses a best-effort 100 ms budget. The latter
is not a hard deadline: Python parsing/hashing and lock waits are not preempted.
Boundary records count, so 1,000 events plus genesis exceed the chain-record cap.
Over-budget recall still returns selected evidence with unknown integrity.

A valid legacy store can contain pre-chain records. Opening it through the normal
migration path preserves those records; their recall is useful but integrity is
unknown when coverage is incomplete. No retroactive verification is inferred.
The existing `read_only=True` schema compatibility requirement is unchanged;
unsupported legacy schemas need normal migration before that constructor accepts
them. Compatible read-only stores do not initialize or migrate during recall.
Fuzzy queries with no index produce a warning without creating an index table.

An intact local chain does not establish correctness of advice, identity,
authenticity against a writer controlling the store, or complete history.
`git_commit`, tool-call telemetry and semantic similarity are outside the
protected event-core check. `confidence="exact"` describes explicitly recorded
provenance, independently of integrity. `selvedge verify` remains the full local
audit, without the recall-specific work cap.
