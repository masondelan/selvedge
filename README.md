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

<p align="center">
  <a href="cursor://anysphere.cursor-deeplink/mcp/install?name=selvedge&amp;config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJzZWx2ZWRnZSIsInNlbHZlZGdlLXNlcnZlciJdfQ=="><img src="https://cursor.com/deeplink/mcp-install-dark.svg" alt="Add selvedge to Cursor" height="32"></a>
  &nbsp;
  <a href="https://insiders.vscode.dev/redirect/mcp/install?name=selvedge&amp;config=%7B%22name%22%3A%22selvedge%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--from%22%2C%22selvedge%22%2C%22selvedge-server%22%5D%7D"><img src="https://img.shields.io/badge/Install_in_VS_Code-0098FF?style=for-the-badge&amp;logo=visualstudiocode&amp;logoColor=white" alt="Install selvedge in VS Code" height="32"></a>
</p>

<p align="center"><sub>One click adds the <code>selvedge</code> MCP server to your editor. It runs via <a href="https://docs.astral.sh/uv/"><code>uvx</code></a>, so you only need <code>uv</code> installed — or <code>pip install selvedge</code> and use the <code>selvedge-server</code> command.</sub></p>

<!-- mcp-name: io.github.masondelan/selvedge -->

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

## What's new in v0.3.9

**Agent Trace export — Selvedge is a compatible producer.**
`selvedge export --format agent-trace` emits
[Agent Trace](https://github.com/cursor/agent-trace) **v0.1.0** records — the
open AI code-attribution wire format from Cursor + Cognition AI — so your
captured history travels to any tool that reads the standard. Selvedge's
reasoning and entity-level provenance ride along in each record's `metadata`
under the `dev.selvedge` namespace. **Drop-in upgrade for anyone on 0.3.8.**

```bash
selvedge export --format agent-trace -o trace.json            # one record per event
selvedge export --format agent-trace --ndjson -o trace.ndjson # stream, one per line
selvedge export --format agent-trace --collapse-by-session    # merge a session into one record
selvedge import trace.json --format agent-trace               # round-trip back in
```

It's **opt-in and additive** — nothing about the native model, the 8 MCP tools,
or local SQLite changes. Entity-level events (a column, an env var, a
dependency) have no line range, so Selvedge marks them
`metadata.dev.selvedge.range_unknown: true` rather than fabricating one — an
honest fidelity signal. This was planned for v0.4.0; only the exporter moved
forward (Postgres + the tool rename remain the v0.4.0 markers; HTTP + auth ships
in v0.4.1). Full mapping in
[`docs/agent-trace-interop.md`](docs/agent-trace-interop.md).

---

## What's new in v0.3.8

**Active memory v1 (date-based).** Selvedge's append-only log learns to know
when its own data is stale. A decision can now carry a revisit date, and the
new **`stale_decisions`** tool surfaces decisions that have aged out — but only
the ones whose entity is *still in active use*, so an old-but-correct decision
nobody touches never nags. **Drop-in upgrade for anyone on 0.3.7.** This brings
the MCP surface to **8 tools**.

### `revisit_after` + `stale_decisions` — decisions with an expiry date

Set `revisit_after` on an architectural `log_change` — an ISO date or a
relative offset like `90d`:

```jsonc
log_change({
  "entity_path": "deps/stripe", "change_type": "add", "entity_type": "dependency",
  "reasoning": "Pinned Stripe SDK to v11 for the billing launch.",
  "revisit_after": "180d"   // revisit this pin in ~6 months
})
```

Later, `stale_decisions` returns the dated decisions that have come due — and
filters out pure age:

```jsonc
stale_decisions({})
// → only decisions past their revisit date whose entity is STILL in use:
[
  {
    "entity_path": "deps/stripe", "change_type": "add",
    "reasoning": "Pinned Stripe SDK to v11 for the billing launch.",
    "revisit_due": "2026-...Z", "days_overdue": 12,
    "active_use_signals": ["queried"],
    "stale_reason": "past its revisit date and still active — the entity was queried (blame/diff/prior_attempts) after the decision."
  }
]
```

**Pure age never surfaces.** A decision only comes back if the entity is still
live — recently queried (`blame` / `diff` / `prior_attempts`) or its changeset
kept moving. That's the noise defense: a dated decision nobody has touched won't
nag. Templated and deterministic — no LLM, ever. The pattern-based half
(`expires_when` grammar, explicit `reject`/`revert` change types) lands in
v0.3.11; the v0.3.8 migration adds the `expires_when` column now so that's a
no-migration release.

### CLI parity for the wedge + CLI-awareness

`selvedge prior-attempts <entity>` lands — the v0.3.7 `prior_attempts` wedge
was the only MCP tool without a CLI command. It's a thin presenter over the
same store, so `--json` emits the identical list the tool returns. New
`selvedge stale` mirrors `stale_decisions` (with `--json` for cron / Slack
jobs). And the canonical agent-instructions block now names the CLI equivalents
alongside the MCP tools, so a shell-having agent is never blocked when the MCP
server isn't loaded. Selvedge stays **MCP-first**; the CLI is the additive
second path.

See [`CHANGELOG.md`](CHANGELOG.md) for the full list, the one-time migration-v3
note (metadata-only `ADD COLUMN`, fast even on multi-million-event DBs), and the
called-out test-budget overage from the bundled CLI + agent-block work.

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
compatible producer. As of **v0.3.9**, `selvedge export --format agent-trace`
emits Agent Trace v0.1.0 records (and `selvedge import --format agent-trace`
reads them back); the mapping is in
[`docs/agent-trace-interop.md`](docs/agent-trace-interop.md). Agent
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

### Claude Code plugin marketplace (alternative)

If you're a Claude Code user and want to install Selvedge through
the official plugin marketplace flow, run these inside Claude Code
*after* `pip install selvedge`:

```
/plugin marketplace add masondelan/selvedge
/plugin install selvedge@selvedge
```

The plugin system wires the MCP server into Claude Code, but it
does **not** install the Python package for you — `pip install
selvedge` first, otherwise the `selvedge-server` command won't
exist on your PATH and the plugin can't start. For the full setup
(post-commit hook, project `CLAUDE.md` instructions block, etc.),
`selvedge setup` is still the recommended path; the plugin
marketplace install is just the lightweight Claude-Code-only
entry point.

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

Prefer to copy-paste? The same block is one click away on the website:
**[selvedge.sh/prompt-block](https://selvedge.sh/prompt-block)** — with a
copy button and notes on what your agent does with it.

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
| `log_change` | Record a change event with entity, diff, and reasoning (pass `rename_from` with `change_type="rename"` for the dual-event rename pattern) |
| `diff` | History for an entity or entity prefix |
| `blame` | Most recent change + context for an exact entity |
| `history` | Filtered history across all entities |
| `changeset` | All events grouped under a named feature/task slug |
| `search` | Full-text search across all events |
| `prior_attempts` | Prior change attempts on an entity + inferred outcome (was it tried and reverted?) — call it before editing |
| `stale_decisions` | Dated decisions past their `revisit_after` that are still in active use (pure age never surfaces) |

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
selvedge prior-attempts ENTITY            Prior attempts + inferred outcome
                       [--description T]   (ENTITY xor --description)
                       [--all]             widen recall to proximity_low
                       [--window 7d]       proximity window
selvedge stale [--entity ENTITY]          Dated decisions due for a revisit
              [--project NAME]
              [--agent NAME]
              [--json]
selvedge stats [--since SINCE]            Tool call coverage report (per-tool, per-agent)
selvedge doctor [--json]                  Health check: DB path, schema, hook, MCP wiring
selvedge install-hook [--path PATH]       Install git post-commit hook
                     [--window MIN]       (default 60 minutes)
selvedge backfill-commit --hash HASH      Backfill git_commit on recent events
                        [--window MIN]    (default 60 minutes)
selvedge import PATH                      Import migrations (SQL / Alembic) or
              [--format auto|sql|         an Agent Trace file (agent-trace)
                 alembic|agent-trace]
              [--project NAME]
              [--dry-run]
selvedge export [--format json|csv|       Export history (agent-trace =
                 agent-trace]              Agent Trace v0.1.0 records)
              [--since SINCE]
              [--entity ENTITY]
              [--ndjson]                  agent-trace: one record per line
              [--collapse-by-session]     agent-trace: merge a session into one
              [--output FILE]
selvedge log ENTITY CHANGE_TYPE           Manually log a change
             [--diff TEXT]                CHANGE_TYPE: add, remove, modify,
             [--reasoning TEXT]           rename, retype, create, delete,
             [--agent NAME]               index_add, index_remove, migrate
             [--commit HASH]
             [--project NAME]
             [--changeset CS]
             [--revisit-after WHEN]       ISO date or offset (e.g. 90d)
             [--rename-from OLD]          OLD path when CHANGE_TYPE is 'rename'
selvedge migrate-paths                    Re-canonicalize stored entity paths
                      [--apply]           (dry-run by default; --apply writes)
                      [--json]
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

### In CI (GitHub Action)

The same check ships as the **Selvedge Coverage Check** composite Action, so
you can track agent coverage on every push — and optionally fail the build
when it drops:

```yaml
# .github/workflows/selvedge-coverage.yml
name: Selvedge coverage
on: [push, pull_request]
jobs:
  coverage:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0            # full history so commits can be matched
      - uses: masondelan/selvedge@v0.3.9   # pin to a release tag (or @main for latest)
        with:
          since: 30d
          fail-under: "0.5"         # optional: fail below 50% coverage; omit to report only
```

It writes a coverage summary to the job summary and exposes `coverage-ratio`,
`covered`, and `total` as step outputs. The action cross-references your git
history against the Selvedge event log, so the runner needs the project's
`.selvedge/selvedge.db` (commit it, or restore it before this step) and full
git history (`fetch-depth: 0`). Inputs: `since`, `window`, `limit`,
`fail-under`, `selvedge-version`, `python-version`, `working-directory`,
`db-path`.

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
