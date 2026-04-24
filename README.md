# Selvedge

[![Tests](https://github.com/masondelan/selvedge/actions/workflows/test.yml/badge.svg)](https://github.com/masondelan/selvedge/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/selvedge)](https://pypi.org/project/selvedge/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Change tracking for AI-era codebases.**

---

Six months ago, your AI agent added a column called `user_tier_v2`. You don't
know why. Git blame points to a commit message that says "Update schema." The
agent session that made the change is long gone.

With Selvedge, you run this instead:

```bash
$ selvedge blame user_tier_v2

  user_tier_v2
  Changed     2025-10-14 09:31:02
  Agent       claude-code
  Commit      3e7a991
  Reasoning   User asked to add a grandfathering flag for legacy free-tier
              users during the pricing migration. Stores the original tier
              so we can backfill discounts without touching billing history.
```

That reasoning came from the original request — captured by the agent in the
moment, before the session ended.

---

<!-- DEMO GIF
     Record a 30–45 second terminal session showing:
     1. `selvedge status`  →  shows N total events
     2. `selvedge blame payments.amount`  →  full output with reasoning
     3. `selvedge diff users --since 30d`  →  table of recent changes
     4. `selvedge search "stripe"`  →  filtered results
     Use `vhs` (https://github.com/charmbracelet/vhs) or Asciinema.
     Replace this comment block with: ![Selvedge demo](docs/demo.gif)
-->

---

## The problem

Human-written code leaks intent everywhere — commit messages, PR descriptions,
inline comments. AI-written code doesn't. The agent knows exactly why it made
each decision, but that context lives in the prompt and evaporates when the
conversation ends.

Six months later, your team is debugging a schema decision with no trail.
`git blame` tells you *what* changed and *when*. It can't tell you *why*.

**Selvedge captures the why.**

---

## What's new in v0.3.0

A correctness and data-quality release — no new feature surface, but several
silent-wrong-answer bugs are now fixed. **Recommended upgrade for everyone
on 0.2.x.**

**Time parsing now follows every other CLI's convention.** `5m` means
5 *minutes*, not 5 months. Use `5mo` (or `5mon`) for months. Unparseable
inputs like `--since yesterday` now exit with a clear error instead of
silently returning empty results from a string-vs-string compare.

```bash
selvedge history --since 15m    # last 15 minutes (was: last ~15 months)
selvedge history --since 5mo    # last 5 months
selvedge history --since 1y     # last year
```

**`search()` no longer treats `_` as a wildcard.** Searching for
`stripe_customer_id` used to match `stripeXcustomerXid`, `stripeYcustomerYid`,
and so on, because SQL `LIKE` treats underscore as "any single character."
All `LIKE` queries now `ESCAPE '\'` and escape the input — so the literal
underscore in your column name does what you expect.

**`selvedge import` finally works for columns defined in `CREATE TABLE`.**
Previously, importing `CREATE TABLE users (id INT, email TEXT)` produced
exactly one event — for the table — and zero for its columns. Six months
later, `selvedge blame users.email` returned "no history found." Now every
column in a `CREATE TABLE` (and every `sa.Column(...)` in `op.create_table`)
gets its own `column.add` event.

**Timestamps normalized to UTC on write.** Mixed-timezone events
(`2025-01-01T09:00:00-08:00` vs `2025-01-01T10:00:00+00:00`) now sort
correctly by real time. Previously they sorted by ASCII order of the
timezone suffix.

**Two events on rename, not one.** Both SQL `ALTER TABLE old RENAME TO new`
and Alembic `op.rename_table('old', 'new')` now emit a `rename` event for
the old name *and* a `create` event for the new name — so `selvedge blame
new_name` returns the rename context instead of "no history found."

**Schema migration on rename for `git_commit` coverage.** The post-commit
hook's default lookback widened from 10 to 60 minutes — long agent
sessions no longer lose their git stamp. `selvedge status` now surfaces
the count of events still missing a `git_commit` so unstamped events
are visible at a glance.

**Validation in `ChangeEvent`.** Empty `entity_path`, hallucinated
`change_type` values like `"banana"`, and typos like `"modifyed"` are now
rejected at write time instead of silently inserting orphan rows.

**Faster bulk imports.** `selvedge import` wraps inserts in a single
transaction (`storage.log_event_batch`) — orders of magnitude faster on
large Alembic histories, and atomic.

**Better project DB resolution.** `get_db_path` now walks up looking for
the actual `selvedge.db` file rather than just the `.selvedge/` directory.
A stray empty `.selvedge/` upstream no longer hijacks resolution. Falling
back to the global `~/.selvedge/selvedge.db` prints a one-time stderr
warning so unintentional global use is visible. (Set `SELVEDGE_QUIET=1`
to suppress.)

**Adversarial test suite.** 25 new tests covering the bug classes above,
so these regressions stay fixed.

See [`CHANGELOG.md`](CHANGELOG.md) for the full list and reasoning.

---

## Install

```bash
pip install selvedge
```

## Quickstart

**1. Initialize in your project**

```bash
cd your-project
selvedge init
```

**2. Add to your Claude Code config**

`~/.claude/config.json`:
```json
{
  "mcpServers": {
    "selvedge": {
      "command": "selvedge-server"
    }
  }
}
```

**3. Tell your agent to use it**

Add to your project's `CLAUDE.md`:
```
You have access to Selvedge for change tracking.
Call selvedge.log_change immediately after adding, modifying, or removing
any DB column, table, function, API endpoint, dependency, or env variable.
Set `reasoning` to the user's original request or the problem being solved.
Set `agent` to "claude-code".
Before modifying an entity, call selvedge.blame to understand its history.
```

**4. Query your history**

```bash
selvedge status                        # recent activity + missing-commit count
selvedge diff users                    # all changes to the users table
selvedge diff users.email              # changes to a specific column
selvedge blame payments.amount         # what changed last and why
selvedge history --since 30d           # last 30 days of changes
selvedge history --since 15m           # last 15 minutes ('m' = minutes)
selvedge changeset add-stripe-billing  # all events for a feature/task
selvedge search "stripe"               # full-text search
selvedge stats                         # log_change coverage report
selvedge install-hook                  # auto-link commits to events
selvedge import migrations/            # backfill from migration files
selvedge export --format csv           # dump history to CSV
```

---

## How it works

Selvedge runs as an MCP server. AI agents in tools like Claude Code call
Selvedge's tools as they work — logging structured change events to a local
SQLite database.

Each event records:
- **What** changed (entity path, change type, diff)
- **When** (timestamp)
- **Who** (agent, session ID)
- **Why** (reasoning — captured from the agent's context in the moment)
- **Where** (git commit, project)

The diff is git's job. The *why* is Selvedge's.

---

## Entity path conventions

```
users.email           DB column (table.column)
users                 DB table
src/auth.py::login    Function in a file (path::symbol)
src/auth.py           File
api/v1/users          API route
deps/stripe           Dependency
env/STRIPE_SECRET_KEY Environment variable
```

Prefix queries work everywhere: `users` returns `users`, `users.email`,
`users.created_at`, and any other entity under the `users.` namespace.

---

## MCP tools

When connected as an MCP server, Selvedge exposes:

| Tool | Description |
|------|-------------|
| `log_change` | Record a change event with entity, diff, and reasoning |
| `diff` | History for an entity or entity prefix |
| `blame` | Most recent change + context for an exact entity |
| `history` | Filtered history across all entities |
| `changeset` | All events grouped under a named feature/task slug |
| `search` | Full-text search across all events |

---

## CLI reference

```
selvedge init [--path PATH]               Initialize in project
selvedge status                           Recent activity summary
selvedge diff ENTITY [--limit N]          Change history for entity
selvedge blame ENTITY                     Most recent change + context
selvedge history [--since SINCE]          Browse all history
              [--entity ENTITY]
              [--project PROJECT]
              [--changeset CS]
              [--summarize]
              [--limit N]
selvedge changeset [CHANGESET_ID]         Show events in a changeset
                  [--list]                or list all changesets
                  [--project NAME]
                  [--since SINCE]
selvedge search QUERY [--limit N]         Full-text search
selvedge stats [--since SINCE]            Tool call coverage report
selvedge install-hook [--path PATH]       Install git post-commit hook
                     [--window MIN]       (default 60 minutes)
selvedge backfill-commit --hash HASH      Backfill git_commit on recent events
                        [--window MIN]    (default 60 minutes)
selvedge import PATH                      Import migration files (SQL / Alembic)
              [--format auto|sql|alembic]
              [--project NAME]
              [--dry-run]
selvedge export [--format json|csv]       Export history to JSON or CSV
              [--since SINCE]
              [--entity ENTITY]
              [--output FILE]
selvedge log ENTITY CHANGE_TYPE           Manually log a change
             [--diff TEXT]                CHANGE_TYPE: add, remove, modify,
             [--reasoning TEXT]           rename, retype, create, delete,
             [--agent NAME]               index_add, index_remove, migrate
             [--commit HASH]
             [--project NAME]
             [--changeset CS]
```

All read commands support `--json` for machine-readable output.

**Relative time in `--since`:**
- `15m` → last 15 minutes (`m` = minutes)
- `24h` → last 24 hours
- `7d` → last 7 days
- `5mo` → last 5 months (`mo` or `mon` = months)
- `1y` → last year

Unparseable inputs (e.g. `--since yesterday`) exit with a clear error
rather than silently returning empty results. ISO 8601 timestamps
are also accepted and normalized to UTC.

---

## Configuration

| Method | Format | Example |
|--------|--------|---------|
| Env var | `SELVEDGE_DB=/path/to/db` | Per-session override |
| Project init | `selvedge init` | Creates `.selvedge/selvedge.db` in CWD |
| Global fallback | `~/.selvedge/selvedge.db` | Used if no project DB found |

---

## Coverage checking

Wondering how often your agent actually calls `log_change`? Two ways to check:

```bash
# Quick summary in the terminal
selvedge stats

# Cross-reference against git commits
python scripts/coverage_check.py --since 30d
```

The coverage script compares your git log against Selvedge events and shows
which commits have associated change events. Low coverage usually means the
system prompt needs strengthening — see `docs/fallbacks.md` for guidance.

---

## Contributing

```bash
git clone https://github.com/masondelan/selvedge
cd selvedge
pip install -e ".[dev]"
pytest
```

See `CLAUDE.md` for architecture details and the phase roadmap.

---

## License

MIT — see [LICENSE](LICENSE).
