<p align="center">
  <img src="docs/wordmark.svg" alt="selvedge" width="480">
</p>

<p align="center">
  <a href="https://selvedge.sh"><strong>selvedge.sh</strong></a>
  &nbsp;·&nbsp;
  <a href="https://pypi.org/project/selvedge/"><strong>PyPI</strong></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/masondelan/selvedge"><strong>GitHub</strong></a>
</p>

<p align="center">
  <a href="https://github.com/masondelan/selvedge/actions/workflows/test.yml"><img src="https://github.com/masondelan/selvedge/actions/workflows/test.yml/badge.svg" alt="Tests"></a>
  <a href="https://pypi.org/project/selvedge/"><img src="https://img.shields.io/pypi/v/selvedge?cacheSeconds=3600" alt="PyPI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
</p>

**Long-term memory for AI-coded codebases.**
A `git blame` for AI agents — but for the *why*, not just which line which
model touched. Captured live, by the agent, as the change happens.

Selvedge is a local MCP server. AI coding agents (Claude Code, Cursor,
Copilot) call it as they work to log structured change events with
reasoning. Your data stays in a SQLite file under `.selvedge/` next to
your code.

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

## What's new in v0.3.6

Two themes in one release as a one-time exception to the single-theme
cadence: **stay-current** (background PyPI version check) and
**retention basics** (`selvedge prune` for the `tool_calls` table).
**Drop-in upgrade for anyone on 0.3.5.**

### Stay-current

**Background version check in `selvedge` (the CLI only — never the MCP
server).** A daemon thread fetches the latest published version from
PyPI's JSON endpoint, caches the result at
`~/.selvedge/update_check.json` (user-global so you don't re-check per
project), and on process exit prints to stderr:

```
selvedge: v0.3.7 available (you're on 0.3.6) — https://selvedge.sh/upgrade
```

The notice is printed via `atexit` so it appears *after* the command's
output, never interleaved. Cache TTL is 24h, matching `gh` and `npm`.

**Generous suppression.** The check is disabled when any of
`SELVEDGE_NO_UPDATE_CHECK=1`, `SELVEDGE_QUIET=1`, or `CI` is set in
the environment, when stderr isn't a TTY (piping, agent stdio,
`--json` to a file), and on dev / editable installs. The TTY gate is
re-checked at print time too — even a cached notice can't pollute
redirected output. The 1.5s fetch timeout and the no-raise posture of
every code path mean a network blip can't slow or break the CLI.

**`selvedge-server` stays silent.** The check is wired into the CLI
group callback only — the MCP server's stdio is the JSON-RPC channel
and an inadvertent stderr write would surface in the calling agent's
logs as noise.

### Retention basics

**`selvedge prune` — trim old `tool_calls` rows.** Default retention
is 90 days; `--days N` overrides. The default is long enough that the
previous month's agents are still in the data. Every run appends a
tab-separated audit line to `.selvedge/prune.log` so the cadence is
visible later — even empty prunes log, so you can tell the difference
between "no prunes yet" and "nothing to prune."

```
selvedge prune                # 90-day default
selvedge prune --days 30      # tighter window
selvedge prune --json         # for cron / scripting
```

Only `tool_calls` is pruned in this release. The destructive
events-table path waits for `.selvedge/config.toml` in v0.3.10 and
will require both `SELVEDGE_DESTRUCTIVE=1` *and* an interactive
confirmation — the cron / non-interactive `--yes` footgun is
defended against by design.

**Doctor — `Last prune` row + oversized-`tool_calls` WARN.** The
doctor table now surfaces the tail of `.selvedge/prune.log` (most
recent timestamp, rows pruned, day threshold) and WARNs when the
`tool_calls` table exceeds 100k rows so users get a nudge to run
`selvedge prune` before the noise table gets large.

### Note on cadence

This release combines two themes — a **one-time exception** to the
single-theme-per-release discipline locked in on 2026-05-10. The
retention work could have slipped to v0.3.7 (entity foundation +
`prior_attempts` wedge), but combining here avoided a renumbering
pass on the phase plan. Single-theme resumes at v0.3.7.

See [`CHANGELOG.md`](CHANGELOG.md) for the full list including the
new tests in `test_prune.py`, `test_update_check.py`, and the
`test_doctor.py` extension.

---

## What's new in v0.3.5

The recovery-basics release. v0.3.1 made the runtime safe; v0.3.2 made
problems visible; v0.3.5 ships the *minimum viable* "what happens when
something has gone wrong" surface. **Drop-in upgrade for anyone on
0.3.4.**

**`selvedge verify` — DB-correctness gate with two exit tiers.** Walks
the store and reports each check as PASS / WARN / FAIL. Must-fail
conditions (SQLite corruption, schema mismatch against the declared
`MIGRATIONS`, empty `entity_path`, unknown `change_type` in the store,
unparseable timestamps, malformed `tool_calls` rows) exit non-zero.
Should-warn conditions (singleton `changeset_id` groups, events past
the 60-minute backfill window with no `git_commit`) print warnings but
exit 0 by default. Pass `--strict` to escalate warnings to failures —
the tiering means `selvedge verify` can drop into CI on day one
without `|| true`. `--json` for machine output.

**`selvedge backup` — online SQLite snapshot via `VACUUM INTO`.**
Default destination
`.selvedge/backups/selvedge-YYYYMMDD-HHMMSS.db`, kept out of git
because `selvedge init` now appends `.selvedge/backups/` to the
project `.gitignore` (and the first `selvedge backup` run on an
existing repo appends it the same way — idempotent). Hardcoded
`keep_last=7` for this release; the setting becomes `backup_keep_last`
in `.selvedge/config.toml` when that file lands in v0.3.10.
`--output <path>` overrides the default and is excluded from rotation
so ad-hoc destinations aren't swept up. Two backups in the same second
don't collide.

**Doctor — `Last backup` row.** INFO when the newest backup is ≤7
days old, WARN when older, FAIL when no backups exist *and* the
events table has ≥10,000 rows (the threshold where no-backups becomes
a real data-loss exposure rather than a CI/scratch DB).

**Doctor — `Schema version` now FAILs on downgrade.** When
`schema_migrations` contains a version not declared in the current
`MIGRATIONS` tuple, the row fails rather than silently passing —
surfaces "this DB was last opened by a newer Selvedge" before any
write attempts schema work it doesn't understand.

See [`CHANGELOG.md`](CHANGELOG.md) for the full list including the
24 new tests across `test_verify.py`, `test_backup.py`, and the
`test_doctor.py` extension.

---

## Where Selvedge fits

<p align="center">
  <img src="docs/ecosystem.svg" alt="Where Selvedge fits in the broader AI-coded-codebase tooling stack" width="720">
</p>

AI agents call Selvedge as they work. Selvedge captures the *why*
into a durable, queryable store and emits it back out — as
[Agent Trace](https://github.com/cursor/agent-trace) records for
cross-tool readers, as observability metadata that links into
Sentry/Datadog stack traces, and as compliance artifacts for SOC 2
and EU AI Act audits.

Selvedge does **not** replace `git` (line-level what/when), PR review
tools (review-time quality), agent observability (LLM call traces),
or general-purpose code-host AI features. It sits between them — the
provenance-as-first-class-citizen layer that everything else
references.

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

## Quickstart

```bash
pip install selvedge
cd your-project
selvedge setup
```

That's it. `selvedge setup` is an interactive wizard: it detects which AI
tools you have (Claude Code, Cursor, Copilot), writes the MCP entry into
each one's config, drops the canonical agent-instructions block into your
project's prompt file (`CLAUDE.md` / `.cursorrules` /
`copilot-instructions.md`), runs `selvedge init`, and installs the
post-commit hook. Every modified file gets a `.bak` written next to it
before any change reaches disk. Re-running is a no-op.

For CI bootstrap or `devcontainer.json` `postCreateCommand`:
```bash
selvedge setup --non-interactive --yes
```

**Verify the wiring** — open a second terminal in the same project:

```bash
selvedge watch
```

Make any change in your AI tool — add a column, rename a function, add an
env var. `selvedge watch` should print the new event within a second of
the agent calling `log_change`. If nothing arrives, run `selvedge doctor`
for a single-command health check that tells you which step is silently
broken.

**Query your history:**

```bash
selvedge status                        # recent activity + missing-commit count
selvedge diff users                    # all changes to the users table
selvedge diff users.email              # changes to a specific column
selvedge blame payments.amount         # what changed last and why
selvedge history --since 30d           # last 30 days of changes
selvedge history --since 15m           # last 15 minutes ('m' = minutes)
selvedge changeset add-stripe-billing  # all events for a feature/task
selvedge search "stripe"               # full-text search
selvedge stats                         # log_change coverage report (per-agent)
selvedge import migrations/            # backfill from migration files
selvedge export --format csv           # dump history to CSV
```

<details>
<summary><b>Manual install</b> — if you'd rather wire it up yourself</summary>

If you don't want to run the wizard, the four manual steps it automates:

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

For Cursor: `~/.cursor/mcp.json`. For Copilot:
`.github/copilot-instructions.md` (different format — see
`selvedge prompt --help`).

**3. Tell your agent to use it**

```bash
selvedge prompt --install CLAUDE.md
```

This installs the canonical agent-instructions block, sentinel-bracketed
(`<!-- selvedge:start -->` / `<!-- selvedge:end -->`) so future
`--install` calls update the bracketed region without disturbing
anything else in the file. Or pipe it:

```bash
selvedge prompt | tee -a CLAUDE.md
```

**4. Install the post-commit hook**

```bash
selvedge install-hook
```

That's the same four steps the wizard runs.

</details>

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
