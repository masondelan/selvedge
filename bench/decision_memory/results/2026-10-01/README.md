# October 1, 2026 rejected-path injection pilot

All **24 measured trials completed**. Same Claude Code 2.1.170, requested and
resolved model `claude-sonnet-4-6`, Selvedge 0.3.15, four fixed synthetic cases,
three fresh sessions per case per condition. Seeded shuffled schedule and source
hashes were frozen before execution in [manifest.json](manifest.json).

| Condition | Still-valid rejected choice repeated | All final choices correct | Stale/unrelated controls correct | Incomplete |
| --- | --- | --- | --- | --- |
| No memory | 4/6 (66.7%) | 8/12 | 6/6 | 0 |
| Selvedge records injected | 0/6 (0%) | 12/12 | 6/6 | 0 |

The denominator for repeat failure is **six completed retained-constraint
trials**, not all twelve trials per condition. Selecting a different wrong
option is a task error but is not counted as repeating a rejected path. Scoring
uses the final written choice; it does not score every exploratory tool call.
The previous failed attempts are author-seeded history, not failures observed
from these same agent sessions. This measures reselection of those paths.

The injected arm calls real `get_prior_attempts` on a fresh pre-seeded store,
then supplies that entity-scoped result before the agent starts. It tests
**controlled injection**, not native hook execution or voluntary retrieval.
Neither condition has Selvedge MCP tools; the shared fixture `read_decisions`
returns no records in both. Both have identical fixture tools, system prompt, current task and project evidence. Only the injected
condition receives the prior-attempts context (an empty list on unrelated-memory).
Context size is not token-matched. Capture quality is not measured.

| Case | No memory correct | Injection correct |
| --- | --- | --- |
| Worker budget | 1/3 | 3/3 |
| Archive format | 1/3 | 3/3 |
| Changed constraint | 3/3 | 3/3 |
| Unrelated memory | 3/3 | 3/3 |

A narrow description supported by this run is: **In a 24-trial synthetic
configuration pilot, the same agent reselected still-valid rejected options in
4 of 6 eligible no-memory trials and 0 of 6 with injected Selvedge records.**
Two author-written eligible tasks and three repeats are too small to establish
general coding performance, statistical reliability, or product superiority.
The [earlier four-condition pilot](../2026-09-25/) tied Selvedge, maintained-file
and inline-fact conditions. This run does not overturn that result or compare
against competing memory products. No broad percentage-reduction headline is warranted.

Every [result](results.jsonl), exact prompt, observable text/tool trace, fixture
execution and final configuration is retained; injected trials additionally
include the exact retrieved payload. Private thinking, raw streams, host paths,
account data, database files and MCP configs are excluded. Two successful
technical smoke trials are preserved separately in `technical-smoke/` and
excluded from every measured count. No failed measured trial was discarded.
Three clients ran concurrently; elapsed times are not latency benchmarks.

Reproduce with the current repository installed and existing Claude subscription:

```bash
python bench/decision_memory/run.py --model claude-sonnet-4-6 \
  --arms no-memory selvedge-injected --trials 3 --workers 3 \
  --output /tmp/selvedge-injection-measured --execute
```

A new output directory is mandatory. No API credits or overages are enabled by
the harness. See [method and limits](../../README.md).
