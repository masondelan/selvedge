# Coding memory evidence

Phase: **technical-smoke**. 1 / 1 planned slots accounted for; 1 author-written synthetic tasks.

Configured model: `gpt-6-sol`; effort: `medium`. Provider identity/build is only verified where the retained client metadata exposes it.

## Every draw

| Slot | Task | Arm | Draw | Status | Correct | Score | Final specific behavior | First-write specific behavior | History evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | retry-backoff | no-memory | 1 | completed | True | 1.0 | none observed | none observed | not observed / N/A |

Specific behavior uses predeclared behavioral predicates. Unassessed code, incomplete runs and arbitrary errors are not classified as a specific failure. Injection records delivery; it does not prove attention or correct application.

| Task | Arm | Stage | Assessed | Unassessed | Repeat | Harmful avoidance | Irrelevant signature |
| --- | --- | --- | --- | --- | --- | --- | --- |
| retry-backoff | no-memory | grade | 1 | 0 | 0 | 0 | 0 |
| retry-backoff | no-memory | first_write_grade | 1 | 0 | 0 | 0 | 0 |

## Task-paired correctness

| Task | Left arm | Right arm | Left | Right | Difference (left − right) | Outcome |
| --- | --- | --- | --- | --- | --- | --- |

All planned draws are in the denominator. Incomplete draws contribute zero to aggregate functional scoring and retain their actual status above. Ties and losses are retained; no best-of selection is used.

## Operational observations

| Arm | Component | Known | Unknown | Mean | Median |
| --- | --- | --- | --- | --- | --- |
| no-memory | seconds | 1 | 0 | 21.768 | 21.768 |
| no-memory | input_tokens | 1 | 0 | 66535.000 | 66535.000 |
| no-memory | cached_input_tokens | 1 | 0 | 55552.000 | 55552.000 |
| no-memory | output_tokens | 1 | 0 | 547.000 | 547.000 |

Unknown usage is not zero. Cached input is a subset of input and is not added again. Latency is elapsed client time, not a controlled speed comparison or a payment receipt.

## Evidence and limits

The manifest retains frozen settings, fixtures and source hashes. export-index.json separately hashes original permitted inputs and sanitized publication files. Sanitized history or prompts may have different bytes from their original input hashes. Raw execution logs, databases, account/session metadata and private client reasoning are excluded. Recorded synthetic decision rationale is retained.

This is a descriptive method pilot. Repeated draws are not independent projects. Three small author-written tasks cannot establish a general confidence interval, significance, superiority, causal memory effect, or real-world product benefit. Native memory, autonomous capture and human review effort are not measured. Technical smoke and the method matrix are separate evidence sets and are not pooled.
