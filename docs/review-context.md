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

The comment is bounded to 100 events by default and under GitHub's comment size
limit. Counts describe the full matching population; omissions are disclosed.
Text fields over 1,200 characters are labeled truncated. Use local `ledger --json`
for selected full text and `--limit` up to 1,000 for larger reports.

A passing chain check means the stored chain recomputes for this snapshot. It
does not authenticate actors, prove capture completeness, attest correctness or
provide an independent external anchor. Unchained legacy rows are reported
separately; a local writer can recompute hashes. `git_commit` is outside the
protected core. Broken chains are reported and make the Action fail; absent
chain coverage is shown as unavailable rather than proof of validity.
