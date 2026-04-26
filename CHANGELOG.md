# Changelog

All notable changes to Selvedge are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Selvedge uses [semantic versioning](https://semver.org/).

---

## [0.3.3] — 2026-04-26

A discoverability + ergonomics release. No new MCP tools, no behavior
changes that affect stored data — but the live tool schema is now
substantially richer for the agents that read it and the directories
that score it. **Drop-in upgrade for anyone on 0.3.2.**

### Added

- **Per-parameter descriptions on every MCP tool.** All 6 tools now
  declare each parameter via `Annotated[T, Field(description=...)]`,
  populating `inputSchema.properties.<param>.description` in the live
  tool listing. Previously each parameter shipped only `type` and
  `title`; the rich docstrings sat in the function body where agents
  couldn't see them at tool-call time. Agents picking which tool to
  call read these descriptions directly, so this is a DX win for
  Claude Code / Cursor / Copilot use, not just a directory-score
  improvement. Coverage went 0/21 → 21/21.
- **MCP tool annotations on every tool.** Each tool now declares
  `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`,
  and a human-friendly `title`. `log_change` is the only writer (not
  destructive — it's append-only — but not idempotent, since each call
  mints a new event). The five readers (`diff`, `blame`, `history`,
  `changeset`, `search`) are all read-only + idempotent. None are
  open-world. Lets MCP clients gate or surface tools appropriately.
- **`outputSchema` on `log_change` and `blame`.** New `LogChangeResult`
  and `BlameResult` TypedDicts (in `selvedge.server`) give the JSON-RPC
  layer something concrete to advertise. The four list-returning tools
  (`diff`, `history`, `changeset`, `search`) already had auto-generated
  schemas from their `list[dict]` annotation; this brings the dict
  returners in line so all 6 tools advertise their output.
- **Custom server icon.** A "stitched timeline" mark — a horizontal
  running stitch crossing the icon, where each visible stitch is a
  captured change event. Lives at `assets/icon.svg` and a 512×512
  `assets/icon.png`. Referenced from `manifest.json` so it ships with
  the Smithery bundle and renders in the directory's thumbnail.

### Changed

- **`log_change` always returns a complete result payload.** The
  result now always includes `id`, `timestamp`, `status`, `error`, and
  `warnings` keys (not just present-when-non-empty). On success,
  `error` is `""` and `warnings` is `[]` if reasoning passed the
  quality validator. On validation failure, `id`/`timestamp`/
  `warnings` are empty and `status` == "error". Required for the new
  `outputSchema` to validate cleanly. Tests updated to match.
- **`blame` returns a stable shape on miss.** Empty-history responses
  now populate every event field with the empty value of its type and
  set `error` to the "no history found" message. Previously returned
  the slim `{"error": "..."}`. Same `error`-key convention, fuller
  payload — easier for callers to type-check without branching.
- **Tool-level descriptions are dedented at startup.** Each tool's
  docstring is run through `inspect.cleandoc` once at import time so
  `tools/list` doesn't leak the function-body indent
  (`"\n    Get change..."` → `"Get change..."`). Cosmetic but visible
  in any directory that surfaces the raw description.

### Documentation

- **`CLAUDE.md` ↔ `docs/architecture.md` split.** `CLAUDE.md` is now a
  thin agent-instructions file (sources of truth, code conventions,
  version bump checklist, scheduled tasks). The architecture, data
  model, MCP tool reference, full CLI reference, phase plan, and
  non-goals all moved to `docs/architecture.md`. Reduces noise on
  every Claude Code / Cowork session boot and gives the architecture
  doc a stable home.
- **isError convention documented.** Empty-history cases (`blame` on
  an unknown entity, `changeset` with no events) intentionally return
  `{"error": "..."}` with protocol-level `isError: false`. Empty
  history isn't a protocol failure; the in-payload `error` key is
  the documented signal. Codified as a comment in `selvedge.server`
  module-level docstring.

### Fixed

- **Test helper handles all three FastMCP response shapes.**
  `tests/test_mcp_protocol.py::_payload` previously assumed
  `structuredContent={"result": ...}` for every tool. With v0.3.3's
  TypedDict returns, the structured content for `log_change` and
  `blame` is the dict itself with no `result` wrap. Helper now
  detects all three shapes (list-wrapped, dict-direct, content-only)
  and unwraps correctly.

---

## [0.3.2] — 2026-04-25

An observability-polish release. No new feature surface — the focus is
making existing functionality discoverable and debuggable, plus locking
in WAL/`busy_timeout` assumptions across SQLite versions in CI.
**Drop-in upgrade for anyone on 0.3.1.**

### Added

- **`selvedge doctor` command.** Walks the ambient state agents typically
  run into and reports each row PASS / WARN / FAIL / INFO:
    * which DB path is being resolved (and which precedence step matched —
      `SELVEDGE_DB`, walkup, or global fallback)
    * whether `.selvedge/` exists where you think it does
    * whether the schema is at the latest migration version
    * whether the post-commit hook is installed
    * whether the post-commit hook has been failing silently
    * last `tool_calls` entry timestamp (proxy for "is the agent wired up?")
    * whether `SELVEDGE_LOG_LEVEL` is set to a recognized value
  Exits 1 if any FAIL row is present so doctor can be wired into CI.
  Supports `--json` for machine-readable output.
- **Post-commit hook failure surfacing.** The previous hook silently died
  when `selvedge` wasn't on the shell PATH that git launched (a common
  symptom under macOS GUI git clients with stripped PATHs). The new hook
  appends a single line to `.selvedge/hook.log` on failure, and both
  `selvedge status` and `selvedge doctor` surface the most recent failure.
  Old hooks keep working — re-running `selvedge install-hook` is enough
  to upgrade.
- **`selvedge stats` upgrades:**
    * **Per-agent breakdown.** Catches the case where one agent (e.g.
      claude-code) is well-instrumented but another (e.g. cursor) is
      only querying history and never logging changes. Each agent shows
      total calls, log_change calls, and coverage ratio.
    * **Missing-reasoning count.** Counts events whose stored reasoning
      fails the quality validator (empty, too short, or generic
      placeholder). A non-zero count means an agent saw a warning at
      log time and shipped the event anyway.
- **`agent` column on `tool_calls` (migration v2).** The MCP server now
  passes the calling agent's name through to the telemetry table, so
  the per-agent stats break down correctly. v0.3.1 databases are
  migrated automatically; fresh DBs get the column from the create
  schema and the migration is recorded via the bootstrap path.
- **Public `selvedge.config.resolve_db_path()`.** Returns both the
  resolved path AND the precedence step that produced it (`env`,
  `walkup`, or `global`). Used by doctor; available for any tool that
  needs to know not just *which* DB is in effect but *why*.
- **Pinned-SQLite CI matrix.** A new `sqlite-matrix` job builds SQLite
  3.37.2, 3.42.0, and 3.45.3 from source and runs the suite against
  each via `LD_PRELOAD`. The implicit Python-bundled-SQLite matrix is
  also expanded with Python 3.13, and each row prints the active
  SQLite version so the matrix is visible in CI logs.

### Internal

- New tests: `test_doctor.py` (20), expanded `test_cli.py`,
  `test_storage.py`, and `test_migrations.py` for the v2 migration and
  the per-agent / missing-reasoning paths. Total suite is now 282 tests.
- `selvedge.cli.last_hook_failure()` and `selvedge.cli.hook_log_path()`
  expose the hook log to both status and doctor without duplication.
- `selvedge.migrations.latest_version()` so doctor can compare a DB's
  applied set against "what should be there" without knowing the
  migration list itself.

---

## [0.3.1] — 2026-04-23

A hardening release. No new feature surface — concurrency, observability,
schema-versioning, and developer-quality changes that take the codebase
from "works on my machine" to "safe to run in a long-lived agent pool."

### Added

- **Connection-with-retry on every storage write.** SQLite `database is locked`
  errors that escape the C-level `busy_timeout` (5s) now retry with exponential
  backoff (5 attempts, capped at 1s sleeps) before raising. Combined with WAL
  mode, this makes Selvedge safe under concurrent writers — `tests/test_concurrency.py`
  spawns 8 threads writing 25 events each and asserts all 200 land.
- **`PRAGMA busy_timeout = 5000` set on every connection** so SQLite's own
  retry handler covers the common contention case before Python ever sees it.
- **`schema_migrations` table.** Replaces the previous swallow-OperationalError
  ALTER pattern with an explicit, versioned migration runner. Every migration
  is recorded with version, name, and applied-at timestamp; partial failures
  roll back the DDL atomically. Pre-versioning databases (v0.2.1+ with
  `changeset_id` already present) are bootstrapped without re-running DDL
  that would error.
- **Structured logging (`selvedge.logging_config`).** All library modules now
  log under the `selvedge.*` namespace. Entry points (`selvedge` CLI,
  `selvedge-server` MCP) call `configure_logging()` once at startup. Set
  `SELVEDGE_LOG_LEVEL=DEBUG|INFO|WARNING|ERROR` to control verbosity.
- **Public API exports in `selvedge/__init__.py`.** Library users can now
  `from selvedge import SelvedgeStorage, ChangeEvent, parse_time_string`
  instead of reaching into internal modules. The frozen surface is locked
  in by `tests/test_public_api.py`.
- **Shared `selvedge.validation`.** The reasoning-quality validator moved
  out of `server.py` so the CLI's `selvedge log` command emits the same
  warnings as agent-driven `log_change` calls.
- **MCP protocol smoke tests (`tests/test_mcp_protocol.py`).** Boot the
  real `selvedge-server` subprocess and round-trip every tool over the
  actual JSON-RPC stdio transport. Catches contract drift the in-process
  tool tests miss.
- **CI gates: `ruff`, `mypy`, coverage ≥85%.** Added a separate `lint` job
  and `pytest-cov` to the test job. Current coverage is 92%.
- **`SelvedgeStorage._session()` context manager.** Yields a connection,
  commits on success, rolls back on error, ALWAYS closes — fixes a
  long-standing connection leak where `with self._connect()` managed the
  transaction but never closed the underlying socket.

### Fixed

- **Reasoning-quality regex bug.** Patterns like `^fixed?$` were intended
  to match both "fix" and "fixed" but actually matched "fixe"/"fixed" —
  the `?` only made the trailing `d` optional. Rewritten as `^fix(?:ed)?$`
  (and the same for `add`, `remove`, `update`, `change`, `see (...)`).
  Previously-uncaught placeholder reasonings now produce warnings.
- **Connection lifecycle.** Storage methods previously used
  `with self._connect() as conn:` which calls `Connection.__exit__()` for
  commit/rollback but never closes the connection — Python's GC eventually
  reclaimed it. All read/write methods now use `_session()` which closes
  explicitly. Affects long-running agent sessions where leaked connections
  could accumulate.

### Changed

- **`record_tool_call()` exception handling.** Still swallows so telemetry
  failures never crash the parent tool, but now routes through
  `logger.exception("…")` so the failure is visible at `SELVEDGE_LOG_LEVEL=DEBUG`.

### Internal

- New modules: `selvedge.migrations`, `selvedge.logging_config`,
  `selvedge.validation`. Imports are flat (no circular deps).
- New tests: `test_concurrency.py` (9), `test_migrations.py` (8),
  `test_logging_config.py` (11), `test_validation.py` (32),
  `test_public_api.py` (7), `test_mcp_protocol.py` (8). Total suite is
  now 244 tests.
- `pyproject.toml` configuration for ruff, mypy, and coverage.

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
