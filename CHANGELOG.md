# Changelog

All notable changes to Selvedge are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Selvedge uses [semantic versioning](https://semver.org/).

---

## [0.2.0] — 2026-04-22

### Added

- **`selvedge install-hook`** — installs a git post-commit hook that automatically
  backfills `git_commit` on Selvedge events after each commit. Safe to run on repos
  with existing post-commit hooks (appends rather than overwrites). Idempotent.
- **`selvedge backfill-commit --hash HASH`** — manually backfill `git_commit` on
  recent events within a configurable time window. Called by the git hook automatically.
- **`selvedge import PATH`** — parse migration files and backfill schema history:
  - Raw SQL DDL: `CREATE TABLE`, `ALTER TABLE ADD/DROP/RENAME/ALTER COLUMN`,
    `DROP TABLE`, `CREATE/DROP INDEX`, `RENAME TABLE`
  - Alembic Python migrations: `op.add_column`, `op.drop_column`, `op.create_table`,
    `op.drop_table`, `op.alter_column`, `op.rename_table`, `op.create_index`,
    `op.drop_index`, `op.execute()` (with inline SQL parsing)
  - Supports `--dry-run` (preview without writing), `--json`, `--project`, `--format`
  - Directories walked recursively; files sorted by name for chronological order
- **`selvedge export`** — dump change history to JSON or CSV with full filter support
  (`--since`, `--entity`, `--project`, `--limit`, `--output`)

### Changed

- `selvedge stats` added in 0.1.0 now documented in CHANGELOG (was omitted)

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
