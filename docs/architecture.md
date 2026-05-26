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

## Product values

Three values gate every phase addition. New bullets in the phase plan
must defensibly serve at least one of these; bullets that serve none
of them are deferred to the "Future work" appendix.

**Easy to use** — minimal install friction (one-command setup,
doctor-driven self-diagnosis), conventions over configuration
(sensible defaults, no required flags for happy-path use),
discoverable inside the developer's existing tooling (MCP-first,
IDE-discoverable). The user shouldn't have to learn Selvedge before
Selvedge becomes useful.

**Robust** — data integrity over feature breadth (the events table
never silently loses or corrupts data), failure modes are observable
(`selvedge doctor`, namespaced logs, structured errors with stable
keys), runtime safe under concurrency. Robustness is what makes the
captured intent worth trusting six months later — without it the
product is decoration.

**Dev focused** — built for developers in their existing toolchain,
not enterprise procurement. CLI + MCP + git hooks. No SSO,
no dashboards-as-the-product, no cloud tier as a paywall, no
phone-home telemetry. MIT-licensed end to end. The buyer-of-record
is a developer; if the feature would only be discovered through a
procurement deck, it doesn't belong in v0.x.

**Discipline:** every new phase bullet's prose must make plain which
value(s) it serves. Reviewers (= the maintainer) reject vague
additions; "nice to have" is not a value. When a feature serves
*multiple* values, that's a signal it's a strong addition. When the
only justification is competitive parity, name the competitive risk
explicitly in the bullet and cross-link to the "Competitive narrative
drift" cross-cutting entry.

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
      event. Originally lived at `assets/icon.svg` and a 512×512
      `assets/icon.png`, shipped in the Smithery bundle. Replaces the
      auto-generated mosaic. *(Superseded post-v0.3.6: redesigned to
      a minimalist 'S' + selvedge edge stitch mark; current art lives
      at `docs/icon.png` and is referenced by `manifest.json`. See
      [Unreleased] in `CHANGELOG.md`.)*
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

> **Release-scope rule (codified 2026-05-10):** Every v0.3.x and
> v0.4.x phase below is sized as one focused unit of work — one
> coherent theme, ~3-5 features, ≤30 new tests, ~400-800 LoC, one
> focused week. When a theme grew larger than that during planning,
> it was split across multiple phases. The cost is more releases;
> the win is smaller blast radius per ship and tighter feedback
> loops. See "Release scope discipline" in the cross-cutting risk
> register.

### Phase 2.11 — Recovery basics (v0.3.5)
> v0.3.1 made the runtime safe; v0.3.2 made problems visible; v0.3.5
> ships the *minimum viable* "what happens when something has gone
> wrong" surface. Verify so users can detect corruption. Backup so
> they have a known-good snapshot to fall back to. The retention
> half ships separately in v0.3.6; salvage (`selvedge repair`)
> ships in v0.3.15 only if telemetry shows corruption incidents
> warrant it. Theme: *find out what's wrong, take a safe snapshot.*

- [x] **`selvedge verify` command** — runs SQLite's
      `PRAGMA integrity_check`, validates the `schema_migrations` set
      against `MIGRATIONS`, and walks both tables for invariants
      (entity_path non-empty, change_type in valid set, timestamp
      parseable, no orphaned tool_calls). **Two-tier exit codes:**
      must-fail conditions (corruption, schema violation, unknown
      change_type values found in the store) exit non-zero.
      Should-warn conditions (orphan `changeset_id` references,
      missing `git_commit` past the backfill window) print warnings
      but exit 0 by default; pass `--strict` to escalate warnings to
      failures. Categorization lives in `selvedge.verify.CHECK_TIERS`
      and is locked in by `tests/test_verify.py`.
- [x] **`selvedge backup` command** — produces a known-good copy of
      `.selvedge/selvedge.db` via SQLite's online backup
      (`VACUUM INTO`), default destination
      `.selvedge/backups/selvedge-YYYYMMDD-HHMMSS.db`. Hardcoded
      `keep_last=7` for this release; the setting becomes
      `backup_keep_last` in `.selvedge/config.toml` when that file
      lands in v0.3.10. `.selvedge/backups/` added to the `.gitignore`
      template applied by `selvedge init`; existing repos get the
      same line on first `selvedge backup` run.
- [x] **Doctor — minimal expansion**: `last_backup` row (INFO when
      fresh, WARN >7 days, FAIL when no backups exist and the events
      table has ≥10k rows) plus `schema_migrations`-downgrade
      detection (FAIL when `schema_migrations` contains a version
      not in the current `MIGRATIONS` tuple). Bigger doctor curation
      pass deferred to v0.3.8 when more checks are stacking up.
- [x] **Release-cycle checklist** — already landed in this doc and
      in `CLAUDE.md` via the prior planning pass. No code work; the
      v0.3.5 ship just exercises it for the first time: pyproject +
      `__init__.py` + manifest.json + server.json bumped together,
      `actions/checkout` / `setup-python` / `action-gh-release` to
      Node-20-supported majors (deadline 2026-06-02), README
      "What's new" stack-cap at 2, Smithery hand-zip + publish,
      paired PR against `masondelan/selvedge-site`.
- [x] **Tests** — `test_verify.py` (13), `test_backup.py` (7),
      `test_doctor.py` extension (4). Total: 24 new tests (within ≤25 budget).

#### Risks acknowledged & mitigations

- **`selvedge verify` as a CI gate becoming `|| true`**: defended
  with the must-fail vs. should-warn tiering and `--strict` to opt
  into hard-failure mode. Users can wire verify into CI on day one
  without turning it off the first time a warning fires.
- **`integrity_check` slow on large DBs**: documented in `--help`;
  a `--quick` mode lands in a later release if telemetry shows it's
  needed.
- **Backups accidentally committed to git**: defended by adding
  `.selvedge/backups/` to `.gitignore` on first init and on first
  backup run in existing repos.

### Phase 2.12 — Retention basics (v0.3.6)
> The orthogonal half of the recovery-and-retention theme: keep the
> noise table from growing forever. Standalone, single-theme. No
> destructive operations on the events table in this release — the
> events-prune path requires `.selvedge/config.toml` (v0.3.10) and
> ships then. Theme: *bound the noise.*
>
> **Shipped combined with the stay-current work as v0.3.6** — a
> one-time exception to the single-theme-per-release discipline
> locked in on 2026-05-10. Single-theme resumes at v0.3.7.

- [x] **`selvedge prune` command** — prunes `tool_calls` only in this
      release. Hardcoded default of 90 days; CLI flag `--days N`
      overrides. **No `--include-events` flag in v0.3.6** — the
      destructive path waits for `config.toml` in v0.3.10. Every
      prune writes a one-liner to `.selvedge/prune.log` (timestamp,
      count pruned, days threshold) so the pattern is visible later.
- [x] **Doctor — `prune.log` tail row + oversized-`tool_calls`
      warning** (WARN at row-count >100k for `tool_calls`; threshold
      revisitable once v0.3.5 telemetry has bedded in).
- [x] **Tests** — `test_prune.py` (~8), `test_doctor.py` extension
      (~2). Soft budget: ≤15 new tests.

#### Risks acknowledged & mitigations

- **Cron prune racing with `selvedge log`**: defended by WAL +
  `busy_timeout` already in place since v0.3.1. Prune's DELETE
  transaction won't deadlock with a concurrent insert.
- **Default retention too aggressive**: 90 days is long enough that
  the previous month's agents are still in the data. Surfaced
  explicitly in `selvedge prune --help`.

### Phase 2.13 — `prior_attempts` wedge + entity foundation (v0.3.7)
> Brand-defining release. `prior_attempts` is the MCP tool that makes
> Selvedge's MCP-first / decision-archaeology positioning legible —
> an agent about to attempt X gets told "this was tried before and
> rejected, here's why." `prior_attempts` queries by entity, so this
> release first ships the entity-canonicalization foundation it sits
> on top of: fixing the silent history-split problem (where
> `src/auth.py::login` and `./src/auth.py::login` resolve to
> different entities) before a feature whose quality depends on
> accurate entity matching makes the bug user-visible. Ships *with*
> the positioning artifacts (demo, comparison page, README) because
> shipping the tool without making the wedge visible wastes the
> cycle. Supporting CLI surface (`audit` / `digest` / `pr-comment`)
> ships separately in v0.3.9. Theme: *make the wedge legible — and
> the entity matching it relies on canonical.*

#### Entity foundation (lands first within the release)

- [ ] **Entity-path canonicalization on write** — the storage write
      path normalizes `entity_path` deterministically: strip leading
      `./`, collapse `//`, normalize separators to `/`, trim
      whitespace. **Case preservation is intentional** — filesystems
      vary (case-insensitive on macOS/Windows by default,
      case-sensitive on most Linux), and silent case-folding would
      collapse entities that are genuinely distinct on case-sensitive
      hosts. The cross-platform stance is documented; lint suggests
      case-collision watch in `selvedge doctor` as a should-warn row
      when sibling paths differ only by case. Canonicalization lives
      in `selvedge.storage.canonicalize_entity_path` and is the
      single chokepoint exercised by both the MCP write and the CLI.
- [ ] **`selvedge migrate-paths` one-shot backfill** — re-canonicalizes
      existing rows. **`--dry-run` is default-on** (must pass
      `--apply` to write) and prints a collisions report:
      pre-canonicalization paths that would converge to the same
      value, letting the user inspect before the merge collapses
      history. Idempotent — re-running after `--apply` is a no-op.
      One audit row per run goes to a new `path_migrations` table
      so the operation is visible to `doctor` and to future
      releases.
- [ ] **Rename support via `log_change` extension** (no new MCP
      tool). Add a `rename_from` parameter to `log_change`; when
      set together with `change_type="rename"`, the storage layer
      emits the same dual-event pattern the SQL DDL importer already
      uses internally — rename event on the old path, create event
      on the new path with `metadata.renamed_from` set. **Tool-
      surface discipline call**: rename is the same write primitive
      as `log_change` with one extra parameter, not a genuinely new
      shape. Folding here keeps the v0.3.7 MCP surface at +1 tool
      (`prior_attempts` only) instead of +3, and agents discover
      the rename pattern through the existing `log_change`
      docstring (worked rename example added in the same PR).
- [ ] **Soft validation warnings in `log_change`** — pattern-shape
      checks per `entity_type` that warn but never reject:
      `function`/`method` paths without `::`, `column` paths without
      `.`, `file` paths without a separator or extension. Consistent
      with the v0.3.4 reasoning-quality validator pattern (nudge,
      not gate). Patterns live in `selvedge.validate.ENTITY_PATTERNS`
      so they can be extended without touching the write path.
- [ ] **Explicit non-goal — no code parser, no AST.** Selvedge does
      not extract entities from source code; it stores what the
      agent tells it, canonicalized and queryable. AST work is
      language-specific, drags in dependencies, and fights the
      dependency-free-core rule. Documented in the release notes
      and added to the cross-cutting non-goals section so the
      boundary survives roadmap pressure.

#### Wedge

- [ ] **`prior_attempts` MCP tool** — given a description or
      `entity_path`, returns prior change events at the same path or
      shape with their `reasoning`, `change_type`, and inferred
      outcome. v0.3.7 infers outcome from add→remove proximity
      (within a configurable window) because explicit
      `reject`/`revert` change_types don't ship until v0.3.11.
      **Conservative-recall posture**: each result carries a
      `confidence` field (`proximity_high` | `proximity_low`),
      default returns only `proximity_high`; callers must pass
      `min_confidence="proximity_low"` to see the noisy long tail.
      Empty result preferred over false positives — one shot at the
      agent's trust budget. Pull-model only. Templated output, no
      LLM calls.
- [ ] **Aggregate helper ships as a library, not an MCP tool.**
      The grouped-digest logic (changesets touched, agents
      involved, top entities by activity) lands in
      `selvedge.aggregates.summary()` as a pure Python function
      that v0.3.9's `selvedge audit` and `selvedge digest`
      consume directly. **Tool-surface discipline call**: an
      MCP `summary` tool would be a genuinely new shape (aggregate
      vs. event-list), but the agent-facing use case is thin —
      agents calling `prior_attempts` already get the relevant
      events and can roll up client-side if they want. Promote
      to MCP later only if usage telemetry shows agents actually
      reach for an aggregate primitive. Schema-versioned via
      `summary_version` in the dataclass so the shape can be
      lifted to MCP without breaking the v0.3.9 CLI consumers.
- [ ] **Positioning artifacts (release-blocker, not optional polish):**
    * `docs/comparison.html` update naming `prior_attempts` as the
      "alternatives tried, rejected paths" capability the
      line-attribution competitors don't have. Site PR in
      `masondelan/selvedge-site` mirrors this.
    * 60–120-second demo recording — Claude Code session that calls
      `prior_attempts` mid-task, sees a past rejection, changes its
      plan — saved to `docs/demos/prior-attempts.mp4` with a
      checked-in transcript at `docs/demos/prior-attempts.md`.
    * Worked-example section in `README.md` (counts toward the
      "What's new" stack cap for this release).
- [ ] **Tests** — `test_entity_canonicalize.py` for the
      canonicalization function and its invariants (~6),
      `test_migrate_paths.py` for the backfill including the
      dry-run collisions report (~6), `test_server.py` /
      `test_mcp_protocol.py` extensions covering `prior_attempts`
      and the rename extension to `log_change` (~10),
      `test_aggregates.py` for the library-level digest helper
      (~6), `test_prior_attempts.py` for confidence-tier +
      proximity-window behavior (~8). Soft budget: ≤40 new tests
      (overrun vs. the standard ≤30 because the entity foundation
      lands in the same release as the wedge; release notes call
      this out explicitly per the budget-overrun discipline).

#### Risks acknowledged & mitigations

- **`prior_attempts` false-positive trust erosion**: defended with
  the conservative-recall default (`proximity_high` only). One shot
  at the agent's trust budget; empty result preferred over wrong
  result. The exact classifier in v0.3.11 (`reject`/`revert`)
  upgrades the high-confidence tier without changing the API.
- **`summary` LLM-pressure**: templated is intentionally generic so
  consumers do their own downstream rendering. Schema versioned to
  defend against future shape changes pretextually justifying an
  LLM hop.
- **Wedge demo bit-rot**: transcript checked in (not just the .mp4),
  version-tagged recordings (`prior-attempts-v1.mp4`, etc.), and a
  doctor INFO line if the `prior_attempts` response shape changes
  so we know to re-record.
- **`migrate-paths` collapsing genuinely-distinct entities**:
  defended by `--dry-run` default-on plus the collisions report
  that surfaces converging paths *before* the write. The
  `path_migrations` audit table makes the operation reversible by
  inspection (which row was rewritten from what) even if the v3.x
  line doesn't ship a programmatic undo.
- **Case-folding pressure from contributors on macOS**: documented
  cross-platform stance — we preserve case so Linux installs don't
  get silent collisions. `doctor` should-warn row surfaces
  sibling paths that differ only by case so case-collisions stay
  visible without being silently merged.
- **Tool-count growth in the active-memory arc**: v0.3.7 adds
  one new tool (`prior_attempts`) → 7 total; v0.3.8 adds one
  more (`stale_decisions`) → 8 total. Two earlier candidates
  (`log_rename`, `summary`) were dropped from the MCP surface in
  favor of folds — rename as a `log_change` extension, summary
  as a `selvedge.aggregates` library helper — after a
  consolidation review on 2026-05-10. The v0.4.0 consolidation
  review still happens but with a lighter list; its main target
  becomes the `diff` / `blame` / `history` overlap that's been
  the real decision-fatigue surface since Phase 1.
- **`summary` library-only ships under-served if agents *do*
  want an aggregate MCP primitive**: defended by the
  `summary_version` field on the dataclass — schema-versioned
  from day one so lifting it to MCP later doesn't break the
  v0.3.9 CLI consumers. Promotion criterion is telemetry signal,
  not a roadmap commitment.
- **Keep-separate decision for `prior_attempts` (and v0.3.8's
  `stale_decisions`) deserves to be documented, not just
  defaulted**: both are *wedge primitives* whose discoverability
  in the agent's tool listing is the point. Folding either into
  `history(prior_attempts=True)` / `history(stale=True)` would
  preserve the capability but lose the legibility, which is the
  asset. Reviewed and reaffirmed 2026-05-10 alongside the
  `log_rename` / `summary` folds. Any future "why not just fold
  these too?" pressure bounces against this paragraph.
- **Entity foundation scope leaking into a code-extraction roadmap**:
  the explicit-non-goal bullet plus the cross-cutting non-goals
  section update is the durable defense. Any future "wouldn't it
  be cool if Selvedge parsed Python" PR is bounced against this
  line.

### Phase 2.14 — Active memory v1 / date-based (v0.3.8)
> Selvedge's append-only log learns to know when its own data is
> stale. v0.3.8 ships the *date-based* half — a `revisit_after`
> column and a stale-decisions surface that consumes it. The
> pattern-based half (`expires_when` grammar, `reject`/`revert`
> change_types) ships in v0.3.11. **The v3 schema migration in this
> release adds *both* nullable columns** (`revisit_after` and
> `expires_when`) even though the `expires_when` evaluator doesn't
> land until v0.3.11 — adding both nullable columns in one
> migration avoids a second migration two releases later. Theme:
> *decisions can carry an expiry date.*

- [ ] **Schema migration v3** — adds two nullable TEXT columns to
      `events`: `revisit_after` (ISO-8601 date or relative offset
      from `timestamp`, e.g. `90d`) and `expires_when` (closed
      grammar, evaluator deferred to v0.3.11). Existing events get
      NULL on both. **Perf-regression test** runs the v3 migration
      against synthetic DBs at 10k, 100k, and 1M events with bounded
      time gates. Release notes call out the one-time migration
      cost on multi-million-event installs.
- [ ] **`stale_decisions` MCP tool** — returns events whose
      `revisit_after` has passed. **Active-use weighting**: pure age
      does not surface as stale; an additional signal is required
      (recent `blame`/`diff` query of the entity OR sibling
      `changeset_id` activity OR `prior_attempts` lookup). Filterable
      by `entity_path`, `project`, `agent`. Date-based only in
      v0.3.8; `expires_when` evaluation lands in v0.3.11. Templated
      output, no LLM calls.
- [ ] **`selvedge stale` CLI command** — same data surface,
      terminal-formatted, `--json` for cron / Slack jobs. Composes
      with `selvedge digest` (v0.3.9) so the morning report can
      include "decisions that aged out yesterday."
- [ ] **Reasoning-quality validator nudge** — when `change_type` is
      in `{add, modify, create, migrate}` and `entity_type` looks
      architectural (table, schema, dependency, config), the
      validator suggests setting `revisit_after`. Soft warning only,
      doesn't block writes.
- [ ] **Doctor — signal-to-noise pass + stale-decisions check**.
      Curation pass deferred from earlier phases lands here: review
      every existing doctor row, demote ones that no longer fire
      usefully to INFO, retire any that have become wallpaper. Net
      warning count should not monotonically grow.
- [ ] **Tests** — `test_active_memory.py` for schema migration v3
      backfill and stale-decision query semantics (~12),
      `test_migrations_perf.py` for the v3 perf gates (~4),
      `test_public_api.py` update for the new `ChangeEvent` fields
      (~2). Soft budget: ≤25 new tests.

#### Risks acknowledged & mitigations

- **`stale_decisions` noise from old-but-correct decisions**:
  defended with active-use weighting. Pure age does not surface as
  stale.
- **Schema migration v3 on large DBs**: `test_migrations_perf.py`
  asserts bounded time at 10k / 100k / 1M events.
- **Doctor warning fatigue**: signal-to-noise curation pass paired
  with the new stale-decisions row. Each new check requires a
  review of existing checks.
- **`expires_when` column unused until v0.3.11**: deliberate. The
  column add is in v0.3.8 so v0.3.11's evaluator landing doesn't
  require a second migration. Documented in v0.3.8's release notes.

### Phase 2.15 — Developer ergonomics (v0.3.9)
> The CLI surface that moves Selvedge into the developer's existing
> review and reporting workflow. None of these are wedges — they're
> the supporting cast that turns `prior_attempts` + `summary` into
> a usable everyday surface. Theme: *make captured intent visible
> in the developer's existing review loop.*

- [ ] **`selvedge audit` command** — PR-review-ready quality report
      for a given branch or commit range. Lists every entity touched
      in the range, flags missing/short reasoning, surfaces unstamped
      commits, renders a table grouped by changeset. `--format
      markdown` for PR-comment use.
- [ ] **`selvedge digest` CLI command** — terminal rendering of the
      `summary` MCP tool's templated output. Default `--since 24h`,
      designed for cron / Slack / email jobs. Shares the
      digest/aggregate helper from v0.3.7's `summary` work.
- [ ] **`selvedge pr-comment --pr 123`** — formats `audit` output for
      `gh pr comment`. No GitHub API calls in core (keeps the dep
      footprint small). **Format versioned from day one**: every
      generated comment wrapped in
      `<!-- selvedge:pr-comment v1 -->` /
      `<!-- selvedge:pr-comment-end -->` sentinels so future format
      changes can ship without breaking downstream parsers. v1 schema
      documented in new `docs/pr-comment-format.md`.
- [ ] **Setup detection version contract** — `selvedge setup` and
      `selvedge doctor` learn a "supported agent versions" table
      that surfaces the agent-config locations the wizard knows
      about (Claude Code, Cursor, Copilot) plus the format version
      detected on the user's machine. When upstream changes a config
      path (already happened with Cursor between minor versions),
      doctor flags the mismatch as WARN with a remediation hint.
      Treats detection paths as a versioned contract.
- [ ] **Tests** — `test_cli.py` extensions for `audit`, `digest`,
      `pr-comment` (~18), `test_setup_version_contract.py` (~6).
      Soft budget: ≤30 new tests.

#### Risks acknowledged & mitigations

- **PR comment markdown calcification**: format versioned from v1
  with sentinel markers and a documented schema, so format
  evolution doesn't break parsers downstream.
- **Setup detection brittleness**: surfaced through the new
  doctor row, treating third-party config paths as a versioned
  contract.

### Phase 2.16 — Config + advanced retention (v0.3.10)
> First-class `.selvedge/config.toml` lands here, paired with the
> destructive prune path (which has needed somewhere to read events
> retention from) and event-size bounds (which has needed somewhere
> to read truncation limits from). All three features chain on
> config.toml; deferring config.toml from v0.3.5 lets the grammar
> settle in one release rather than expanding it across five.
> Theme: *configuration as a foundation, with the dependent features
> riding alongside.*

- [ ] **`.selvedge/config.toml`** — first-class project config,
      read on every entry point. Houses `retention_days_events`
      (default ∞), `retention_days_tool_calls` (default 90),
      `backup_keep_last` (default 7), `diff_bytes` (default 65536),
      `reasoning_bytes` (default 32768), `db_size_warn_mb` (default
      500), `stale_days` (default off, used by v0.3.8's
      `stale_decisions` fallback). Backwards compatible: missing
      file = current defaults. **Precedence rule (canonical):**
      `SELVEDGE_DB` env var always wins for DB-path resolution.
      All other settings: CLI flags > env vars > project-local
      `.selvedge/config.toml` > global `~/.selvedge/config.toml` >
      hardcoded defaults. Doctor prints which precedence step
      produced each effective setting. **Declared dependency:**
      `tomli` (Python 3.10) / stdlib `tomllib` (3.11+).
- [ ] **`selvedge prune --include-events` path** — now possible
      because config.toml hosts `retention_days_events`. Requires
      confirmation prompt AND `SELVEDGE_DESTRUCTIVE=1` in environment
      AND audit-log append to `.selvedge/prune.log`. Default
      `retention_days_events` is *infinity* — users must opt in to
      ever deleting events.
- [ ] **Event-size bounds at log time** — `diff_bytes` and
      `reasoning_bytes` from config. Over-the-limit values truncated
      with `…[truncated 12KB]` marker; truncation surfaced as a
      validator warning at write time so agents can re-call with
      concise content. `selvedge stats` learns to count truncated
      events. Spill-to-blob alternative considered and rejected
      (would complicate the flat-file-the-user-owns invariant).
- [ ] **Doctor — `oversized-tables` warning + per-setting precedence
      surfacing.** Warns when DB exceeds `db_size_warn_mb`; doctor's
      config-precedence output shows the source of each effective
      setting (the same shape as the existing DB-path precedence).
- [ ] **Tests** — `test_config_precedence.py` (~8),
      `test_prune.py::test_cron_footgun_yes_without_destructive_env_errors`
      and the events-prune suite (~10),
      `test_event_size_bounds.py` (~6). Soft budget: ≤25 new tests.

#### Risks acknowledged & mitigations

- **Destructive actions on the events table**: defended with
  confirmation prompt + `SELVEDGE_DESTRUCTIVE=1` env var +
  `prune.log` audit trail. Cron-footgun test
  (`test_cron_footgun_yes_without_destructive_env_errors`) named
  explicitly so a future test-suite cleanup can't quietly retire it.
- **Config precedence drift**: `test_config_precedence.py` asserts
  each step of the chart wins where it should, plus the special-
  case `SELVEDGE_DB` exception (config cannot override the
  environment for DB-path resolution). Every later phase that adds
  a config.toml setting extends this test, not adds a one-off check.
- **3.10 compatibility**: `tomli` declared explicitly in
  `pyproject.toml` (3.10-only conditional dependency). Preserves
  the "no external dependencies beyond declared ones" rule —
  declared, therefore fine.
- **Truncation silently losing high-stakes reasoning**: surfaced as
  validator warning at write time; `selvedge stats` counts
  truncated events so the pattern is visible.

### Phase 2.17 — Active memory v2 / semantic (v0.3.11)
> The pattern-based half of active memory. The `expires_when` column
> was added in v0.3.8's schema migration v3 but went unused; this
> release lights up the evaluator. Plus new `reject`/`revert`
> change_types and the `prior_attempts` outcome-classifier upgrade
> that consumes them. No new migration. Theme: *abandoned
> alternatives are first-class events.*

- [ ] **`expires_when` evaluation in `stale_decisions`** — column
      exists since v0.3.8; v0.3.11 ships the evaluator. **Closed
      grammar in v1** (NOT free-form): `library:NAME>=VERSION`
      (revisit when a named dependency hits a version),
      `entity:PATH:changes` (revisit when a named entity next
      changes), `date:ISO` (revisit on a specific date),
      `manual:LABEL` (opaque label for human review). Values that
      don't match the grammar are rejected at write time. Grammar
      lives in `selvedge.expires_when.PATTERNS` and grows
      deliberately, versioned in this doc. Patterns chosen because
      they can be evaluated from local state only — no network, no
      LLM.
- [ ] **New `change_type` values: `reject` and `revert`** — added
      to the `ChangeType` enum. `reject` records "we considered this
      and decided against it" without writing the change; `revert`
      records "we tried this and rolled it back," distinct from a
      regular `remove`. No new MCP tool — logged via existing
      `log_change`. **Adoption defended on three surfaces:**
      `log_change` docstring gains a worked example for the
      rejection use case; `selvedge.prompt.PROMPT_BLOCK` gains a
      sentence telling agents to log rejections (the load-bearing
      surface — prompt block reaches the agent every session);
      reasoning-quality validator gets a `reject`-specific rule
      that encourages reasoning to name *what was rejected* and
      *what was chosen instead*.
- [ ] **`prior_attempts` outcome-classifier upgrade** — proximity
      heuristic from v0.3.7 becomes a tiebreaker; explicit
      `reject`/`revert` events become the high-confidence tier
      directly. No API change for callers; existing
      `confidence: proximity_high` results now sometimes come back
      as `confidence: exact` instead.
- [ ] **`tests/test_prompt.py` update** — `PROMPT_BLOCK` change for
      `reject`/`revert` adoption ships in this PR. The sentinel-
      bracketed `--install` path must continue to work idempotently
      across the new content.
- [ ] **Tests** — `test_expires_when_grammar.py` covering each
      recognized pattern + a rejection case per malformed shape
      (~8), `test_active_memory.py` extension for `reject`/`revert`
      round-trip + classifier upgrade (~10), `test_prompt.py`
      update (~3), `test_public_api.py` extension for new enum
      values (~2). Soft budget: ≤25 new tests.

#### Risks acknowledged & mitigations

- **`expires_when` syntax fragmentation**: defended with the closed
  grammar in `PATTERNS`. Non-matching values rejected at write time.
  Grammar grows deliberately.
- **`reject`/`revert` under-call**: defended on three surfaces
  (docstring, PROMPT_BLOCK, validator). Prompt block is the
  load-bearing one because it reaches the agent every session.
- **Classifier upgrade silently changes existing query results**:
  v0.3.11 release notes call this out explicitly. Callers that
  filtered on `confidence: proximity_high` should also accept
  `confidence: exact`.

### Phase 2.18 — Competitive interop + verifiable claims (v0.3.12)
> Three items sharing a theme: making Selvedge's positioning claims
> observable, verifiable, and interoperable. Git Notes reader makes
> Selvedge a complement to Git AI's "open standard" framing rather
> than a substitute. Verifiable-no-network test backs the
> "your data stays on your machine" claim with CI. `ci-check` reporter
> ships the metrics surface that a later release's enforcement mode
> will gate on. Theme: *the positioning we already claim, now
> machine-checkable.*

- [ ] **Git Notes one-way reader — `selvedge import --format
      git-notes`.** Competition response: Git AI pitches Git Notes
      as "the open standard for tracking AI authorship in Git"; if
      that framing gets endorsed by Cursor / Anthropic / GitHub,
      Selvedge needs to be a *complement* to it, not a substitute.
      Reads `refs/notes/git-ai` (or whatever ref the format settles
      on) and maps line-range authorship into per-file `ChangeEvent`
      rows with `agent` populated from the note. Line ranges stored
      in `metadata` under `source_format: "git-notes"`. **Read-only
      on purpose** — export deferred to avoid entangling release
      pacing with Git AI's format evolution. Implementation in
      `selvedge/importers.py::parse_git_notes`.
- [ ] **Verifiable-no-network test** — `tests/test_no_network.py`
      imports every Selvedge entry-point module and asserts
      `socket.socket` / `urllib.request` are never called during
      normal operation, with a fixture that monkeypatches the
      socket factory to raise. **Scoped to Selvedge code paths
      only** — the `mcp` dependency's import-time socket
      initialization is mocked out so the test doesn't false-fail
      on dependencies. Pairs with a doctor line: "Network calls:
      none expected (verified by `test_no_network.py`)."
- [ ] **`selvedge ci-check` — reporter mode only.** Runs in CI on
      PR branches, computes reasoning quality / coverage ratio /
      changeset coverage against thresholds in `config.toml`.
      v0.3.12 **always exits 0**, prints metrics, posts PR comment
      if configured. A `--enforce` flag opts in to gating. Default
      enforcement remains deferred (no version commitment) until
      telemetry shows what natural reasoning-quality distributions
      look like — Goodhart-trap defense.
- [ ] **Tests** — `test_importers.py` Git Notes parser extensions
      (~10), `test_no_network.py` (~3), `test_ci_check.py`
      specifically asserting exit 0 under threshold violation
      without `--enforce` and non-zero with it (~6). Soft budget:
      ≤20 new tests.

#### Risks acknowledged & mitigations

- **Git Notes format drift**: parser emits `_unparseable` warnings
  rather than crashes. Doctor surfaces the count. Selvedge does
  NOT pin upstream version-for-version.
- **No-network test false positives**: scoped to Selvedge code
  paths; `mcp` dependency's import-time networking mocked.
- **`ci-check` Goodhart trap**: reporter-only by default; gating
  opt-in via `--enforce`. Default enforcement deferred until we
  have telemetry on natural distributions.

### Phase 2.19 — Cross-repo CLI (v0.3.13)
> First half of cross-repo personal-OSS memory. CLI surface ships
> first; MCP-parameter half waits for v0.3.14 once CLI usage tells
> us if agents would even use it. Read-only union over N local
> `.selvedge/` directories; writes still scope to the current
> project. Theme: *your portfolio is one queryable surface, opted
> in deliberately.*

- [ ] **Link registry — `links.toml`** — listing other `.selvedge/`
      directories the user owns. Resolution order mirrors DB-path
      precedence: `SELVEDGE_LINKS` env var > project-local
      `.selvedge/links.toml` > `~/.selvedge/links.toml`.
      **Per-project allowlist**: `links.toml` includes an
      `[allowlist]` section listing projects permitted to read this
      project via `--all-projects`. Default for a fresh project is
      empty — `--all-projects` from other projects skips this DB
      entirely. Users explicitly opt projects into being read.
      Enforcement layer, not documentation.
- [ ] **`selvedge link` / `unlink` / `linked` CLI commands** —
      manage the registry without hand-editing TOML. `selvedge link
      ~/projects/other-repo` validates the path, walks up looking
      for `.selvedge/`, refuses to add broken or schema-mismatched
      DBs. `selvedge linked` lists with health status (reachable /
      missing / version-skew / allowlist-status). Every `link` and
      `unlink` writes an audit entry to `.selvedge/links.audit.log`.
- [ ] **`--all-projects` flag on read CLI commands** — `selvedge
      history`, `search`, `diff`, `blame`, `stale` accept
      `--all-projects` to union across linked DBs (filtered by
      allowlist). Default behavior unchanged. Output gains a
      `project` column so users can tell which repo a result came
      from. **First-time consent prompt** the first time
      `--all-projects` runs in a given project — requires `--yes`
      or interactive confirmation before unioning.
- [ ] **`LinkedReadStorage` (read-only invariant)** — wraps N
      read-only `SelvedgeStorage` handles and refuses any write
      call. Architectural detail worth surfacing so future
      contributors don't bolt cross-repo writes on by accident.
- [ ] **Doctor — `linked projects` row** — each entry in
      `links.toml` reachable, schema version compatible, allowlist
      relationship consistent in both directions. Schema skew
      surfaces as WARN, not FAIL (the union still works).
- [ ] **Tests** — `test_linked_projects.py` covering link-file
      resolution order, broken-link skip behavior, schema-skew
      detection, union ordering, read-only invariant, allowlist
      enforcement (~22), `test_doctor.py` extension for the
      linked-projects row (~4). Soft budget: ≤30 new tests.

#### Risks acknowledged & mitigations

- **`--all-projects` privacy bleed**: defended at the *enforcement*
  layer with the per-project allowlist in `links.toml`. Docs alone
  aren't enough when agents learn flags. First-time consent prompt
  in the CLI is an additional speed-bump.
- **Implicit privilege escalation on linking**:
  `links.audit.log` audit trail + doctor surfacing recent links.
- **Schema skew across linked DBs**: surfaced as WARN by doctor;
  union still works. Per-DB scan summary on every query (the
  `_scan_summary` field) lands in v0.3.14 alongside the MCP
  parameter work.

### Phase 2.20 — Cross-repo MCP + write disambiguation (v0.3.14)
> Second half of cross-repo personal-OSS memory. Lights up the
> `all_projects: bool = False` parameter across MCP read tools, adds
> the `project` field to `LogChangeResult` so cross-repo writes are
> never ambiguous, and ships the `_scan_summary` response field that
> makes filter-coverage gaps visible. Ships only if v0.3.13's CLI
> cross-repo gets adopted — otherwise the MCP parameter is overhead
> for no demonstrated demand. Theme: *agents get the same cross-repo
> view as the CLI, with write resolution made visible.*

- [ ] **`all_projects: bool = False` parameter on read MCP tools** —
      same opt-in shape on `diff`, `history`, `search`,
      `prior_attempts`, `stale_decisions`. `log_change` and
      `changeset` are unchanged (writes always scope to the current
      project; changesets are per-project by definition). Allowlist
      enforced the same as in the CLI path.
- [ ] **`LogChangeResult.project` field** — when an agent runs
      `log_change` while ambient context is cross-repo, the response
      surfaces the project name that absorbed the write. Current
      project always wins for writes; this just makes the
      resolution visible.
- [ ] **`_scan_summary` field on `--all-projects` responses** —
      lists each linked project, schema version, allowlist status,
      row-count contribution, and whether NULL fields in older
      entries caused filter coverage gaps for `revisit_after` /
      `expires_when` / `changeset_id`. Closes the silent-miss
      failure mode where a filter on a field that didn't exist in
      older schemas drops entries with no indication.
- [ ] **Doctor — schema-skew + allowlist-symmetry check** for linked
      DBs (lands here rather than v0.3.13 because the MCP-side
      complications mean this needs to be tested at both surfaces).
- [ ] **Read-union performance test** —
      `tests/test_linked_projects_perf.py` runs common queries
      (`history --since 7d`, `search`, `blame`) against N=2, 5, 10,
      20 linked DBs and asserts response-time bounds. If N=20 is
      consistently slow, surface a doctor INFO line recommending a
      lower max.
- [ ] **Tests** — `test_server.py` extensions for the MCP
      parameter (~10), `test_log_change_result.py` extension for
      the new field (~3), `test_linked_projects_perf.py` (~8),
      `test_public_api.py` update (~2). Soft budget: ≤25 new tests.

#### Risks acknowledged & mitigations

- **MCP parameter overhead for unproven demand**: deferred from
  v0.3.13 specifically so we observe whether CLI cross-repo gets
  adopted. If v0.3.13 ships and no one uses `--all-projects` after
  a release cycle, v0.3.14 ships only the `LogChangeResult.project`
  field and the rest deprecates back to research.
- **Filter coverage silently missing old entries**:
  `_scan_summary` makes the gap explicit per linked DB.
- **Read-union performance scaling**: `test_linked_projects_perf.py`
  regression test; doctor-surfaced N-recommendation if perf
  degrades.

### Phase 2.21 — Salvage when needed (v0.3.15, conditional)
> Originally scoped as part of v0.3.5; deferred here because corruption
> is the rarest failure mode in the install base and `selvedge backup`
> (v0.3.5) plus `selvedge verify` (v0.3.5) cover 95% of the recovery
> need. v0.3.15 **ships only if** telemetry from the install base
> shows real corruption incidents that backup-restoration alone
> doesn't address. Otherwise the bullet stays open. Theme:
> *salvage, when telemetry shows we need it.*

- [ ] **`selvedge repair` command** — wraps SQLite's `.recover` to
      dump events from a corrupted DB into a salvage file; a
      `--from-recover` mode re-imports the dump into a fresh DB.
      Default dry-run; `--apply` actually writes. **Repair is
      salvage, not restoration** — `.recover` is probabilistic and
      may drop rows. With `--apply`, refuses to run if no
      `selvedge backup` has been taken in the last 7 days unless
      `--no-backup-required` is also passed. **Shell-out
      dependency on the `sqlite3` CLI binary** declared in
      `pyproject.toml` and surfaced by `selvedge doctor` if missing.
- [ ] **Doctor — `last_backup` escalation** — WARN >7 days, FAIL
      when no backups exist and events table >10k rows (the v0.3.5
      check stays informational; the failure escalation only
      matters when repair is shippable).
- [ ] **Tests** — `test_repair.py` covering dry-run vs. apply,
      backup-required gate, missing-sqlite3 binary handling (~12),
      `test_doctor.py` extension for the escalation (~3). Soft
      budget: ≤15 new tests.

#### Risks acknowledged & mitigations

- **Probabilistic salvage as restoration**: defended with the
  7-day backup gate, `--apply` requirement, and explicit "salvage
  not restoration" framing in help text.
- **`sqlite3` CLI not on PATH**: declared dependency + doctor
  surfacing missing-binary case + clear error message.
- **Shipping repair without demand**: explicitly conditional on
  telemetry. If install-base corruption incidents stay near zero,
  this phase stays open and the engineering effort goes elsewhere.



### Phase 3 — Backend rewrite + tool rename (v0.4.0)
> First release in the breaking-changes window. Bundles the storage
> backend abstraction and the deferred MCP tool-name rename so users
> absorb both API breaks in one cycle. HTTP+auth ships separately in
> v0.4.1 to keep each release's surface tightly scoped and isolate
> which subsystem is responsible if something regresses.
> The MCP tool-consolidation review gates everything else in v0.4.0
> — naming and consolidation in one cycle is cheaper than two.
> Theme: *one breaking-change cycle, one focused scope.*

- [ ] **MCP tool-surface consolidation review (gate before any
      other v0.4.0 changes ship).** By v0.3.11 the tool count is
      ~9 (`log_change`, `diff`, `blame`, `history`, `changeset`,
      `search`, `summary`, `prior_attempts`, `stale_decisions`).
      `history` plus `changeset_id` filter overlaps `changeset`;
      `summary` plus `selvedge digest` / `selvedge audit` overlap
      in shape. Past ~10 tools agents hit decision-fatigue.
      Required output: a written decision in this section on
      whether `changeset` is subsumed by `history`, whether
      `summary` is the canonical digest with `digest` / `audit`
      becoming pure CLI views, and what the final v0.4.0 tool list
      is. Decision lands BEFORE the tool-prefix migration below.
- [ ] **`StorageBackend` protocol + PostgreSQL backend** —
      `storage_sqlite.py` and `storage_pg.py` both implement
      `StorageBackend`. Configurable via `SELVEDGE_BACKEND` env var
      (e.g. `postgresql://...`). **`LinkedReadStorage` rewrite**
      lands as part of this work — v0.3.13 shipped SQLite-only;
      this release reimplements it against the new protocol so
      cross-repo union queries work against any backend mix.
- [ ] **MCP tool-name prefix migration** — rename `diff`,
      `history`, `search` (and any tool kept after consolidation
      with a too-generic name) to `selvedge_*` form. Deprecation
      aliases ship simultaneously; each carries a
      `DEPRECATED_UNTIL_VERSION` constant in code. Doctor warns
      loudly when an aliased name is called (counted from
      `tool_calls` telemetry). CI lint job fails the build if
      `DEPRECATED_UNTIL_VERSION` is reached without the alias
      being removed. The "one minor cycle" deprecation promise is
      enforceable, not aspirational.
- [ ] **Tests** — `test_storage_protocol.py` for the contract
      (~12), `test_storage_pg.py` against a PostgreSQL test fixture
      (~15), `test_linked_projects.py` extension for the rewritten
      `LinkedReadStorage` (~6), `test_tool_rename.py` for alias
      surfacing + deprecation mechanics (~10). Soft budget: ≤50
      new tests (the backend-abstraction work is the largest single
      piece of test surface in any v0.3.x or v0.4.x release).

#### Risks acknowledged & mitigations

- **Tool decision-fatigue at 10+ tools**: defended with the
  consolidation review gating the rest of v0.4.0. Not deciding is
  itself a decision.
- **Deprecation aliases lingering forever**:
  `DEPRECATED_UNTIL_VERSION` + CI lint + doctor surfaces recent
  alias usage. Enforceable, not aspirational.
- **External `CLAUDE.md` / `.cursorrules` references breaking**:
  deprecation aliases ship in v0.4.0; aliases removed in v0.5.0.
  One full minor cycle to migrate.
- **`StorageBackend` abstraction leaking SQLite assumptions**:
  full test suite runs against both backends in CI from day one.

### Phase 3.1 — HTTP REST + auth (v0.4.1)
> The wire-protocol layer. Exposes every MCP server operation over
> HTTP for clients that can't speak MCP directly (CI gates,
> compliance scanners, dashboards). API-key authentication for the
> HTTP path; the MCP stdio path stays unauthenticated (local-only
> by design — agent and server are the same machine). Released
> after v0.4.0's backend rewrite has bedded in so HTTP regressions
> aren't tangled with storage regressions. Theme: *Selvedge over
> the wire, gated.*

- [ ] **HTTP REST API layer (FastAPI)** — exposes every MCP server
      operation over HTTP. Endpoint list reflects whatever the
      v0.4.0 tool-consolidation review produced.
      **`test_http_protocol.py` is a release-blocker for v0.4.1** —
      boots a real `selvedge-server-http` subprocess (parallel to
      the existing `test_mcp_protocol.py`) and round-trips every
      endpoint over HTTP. The MCP smoke-test pattern caught contract
      drift the in-process tests missed; the HTTP surface needs
      the same coverage from day one.
- [ ] **Auth (API keys) for the HTTP layer** — bearer-token
      authentication with rotation. MCP stdio path stays
      unauthenticated. Out of scope: SSO, OAuth, RBAC (Phase 4
      hosted).
- [ ] **`selvedge-server-http` entry point** — installs alongside
      `selvedge-server` (stdio). Both share the same underlying
      tool implementations via the consolidated tool list from
      v0.4.0.
- [ ] **Tests** — `test_http_protocol.py` round-tripping every
      endpoint (~20), `test_auth.py` covering API-key happy and
      sad paths (~8). Soft budget: ≤30 new tests.

#### Risks acknowledged & mitigations

- **HTTP layer untested at protocol level**: `test_http_protocol.py`
  is a release-blocker, parallel to `test_mcp_protocol.py`.
- **Auth as security theater**: API keys are the entry-level
  defense for HTTP, not the long-term auth story. Phase 4 hosted
  introduces SSO + RBAC for the multi-user case. v0.4.1 covers
  single-user / single-org / CI-gate scenarios.
- **MCP path accidentally requiring auth**: stdio assumption
  documented in `selvedge.server` module docstring and asserted
  by a test that hits MCP without credentials and expects success.

### Phase 3.2 — Agent Trace interop (v0.4.2)
> Selvedge becomes a compatible producer of [Agent Trace](https://github.com/cursor/agent-trace),
> the open RFC (Cursor + Cognition AI, Jan 2026) for AI code
> attribution traces. Non-breaking — purely additive export/import
> formats. Ships after v0.4.1 because the AT spec may move during
> the v0.4.0 / v0.4.1 window and we'd rather lock against a
> settled version. Full design in `docs/agent-trace-interop.md`.
> Theme: *compatible producer, not competitor.*

- [ ] **`selvedge export --format agent-trace --output trace.json`**
      — emits AT v0.1.0 records from the local event store.
      Supports existing `--since` / `--entity` / `--project`
      filters plus a new `--ndjson` mode for large histories.
      Selvedge stays entity-centric internally; AT is purely a
      wire format. Reasoning, change_type, entity_path for
      non-file entities, changeset_id, and project all land in
      `extensions.selvedge.*`.
- [ ] **`selvedge import trace.json --format agent-trace`** —
      best-effort round-trip. Other tools' AT output won't populate
      `extensions.selvedge.*`, so Selvedge fills defaults
      (`entity_path = files[].path`, `change_type = "modify"`,
      `reasoning = ""` — validator warns at log time).
- [ ] **`range_unknown` preamble** — every Selvedge AT export
      emits a preamble explaining the fidelity profile. Events
      imported from migration files genuinely don't have line
      ranges; DB columns / env vars / dependencies don't either.
      Selvedge emits `extensions.selvedge.range_unknown: true`
      rather than fabricating `[1, 1]` placeholders.
- [ ] **Tests** — `test_agent_trace_export.py` covering round-trip,
      spec validation, non-file entity preservation, multi-event
      session, reasoning quality pass-through (~15). Soft budget:
      ≤20 new tests.

#### Risks acknowledged & mitigations

- **AT export low-fidelity perception (`range_unknown`)**:
  preamble language explains the source of the flag, plus README
  guidance so consumers understand the fidelity profile.
- **AT spec movement during the v0.4.x window**: pin to the spec
  version current at v0.4.2 ship; document mapping per version in
  `docs/agent-trace-interop.md`.
- **`extensions.selvedge.*` namespace squat**: AT spec recommends
  reverse-domain notation; we use `selvedge.*` because flat
  namespaces are in the wild and we don't want to gate on
  registering `dev.selvedge`. Reversible later if it matters.


### Phase 4 — Platform (hosted business)
- [ ] Web dashboard (React + the REST API)
- [ ] Cross-repo queries (server-side, multi-tenant, with auth and
      cross-user permissioning). The single-user OSS variant —
      read-only local overlay across `.selvedge/` directories the same
      user owns — ships separately as Phases 2.19 / 2.20 (v0.3.13 +
      v0.3.14). Hosted is for teams sharing context across users;
      OSS is for individuals across their own portfolio.
- [ ] Team/org-level retention policies (per-tenant, configurable
      independently from the project-local `retention_days_events` +
      `retention_days_tool_calls` settings shipped in v0.3.10)
- [ ] Team/org management
- [ ] Webhook events (Slack, PagerDuty, etc. on schema changes)

---

## Cross-cutting risk register

Each phase carries its own "Risks acknowledged & mitigations" subsection
covering risks specific to that release. These are the risks that span
the v0.3.5 → v0.4.0 arc and need active discipline at every release,
not just one.

### "No LLM calls inside Selvedge" non-goal — per-feature stress test

Every feature in Phases 2.12–2.14 (`summary`, `prior_attempts`,
`stale_decisions`, `audit`, `digest`) sits near the boundary where a
small LLM call would *seem* to make the output meaningfully better.
Each feature individually says "templated is enough"; the cumulative
pressure is real and grows monotonically.

**Discipline**: every PR that ships one of these features must include
a one-paragraph entry in its description explaining how the templated
output covers the user need, and what the explicit guard is that
prevents an LLM from creeping into Selvedge core. Reviewers (= the
maintainer) reject the PR if the answer is "we'll add an LLM later if
needed." Defending the non-goal is what makes Selvedge cheap,
dependency-light, and auditable; it is also the principle most likely
to drift quietly. See `long-term-thesis.md` §6 ("Things to leave
alone") for the strategic version.

### Output shape proliferation

By v0.4.0 the public API surface includes ~10 MCP tools, each with a
TypedDict result. Every new field on `ChangeEvent` becomes a forward-
compatibility constraint via `tests/test_public_api.py`. Without
discipline, the surface grows monotonically and refactoring becomes
expensive.

**Discipline** (added to `CLAUDE.md` code conventions): new TypedDicts
must be reviewed against the existing set for shared-shape
opportunities before merge. Prefer extending an existing result type
to introducing a new one. The `LogChangeResult` /
`BlameResult` pattern (every field always populated, never `null`)
applies to every new result type by default.

### Test-surface budget per phase

Test count: 57 at v0.1.0 → 244 at v0.3.1 → 282 at v0.3.2 → ~336 at
v0.3.4. Continuing the trajectory naively puts the suite at 500+ by
v0.4.0, with proportional CI-runtime and flakiness costs. The
release-scope restructure (2026-05-10) replaced 4 broad phases with
11 narrower phases, so the per-phase budgets shrunk in step.

**Soft budget per phase** (target, not a hard cap):

| Phase | Version | Target test delta |
|---|---|---|
| 2.11 | v0.3.5  | ≤ 25 new tests |
| 2.12 | v0.3.6  | ≤ 15 new tests |
| 2.13 | v0.3.7  | ≤ 40 new tests (entity foundation + wedge share the release) |
| 2.14 | v0.3.8  | ≤ 25 new tests |
| 2.15 | v0.3.9  | ≤ 30 new tests |
| 2.16 | v0.3.10 | ≤ 25 new tests |
| 2.17 | v0.3.11 | ≤ 25 new tests |
| 2.18 | v0.3.12 | ≤ 20 new tests |
| 2.19 | v0.3.13 | ≤ 30 new tests |
| 2.20 | v0.3.14 | ≤ 25 new tests |
| 2.21 | v0.3.15 | ≤ 15 new tests (conditional ship) |
| 3    | v0.4.0  | ≤ 50 new tests |
| 3.1  | v0.4.1  | ≤ 30 new tests |
| 3.2  | v0.4.2  | ≤ 20 new tests |

When a phase exceeds its budget, the release notes call out *why* —
typically a perf-regression suite (test_migrations_perf,
test_linked_projects_perf) or a new protocol smoke test
(test_http_protocol). Budget overruns aren't a failure; they're a
visibility signal that the phase scope grew or that the test design
needs review. Aggregate cap target at v0.4.2 ship: ~700 tests
(versus ~500 in the original plan; the release-scope restructure
shifted total test surface up because each release ships with
narrower scope but the same coverage discipline).

### MCP tool count discipline

Tool count grows: 6 today → 6 at v0.3.5 (no new tools) → 7 at v0.3.7
(`prior_attempts` only — `log_rename` folded into `log_change` as a
`rename_from` parameter, `summary` shipped as a
`selvedge.aggregates` library helper rather than an MCP tool) → 8
at v0.3.8 (`stale_decisions`) → tool consolidation review at v0.4.0
(Phase 3). Past ~10 tools, agents hit decision-fatigue picking the
right one, and overlap between tools (e.g. `history`+`changeset_id`
vs. `changeset`) makes wrong-tool calls likely. The 2026-05-10
consolidation pass cut two candidate tools out of the v0.3.7 surface
ahead of the v0.4.0 review, leaving Selvedge well under the ~10-tool
threshold through the entire active-memory arc and letting the
v0.4.0 review focus on the real overlap target (`diff` / `blame` /
`history`).

**Discipline**: every new MCP tool proposal answers "is this an
existing tool with a different default, or genuinely a new shape?"
before the design lands. The v0.4.0 consolidation review is the
explicit budget-reset moment.

### Release scope discipline

Codified 2026-05-10 alongside the v0.3.5-onward phase restructure.
Each v0.3.x and v0.4.x phase below must be sized as one focused
unit of work: **one coherent theme, 3–5 features, ≤30 new tests,
~400–800 LoC, one focused week.** When a planning pass produces a
phase that exceeds those bounds, the phase splits before the work
starts — not after a mid-ship realization that it was too big.

**Why this matters**: smaller per-release surface means smaller
blast radius if something regresses, cleaner CHANGELOG entries, and
release-quality checkpoints that map 1:1 to a theme. A six-feature
release where one feature regresses leaves the other five tangled
in the rollback decision; a single-theme release just rolls back
to the previous tag. Scope discipline at release-time is what
turns the test suite, the doctor surface, and the version-bump
checklist into useful gates instead of paperwork.

**Discipline**: at the start of every phase, review the bullet list
and ask "is this one theme, or am I bundling two adjacent themes
because they're conveniently shippable together?" If it's the
latter, split. Splits cost a release-cycle checklist run (more
releases, more Smithery republishes, more website-sync PRs) but
the per-ship risk shrinks dramatically. Net win is bigger than the
cost.

**Trade-off acknowledged**: more releases means more cumulative
release-cycle overhead. The v0.3.5 → v0.4.2 arc now contains 14
releases instead of 5. Each release's manual Smithery republish and
selvedge-site PR is friction. The auto-PR GitHub Action listed in
"Open follow-ups" (under Website ↔ codebase sync) gets more urgent
as a result; the manual cadence stops being acceptable past ~v0.3.8
if the work hasn't been built.

### Maintainer-capacity check

v0.3.5 → v0.4.2 spans roughly a year of solo-maintainer effort under
the new release-scope discipline. The 2026-05-10 restructure traded
4 broad phases for 14 narrower ones; calendar time is roughly similar
but ship
rate increases from ~5 releases per year to ~14 per year. Ship rate
is itself a defense — "actively maintained, fast-evolving" reads
differently from "big-bang every quarter."

**Discipline** (cross-referenced in `long-term-thesis.md` §7): at the
end of each phase ship, review the *next* phase's bullet list and
*explicitly defer* any feature that isn't load-bearing for that
release's headline goal. Deferred items move to the next phase or
into the "Future work" appendix near the end of this doc. The phase
plan is a budget, not a vow.

### Website ↔ codebase sync

Selvedge ships from two repos: `masondelan/selvedge` (this one — the
code, MCP server, CLI, CHANGELOG, README) and
`masondelan/selvedge-site` (Astro + Starlight, auto-deploys to
selvedge.sh on push). The codebase repo is the source of truth for
*what* shipped — CHANGELOG, server.py docstrings, manifest.json. The
site is the source of truth for *how it's described* — homepage
copy, the comparison page, the "decision archaeology" positioning,
release-notes prose for non-users. Both must stay in lockstep or
the most-visible-to-the-internet version of Selvedge ages out of
date.

`docs/comparison.html` is a transitional artifact: it predates
selvedge-site (carried over from the old GitHub Pages deploy), and
its canonical link points at `selvedge.sh/compare/agent-tools/`.
Treat the site copy as canonical; `docs/comparison.html` is a stale
mirror until the Pages-migration follow-up retires it.

**Discipline (codified in this section, enforced via the release-
cycle checklist in every phase from v0.3.5 onward):**

* Every version bump triggers a paired commit/PR in
  `masondelan/selvedge-site` — at minimum the version string and
  the release-notes mirror; more if positioning or behavior
  described on the site changed.
* If a change in the codebase repo modifies the user-facing
  narrative (new MCP tool, new CLI command, a wedge feature like
  `prior_attempts`, a values-shift like the no-cloud claim), the
  PR description on the codebase side must name the corresponding
  site change in a "Site sync" section. No "Site sync: none"
  default — the writer asserts it explicitly or it didn't get
  considered.
* Doctor's status output will surface the *installed* Selvedge
  version. The site's homepage shows the *advertised* version. A
  release where these two diverge for more than 24 hours is a
  release-quality failure, not a marketing miss — track it in the
  cross-cutting risk register if it happens.

**Open follow-up** (tracked in the Future Work appendix): an
auto-PR GitHub Action that opens a draft PR against `selvedge-site`
on tag push. The release-scope restructure turned 5 releases into 14
across the v0.3.5 → v0.4.2 arc, which means the manual selvedge-
site PR runs 14 times instead of 5. Past ~v0.3.8 the manual cadence
becomes the bottleneck and the auto-PR action needs to land.

### Competitive narrative drift

The 2026-05-07 internal teardown of Git AI (kept off-repo as
competitive intel) identified five competitive moves that
could pull Selvedge into a category it doesn't want to compete in:
(1) Git AI ships an MCP server and narrows the architectural moat;
(2) Git AI raises a public seed/Series A and the marketing-spend
asymmetry widens; (3) Cursor / Anthropic / GitHub endorse Git AI's
note format as the open standard for AI authorship; (4) an
"AI-era observability" analyst category coalesces around Git AI's
framing and Selvedge gets read as a weaker alternative;
(5) Git AI's agent-vendor cooperation strategy works and every
major agent ships a Git AI hook before Selvedge has equivalent
reach. None of these are imminent; all of them are plausible inside
the v0.3.5 → v0.4.2 window (the 2026-05-10 release-scope
restructure widened the named window but did not lengthen the
calendar arc).

**Discipline**: every phase ship reviews this list and answers one
question — "does this release widen the MCP-first / decision-
archaeology lead, or does it cede ground?" Concrete defenses
already sequenced into the phase plan: `prior_attempts` shipped
with its positioning artifacts in v0.3.7 (wedge legible); Git Notes
one-way reader in v0.3.12 (interop, not substitute); verifiable-
no-network test in v0.3.12 (positioning claim made auditable); the
cross-repo `prior_attempts` extension in v0.3.14 ("you considered
this in your other project six months ago" — Git AI cannot match
this with their data model); Agent Trace export in v0.4.2
(compatible producer, not competitor). Position changes (homepage,
comparison page) live in `docs/engagement-strategy.md` and
`docs/comparison.html`; this section exists so the *engineering*
phase plan keeps line-of-sight to the
narrative those documents are trying to hold. If a future phase
removes a competitive defense from this list without replacing it,
the PR description must say which defense and why.

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
- No AST / code parser — Selvedge stores entity events the agent supplies, canonicalized and queryable, but does not extract entities from source code. Entity *extraction* is language-specific (Python, TS, Go, Rust, Java, …), drags in per-language dependencies, and fights the dependency-free-core rule. The v0.3.7 entity foundation (canonicalization, `log_rename`, soft validation) is the durable answer; "wouldn't it be cool if Selvedge parsed Python" is the wrong fork and PRs proposing it bounce against this line.

---

## Future work (no version assigned)

Items removed from the versioned roadmap in the 2026-05-10
release-scope restructure. Each was either gated on prerequisites
that haven't been met, or wasn't load-bearing for any release's
headline goal. They live here so the decision to defer is visible —
not lost in chat history — and so they can be promoted back into a
phase when the gating condition is satisfied.

**VS Code extension scaffolding.** Lives in a separate repo, has a
separate ship cadence, and depends on the `summary` MCP tool from
v0.3.7 to spec against. Promote to a phase when (a) a tracking
issue exists, (b) a named owner commits to a 90-day shipping
review, and (c) the extension repo has been created. Until then,
roadmap noise.

**Auto-PR GitHub Action for selvedge-site.** Build-process
improvement that opens a draft PR against `masondelan/selvedge-site`
on every tag push, with version bumps and CHANGELOG diff
pre-filled. Manual review still required (positioning prose isn't
auto-translatable from CHANGELOG bullets), but the draft removes
the "did anyone update the site?" friction. Promote to a phase
when the manual release-cycle cadence has bedded in (post-v0.3.8)
and the friction is observable.

**Push-model `prior_attempts` variant.** Currently pull-only.
The push model would auto-warn on `log_change` when the entity
has prior rejected attempts. Deferred until pull-tool adoption
signal shows agents actually act on what `prior_attempts` returns.
If agents ignore the pull tool, the push tool is noise; if they
act on it, the push tool is leverage.

**`selvedge-server-http` health endpoint + structured logs.**
Add observability surface to the HTTP layer post-v0.4.1.
Promote when the HTTP layer ships and operational requirements
surface from real deployments.

**Selvedge → Git Notes writer (export direction).** v0.3.12 ships
a one-way reader from Git Notes; the write direction would
emit Selvedge events back into `refs/notes/selvedge-intent`. The
read direction is the load-bearing competitive defense; write
adds two-product release-pacing entanglement (Selvedge's note
format would need to track Git AI's spec movement). Promote only
if the Agent Trace alliance moves slower than expected and Git
Notes becomes a de-facto cross-tool surface.

**Tool consolidation as a v0.5.0 follow-on.** v0.4.0's
consolidation review is the budget-reset; v0.5.0 reviews again
once the v0.4.x line has shipped and we have post-rename
usage data. Not a release commitment, just a tracking note.
