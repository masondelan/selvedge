# Coding memory evidence

Phase: **method-pilot**. 18 / 18 planned slots accounted for; 3 author-written synthetic tasks.

Configured model: `gpt-6-sol`; effort: `medium`. Provider identity/build is only verified where the retained client metadata exposes it.

## Every draw

| Slot | Task | Arm | Draw | Status | Correct | Score | Final specific behavior | First-write specific behavior | History evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | message-visibility | maintained-markdown | 1 | completed | True | 1.0 | none observed | none observed | file read before write |
| 2 | message-visibility | selvedge-injected | 1 | completed | True | 1.0 | none observed | none observed | injected |
| 3 | message-visibility | no-memory | 1 | completed | True | 1.0 | none observed | none observed | not observed / N/A |
| 4 | chunking | no-memory | 1 | completed | True | 1.0 | none observed | none observed | not observed / N/A |
| 5 | chunking | maintained-markdown | 1 | completed | True | 1.0 | none observed | none observed | file read before write |
| 6 | chunking | selvedge-injected | 1 | completed | True | 1.0 | none observed | none observed | injected |
| 7 | retry-backoff | maintained-markdown | 1 | completed | True | 1.0 | none observed | none observed | file read before write |
| 8 | retry-backoff | selvedge-injected | 1 | completed | True | 1.0 | none observed | none observed | injected |
| 9 | retry-backoff | no-memory | 1 | completed | True | 1.0 | none observed | none observed | not observed / N/A |
| 10 | message-visibility | no-memory | 2 | completed | True | 1.0 | none observed | none observed | not observed / N/A |
| 11 | message-visibility | selvedge-injected | 2 | completed | True | 1.0 | none observed | none observed | injected |
| 12 | message-visibility | maintained-markdown | 2 | completed | True | 1.0 | none observed | none observed | file read before write |
| 13 | retry-backoff | no-memory | 2 | completed | True | 1.0 | none observed | none observed | not observed / N/A |
| 14 | retry-backoff | selvedge-injected | 2 | completed | True | 1.0 | none observed | none observed | injected |
| 15 | retry-backoff | maintained-markdown | 2 | completed | True | 1.0 | none observed | none observed | file read before write |
| 16 | chunking | no-memory | 2 | completed | True | 1.0 | none observed | none observed | not observed / N/A |
| 17 | chunking | selvedge-injected | 2 | completed | True | 1.0 | none observed | none observed | injected |
| 18 | chunking | maintained-markdown | 2 | completed | True | 1.0 | none observed | none observed | file read before write |

Specific behavior uses predeclared behavioral predicates. Unassessed code, incomplete runs and arbitrary errors are not classified as a specific failure. Injection records delivery; it does not prove attention or correct application.

| Task | Arm | Stage | Assessed | Unassessed | Repeat | Harmful avoidance | Irrelevant signature |
| --- | --- | --- | --- | --- | --- | --- | --- |
| retry-backoff | no-memory | grade | 2 | 0 | 0 | 0 | 0 |
| retry-backoff | no-memory | first_write_grade | 2 | 0 | 0 | 0 | 0 |
| retry-backoff | maintained-markdown | grade | 2 | 0 | 0 | 0 | 0 |
| retry-backoff | maintained-markdown | first_write_grade | 2 | 0 | 0 | 0 | 0 |
| retry-backoff | selvedge-injected | grade | 2 | 0 | 0 | 0 | 0 |
| retry-backoff | selvedge-injected | first_write_grade | 2 | 0 | 0 | 0 | 0 |
| message-visibility | no-memory | grade | 2 | 0 | 0 | 0 | 0 |
| message-visibility | no-memory | first_write_grade | 2 | 0 | 0 | 0 | 0 |
| message-visibility | maintained-markdown | grade | 2 | 0 | 0 | 0 | 0 |
| message-visibility | maintained-markdown | first_write_grade | 2 | 0 | 0 | 0 | 0 |
| message-visibility | selvedge-injected | grade | 2 | 0 | 0 | 0 | 0 |
| message-visibility | selvedge-injected | first_write_grade | 2 | 0 | 0 | 0 | 0 |
| chunking | no-memory | grade | 2 | 0 | 0 | 0 | 0 |
| chunking | no-memory | first_write_grade | 2 | 0 | 0 | 0 | 0 |
| chunking | maintained-markdown | grade | 2 | 0 | 0 | 0 | 0 |
| chunking | maintained-markdown | first_write_grade | 2 | 0 | 0 | 0 | 0 |
| chunking | selvedge-injected | grade | 2 | 0 | 0 | 0 | 0 |
| chunking | selvedge-injected | first_write_grade | 2 | 0 | 0 | 0 | 0 |

## Task-paired correctness

| Task | Left arm | Right arm | Left | Right | Difference (left − right) | Outcome |
| --- | --- | --- | --- | --- | --- | --- |
| retry-backoff | no-memory | maintained-markdown | 2/2 | 2/2 | +0.000 | tie |
| retry-backoff | no-memory | selvedge-injected | 2/2 | 2/2 | +0.000 | tie |
| retry-backoff | maintained-markdown | selvedge-injected | 2/2 | 2/2 | +0.000 | tie |
| message-visibility | no-memory | maintained-markdown | 2/2 | 2/2 | +0.000 | tie |
| message-visibility | no-memory | selvedge-injected | 2/2 | 2/2 | +0.000 | tie |
| message-visibility | maintained-markdown | selvedge-injected | 2/2 | 2/2 | +0.000 | tie |
| chunking | no-memory | maintained-markdown | 2/2 | 2/2 | +0.000 | tie |
| chunking | no-memory | selvedge-injected | 2/2 | 2/2 | +0.000 | tie |
| chunking | maintained-markdown | selvedge-injected | 2/2 | 2/2 | +0.000 | tie |

All planned draws are in the denominator. Incomplete draws contribute zero to aggregate functional scoring and retain their actual status above. Ties and losses are retained; no best-of selection is used.

## Operational observations

| Arm | Component | Known | Unknown | Mean | Median |
| --- | --- | --- | --- | --- | --- |
| no-memory | seconds | 6 | 0 | 19.337 | 19.259 |
| no-memory | input_tokens | 6 | 0 | 57286.833 | 57549.500 |
| no-memory | cached_input_tokens | 6 | 0 | 48256.000 | 46976.000 |
| no-memory | output_tokens | 6 | 0 | 543.500 | 538.000 |
| maintained-markdown | seconds | 6 | 0 | 20.190 | 19.631 |
| maintained-markdown | input_tokens | 6 | 0 | 62516.000 | 60042.000 |
| maintained-markdown | cached_input_tokens | 6 | 0 | 52970.667 | 52736.000 |
| maintained-markdown | output_tokens | 6 | 0 | 551.500 | 520.000 |
| selvedge-injected | seconds | 6 | 0 | 20.706 | 19.334 |
| selvedge-injected | input_tokens | 6 | 0 | 61549.667 | 60163.000 |
| selvedge-injected | cached_input_tokens | 6 | 0 | 49792.000 | 50304.000 |
| selvedge-injected | output_tokens | 6 | 0 | 546.667 | 556.500 |

Unknown usage is not zero. Cached input is a subset of input and is not added again. Latency is elapsed client time, not a controlled speed comparison or a payment receipt.

## Evidence and limits

The manifest retains frozen settings, fixtures and source hashes. export-index.json separately hashes original permitted inputs and sanitized publication files. Sanitized history or prompts may have different bytes from their original input hashes. Raw execution logs, databases, account/session metadata and private client reasoning are excluded. Recorded synthetic decision rationale is retained.

This is a descriptive method pilot. Repeated draws are not independent projects. Three small author-written tasks cannot establish a general confidence interval, significance, superiority, causal memory effect, or real-world product benefit. Native memory, autonomous capture and human review effort are not measured. Technical smoke and the method matrix are separate evidence sets and are not pooled.

## Separate manual decision-to-test demonstration

This deterministic synthetic demonstration is not a model draw and is not pooled with the pilot. An explicit changed user preference authorizes supersession; passing tests alone does not authorize a preference change.

| Check | Correct |
| --- | --- |
| old_contract_old_code | True |
| new_contract_old_code | False |
| new_contract_new_code | True |

Old rationale preserved: True. Human review time was not measured; see manual/result.json for the observed material.
