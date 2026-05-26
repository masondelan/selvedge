# Changelog

All notable changes to Selvedge are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Selvedge uses [semantic versioning](https://semver.org/).

---

## [Unreleased]

### Changed

- **Server icon redesigned.** The "stitched timeline" mark from
  v0.3.3 is replaced with a minimalist square mark — a navy 'S'
  beside a red selvedge edge stitch on cream. Lives at
  `docs/icon.png` (canonical tracked location), referenced by
  `manifest.json`, the selvedge.sh favicon / apple-touch-icon, and
  Smithery thumbnail. Verified to hold up from 16×16 favicon scale
  through Smithery thumbnail render sizes.
- **`manifest.json` icon path repointed from `assets/icon.png` to
  `docs/icon.png`.** `assets/` is now gitignored as internal-only,
  so a fresh clone no longer has `assets/icon.png` to bundle. The
  byte-identical-but-tracked copy at `docs/icon.png` becomes the
  single source — bundle, site, and social previews now build from
  the same file.
- **`twitter:card` upgraded from `summary` to `summary_large_image`**
  on `docs/index.html`, `docs/comparison.html`, and `docs/faq.html`.
  The new wide og-image banner would be center-cropped to a small
  square by the summary card; the large-image card renders the full
  banner.

### Added

- **`docs/og-image.png` — wide wordmark banner for social previews.**
  2064×512 'selvedge' wordmark with the red selvedge edge stitch.
  Referenced from `og:image` and `twitter:image` meta tags on the
  three main site pages. Favicon and apple-touch-icon continue to
  use the square `docs/icon.png` mark — wide banner for share
  previews, tight square mark for tab and home-screen icons.

### Internal

- **Bulk `launch/` and `assets/` rules in `.gitignore`** (PR #8).
  Replaces six individual `launch/` sub-path entries with a single
  bulk rule (matches the `.mcpbignore` convention so MCPB builds
  are unaffected). Adds `assets/` and three new strategy-doc paths
  (`docs/strategy-2026-q3.md`, `docs/top-priority-actions.md`,
  `docs/seo-gsc-primer.md`) alongside the existing internal-docs
  block. No public-facing content change; existing local copies
  remain on disk.

---

## [0.3.6] — 2026-05-24

Two themes in one release — a one-time exception to the single-theme
cadence so the retention-basics work can ship without a renumbering
pass on the phase plan. v0.3.4 made install one command; v0.3.5 made
recovery possible; v0.3.6 closes the version-drift gap **and** bounds
the noise table so old `tool_calls` telemetry doesn't accumulate
forever. **Drop-in upgrade for anyone on 0.3.5.**

### Added — stay-current

- **Background PyPI version check in the CLI.** On every `selvedge`
  invocation, a daemon thread fetches the latest published version
  from PyPI's JSON endpoint and caches the result at
  `~/.selvedge/update_check.json` (user-global, not per-project — a
  user with ten Selvedge projects doesn't pay for ten checks). If a
  newer release exists, a one-line notice is printed to stderr on
  process exit:

  ```
  selvedge: v0.3.7 available (you're on 0.3.6) — https://selvedge.sh/upgrade
  ```

  The notice prints *after* the command's output (via `atexit`) so it
  never interleaves with `selvedge log`, `selvedge watch`, or `--json`
  pipelines. Cache TTL is 24h — matches what `gh` and `npm` use; `pip`
  uses 7 days but that's longer than most users want.

- **Generous suppression.** The check is disabled when any of
  `SELVEDGE_NO_UPDATE_CHECK=1`, `SELVEDGE_QUIET=1`, or `CI` is set in
  the environment; when stderr isn't a TTY (piping, agent stdio,
  redirected output); and on dev / editable installs
  (`pip install -e .` where the version contains `.dev` / `+` / `rc`).
  The TTY gate is also re-checked at print time, so even a cached
  notice can't pollute redirected output.

- **Soft-fail everywhere.** The fetch has a 1.5-second timeout and
  every code path in `selvedge/update_check.py` swallows exceptions —
  a network blip, an unwritable `$HOME`, or a malformed PyPI response
  can never affect the command the user invoked. The upgrade-URL is
  the only hard-coded copy; pointing at `selvedge.sh/upgrade` rather
  than inlining `pip install -U selvedge` keeps the notice correct
  across PyPI / Smithery / Glama users.

### Added — retention basics

- **`selvedge prune` — trim old `tool_calls` rows.** Hardcoded default
  of 90 days; `--days N` overrides. The 90-day default is long enough
  that the previous month's agents are still in the data, and is
  surfaced explicitly in `selvedge prune --help`. **Only `tool_calls`
  is pruned in this release** — the events-table prune path waits for
  `.selvedge/config.toml` in v0.3.10 and will require both
  `SELVEDGE_DESTRUCTIVE=1` and an interactive confirmation. `--json`
  for machine output.

- **`.selvedge/prune.log` audit trail.** Every prune appends a
  tab-separated one-liner — `<utc-iso>\t<count_pruned>\t<days_threshold>` —
  mirroring the format of the post-commit `.selvedge/hook.log`. The
  log is written even when 0 rows were deleted, so cadence is visible
  in the doctor row regardless of whether a given run had work to do.

- **Doctor — `Last prune` row.** Parses the tail of
  `.selvedge/prune.log` and surfaces the most recent timestamp,
  pruned-row count, and day threshold. INFO when present, INFO with
  a "run `selvedge prune`" nudge when the log doesn't exist yet.

- **Doctor — `tool_calls size` WARN at >100k rows.** A rough oversized-
  table signal that points users at `selvedge prune`. Threshold lives
  at `selvedge.prune.TOOL_CALLS_WARN_ROWS` and is revisitable once the
  v0.3.5/v0.3.6 telemetry has bedded in.

### Changed

- **`selvedge-server` stays silent.** The update check is deliberately
  wired into `selvedge/cli.py` only — not `server.py`. The MCP
  server's stdio is the JSON-RPC channel; a stray stderr write from a
  daemon thread would surface in the calling agent's logs as noise.

### Tests

- **`tests/test_update_check.py`** (24 tests) — covers env-var and TTY
  gating, dev-install detection, 24h TTL behavior, malformed-cache
  recovery, the network-error / timeout / unexpected-exception soft
  fails, the `packaging`-vs-fallback comparison paths, and the
  notice's once-per-process idempotency. **No test in the module hits
  the network** — `urlopen` is monkeypatched everywhere.
- **`tests/test_prune.py`** (10 tests) — covers old-row deletion,
  preserve-recent semantics, `--days` override, log-line shape,
  empty-table still-logs, append-on-subsequent-run, last-line parsing,
  missing-log handling, and the CLI `--json` shape.
- **`tests/test_doctor.py`** — +2 tests for the new `Last prune` row
  (parsed from the log) and the oversized-`tool_calls` WARN (stubbed
  via a monkeypatched count helper rather than inserting 100k rows).

### Note on cadence

This release combines two themes — stay-current (Phase 2.11 leftover,
deferred from v0.3.5) and retention basics (Phase 2.12). It is a
**one-time exception** to the single-theme-per-release discipline
locked in on 2026-05-10. Single-theme resumes at v0.3.7 (entity
foundation + `prior_attempts` wedge).

---

## [0.3.5] — 2026-05-11

The recovery-basics release. v0.3.1 made the runtime safe; v0.3.2 made
problems visible; v0.3.5 adds the *minimum viable* "what happens when
something has gone wrong" surface. Verify so you can detect corruption.
Backup so you have a known-good snapshot to fall back to. **Drop-in
upgrade for anyone on 0.3.4.**

### Added

- **`selvedge verify` — DB-correctness gate with two exit tiers.** Walks
  the store and reports each check as PASS / WARN / FAIL. Must-fail
  conditions (SQLite corruption from `PRAGMA integrity_check`, schema
  mismatch against the declared `MIGRATIONS` tuple, empty `entity_path`,
  unknown `change_type` in the store, unparseable timestamps, malformed
  `tool_calls` rows) exit non-zero. Should-warn conditions (singleton
  `changeset_id` groups, events past the 60-minute backfill window with
  no `git_commit`) print warnings but exit 0 by default. Pass `--strict`
  to escalate warnings to failures — `selvedge verify` is meant to drop
  into CI on day one without `|| true`. `--json` for machine output.
  Tier mapping is locked in by `selvedge.verify.CHECK_TIERS` and
  asserted by `tests/test_verify.py` — adding a check without a tier
  trips CI.
- **`selvedge backup` — online SQLite snapshot via VACUUM INTO.**
  Default destination
  `.selvedge/backups/selvedge-YYYYMMDD-HHMMSS.db`. Hardcoded
  `keep_last=7` for this release; the setting becomes
  `backup_keep_last` in `.selvedge/config.toml` when that file lands
  in v0.3.10. Two backups within the same second don't collide — the
  second one gets a `-1` suffix rather than clobbering the first.
  `--output <path>` overrides the default destination and is excluded
  from rotation. `--json` for scripting.
- **`.selvedge/backups/` added to the project `.gitignore`.**
  `selvedge init` writes it on fresh repos; the first `selvedge backup`
  run on an existing repo appends it the same way. Idempotent — safe
  to re-run.
- **Doctor — `Last backup` row.** INFO when the newest backup is ≤7
  days old, WARN when older, FAIL when no backups exist *and* the
  events table has ≥10,000 rows (the threshold where no-backups
  becomes a real data-loss exposure rather than a CI/scratch DB).
- **Doctor — `Schema version` now FAILs on downgrade.** When
  `schema_migrations` contains a version not declared in the current
  `MIGRATIONS` tuple, the row fails rather than silently appearing
  PASS — surfaces "this DB was last opened by a newer Selvedge" before
  any write attempts schema work it doesn't understand.

### Changed

- **`selvedge doctor` docstring** updated to describe the new rows and
  the downgrade-failure semantics.

### Tests

- **`tests/test_verify.py`** (13 tests) — covers tier locking, the
  happy path, every must-fail trigger, the warn-only paths, and the
  CLI surface including `--strict` escalation.
- **`tests/test_backup.py`** (7 tests) — covers snapshot validity,
  rotation, same-second collisions, missing-DB error path, and the
  `.gitignore` append idempotency.
- **`tests/test_doctor.py`** — 4 new tests for the `Last backup` row
  and the downgrade-detection branch.

---

## [0.3.4] — 2026-04-26

The first-run release. The install funnel was six manual steps with
three documentation lookups; v0.3.4 collapses it to one command and
makes the agent integration discoverable from inside the tool instead
of from the README. **Drop-in upgrade for anyone on 0.3.3.**

### Added

- **`selvedge setup` — interactive first-run wizard.** Detects the AI
  tooling already on your machine (Claude Code via
  `~/.claude/config.json`, Cursor via `~/.cursor/mcp.json` and
  `.cursorrules`, GitHub Copilot via `.github/copilot-instructions.md`)
  and walks through every install step in one pass: adds the Selvedge
  MCP entry to each tool's config, drops the canonical agent-instructions
  block into the project's prompt file (`CLAUDE.md` / `.cursorrules` /
  copilot-instructions.md), runs `selvedge init` if `.selvedge/`
  doesn't exist, and installs the post-commit hook. Every modified
  file gets a `.bak` written next to it before any change reaches
  disk. Re-running on an already-set-up project is a no-op
  (idempotent). Existing-but-different MCP entries trigger a conflict
  warning rather than silent overwrite — pass `--force` to overwrite,
  or update by hand. For CI / devcontainer `postCreateCommand`:
  `selvedge setup --non-interactive --yes`.
- **`selvedge prompt` — canonical agent instructions on tap.** Prints
  the recommended system-prompt block to stdout
  (pipe-friendly: `selvedge prompt | tee -a CLAUDE.md`) or installs it
  idempotently into a target file with `--install <file>`. The block
  is wrapped in `<!-- selvedge:start -->` / `<!-- selvedge:end -->`
  sentinel markers, so re-running `--install` updates the bracketed
  region without disturbing anything else in the file. The block
  source lives at `selvedge.prompt.PROMPT_BLOCK` — single source of
  truth, so it stays in lockstep with the docs.
- **`selvedge watch` — live tail of newly-logged events.** Polls the
  SQLite store at a configurable `--interval` (default 1s) and prints
  each new event as it lands, Rich-formatted. Filters mirror
  `selvedge history` exactly: `--since`, `--entity`, `--project`,
  `--agent`. `--json` emits one compact JSON object per line for
  piping into other tools. WAL mode means the polling SELECT never
  blocks the writer; the runtime cost is one indexed query per second
  while the command is running. Ctrl-C exits cleanly.
- **`selvedge.prompt.PROMPT_BLOCK` is now public.** Library users can
  import the canonical agent-instructions block as a constant for
  templating into their own onboarding flows.

### Changed

- **Better empty-state diagnosis in `selvedge status`.** Replaces the
  generic "No changes logged yet" with a decision-tree-driven hint:
    * MCP entry installed in some agent's config but no tool_calls
      received → "MCP entry installed but no tool_calls received yet…
      try restarting your agent" (with the config path printed)
    * MCP entry not detected anywhere → "Run `selvedge setup` to wire
      Selvedge into your AI tools"
    * Detection error → falls back to the setup nudge gracefully
  Detects "MCP entry installed but agent never reloaded" by reading
  the agent config files, comparing modification times against the
  current time. Five-minute grace window before the hint shifts from
  "restart your agent" to "run `selvedge doctor` for a full health
  check."
- **`selvedge doctor`'s "MCP wiring" check now points at `selvedge
  setup`.** Same diagnostic improvement, surfaced through the doctor
  table for users who hit that command first.
- **`server.json` (Glama / catalog descriptor) regenerated from live
  server.py.** Was still showing v0.3.2 tool descriptions through the
  v0.3.3 release; now in lockstep with `manifest.json`. Folded into
  the version-bump checklist so this can't drift again.

### Tests

- **`tests/test_setup.py`** — 18 tests covering detect-and-install for
  every agent type. Uses `tmp_path` + a fake home/project — never
  touches real `~/.claude/`, `~/.cursor/`, or `.git/`. Idempotent
  re-runs, malformed JSON handling, conflict detection, `--force`
  overwrites, errors don't abort later steps, hook step skipped when
  not in a git repo, prompt-block wired correctly.
- **`tests/test_prompt.py`** — 18 tests covering the prompt installer.
  Greenfield install, append, in-place update, idempotence, backup
  numbering (consecutive edits don't overwrite the first `.bak`),
  trailing-newline-convention preservation, sentinel-bracketed block
  detection survives whitespace around markers.
- **`tests/test_watch.py`** — 18 tests covering filter semantics
  (entity prefix-aware, project/agent exact-match), cursor advancement
  (no re-emission across polls), interval clamping, catch-up window
  emits chronologically before the loop starts, `--json` mode emits
  one compact line per event. Uses a `max_iterations` test seam so
  the loop exits deterministically without needing SIGINT delivery.

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
