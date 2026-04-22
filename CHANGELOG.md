# Changelog

All notable changes to Selvedge are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Selvedge uses [semantic versioning](https://semver.org/).

---

## [0.1.0] — 2025-04-21

Initial release.

### Added

- **MCP server** (`selvedge-server`) with 5 tools: `log_change`, `diff`, `blame`, `history`, `search`
- **SQLite storage** with WAL mode and graceful fallback for mounted filesystems
- **DB path resolution**: `SELVEDGE_DB` env var → walk-up `.selvedge/` → `~/.selvedge/selvedge.db`
- **CLI** (`selvedge`) with commands: `init`, `status`, `diff`, `blame`, `history`, `search`, `log`, `stats`
- **`selvedge stats`** — tool call coverage report: shows log_change call ratio, per-tool breakdown, and recent call history. Answers "is my agent actually logging changes?"
- **Local tool call telemetry** — every MCP tool invocation is recorded to a `tool_calls` table (local only, never networked). Powers `selvedge stats` and `scripts/coverage_check.py`
- **`scripts/coverage_check.py`** — cross-references git log against Selvedge events to measure coverage ratio per commit
- `--json` flag on all read commands for machine-readable output
- Relative time support in `--since` flag (`7d`, `24h`, `3m`, `1y`)
- Rich terminal output with tables and styled panels
- Full test suite: storage, server, and CLI tests (57 tests)
- PyPI package with `selvedge` and `selvedge-server` entry points

### Entity types supported
`column`, `table`, `file`, `function`, `class`, `endpoint`, `dependency`, `env_var`, `index`, `schema`, `config`, `other`

### Change types supported
`add`, `remove`, `modify`, `rename`, `retype`, `create`, `delete`, `index_add`, `index_remove`, `migrate`

---

## Roadmap

### [0.2.0] — planned
- Git post-commit hook: auto-backfill `git_commit` on events matching the commit window
- `selvedge import`: parse Alembic / Liquibase / raw SQL migration files to backfill schema history
- `selvedge export`: dump history as JSON or CSV

### [0.3.0] — planned
- PostgreSQL backend option (`SELVEDGE_BACKEND=postgresql://...`)
- HTTP REST API layer

### [1.0.0] — planned
- Web dashboard
- Cross-repo queries
- Team / org management
