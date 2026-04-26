# Selvedge

[![Tests](https://github.com/masondelan/selvedge/actions/workflows/test.yml/badge.svg)](https://github.com/masondelan/selvedge/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/selvedge?cacheSeconds=3600)](https://pypi.org/project/selvedge/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Long-term memory for AI-coded codebases.**
A `git blame` for AI agents — but for the *why*, not just which line which
model touched. Captured live, by the agent, as the change happens.

---

Six months ago, your AI agent added a column called `user_tier_v2`. You don't
know why. `git blame` points to a commit from `claude-code` with a generated
message that says "Update schema." The session that made the change is long
gone — and so is the prompt that produced it.

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

That reasoning was **captured by the agent in the moment** — written into
Selvedge from the same context that produced the change. Not inferred from
the diff afterward by a second LLM. Not a hand-typed commit message.

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

## Who Selvedge is for

Selvedge has two audiences. Same tool, same `pip install`, same SQLite
file under `.selvedge/`. Different scale of pain.

**Teams running long-term, AI-coded codebases.**
When the project is big enough that you (or someone else) will touch it
again in six months, twelve months, three years — but most of it was written
by an agent whose context evaporated the day each PR shipped. `git blame`
tells you what changed. Selvedge tells you *why* — even after the agent
session, the prompt template, the developer who asked for it, and the model
version are all long gone. This is the original use case: production
codebases, schema decisions, migrations, dependency changes that need an
audit trail that survives turnover.

**Solo developers using Claude Code on everyday projects.**
Side projects, weekend builds, the small internal tool you keep poking at.
You don't need enterprise governance — you just need to remember why you (or
your agent) did the thing you did yesterday, last week, last sprint. Run
`selvedge init` once. Add four lines to your `CLAUDE.md`. From then on,
`selvedge blame` is muscle memory — a way to talk to your past self when
your past self was an LLM.

If you've ever come back to your own AI-built project and thought "what was
this *for* again?", Selvedge is the missing piece.

---

## The problem

Human-written code leaks intent everywhere — commit messages, PR descriptions,
inline comments, the Slack thread that preceded it. AI-written code doesn't.
The agent has perfect clarity about why it made each decision, but that
context lives in the prompt and evaporates when the conversation ends.

Six months later, your team is debugging a schema decision with no trail.
`git blame` tells you *what* changed and *when*. It can't tell you *why*.

**Selvedge captures the why — live, by the agent itself, as the change is
made.** The diff is git's job. The why is Selvedge's.

---

## What's new in v0.3.3

A discoverability + ergonomics release. No new MCP tools, no behavior
changes that affect stored data — but the live tool schema is now
substantially richer for the agents that call it and the directories
that score it. **Drop-in upgrade for anyone on 0.3.2.**

**Per-parameter descriptions on every tool.** All 6 MCP tools now
declare each parameter via `Annotated[T, Field(description=...)]`,
populating the per-parameter `description` field in `tools/list`.
Previously each parameter shipped only `type` and `title`; the rich
docstrings sat in the function body where agents couldn't see them at
tool-call time. Coverage went 0/21 → 21/21. Agents picking which tool
to call read these directly, so it's a DX win for Claude Code / Cursor
use — not just a directory-score win.

**MCP tool annotations.** Each tool now declares `readOnlyHint`,
`destructiveHint`, `idempotentHint`, `openWorldHint`, and a
human-friendly `title`. `log_change` is the only writer (append-only,
not idempotent — each call mints a new event). The five readers
(`diff`, `blame`, `history`, `changeset`, `search`) are read-only and
idempotent. None are open-world. Lets MCP clients gate or surface the
tools appropriately.

**Output schemas on every tool.** New `LogChangeResult` and
`BlameResult` TypedDicts give the JSON-RPC layer concrete output
schemas to advertise — was missing on the two `dict`-returning tools.
The list-returning ones already had auto-generated schemas, so all 6
are now consistent.

**Custom server icon.** A "stitched timeline" mark — a horizontal
running stitch where each visible stitch is a captured change event.
Lives at `assets/icon.svg` and a 512×512 `assets/icon.png`, shipped
in the Smithery bundle.

**`log_change` and `blame` return stable shapes.** `log_change` now
always returns `id`, `timestamp`, `status`, `error`, and `warnings`
keys (with empty values when not applicable) — easier to type-check
without branching. `blame` does the same on miss: every event field is
empty, `error` carries the "no history found" message. Same conventions
as before, just consistent payloads.

**`CLAUDE.md` ↔ `docs/architecture.md` split.** `CLAUDE.md` is now a
slim agent-instructions file (sources of truth, code conventions,
version bump checklist). Architecture, data model, MCP tool reference,
full CLI reference, and the phase plan all moved to
`docs/architecture.md`.

See [`CHANGELOG.md`](CHANGELOG.md) for the full list and reasoning.

---

## What's new in v0.3.2

An observability-polish release. No new feature surface — the focus is
making existing functionality discoverable and debuggable, plus locking
in WAL/`busy_timeout` assumptions across SQLite versions.
**Drop-in upgrade for anyone on 0.3.1.**

**`selvedge doctor`.** A single health-check that walks the ambient state
agents typically run into and reports each row PASS / WARN / FAIL / INFO:
which DB path is being resolved (and which precedence step matched —
`SELVEDGE_DB`, walkup, or global fallback), whether `.selvedge/` exists,
whether the schema is at the latest migration version, whether the
post-commit hook is installed and not silently failing, last `tool_calls`
timestamp (proxy for "is the agent wired up?"), and whether
`SELVEDGE_LOG_LEVEL` is set to a recognized value. Exits 1 on any FAIL,
so `doctor` can be wired into CI. `--json` for machine-readable output.

```bash
$ selvedge doctor
Selvedge doctor

  Check                  Status      Detail
 ───────────────────────────────────────────────────────────────────
  Database path          i INFO      ~/.selvedge/selvedge.db  [via SELVEDGE_DB env var]
  .selvedge/ directory   ✓ PASS      ~/.selvedge
  Schema version         ✓ PASS      at v2 (latest)
  Post-commit hook       ✓ PASS      ~/code/myapp/.git/hooks/post-commit
  Last hook failure      ✓ PASS      no failures recorded
  MCP wiring             ✓ PASS      last tool_call at 2026-04-25T14:21:08Z
  SELVEDGE_LOG_LEVEL     i INFO      unset (defaults to WARNING)

All checks passed.
```

**Post-commit hook no longer fails silently.** The previous hook silently
died when `selvedge` wasn't on the PATH that git launched (a common
symptom under macOS GUI git clients with stripped PATHs). The new hook
appends a single line to `.selvedge/hook.log` on failure, and both
`selvedge status` and `selvedge doctor` surface the most recent failure.
Old hooks keep working — re-running `selvedge install-hook` upgrades to
the new wrapper.

**`selvedge stats` upgrades.** The coverage report now breaks calls
down per-agent so you can catch the case where claude-code is logging
changes but cursor is only querying history. A separate
`missing_reasoning` count surfaces stored events whose reasoning fails
the quality validator — empty, too short, or a generic placeholder the
agent shipped despite the warning.

**Pinned-SQLite CI matrix.** A new CI job builds SQLite 3.37.2, 3.42.0,
and 3.45.3 from source and runs the suite against each via `LD_PRELOAD`,
locking in WAL + `busy_timeout` behavior across the SQLite versions
selvedge users are most likely to be on. The Python matrix also gains
3.13.

---

## How Selvedge compares

There's a fast-growing "git blame for AI agents" category. Here's where
Selvedge fits — and where it deliberately doesn't.

|  | Reasoning source | Granularity | Mechanism | Grouping | Storage |
|---|---|---|---|---|---|
| **Selvedge** | **Captured live**, by the agent in the same context that produced the change | **Entity** — DB column, table, env var, dep, API route, function | **MCP server** — agent calls it as work happens | **Changesets** — named feature/task slugs across many entities | SQLite, zero deps |
| AgentDiff | **Inferred post-hoc** by Claude Haiku from the diff at session end | Line | Git pre/post-commit hook | None | JSONL on disk |
| Origin | Captured at commit time | Line | Git hook | None | Local |
| Git AI | Attribution metadata | Line | Git hook + Agent Trace alliance | None | Git notes |
| BlamePrompt | Prompt-only | Line | Git hook | None | Local |

**Why "captured live" matters.** AgentDiff and Origin generate reasoning
*after* the change is made, by feeding the diff back to a second LLM call.
Selvedge's reasoning is the agent's own intent, written from the same
context window that produced the change — no inference, no hallucinated
explanations, and an empty `reasoning` field is itself a useful signal
(the agent didn't have one).

**Why "entity-level" matters.** Most tools attribute *lines*. Selvedge
attributes *things you actually search for*: `users.email`,
`env/STRIPE_SECRET_KEY`, `api/v1/checkout`, `deps/stripe`. The first
question after `git blame` is usually *"what's the history of this column"*,
not *"what's the history of lines 40–48 of users.py"*.

**Why "changesets" matter.** A Stripe billing rollout touches the `users`
table, two new env vars, three new API routes, one dependency, and four
functions across the codebase. Tag every event with `changeset:add-stripe-billing`
and you can pull the entire scope back later — even if the original PR was
broken into eight smaller ones over a month.

**Selvedge ↔ Agent Trace.** [Agent Trace](https://github.com/cursor/agent-trace)
(Cursor + Cognition AI, RFC Jan 2026, backed by Cloudflare, Vercel, Google
Jules, Amp, OpenCode, and git-ai) is an emerging *open standard* for AI
code attribution traces. Selvedge isn't a competitor to it — it's a
compatible producer. The design for `selvedge export --format agent-trace`
is at [`docs/agent-trace-interop.md`](docs/agent-trace-interop.md). Agent
Trace is the wire format. Selvedge is the live capture + query layer that
emits it.

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
selvedge stats [--since SINCE]            Tool call coverage report (per-tool, per-agent)
selvedge doctor [--json]                  Health check: DB path, schema, hook, MCP wiring
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
