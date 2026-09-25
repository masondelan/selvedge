# September 25, 2026 configuration pilot

48 measured trials, all completed. Claude Code **2.1.170**, requested and resolved
model **claude-sonnet-4-6**, Selvedge **0.3.14**, three concurrent clients. Source
hashes in the [manifest](manifest.json) match the published runner, fixture server
and cases. These are four synthetic cases, three repeats per condition.

| Condition | Correct final choices | Observed retrieval before first edit |
| --- | --- | --- |
| No memory | 8/12 | 0/12 |
| Decision-file fixture | 12/12 | 12/12 |
| Same facts inline | 12/12 | 0/12 (facts supplied directly) |
| Selvedge MCP retrieval | 12/12 | 9/12 |

Selvedge retrieved the relevant record before editing in all nine trials with
relevant prior memory. In the three unrelated-memory trials, it selected the
correct thumbnail format without retrieving the irrelevant archive decision.
The decision-file fixture returned its full record set, including that irrelevant
record. Thus the retrieval totals are observations, not comparable success rates.

| Case | No memory | Decision file | Inline facts | Selvedge |
| --- | --- | --- | --- | --- |
| Worker budget | 1/3 | 3/3 | 3/3 | 3/3 |
| Archive format | 1/3 | 3/3 | 3/3 | 3/3 |
| Changed constraint | 3/3 | 3/3 | 3/3 | 3/3 |
| Unrelated memory | 3/3 | 3/3 | 3/3 | 3/3 |

These results are consistent with prior decision information helping on the two
retained-constraint tasks. They **do not show an advantage over a maintained file
or well-supplied prompt**, and do not establish a general causal effect, native
hook reliability, real coding performance or recommendation ranking. See the
[method and limitations](../../README.md).

[Every measured result](results.jsonl) is retained, including the four incorrect
no-memory choices. Each named trial directory contains the exact prompt, public
text/tool trace, fixture execution trace and final configuration. Thinking blocks,
account initialization data, local paths, raw streams, MCP launch configurations
and database files are excluded. CLI usage estimates are not extra-billing receipts.

## Technical smoke history

Before measuring, one trial failed because deferred MCP tools were not exposed
to the model. It claimed an edit but wrote no configuration. The scorer marked
it incomplete. The runner was corrected to load tools eagerly and validate the
available tool set. A separate four-run smoke then completed: three memory
conditions correct, no-memory incorrect. Neither smoke is included in the 48
measured trials. Their sanitized results are retained in `technical-smoke/`;
the first failure used the pre-fix runner and is not a scored run of this version.
