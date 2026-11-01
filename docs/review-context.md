# Shared decision ledger and PR review context

Use one SQLite store on the same host for the agents working on a project. Each
MCP server can point `SELVEDGE_DB` at that same absolute path. WAL serializes
writes; each event retains its `agent`, `session_id`, `changeset_id`, reasons,
rejected alternatives and explicit `supersedes` link. Give each agent a stable
name and each run a distinct session ID in `log_change`. These are self-reported
labels, not verified identities or signed attribution.

```bash
SELVEDGE_DB=/path/to/shared/project.db selvedge ledger --entity src/cache.py
SELVEDGE_DB=/path/to/shared/project.db selvedge ledger --json
```

The ledger reads one consistent snapshot without migrations or implicit database
creation. It resolves explicit revisions against the complete store even when
older events are outside the display limit. Multiple agents writing about one
entity is not automatically a conflict. Explicit cross-agent supersessions are
highlighted; semantic disagreement and unrecorded decisions remain unknown.
The existing MCP history/blame tools already expose actor and session fields;
no additional resident MCP schema is introduced.

Remote agents currently need a coordinator to relay their actual decisions into
the store. Identify the original actor **and relay**, preserve the source receipt,
and do not invent a direct MCP write. Do not share a WAL database through a
network filesystem or sync live DB copies. Authenticated HTTP access and a
server database remain separate work; this slice does not ship either.

## PR comment Action

`actions/review-context` comments the recorded decision trail for files changed
in a PR, including both names of a rename, rejected/reverted paths, recorded
reasons and invalidation conditions, agent/session attribution, explicit revision
links and whole-store chain verification/coverage. It updates its existing bot
comment on reruns. It uses deterministic templates and makes no model calls.
Recorded reasons are an explicit project artifact, not private model thinking.

When no recorded history matches and the chain check has no failure, the Action
does not create a new PR comment. It still writes the full job summary; no match
means unknown, not approval. If an earlier run already posted a comment, it
updates that comment to the current no-match result so old context is not left
looking current. Chain failures remain visible and fail the run, even with no
matching history. Dry-run continues to write only the job summary.

Example workflow (pin the Selvedge Action to a reviewed full commit SHA):

```yaml
name: Decision context
on:
  pull_request_target:
    types: [opened, synchronize, reopened]
permissions:
  contents: read
  pull-requests: write
concurrency:
  group: selvedge-review-${{ github.event.pull_request.number }}
  cancel-in-progress: false
jobs:
  context:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v7
        with:
          ref: ${{ github.event.pull_request.base.sha }}
          persist-credentials: false
      - uses: masondelan/selvedge/actions/review-context@REVIEWED_COMMIT_SHA
        with:
          token: ${{ github.token }}
          db-path: .selvedge/selvedge.db
          limit: '100'
          budget: '6000'
          dry-run: 'true' # inspect the job summary before enabling comments
```

Only point this Action at a tracked database already approved for publication
in that repository. Never supply a private operations store. Dry-run still
writes a job summary visible to workflow readers. The Action verifies that the
DB bytes equal the base commit and refuses WAL/SHM sidecars, a modified database,
checkout mismatch, incomplete file list or changed PR head/base. It does not
check out or execute PR head code; Python isolated mode imports the trusted
Action source directly, with no pip installation. Inputs are environment values,
not script interpolation. See GitHub's [pull_request_target guidance](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target).

The report shows **base history**, so decisions first logged in the PR do not
appear until merged. File matching follows exact paths and dotted subentities;
there is no inferred SQL-table/code-symbol mapping. No matching history means
unknown. A new PR revision may arrive after the final API check; every comment
therefore names its exact base/head hashes and should be read against those.
Workflow serialization reduces duplicate creation, but GitHub offers no atomic
comment upsert or compare-and-swap. Large PRs beyond GitHub's 3,000-file ceiling
fail explicitly. This first Action supports GitHub.com only.

The comment is compact. Decision evidence comes first: rejected and reverted
approaches, explicit revisions, superseded decisions and recorded constraints or
revisit/expiry conditions. Each entry leads with the recorded reason, then those
conditions and revision links, then one line of routine metadata (time, change
type, actor, event ID). Other recorded changes follow. Expiry and revisit-after
values are labeled as not evaluated. Each group keeps newest-first order.

The whole comment stays within the `budget` input (6,000 characters by default,
2,000–60,000). It links to the tracked store at the exact base commit and states
how many matching events were omitted by the event limit (`limit`, 100 by
default) and by the output budget, and how many of the events beyond `limit`
are decision evidence. It points to the job summary of the latest Selvedge
review run in fixed text, with no per-run link, so re-running on an unchanged
report leaves the comment untouched. The job summary repeats the compact view
followed by the longer per-event report with session, changeset, recorded commit
and chain head. Events beyond `limit` are counted but appear in neither report.
A source link too long for the budget (for example a very long `db-path`) is
replaced by a fixed note. Counts describe the full
matching population. Comment fields over 300 characters (1,200 in the long
report) are labeled truncated. Use local `ledger --json` for selected full text
and `--limit` up to 1,000 for larger reports. Protocol tests and the limited
synthetic native runs below cover the reported behavior.

### Native validation

On November 1, 2026 UTC, two temporary same-repository PRs exercised a frozen
three-record synthetic store at base
`2aae718a902cc42a6b8927602cca48832e69e231`, with `limit: 10` and `budget: 2000`.
[PR82's initial run and rerun](https://github.com/masondelan/selvedge/actions/runs/41263574121)
created one 1,556-character comment, prioritized the rejection and disclosed
the omitted routine match. The second invocation and a full unchanged rerun
left the comment untouched. After replacing the matching path,
[the refresh run](https://github.com/masondelan/selvedge/actions/runs/41263735508)
updated that same comment to a 961-character no-history report, then made no
further change. [Fresh no-history PR83](https://github.com/masondelan/selvedge/actions/runs/41263617441)
returned `no-matching-history` twice and created no Selvedge comment.

Every run used the exact trusted base and explicit invented fixture; a read-only
dry-run preceded the comment job. Both probe PRs were closed without merging.
The temporary workflow, fixture and dedicated tests were removed before release;
the Action's pinned Python-setup dependency remains. These receipts establish
only this synthetic lifecycle. Superseded and renamed paths, invalid-chain
handling and other layouts retain local test coverage; broader native cases,
human usefulness and native agent-hook activation are not established here.

A passing chain check means the stored chain recomputes for this snapshot. It
does not authenticate actors, prove capture completeness, attest correctness or
provide an independent external anchor. Unchained legacy rows are reported
separately; a local writer can recompute hashes. `git_commit` is outside the
protected core. Broken chains are reported and make the Action fail; absent
chain coverage is shown as unavailable rather than proof of validity.
