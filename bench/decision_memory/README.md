# Decision-memory configuration pilot

A reproducible **synthetic configuration-choice pilot**, separate from Selvedge
core. It tests whether a fresh agent retrieves supplied decisions and makes an
appropriate choice. It is not a repository coding benchmark or evidence that
Selvedge is universally better than other memory tools.

[Published September 25, 2026 results](results/2026-09-25/): 48 completed trials.
Selvedge, the decision-file fixture and inline facts each made 12/12 correct
choices; no memory made 8/12. This does not demonstrate a Selvedge advantage over
the other information-delivery conditions.

## Cases and controls

Four transparent cases cover a worker-budget constraint, a rejected archive
format, changed evidence that makes the old rejection obsolete, and irrelevant
memory from another entity. Each has one expected configuration value.

Every case runs in four conditions:

| Condition | Information available |
| --- | --- |
| `no-memory` | Current project evidence only |
| `decision-file` | Same prior facts through a fixture's `read_decisions` tool |
| `inline-context` | Same prior facts supplied directly in the task prompt |
| `selvedge-pull` | Same prior facts pre-seeded in a fresh real Selvedge SQLite store, queried through its MCP server |

The decision-file condition is a controlled stand-in for a maintained file, not
Claude's native memory implementation. The inline condition contains the same
facts, but tool schemas mean total context is **not token-matched**. No arm sees
the expected answer. Only fixture tools can change the chosen configuration.

## Run

Use Python 3.10+ with the repository installed (`python -m pip install -e '.[dev]'`),
and an installed Claude Code CLI authenticated to an existing Claude subscription.
Runs consume the subscription's available allowance. Check your own usage and
billing settings; this script does not enable or purchase usage credits. API-key
and alternate-provider environment overrides are removed for the child process.

```bash
# Prepare and inspect a frozen schedule without any model call.
python bench/decision_memory/run.py --model claude-sonnet-4-6 \
  --output /tmp/selvedge-pilot-plan

# Technical smoke: four runs, excluded from the measured pilot.
python bench/decision_memory/run.py --model claude-sonnet-4-6 \
  --case worker-budget --trials 1 --output /tmp/selvedge-pilot-smoke --execute

# Measured pilot: 4 cases × 4 conditions × 3 trials = 48 fresh processes.
python bench/decision_memory/run.py --model claude-sonnet-4-6 \
  --output /tmp/selvedge-pilot-measured --workers 3 --execute
```

Choose a full supported model ID for your run. The runner checks the resolved
model matches the requested ID. A new output directory is required, so a rerun
cannot overwrite a failure. At most three fresh clients run concurrently when `--workers 3` is selected.
Elapsed times are operational observations, not a controlled latency benchmark;
clients may share provider prompt caches. The seeded schedule, cases, system prompt, source
hashes, package version and CLI version are retained in `manifest.json`.

Native auto-memory, hooks, skills and unrelated MCP servers are disabled.
Built-in filesystem/network tools are unavailable. MCP tools are loaded eagerly;
a missing or unexpected tool set invalidates the run. Each subprocess has a fresh
working directory and database, and cannot read the scorer through its tools.
The custom system prompt is the same across conditions.

## Evidence and scoring

`results.jsonl` includes every attempted trial. The runner stops after the current batch if a trial is incomplete; retain it and label a rerun separately. A successful
completion requires the expected tool configuration, successful client process,
final result, and an actual written configuration. Text claiming an edit is not
an edit.

`correct_application` compares the final written value with the case's expected
value. `retrieved_before_first_edit` requires an observed successful tool result
containing a seeded record ID (Selvedge) or the saved rationale (decision-file)
before the first edit call. It counts retrieval even for the deliberately
irrelevant record; applicability is scored separately. Inline context is supplied
by design and is not counted as a retrieval. Tool errors do not count.

Each trial retains a tool trace, final configuration, elapsed time and the CLI's
reported token usage. Token usage can include provider caching. Any reported
`costUSD` is the CLI's accounting estimate, **not a receipt for extra billing**.
Inspect `tool-trace.json` before publication; it omits initialization/account
metadata and thinking blocks and replaces local home/trial paths. Raw streams,
MCP configs and database files are local debugging evidence and must not be
published without separate review.

## Limits and next evidence

These are four author-written, small-choice fixtures with only three repeated
trials per condition. Decisions are pre-seeded; capture quality and write-time
cost are unmeasured. Correct choice does not establish a causal effect of
retrieval. This pilot does not test autonomous discovery, real code edits,
native agent memory, hook delivery, competing products, cross-model behavior,
long-term retention or recommendation ranking. Publish all conditions, controls
and failures rather than only favorable examples.

The larger design in [evaluation feasibility](../../docs/eval-feasibility.md)
remains separate: real project tasks, stronger baselines, capture cost and
delivery ablations are still required for broader product claims.
