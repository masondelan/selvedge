# Synthetic coding-memory pilot

Three runnable Python function tasks test a still-valid technical rejection,
a changed user preference with explicit supersession, and irrelevant history.
They extend the evidence beyond the earlier [configuration-choice pilot](../decision_memory/)
without changing Selvedge runtime behavior. Read the [versioned protocol](PROTOCOL.md)
before interpreting or running anything.

[October 10 method-pilot evidence](results/2026-10-10/README.md): all 18 attempts
passed, with a ceiling tie across all three arms. No performance advantage is
established; both technical smoke attempts are retained separately.

The fixture/grader and memory reconstruction are agent-agnostic. The optional
`codex_adapter.py` runs one installed client on an existing ChatGPT subscription;
it is an execution adapter, not a product compatibility restriction. No API
fallback or credit purchase is performed. Native agent memory is not tested.

## Reproduce locally

Install the repository in a supported Python 3.11–3.14 environment, including development
dependencies for tests. Candidate grading uses a restricted Python subset;
these are small function edits, not arbitrary repository coding tasks.

```sh
python -m pytest tests/test_coding_memory_*.py

# Offline: actual before/after tests and existing Selvedge decision APIs.
python -m bench.coding_memory.manual_decision_test --output /tmp/decision-test-1

# Offline: freeze fixtures, sources, history and a seeded schedule; no model call.
python -m bench.coding_memory.run prepare --phase technical-smoke \
  --case retry-backoff --arms no-memory --trials 1 \
  --output /tmp/coding-memory-smoke-1

# Explicitly execute only after checking the frozen plan and included allowance.
python -m bench.coding_memory.run execute --output /tmp/coding-memory-smoke-1

# Separate 18-run matrix. Every draw is fresh; both draws and all losses are kept.
python -m bench.coding_memory.run prepare --phase method-pilot \
  --model gpt-6-sol --output /tmp/coding-memory-method-1
python -m bench.coding_memory.run execute --output /tmp/coding-memory-method-1

# Offline: export only the allowlisted, sanitized review evidence.
python -m bench.coding_memory.export --input /tmp/coding-memory-method-1 \
  --output /tmp/coding-memory-public-1 --manual-input /tmp/decision-test-1
```

Use new output directories. Execution refuses a changed source or previously
started plan. Every scheduled slot is accounted for; an incomplete run stops the
matrix without retrying or silently replacing failed draws.

The output folder is **private raw evidence**: never commit it wholesale.
Account/session metadata and private reasoning may occur in client streams.
Share only inspected sanitized prompts, observable tool evidence, source edits,
checklists, accounting and their checksums. A sanitization step is not a claim
that the raw stream is safe to publish.

## What can be concluded

Full correctness, specific adverse behaviors, first versus final code, actual
file retrieval, supplied history, process failures and usage are separate
measurements. All current requirements are visible in every arm; no-memory is
allowed to solve every task. Ties and ceiling effects are valid results.

There are only three author-written tasks. Two blind draws are not two projects.
This slice establishes whether the method runs and exposes regressions; it
cannot establish general coding benefit or superiority over Markdown or native
memory. The manual experiment shows that old reasoning can remain intact while
an explicitly authorized newer decision and ordinary tests describe changed
behavior. Passing tests never authorizes a preference change by itself.
