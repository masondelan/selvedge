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
│   ├── server.py         FastMCP server — 5 tools exposed to AI agents
│   └── cli.py            Click + Rich CLI — init, status, diff, blame, history, search, log
├── tests/
│   ├── test_storage.py
│   ├── test_server.py
│   └── test_cli.py
├── docs/
│   └── getting-started.md
├── pyproject.toml
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

The MCP server (`selvedge/server.py`) exposes these tools to AI agents:

### `log_change`
Record a change. Call this immediately after making any meaningful change.

**Required:** `entity_path`, `change_type`
**Optional:** `diff`, `entity_type`, `reasoning`, `agent`, `session_id`, `git_commit`, `project`

### `diff`
Get change history for an entity or entity prefix. Returns list of events, newest first.

### `blame`
Get the most recent change to an exact entity path — what, when, who, why.

### `history`
Filtered history across all entities. Supports `since` (ISO or relative like `7d`, `30d`, `1y`), `entity_path`, `project`, `limit`.

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
- Set `agent` to "claude-code" (or whichever agent you are).
- Set `session_id` if you have access to the current session/conversation ID.
- Set `git_commit` to the commit hash once you know it.
- Before modifying an entity, call selvedge.diff or selvedge.blame to understand
  its history and avoid conflicting with past decisions.
```

---

## Phase plan

### Phase 1 — Core (DONE ✓)
- [x] MCP server with 5 tools
- [x] SQLite storage with WAL mode
- [x] CLI (init, status, diff, blame, history, search, log)
- [x] PyPI package with entry points
- [x] Test suite (storage, server, CLI)

### Phase 2 — Integrations
- [ ] Git hook: auto-link selvedge events to commit hashes at commit time
  - Post-commit hook reads `git rev-parse HEAD`, backfills `git_commit` on events with empty commit field and matching timestamp window
- [ ] Migration file parser: ingest Alembic / Liquibase / raw SQL migrations to backfill schema history
  - Parse migration files, extract column/table ops, write ChangeEvents with `change_type` inferred from SQL
- [ ] `selvedge import` CLI command for the above parsers
- [ ] `selvedge export` — dump history as JSON/CSV

### Phase 3 — Team features
- [ ] PostgreSQL backend option (configurable via `SELVEDGE_BACKEND=postgresql://...`)
  - Abstract `SelvedgeStorage` behind a protocol/interface so backends are swappable
  - `storage_sqlite.py` and `storage_pg.py` both implement `StorageBackend`
- [ ] HTTP REST API layer (FastAPI) — exposes the same 5 operations over HTTP
- [ ] Auth (API keys) for the HTTP layer

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

## Non-goals (Phase 1)

- No web UI
- No PostgreSQL
- No authentication
- No real-time streaming
- No git integration (Phase 2)
- No migration file parsing (Phase 2)
- No multi-user/team features (Phase 3)
- No LLM calls inside Selvedge itself — reasoning is captured FROM agents, not generated by Selvedge
