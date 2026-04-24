# Changelog

All notable changes to Selvedge are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Selvedge uses [semantic versioning](https://semver.org/).

---

## [0.3.0] — 2026-04-23

A correctness and data-quality release. No new feature surface — every
change here either prevents a wrong answer, prevents silent data loss,
or makes the import story actually work end-to-end.

### Fixed (correctness — high severity)

- **`5m` now means 5 minutes, not 5 months.** `_parse_relative_time` mapped
  `m` to months, contradicting every CLI convention (`sleep 5m`, `kubectl
  --since=5m`, Prometheus). New mapping: `m` = minutes, `mo`/`mon` = months.
  Users typing `selvedge history --since 5m` get the last 5 minutes as
  expected.
- **`search()` and prefix matching escape SQL `LIKE` wildcards.** Previously
  `selvedge search "stripe_customer_id"` matched `stripeXcustomerXid` and
  similar (because `_` is a `LIKE` wildcard). All five `LIKE` queries in
  `storage.py` now use `ESCAPE '\'` and escape `\`, `_`, `%` in user input.
- **Unparseable `--since` raises instead of silently returning empty.**
  `selvedge history --since yesterday` previously did `WHERE timestamp >=
  'yesterday'`, lexicographically matched nothing, and returned no error.
  Now both the CLI and MCP server validate the input and surface a clear
  error.
- **`CREATE TABLE` import emits a `column.add` event for every column.**
  Previously importing `CREATE TABLE users (id INT, email TEXT)` created
  one event for the table and zero for its columns, so `selvedge blame
  users.email` returned "no history" for any column defined only in the
  initial schema. The import story now works end-to-end.
- **All timestamps normalized to canonical UTC (`...Z` suffix) on write.**
  Previously a tz-aware timestamp like `09:00:00-08:00` (= 17:00 UTC)
  sorted lexicographically *before* `10:00:00+00:00` (because `-` < `+`
  in ASCII), even though the PST time is later. All stored timestamps
  are now converted to UTC and serialized with a fixed `Z` suffix so
  lexicographic and chronological order match.

### Fixed (data quality — medium severity)

- **`change_type` validated against the `ChangeType` enum.** Hallucinated
  types (`"banana"`) and typos (`"modifyed"`) are now rejected with a
  clear error rather than silently inserted, which kept stats grouping
  honest.
- **`entity_type` coerced to `"other"`** when not a known `EntityType`.
  Descriptive metadata, not load-bearing for queries — coerce rather
  than reject.
- **Empty `entity_path` rejected.** `ChangeEvent(entity_path="", ...)`
  used to insert orphan rows that broke prefix queries.
- **Alembic and SQL `RENAME TABLE` emit two events.** A `rename` event
  for the old name and a `create` event for the new name, so `selvedge
  blame` works under both names after a rename. Same pattern for
  `RENAME COLUMN` (column `add` event for the new name).
- **`get_db_path` requires the DB file to exist**, not just the
  `.selvedge/` directory. A stray empty `.selvedge/` upstream no longer
  hijacks resolution. Falling back to the global `~/.selvedge/`
  database now prints a one-time stderr warning so unintentional
  global use is visible. Suppress with `SELVEDGE_QUIET=1`.
- **`backfill_git_commit` window widened from 10 to 60 minutes** so
  longer agent sessions still get their events stamped after a commit.
  `selvedge status` now shows the count of events missing `git_commit`
  to nudge users toward installing the post-commit hook.

### Added

- **`storage.log_event_batch()`** — wraps multiple inserts in a single
  transaction. Used by `selvedge import` for orders-of-magnitude faster
  bulk imports of large Alembic histories, and makes the import atomic.
- **`storage.count_missing_git_commit()`** — surfaced in `selvedge status`.
- **`selvedge.timeutil`** — shared `parse_time_string()` and
  `normalize_timestamp()` helpers, deduplicating the relative-time
  parsing previously copy-pasted between `server.py` and `cli.py`.
- **`selvedge log` CLI** uses `click.Choice` for `change_type`, so
  invalid types are caught at the argument-parsing layer with the
  full list of valid choices.
- **Adversarial-input test suite** (`tests/test_adversarial.py`) with
  25 tests covering underscore-in-search, `--since yesterday`,
  `CREATE TABLE` blame for inline columns, mixed-tz ordering, and
  validation rejection paths.

---

## [0.2.1] — 2026-04-22

### Added

- **`changeset_id` field on `ChangeEvent`** — optional slug to group related changes
  under a named feature or task (e.g. `"add-stripe-billing"`). Indexed in SQLite.
- **`changeset` MCP tool** — retrieve all events belonging to a `changeset_id`,
  returned oldest-first so you can reconstruct the full scope of a feature.
- **`storage.list_changesets()`** — summary view of all changesets: id, event count,
  agent, and time range.
- **Reasoning quality validation in `log_change`** — the server now returns a
  `warnings` array if `reasoning` is empty, under 20 characters, or matches a
  generic placeholder (`"user request"`, `"done"`, `"n/a"`, etc.). Logged event
  is still written; warnings are advisory only.

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

### [0.4.0] — planned (Phase 3 — team features)
- PostgreSQL backend option (`SELVEDGE_BACKEND=postgresql://...`)
- HTTP REST API layer (FastAPI)
- Auth (API keys) for the HTTP layer

### [1.0.0] — planned
- Web dashboard
- Cross-repo queries
- Team / org management
- Webhook events on schema changes
