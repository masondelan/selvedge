# Selvedge — architecture & roadmap

> Internal docs: data model, MCP tool reference, CLI reference, phase plan, non-goals.
> User-facing docs live in [`README.md`](../README.md). Agent rules and conventions live in [`CLAUDE.md`](../CLAUDE.md).

---

## What this is

Selvedge is an open-source MCP server that AI coding agents call as they work to log structured change events. It answers questions like:

- "When was `users.stripe_customer_id` added and why?"
- "What changed in the auth module in the last 30 days?"
- "Which agent added the payments table and what was the reasoning?"

The core insight: with human-written code, intent leaked into commit messages and PR descriptions. With AI-written code, intent lives in a prompt that evaporates when the session ends. Selvedge captures it before it's gone.

**Positioning:** "What pandas is to data manipulation, Selvedge is to codebase change tracking." Open source core, hosted platform as the business model.

---

## Architecture

```
selvedge/
├── selvedge/
│   ├── __init__.py       version string
│   ├── models.py         ChangeEvent dataclass, ChangeType + EntityType enums
│   ├── config.py         DB path resolution (env → walk-up → ~/.selvedge)
│   ├── storage.py        SelvedgeStorage — SQLite CRUD layer
│   ├── server.py         FastMCP server — 6 tools exposed to AI agents
│   ├── importers.py      Migration file parsers — SQL DDL + Alembic
│   └── cli.py            Click + Rich CLI — init, status, diff, blame, history, search, log, import, export, install-hook
├── scripts/
│   └── coverage_check.py cross-references git log vs Selvedge events
├── tests/
│   ├── test_storage.py
│   ├── test_server.py
│   ├── test_cli.py
│   └── test_importers.py
├── docs/
│   ├── getting-started.md
│   └── architecture.md   (this file)
├── pyproject.toml
├── CHANGELOG.md
├── README.md
└── CLAUDE.md
```

### Tech stack
- **Python 3.10+** — matches pandas positioning (Python-first)
- **mcp** — official Anthropic MCP Python SDK (FastMCP)
- **SQLite** — zero-config local storage; WAL mode for concurrency
- **Click** — CLI framework
- **Rich** — terminal output formatting
- **Hatchling** — build backend
- **pytest** — test runner

---

## Data model

### ChangeEvent

The central entity. Every recorded change is one row in the `events` table.

| Field | Type | Description |
|-------|------|-------------|
| `id` | TEXT PK | UUID4 |
| `timestamp` | TEXT | UTC ISO 8601 |
| `entity_type` | TEXT | column, table, file, function, class, endpoint, dependency, env_var, index, schema, config, other |
| `entity_path` | TEXT | Dot/slash notation path (see conventions below) |
| `change_type` | TEXT | add, remove, modify, rename, retype, create, delete, index_add, index_remove, migrate |
| `diff` | TEXT | The actual change — SQL migration, code diff, or description |
| `reasoning` | TEXT | Why the change was made — the captured intent |
| `agent` | TEXT | Which AI agent (claude-code, cursor, copilot, human, etc.) |
| `session_id` | TEXT | Agent session/conversation ID |
| `git_commit` | TEXT | Git commit hash this change lands in |
| `project` | TEXT | Repository/project name |
| `changeset_id` | TEXT | Groups related changes into a named feature/task (e.g. `"add-stripe-billing"`) |
| `metadata` | TEXT | JSON blob for extensibility |

### entity_path conventions

```
users.email           → DB column (table.column)
users                 → DB table
src/auth.py::login    → function in file (path::symbol)
src/auth.py           → file
api/v1/users          → API route
deps/stripe           → dependency
env/STRIPE_SECRET_KEY → environment variable
```

Prefix queries work everywhere: `users` matches `users`, `users.email`, `users.created_at`.

---

## MCP Server tools

The MCP server (`selvedge/server.py`) exposes these 6 tools to AI agents:

### `log_change`
Record a change. Call this immediately after making any meaningful change.

**Required:** `entity_path`, `change_type`
**Optional:** `diff`, `entity_type`, `reasoning`, `agent`, `session_id`, `git_commit`, `project`, `changeset_id`

The `reasoning` field is validated at write time — the server returns a `warnings` array if it's empty, too short (< 20 chars), or a generic placeholder like `"user request"` or `"done"`. Aim for a full sentence describing intent.

The `changeset_id` field groups related events under a shared slug (e.g. `"add-stripe-billing"`). All events in a changeset can be retrieved together via the `changeset` tool.

### `diff`
Get change history for an entity or entity prefix. Returns list of events, newest first.

### `blame`
Get the most recent change to an exact entity path — what, when, who, why.

### `history`
Filtered history across all entities. Supports `since` (ISO or relative like `7d`, `30d`, `1y`), `entity_path`, `project`, `limit`.

### `changeset`
Get all events belonging to a `changeset_id`, oldest first. Use to reconstruct the full scope of a feature or task across multiple entities.

### `search`
Full-text substring search across `entity_path`, `diff`, `reasoning`, `agent`.

---

## CLI commands

```bash
selvedge init                              # init .selvedge/ in current dir
selvedge status                            # summary + recent events
selvedge diff users.email                  # history for an entity
selvedge diff users --limit 50             # all users.* columns, 50 entries
selvedge blame payments.amount             # most recent change + context
selvedge history                           # all history
selvedge history --since 7d                # last 7 days
selvedge history --entity users --since 30d
selvedge history --project my-api
selvedge search "billing"                  # full-text search
selvedge log users.phone add --reasoning "2FA" --agent me  # manual entry
selvedge stats                             # tool call coverage (per-tool, per-agent, missing-reasoning)
selvedge doctor                            # PASS/WARN/FAIL health check (DB path, schema, hook, MCP wiring)
selvedge import ./migrations/              # backfill from SQL/Alembic migration files
selvedge import ./migrations/ --dry-run   # preview without writing
selvedge export --since 30d --output history.json
selvedge install-hook                      # install git post-commit hook
selvedge backfill-commit --hash abc123     # manually backfill a git commit hash
```

All commands support `--json` for machine-readable output.

---

## DB path resolution

Order of precedence:
1. `SELVEDGE_DB` environment variable
2. Walk up from CWD looking for an existing `.selvedge/` directory
3. `~/.selvedge/selvedge.db` (global fallback)

This means `selvedge init` in a project root locks that project to its own DB.
The global fallback ensures agents always have somewhere to write even before `init` is run.

---

## Running the MCP server

```bash
# After pip install
selvedge-server

# Or directly
python -m selvedge.server
```

### Claude Code config (~/.claude/config.json)
```json
{
  "mcpServers": {
    "selvedge": {
      "command": "selvedge-server"
    }
  }
}
```

### With a project-specific DB
```json
{
  "mcpServers": {
    "selvedge": {
      "command": "selvedge-server",
      "env": {
        "SELVEDGE_DB": "/path/to/your/project/.selvedge/selvedge.db"
      }
    }
  }
}
```

---

## System prompt / end-user agent instructions

Add this to the agent's system prompt or `CLAUDE.md` of any project that uses Selvedge:

```
You have access to Selvedge (MCP server: selvedge) for change tracking.

Rules:
- Call selvedge.log_change immediately after adding, modifying, or removing
  any DB column, table, function, API endpoint, dependency, or env variable.
- Set `reasoning` to the user's original request or the problem being solved.
  Write at least one full sentence — the server will warn on empty, very short,
  or generic values like "user request" or "done".
  Good example: "User asked to add 2FA — needs phone number to send SMS codes."
- Set `agent` to "claude-code" (or whichever agent you are).
- Set `session_id` if you have access to the current session/conversation ID.
- Set `git_commit` to the commit hash once you know it.
- For multi-entity changes (e.g. adding a whole feature), set a shared `changeset_id`
  on all related log_change calls — use a short slug like "add-stripe-billing".
  This lets anyone query the full scope of the change with selvedge.changeset().
- Before modifying an entity, call selvedge.diff or selvedge.blame to understand
  its history and avoid conflicting with past decisions.
```

---

## Phase plan

> **Keeping this accurate:** The source of truth for what's shipped is `CHANGELOG.md`.
> If checkboxes here drift from reality, trust the changelog and update this file.
> A weekly Cowork task flags any mismatch automatically.

### Phase 1 — Core (DONE ✓ · v0.1.0)
- [x] MCP server with 5 tools (log_change, diff, blame, history, search) — `changeset` added in v0.2.1, current count is 6
- [x] SQLite storage with WAL mode
- [x] CLI (init, status, diff, blame, history, search, log, stats)
- [x] Local tool call telemetry + `scripts/coverage_check.py`
- [x] PyPI package with entry points
- [x] Test suite — storage, server, CLI (57 tests)

### Phase 2 — Integrations (DONE ✓ · v0.2.0)
- [x] Git hook: `selvedge install-hook` — post-commit hook auto-backfills `git_commit`
- [x] `selvedge backfill-commit --hash HASH` — manual git hash backfill
- [x] Migration file parser (`importers.py`) — raw SQL DDL + Alembic Python files
- [x] `selvedge import PATH` — CLI command with `--dry-run`, `--json`, `--format`, `--project`
- [x] `selvedge export` — dump history as JSON/CSV with full filter support

### Phase 2.5 — Quality + Grouping (DONE ✓ · v0.2.1)
- [x] `changeset_id` field on ChangeEvent — groups related changes under a named slug
- [x] `changeset` MCP tool — retrieve all events in a changeset, oldest first
- [x] `storage.list_changesets()` — summary view of all changesets with event counts
- [x] Reasoning quality validator in `log_change` — warns on empty, short, or generic reasoning

### Phase 2.6 — Correctness pass (DONE ✓ · v0.3.0)
- [x] `selvedge.timeutil` module — shared relative-time parser and UTC normalizer
- [x] `m` = minutes / `mo` = months (was: `m` = months, contradicting every CLI convention)
- [x] Unparseable `--since` raises rather than silently returning empty results
- [x] `LIKE` queries escape `_` and `%` (was: underscore matched any char in search/prefix queries)
- [x] All timestamps normalized to UTC `...Z` form on write (was: mixed-tz sorted by ASCII order)
- [x] `CREATE TABLE` importer emits per-column events (was: zero events for inline columns → blame failed)
- [x] `RENAME TABLE` / `RENAME COLUMN` emit two events so blame works under both old and new names
- [x] `ChangeEvent.__post_init__` validates `entity_path`, `change_type`, `entity_type`
- [x] `get_db_path` requires the DB file (not just dir), warns on global fallback
- [x] `backfill_git_commit` window 10 → 60 min; `selvedge status` shows missing-commit count
- [x] `storage.log_event_batch()` for atomic, fast bulk imports
- [x] `selvedge log` CLI uses `click.Choice` for `change_type`
- [x] `tests/test_adversarial.py` — 25 tests locking in the new behavior
- [x] README "What's new in v0.3.0" section + outdated docs fixed (`m`/`mo`, `changeset` CLI)

### Phase 2.7 — Hardening (DONE ✓ · v0.3.1)
- [x] Concurrency safety: connection-with-retry on `database is locked`,
      exponential backoff, `PRAGMA busy_timeout = 5000`, multi-threaded
      writer test
- [x] `_session()` context manager fixes the long-standing connection leak
      (`with self._connect()` managed the transaction but never closed)
- [x] `selvedge.migrations` — explicit `schema_migrations` table, atomic
      per-migration transactions, bootstrap detection for pre-versioning DBs
- [x] `selvedge.logging_config` — `SELVEDGE_LOG_LEVEL` env var, namespaced
      `selvedge.*` loggers, entry-point-only `configure_logging()`
- [x] `selvedge.validation` — shared reasoning-quality validator used by
      both `server.log_change` and CLI `selvedge log`
- [x] Fixed regex bug in generic-reasoning patterns (`^fixed?$` matched
      "fixe"/"fixed", not "fix"/"fixed"; same for add/remove/update/change)
- [x] Public API exports in `__init__.py` with `__all__` and frozen-surface
      test (`tests/test_public_api.py`)
- [x] CI gates: ruff, mypy (pragmatic strict), pytest-cov ≥85%; current 92%
- [x] MCP protocol smoke tests (`tests/test_mcp_protocol.py`) — boots real
      `selvedge-server` subprocess and round-trips every tool over stdio

### Phase 2.8 — Observability polish (DONE ✓ · v0.3.2)
- [x] **`selvedge doctor` command** — single health-check that walks the
      ambient state agents run into and reports each one PASS/WARN/FAIL/INFO:
      DB path resolution (with precedence step), `.selvedge/` existence,
      schema migration version, post-commit hook status, last hook failure,
      last `tool_calls` timestamp, `SELVEDGE_LOG_LEVEL` validity. `--json`
      for machine output, exits 1 on any FAIL.
- [x] **Hook failure surfacing** — post-commit hook now writes to
      `.selvedge/hook.log` on failure (shell PATH missing, backfill error),
      and both `selvedge status` and `selvedge doctor` surface the most
      recent failure line.
- [x] **`selvedge stats` upgrades**: per-agent breakdown (catches
      under-instrumented agents) and `missing_reasoning` count (events
      whose validator-flagged reasoning was logged anyway). `--since`
      filter already shipped in v0.3.0.
- [x] **CI matrix for SQLite versions** — `sqlite-matrix` job builds
      SQLite 3.37.2 / 3.42.0 / 3.45.3 from source and runs the suite
      against each via LD_PRELOAD; verifies the swap took before running.
      Python matrix expanded to 3.10–3.13 with bundled-SQLite version
      printed per row.

### Phase 2.9 — Discoverability + ergonomics (DONE ✓ · v0.3.3)
> Originally scoped as "First-run that just works" — but a stronger
> need surfaced mid-cycle around MCP tool schema completeness for
> directory scoring (Smithery quality score 78/100, blocking the
> verified badge). The first-run wizard work moved to Phase 2.10; this
> phase shipped as a discoverability + DX-polish release. No new tools,
> no behavior changes that affect stored data.

- [x] **Per-parameter descriptions on every MCP tool.** All 6 tools
      now declare each parameter via `Annotated[T, Field(description=...)]`,
      populating `inputSchema.properties.<param>.description` in the
      live tool listing. Coverage went 0/21 → 21/21. Helps any agent
      reading `tools/list` to pick the right tool — Claude Code,
      Cursor, Copilot, MCP Inspector all surface these at decision
      time. Knock-on benefit for every directory that introspects the
      live server (Smithery, Glama, PulseMCP).
- [x] **MCP tool annotations on every tool.** `readOnlyHint`,
      `destructiveHint`, `idempotentHint`, `openWorldHint`, and a
      human-friendly `title`. `log_change` is the only writer
      (append-only, not idempotent). The five readers are all
      read-only + idempotent. None are open-world. Lets MCP clients
      gate or surface tools appropriately.
- [x] **`outputSchema` on `log_change` and `blame`.** New
      `LogChangeResult` and `BlameResult` TypedDicts in
      `selvedge.server` give the JSON-RPC layer concrete output
      schemas to advertise. The four list-returning tools already had
      auto-generated schemas; this brings the dict returners in line
      so all 6 tools advertise their output.
- [x] **Stable response shapes.** `log_change` always returns `id`,
      `timestamp`, `status`, `error`, and `warnings` — empty values
      when not applicable, easier to type-check without branching.
      `blame` does the same on miss: every event field empty, `error`
      carries the "no history found" message. The `isError` convention
      (empty-history → `{"error": "..."}` with protocol-level
      `isError: false`) is now codified in the module docstring so
      the decision is intentional, not accidental.
- [x] **Custom server icon.** "Stitched timeline" mark — a horizontal
      running stitch where each visible stitch is a captured change
      event. Lives at `assets/icon.svg` and a 512×512 `assets/icon.png`,
      shipped in the Smithery bundle. Replaces the auto-generated
      mosaic.
- [x] **Tool-level descriptions dedented at startup.** Each tool's
      docstring runs through `inspect.cleandoc` once at import time so
      `tools/list` doesn't leak the function-body indent.
- [x] **`CLAUDE.md` ↔ `docs/architecture.md` split.** `CLAUDE.md` is
      now a thin agent-instructions file (sources of truth, code
      conventions, version bump checklist, scheduled tasks). The
      architecture, data model, MCP tool reference, full CLI
      reference, phase plan, and non-goals all moved to
      `docs/architecture.md`. Reduces noise on every Claude Code /
      Cowork session boot and gives the architecture doc a stable home.
- [x] **MCP Inspector smoke test in CI parity.** The new test helper
      in `tests/test_mcp_protocol.py::_payload` handles all three
      FastMCP response shapes (list-wrapped, dict-direct, content-only),
      so the round-trip suite works against the new TypedDict returns.
- [x] **Naming (+6pt) — deferred to v0.4.x.** Smithery flags `diff`,
      `history`, `search` as too-generic tool names. Adding a
      `selvedge_` prefix would clear it but is a breaking change for
      users with existing `CLAUDE.md` instructions referencing
      `selvedge.diff` etc. Wait for v0.4.x where breaking changes are
      already on the table.

Outcome: projected Smithery quality score 78 → ~94, clearing the >80
threshold for the **verified** badge (the only other verification
route — TXT record on homepage host — is blocked while homepage is
github.com).

### Phase 2.10 — First-run that just works (DONE ✓ · v0.3.4)
> The biggest user-funnel cliff today is first-run: pip install, edit
> `~/.claude/config.json`, restart agent, `selvedge init`, copy-paste a
> system prompt, install the git hook — six steps and three of them are
> documentation lookups. Goal: collapse this to one command and make the
> agent integration discoverable instead of memorized. (Originally
> scoped as Phase 2.9 / v0.3.3; deferred when v0.3.3 became a
> discoverability-only release.)

- [x] **`selvedge setup` interactive wizard** — detects installed AI
      tooling (Claude Code, Cursor, Copilot) by looking for their config
      files, offers to install the MCP entry into each one in place,
      runs `selvedge init` if not already done, prompts to install the
      post-commit hook, and offers to drop the recommended agent prompt
      block into `CLAUDE.md` / `.cursorrules`. `--non-interactive` for
      scripted installs (CI bootstrap, devcontainer postCreate).
- [x] **`selvedge prompt` command** — prints the canonical agent
      instructions paragraph; `--install <file>` writes the block to a
      target file (idempotent, preserves the rest of the file). Lets
      users keep the prompt in source control without copy-paste drift.
- [x] **`selvedge watch`** — live-tail of new events as they're logged.
      Trust-but-verify for users who want to see what their agent is
      capturing in real time, and a much better debugging surface than
      "run `selvedge status` repeatedly." Should respect `--since`,
      `--entity`, `--project`, and `--agent` filters.
- [x] **Better first-run errors** — replace "no tool calls recorded yet"
      with a one-liner that points at `selvedge setup`. Detect the
      common "MCP entry exists but agent not restarted" case from
      `tool_calls` being empty for ≥5 minutes after install.
- [x] **Onboarding test coverage** — `tests/test_setup.py` covering the
      detect/install paths for each agent type (uses tmp_path config
      fixtures, no real config touched).

### Phase 2.11 — Recovery and retention (v0.3.5)
> v0.3.1 made the runtime safe; v0.3.2 made problems visible. This phase
> handles what happens AFTER something has already gone wrong (corruption,
> orphaned data, runaway growth). All of these have a "Selvedge took down
> the agent's working DB" failure mode if we don't ship them — the bigger
> the install base gets, the more these matter.

- [ ] **`selvedge verify` command** — runs SQLite's
      `PRAGMA integrity_check`, validates the `schema_migrations` set
      against `MIGRATIONS`, and walks both tables for invariants
      (entity_path non-empty, change_type in valid set, timestamp
      parseable, no orphaned tool_calls). Exits non-zero on any failure
      so it can run in CI.
- [ ] **`selvedge repair` command** — wraps SQLite's `.recover` to dump
      events from a corrupted DB into a salvage file, plus a
      `--from-recover` mode that re-imports the dump into a fresh DB.
      Default behavior is dry-run; `--apply` actually writes.
- [ ] **Retention policy** — `selvedge prune` command and a config
      setting (`retention_days` in `.selvedge/config.toml`). Prunes
      `tool_calls` (low value over time) by default; events table only
      with `--include-events` and a confirmation prompt. Doctor learns
      to warn when DB size exceeds a configurable threshold (default
      500 MB).
- [ ] **Bounds on event size at log time** — configurable max
      `diff_bytes` and `reasoning_bytes`. Over-the-limit values are
      truncated with a marker (`…[truncated 12KB]`) and logged as a
      validator warning. Prevents an agent dumping a huge generated SQL
      file from blowing up the DB.
- [ ] **Doctor expansion** — detect orphan rows (events with
      `changeset_id` referencing nothing else), oversized tables, and
      `schema_migrations` rows for versions that aren't in the current
      `MIGRATIONS` tuple (indicates a downgrade).
- [ ] **`.selvedge/config.toml`** — first-class project config file
      read on every entry point. Houses retention, size bounds, default
      project name. Backwards compatible: missing file = current defaults.

### Phase 2.12 — Developer integrations (v0.3.6)
> Selvedge today is a CLI you query when you remember to. This phase
> moves it into the developer's existing surface area — PR review,
> standups, IDE — so the captured intent gets used, not just stored.
> Robustness without integration doesn't compound.

- [ ] **`selvedge audit` command** — produces a PR-review-ready quality
      report for a given branch or commit range:
      `selvedge audit --branch feature/x` lists every entity touched in
      the range, flags missing/short reasoning, surfaces unstamped
      commits, and renders a table grouped by changeset. `--format
      markdown` for posting as a PR comment.
- [ ] **`selvedge ci-check` exit-code gate** — runs in CI on PR
      branches; non-zero exit if reasoning quality, coverage ratio, or
      changeset coverage falls below configurable thresholds (read from
      `.selvedge/config.toml`, which lands in v0.3.5). The "selvedge
      says this PR is missing context" early warning.
- [ ] **`summary` MCP tool** — new server tool so agents/IDEs can ask
      "what's been happening in this codebase since X?" Returns a
      grouped, **templated** digest (changesets touched, agents
      involved, top entities by activity) — *not* LLM-generated; the
      "no LLM calls inside Selvedge" non-goal still holds. Useful for
      standup-bot integrations and IDE side-panels. Implementation
      shares a digest/aggregate helper with `selvedge audit` and
      `selvedge digest` so the three surfaces don't drift.
- [ ] **`prior_attempts` MCP tool** — agent-side counterpart to
      `summary`. Given a description or `entity_path`, returns prior
      change events at the same path/shape with their `reasoning`,
      change_type, and downstream outcome. v0.3.6 *infers* the outcome
      from add→remove proximity (within a configurable window) because
      explicit `reject`/`revert` change_types don't ship until v0.3.7.
      Once 2.13 lands, this tool's outcome classifier becomes exact and
      the proximity heuristic falls back to a tiebreaker. Lets an agent
      ask "have we tried this before?" *before* proposing an approach,
      closing the LLM-amnesia loop where every fresh session re-suggests
      the same dead idea. No schema change in 2.12 — pure read tool over
      the existing event store. Templated output only, no LLM calls.
      Pull-model only in v0.3.6; the push-model auto-warn-on-`log_change`
      variant is deferred until we have signal on whether agents actually
      act on the pull-tool results.
- [ ] **`selvedge digest` CLI command** — same shape as the MCP
      `summary` tool but renders to terminal. Default `--since 24h`,
      designed to be fed into Slack/email cron jobs.
- [ ] **PR comment helper** — `selvedge pr-comment --pr 123` that
      formats `audit` output for posting via `gh pr comment`. No GitHub
      API calls in core (keeps the dep footprint small); just emits the
      markdown.
- [ ] **VS Code extension scaffolding (separate repo)** — design doc
      lands in `docs/vscode-integration.md` this phase; actual
      extension built outside this repo. Hover a column name to see
      blame inline; `:SelvedgeBlame` command palette entry. Spec
      against the live `summary` MCP tool, so this bullet sequences
      *after* the MCP tool work within the phase.
- [ ] **Tests** — new MCP tools (`summary`, `prior_attempts`) land in
      `tests/test_server.py`; new CLI commands (`audit`, `ci-check`,
      `digest`, `pr-comment`) land in `tests/test_cli.py`. Shared
      digest/aggregate helper gets its own `tests/test_digest.py`
      covering grouping rules, time-bucket boundaries, and the
      add→remove proximity heuristic that backs `prior_attempts`.

### Phase 2.13 — Active memory (v0.3.7)
> Selvedge to date is an append-only log: every event lives forever and
> reads identically the day it's written and a year later. This phase
> turns Selvedge into *active* memory — decisions can carry an expiry
> condition, abandoned alternatives are first-class events, and the
> server can answer "what reasoning is stale?" Pairs naturally with the
> `prior_attempts` tool from v0.3.6: 2.12 lets agents query past
> decisions, 2.13 lets the store know which past decisions still matter.
>
> Brand-defining release for the LLM-amnesia thesis. No breaking changes
> — new fields are nullable, new event types are additive — so safe to
> ship before the v0.4.0 breaking-changes window.

- [ ] **Optional `expires_when` / `revisit_after` columns on
      `ChangeEvent`** — schema migration v3 adds two nullable TEXT
      columns. `revisit_after` is an ISO-8601 date or a relative offset
      from `timestamp` (e.g. `90d`). `expires_when` is a free-form
      symbolic condition (e.g. `"library:stripe>=v12"`) — opaque to v1
      Selvedge, surfaced as a string for humans/agents to act on.
      Existing events get NULL on both (current behavior preserved).
- [ ] **`stale_decisions` MCP tool** — returns events whose
      `revisit_after` has passed, plus events older than a configurable
      default (`stale_days` in `.selvedge/config.toml`, opt-in) when no
      explicit revisit is set. `stale_days` is independent from v0.3.5's
      `retention_days` — they govern *surfacing* and *deletion*
      respectively, and one shouldn't imply the other. Filterable by
      `entity_path`, `project`, `agent`. Date-based v1; symbolic
      `expires_when` evaluation deferred to a later release. Templated
      output, no LLM calls.
- [ ] **`selvedge stale` CLI command** — same data surface, terminal
      formatted, `--json` for cron / Slack jobs. Composes with
      `selvedge digest` so the morning report can include "decisions
      that aged out yesterday."
- [ ] **New `change_type` values: `reject` and `revert`** — added to the
      `ChangeType` enum. `reject` records "we considered this and
      decided against it" without ever writing the change; `revert`
      records "we tried this and rolled it back," distinct from a
      regular `remove` (which conflates "feature removed" with
      "approach rejected"). Distinguishing rejected-from-removed at the
      schema level avoids the inference-from-proximity heuristic the
      pull-only `prior_attempts` tool has to fall back on. **No new MCP
      tool** — these are logged via the existing `log_change` tool with
      `change_type=reject` / `change_type=revert`. The `log_change`
      docstring gains a worked example for the rejection use case so
      agents discover the pattern from the tool description without
      Selvedge growing its tool surface. Reasoning-quality validator
      gets a `reject`-specific rule: encourage reasoning to name *what*
      was rejected and *what was chosen instead*.
- [ ] **Reasoning-quality validator gains an opt-in nudge** — when
      `change_type` is in `{add, modify, create, migrate}` and the
      `entity_type` looks architectural (table, schema, dependency,
      config), the validator suggests setting `revisit_after`. Soft
      warning only, doesn't block writes — same posture as the existing
      empty/short/generic checks.
- [ ] **`doctor` learns a stale-decisions check** — counts events past
      their explicit `revisit_after` as a soft warning ("12 decisions
      flagged for review are past their revisit date"). Counts
      `reject`/`revert` events separately in the doctor summary so
      agents can see the rejected-paths population at a glance.
- [ ] **Tests** — `tests/test_active_memory.py` covering schema
      migration v3 backfill, stale-decision query semantics
      (`revisit_after` past vs. fallback `stale_days`), reject/revert
      change_type round-trip through the existing `log_change` tool,
      and doctor's stale-count output. **`tests/test_public_api.py`
      update required**: the frozen-shape test will fail when
      `revisit_after` / `expires_when` land on `ChangeEvent` — update
      the expected dataclass shape in the same PR as the migration so
      CI doesn't go red.

### Phase 3 — Team features (v0.4.0)
> First release in the breaking-changes window. Bundles the backend
> abstraction, the HTTP+auth surface, and the deferred MCP tool-name
> rename so users only absorb one breaking-change cycle.

- [ ] PostgreSQL backend option (configurable via `SELVEDGE_BACKEND=postgresql://...`)
  - Abstract `SelvedgeStorage` behind a protocol/interface so backends are swappable
  - `storage_sqlite.py` and `storage_pg.py` both implement `StorageBackend`
- [ ] HTTP REST API layer (FastAPI) — exposes every MCP server operation
      over HTTP. The list at the time of v0.4.0 is the v0.3.7 set
      (`log_change`, `diff`, `blame`, `history`, `changeset`, `search`,
      `summary`, `prior_attempts`, `stale_decisions`,
      `log_rejected_alternative`); count and shape track the live
      `selvedge/server.py` rather than being hardcoded here.
- [ ] Auth (API keys) for the HTTP layer
- [ ] **MCP tool-name prefix migration** — rename `diff`, `history`,
      `search` (and any other generic verbs) to `selvedge_*` form,
      deferred from v0.3.3 because of the breaking-change cost. Lands
      here alongside the other v0.4.0 breaking changes so users only
      update their `CLAUDE.md` / `.cursorrules` once. Old names remain
      registered as deprecated aliases for one minor cycle, with a
      stderr warning on call.
- [ ] **Agent Trace interop** — `selvedge export --format agent-trace` and
      `selvedge import --format agent-trace` (Cursor/Cognition open RFC, Jan 2026).
      Design doc: [`agent-trace-interop.md`](agent-trace-interop.md).
      Selvedge stays entity-centric internally; AT is purely a wire format
      for cross-tool readers and compliance audits.
- [ ] **Hosted-MCP directory listings — launch checklist item.** Once HTTP +
      auth ship, Selvedge becomes eligible for the connector marketplaces
      that require a remote endpoint (Anthropic Claude connectors registry,
      hosted MCP catalogs, etc.). Today we're Local-only on Smithery, which
      caps reach. Park the connector-listing question until that endpoint
      exists — paired with the HTTP layer + auth above so the launch goes
      out as one coordinated push, not a feature-by-feature drip. No action
      required while we're pre-v0.4.0.

### Phase 4 — Platform (hosted business)
- [ ] Web dashboard (React + the REST API)
- [ ] Cross-repo queries (server-side, multi-tenant). The
      single-user OSS variant — local overlay across multiple
      `.selvedge/` directories the same person owns — is intentionally
      *not* on Phase 4 scope; it lives in the OSS track and will be
      considered for a v0.3.x point release post-v0.3.7. Hosted is for
      teams; OSS is for individuals.
- [ ] Team/org-level retention policies (per-tenant, configurable
      independently from the project-local `retention_days` shipped in
      v0.3.5)
- [ ] Team/org management
- [ ] Webhook events (Slack, PagerDuty, etc. on schema changes)

---

## Development setup

```bash
git clone https://github.com/masondelan/selvedge
cd selvedge
pip install -e ".[dev]"
pytest
selvedge --version
```

---

## Non-goals (through Phase 2)

- No web UI (Phase 4)
- No PostgreSQL (Phase 3)
- No authentication (Phase 3)
- No real-time streaming
- No multi-user/team features (Phase 3)
- No LLM calls inside Selvedge itself — reasoning is captured FROM agents, not generated by Selvedge
