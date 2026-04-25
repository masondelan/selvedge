# Selvedge — CLAUDE.md

> Change tracking for AI-era codebases.
> The pandas of codebase history — essential infrastructure, not a feature.

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
│   └── getting-started.md
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

## System prompt / agent instructions

Add this to your agent's system prompt or CLAUDE.md to activate logging:

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

### Phase 3 — Team features (v0.4.0)
- [ ] PostgreSQL backend option (configurable via `SELVEDGE_BACKEND=postgresql://...`)
  - Abstract `SelvedgeStorage` behind a protocol/interface so backends are swappable
  - `storage_sqlite.py` and `storage_pg.py` both implement `StorageBackend`
- [ ] HTTP REST API layer (FastAPI) — exposes the same 6 operations over HTTP
- [ ] Auth (API keys) for the HTTP layer
- [ ] **Agent Trace interop** — `selvedge export --format agent-trace` and
      `selvedge import --format agent-trace` (Cursor/Cognition open RFC, Jan 2026).
      Design doc: [`docs/agent-trace-interop.md`](docs/agent-trace-interop.md).
      Selvedge stays entity-centric internally; AT is purely a wire format
      for cross-tool readers and compliance audits.

### Phase 4 — Platform (hosted business)
- [ ] Web dashboard (React + the REST API)
- [ ] Cross-repo queries
- [ ] Retention policies
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

## Code conventions

- **No external dependencies beyond the declared ones.** Keep the install footprint small.
- **SQLite first, always.** Don't reach for Postgres until Phase 3. SQLite with WAL handles concurrent reads fine.
- **ChangeEvent is a dataclass, not Pydantic.** Keep the core dependency-free. MCP serialization uses `to_dict()`.
- **Every public function has a docstring.** The MCP tool docstrings are user-facing — they appear in agent tool listings.
- **Tests use `tmp_path` fixtures and `SELVEDGE_DB` env var.** Never write to the real DB in tests.
- **Rich for all terminal output.** No bare `print()` in cli.py.
- **`--json` flag on every read command.** Machine-readable output is a first-class concern.
- **Type hints everywhere.** Python 3.10+ syntax (`X | Y`, `list[dict]`, etc.).

---

## Non-goals (through Phase 2)

- No web UI (Phase 4)
- No PostgreSQL (Phase 3)
- No authentication (Phase 3)
- No real-time streaming
- No multi-user/team features (Phase 3)
- No LLM calls inside Selvedge itself — reasoning is captured FROM agents, not generated by Selvedge

---

## Cowork instructions

Guidelines for the Cowork AI assistant working on this project.

**Phase plan maintenance**
- The source of truth for shipped work is `CHANGELOG.md`, not the phase checkboxes.
- When asked to update the phase plan, read `CHANGELOG.md` and `selvedge/server.py` (for current tool count/names) and compare against the checkboxes in this file. Mark anything shipped as done.
- Version bumps require updating both `pyproject.toml` AND `selvedge/__init__.py`, then tagging the commit to trigger the PyPI publish workflow.

**Release notes**
- Pull content from `CHANGELOG.md`. Group into Added / Changed / Fixed sections.
- The MCP tool docstrings in `server.py` are user-facing — keep them accurate after any tool changes.

**Test suite**
- Tests live in `tests/`. Run with `pytest` from the repo root.
- Never write to the real DB in tests — always set `SELVEDGE_DB` env var to a `tmp_path` fixture path.
- `test_importers.py` covers the migration parsers; `test_server.py` covers MCP tools.

**Scheduled recurring tasks (managed by Cowork)**
- **Weekly CLAUDE.md drift check** — compare `CHANGELOG.md` against the phase plan and flag any shipped items still showing as unchecked.
- **Weekly coverage report** — run `scripts/coverage_check.py` against the git log and report the `log_change` call ratio per commit.
