# Using Selvedge with the pre-commit framework

Selvedge ships hook definitions for [pre-commit](https://pre-commit.com), so any repo that uses Selvedge can gate commits on the health of its `.selvedge/` store with three lines of config. Both hooks are deterministic CLI commands — no LLM calls, no network, no dependencies beyond Selvedge itself.

> Availability: the hook definitions live in `.pre-commit-hooks.yaml` at the repo root, so `rev:` must point at a tag (or commit SHA) that contains that file. The first release tag carrying it is the one after v0.3.9 — until then, pin a commit SHA from `main`.

## Quickstart

Add to your repo's `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/masondelan/selvedge
    rev: v0.3.10  # or a commit SHA that contains .pre-commit-hooks.yaml
    hooks:
      - id: selvedge-verify
```

Then:

```bash
pre-commit install
pre-commit run selvedge-verify --all-files   # try it now
```

The pre-commit framework pip-installs Selvedge into an isolated virtualenv at the pinned `rev` — you don't need `selvedge` on your global PATH for the hook to run. Python >= 3.10 is required.

## The hooks

### `selvedge-verify` — store correctness (recommended default)

Runs [`selvedge verify`](architecture.md), the same command the docs recommend wiring into CI "without `|| true` on day one." Two tiers:

- **must_fail** — SQLite corruption, schema mismatch, invariant violations (empty entity paths, unknown change types, bad timestamps). These block the commit.
- **should_warn** — soft signals: singleton changesets, and events past the backfill window with **no `git_commit`** (the provenance gap — changes were logged but never tied back to a commit; `selvedge install-hook` fixes the cause). Warnings never block the commit by default.

Escalate warnings to failures once your team is comfortable:

```yaml
      - id: selvedge-verify
        args: ["--strict"]
```

**Note on empty stores:** a missing DB file is a *must-fail*, by design — a correctness gate that silently passes on a nonexistent store isn't a gate. Run `selvedge init` (or `selvedge setup`) before enabling the hook, and don't enable it on repos that don't use Selvedge.

### `selvedge-doctor` — ambient health check

Runs [`selvedge doctor`](architecture.md): DB path resolution, schema version (including downgrade detection), post-commit hook presence and silent hook failures, MCP wiring freshness, backup freshness, `tool_calls` table size. Exits non-zero **only on FAIL rows**, so the (more numerous) WARN/INFO rows never block anyone.

Doctor checks more surface area than a per-commit gate strictly needs, so consider running it less often than every commit:

```yaml
      - id: selvedge-doctor
        stages: [pre-push]        # or [manual], then: pre-commit run selvedge-doctor --hook-stage manual
```

## Full example

```yaml
repos:
  - repo: https://github.com/masondelan/selvedge
    rev: v0.3.10
    hooks:
      - id: selvedge-verify           # every commit: store must be sound
      - id: selvedge-doctor           # every push: wiring must be healthy
        stages: [pre-push]
```

## Behavior notes

- Both hooks set `pass_filenames: false` and `always_run: true` — they check the store, not the staged files, so they run on every commit regardless of which paths changed.
- The store is resolved through Selvedge's normal precedence chain: `SELVEDGE_DB` env var first, then walking up from the working directory to the nearest `.selvedge/`. CI jobs that keep the DB somewhere unusual can export `SELVEDGE_DB` before the hook runs.
- Exit codes are the documented CLI contract: `selvedge verify` — 0 clean or warn-only, 1 on any must-fail (or any warn with `--strict`); `selvedge doctor` — 0 unless a check FAILs.
- For commit-coverage *reporting* (what fraction of commits have logged events nearby), use `selvedge stats --json` in a scheduled CI job rather than a commit gate — coverage is a trailing metric, not a per-commit invariant.
