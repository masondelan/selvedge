# Coding-memory method pilot — October 10, 2026 UTC

All **18 planned attempts completed and passed**: no memory, maintained Markdown
and Selvedge-injected history each scored **6/6**. There was no observed advantage
for either memory arm. The no-memory arm had no repeat failures to reduce; this result does not support performance or superiority copy.

| Applicable behavior | No memory | Markdown | Injected history |
| --- | --- | --- | --- |
| Uncapped retries, still-valid technical rejection | 0/2 | 0/2 | 0/2 |
| Stale participant-only preference after explicit change | 0/2 | 0/2 | 0/2 |
| Unrelated concurrency cap applied to chunk sizing | 0/2 | 0/2 | 0/2 |

Both first-written and final code had zero matching adverse signatures. These
are three author-written function tasks, with two blind draws per task/arm—not
18 independent projects. All current requirements were visible in every arm.
Keep these ties; any broader follow-up needs its own preregistered task sample.

The Markdown file was read before the first write in **6/6** file-arm attempts.
The injection arm received identical history bytes from fresh real Selvedge
stores. This verifies delivery and observable retrieval, not attention,
autonomous capture, native memory or an automatic lifecycle hook.

## Evidence

- [Complete matrix report](matrix/REPORT.md), including every draw, task-paired
  differences, usage, latency and limitations.
- [Frozen matrix manifest](matrix/manifest.json) and
  [hash index](matrix/export-index.json). Model execution used source commit
  `14dd2a5c81b45890ff86f5ba066f8f546aa7c2e9` from 04:00:30 to 04:06:34 UTC.
- [First technical smoke](smoke-1/REPORT.md): failed because all three attempted
  MCP tool calls required approval; no code was written. Its original record
  says `missing_implementation`. This is a setup failure, not a historical-path
  recurrence or model-quality comparison.
- [Second technical smoke](smoke-2/REPORT.md): passed after explicit grants for
  the four bounded fixture tools. Global approval policy and read-only client
  restrictions remained in place. Both smoke attempts are separate from the
  method matrix and are retained, not pooled or replaced.
- [Manual decision-to-test experiment](matrix/manual/result.json): the old code
  passes the old preference, fails the changed preference, then corrected code
  passes. Explicit supersession preserves the old reasoning. This is an authored
  feasibility demonstration; no participant trial or human review time is claimed.

The configured model was `gpt-6-sol`, medium effort, on client build
`0.159.0-alpha.12.1`. Provider-resolved model identity/build was not exposed.
Cached input is part of input usage, not an extra amount to add. Latency is an
operational observation, not a controlled speed comparison or payment receipt.

After the matrix, historical labels in the separate manual demonstration were
corrected to describe the old participant-only contract. The manual demonstration
was rerun and carries its own source hashes. This presentation correction did
not change matrix tasks, grader, inputs, model attempts or results.

Local validation: **1,331 tests passed**, 90.24% core coverage, Ruff passed,
mypy passed for 39 core source files, and the MCP schema budget passed at
4,504/4,600 core tokens. Python 3.14.4 was used locally; other runtime matrix
results are not implied. Independent review and acceptance remain pending.

Exports contain synthetic prompts, visible tool traces, candidates, grades,
settings and checksums. Raw client streams, databases, account/session details
and private model reasoning remain excluded. Original-input hashes and exported
hashes are separate; transformation flags show any changed bytes.
