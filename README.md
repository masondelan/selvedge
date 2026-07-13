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

## What's new in v0.3.9.1

**The dev.to feedback release.** Five improvements publicly promised in the
comment threads of
[the launch post](https://dev.to/masondelan/my-ai-agent-tried-to-ship-a-mistake-wed-already-reverted-4737),
all landed. Append-only store and zero-LLM core, unchanged. **Drop-in
upgrade for anyone on 0.3.9** (the v4 migration is metadata-only, instant at
any size).

**Reverted is no longer a permanent ban.** A reverted decision can become
correct again — so re-open it *explicitly*, without rewriting history:

```bash
selvedge supersede payments.card_token \
  --reasoning "Provider now vaults card data — the PCI constraint is gone."
```

`prior_attempts` / `blame` / `diff` then read the full trail — **tried →
reverted → re-opened** — with a clear current-status line. Decisions can now
also carry a queryable `constraint` (the principle that drove them) and a
`stale_when` condition; when a later change event matches the condition,
`stale_decisions` flags the decision **"review suggested"** (surfacing only —
no automatic un-retiring, ever).

**The prior_attempts check is now enforceable.** `selvedge setup` installs a
Claude Code PreToolUse hook that blocks schema/migration edits until
`prior_attempts` has been checked this session — and puts the prior reasoning
*in the block message*, so the agent gets the skipped context for free.
Fail-open by contract (a miss or error always allows), `--dry-run` and
`SELVEDGE_HOOK_DISABLE=1` included.

**Your pre-Selvedge reverts count too.** `selvedge import --from-git` walks
git history (revert-message commits + file deletions) and seeds
`change_type="revert"` events, idempotently — so the mistakes your repo
already made once gate the hook and `prior_attempts` from day one.

**And recall that survives renames** (optional):

```bash
pip install "selvedge[semantic]" && selvedge index
selvedge prior-attempts --fuzzy "tokenized payment credentials"
# finds the users.card_token revert even though you asked about payment tokens
```

Local static embeddings (model2vec, ~30 MB, no torch), clearly-labeled fuzzy
matches, substring fallback when the extra isn't installed. Core never
imports it. Full details in [`CHANGELOG.md`](CHANGELOG.md).

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
`copilot-instructions.md`), installs the PreToolUse enforcement hook into
`.claude/settings.json` (Claude Code only — blocks schema/migration edits
until `prior_attempts` has been checked; `--skip-enforcement-hook` to opt
out), runs `selvedge init`, and installs the post-commit hook. Every
modified file gets a `.bak` written next to it before any change reaches
disk. Re-running is a no-op.

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

**2. Register the MCP server**

Selvedge is a standard stdio MCP server, so it works with any MCP client —
Claude Code, Cursor, Windsurf, Codex CLI, Gemini CLI, and more. See
**[Works with any MCP client](#works-with-any-mcp-client)** for the exact
config per client. For Claude Code:

```bash
claude mcp add selvedge -- selvedge-server
```

**3. Tell your agent to use it**

```bash
selvedge prompt --install CLAUDE.md
```

Point `--install` at whichever prompt file your client reads — the block
itself is identical across clients:

| Client | Prompt file |
|--------|-------------|
| Claude Code | `CLAUDE.md` |
| Codex CLI (and other `AGENTS.md`-aware tools) | `AGENTS.md` |
| Cursor | `.cursor/rules/selvedge.md` (or legacy `.cursorrules`) |
| Gemini CLI | `GEMINI.md` |

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

## Works with any MCP client

Selvedge is a standard stdio MCP server — its launch command is
`selvedge-server`, put on your `PATH` by `pip install selvedge`. Any
MCP-capable client can run it. Pick yours:

<details>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add selvedge -- selvedge-server
```

Or commit a project-level `.mcp.json` so your whole team gets it:

```json
{
  "mcpServers": {
    "selvedge": { "command": "selvedge-server" }
  }
}
```

Docs: <https://code.claude.com/docs/en/mcp>
</details>

<details>
<summary><b>Cursor</b></summary>

`.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global):

```json
{
  "mcpServers": {
    "selvedge": { "command": "selvedge-server" }
  }
}
```

Cursor's newer schema also accepts an explicit `"type": "stdio"`; the
`command`-only form works too (Cursor infers stdio from `command`).
Docs: <https://cursor.com/docs/mcp>
</details>

<details>
<summary><b>Windsurf</b></summary>

`~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "selvedge": { "command": "selvedge-server" }
  }
}
```

Windsurf hot-reloads the file — no restart needed. The in-app
**Plugins → View raw config** button opens the exact file Cascade reads.
Docs: <https://docs.windsurf.com/windsurf/cascade/mcp>
</details>

<details>
<summary><b>Codex CLI</b></summary>

`~/.codex/config.toml`:

```toml
[mcp_servers.selvedge]
command = "selvedge-server"
```

Or run `codex mcp add selvedge -- selvedge-server`.
Docs: <https://developers.openai.com/codex/config-reference>
</details>

<details>
<summary><b>Gemini CLI</b></summary>

`~/.gemini/settings.json` (or `.gemini/settings.json` per project):

```json
{
  "mcpServers": {
    "selvedge": { "command": "selvedge-server" }
  }
}
```

Or run `gemini mcp add -s user selvedge selvedge-server`.
Docs: <https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md>
</details>

<details>
<summary><b>Any other MCP client</b></summary>

Most clients share the same JSON shape — point yours at:

```json
{
  "mcpServers": {
    "selvedge": { "command": "selvedge-server" }
  }
}
```

If `selvedge-server` isn't found, use its absolute path (`which
selvedge-server`).
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

## Selvedge tracks its own history

This repo dogfoods Selvedge: its `.selvedge/selvedge.db` is committed, so a
fresh clone ships with Selvedge's own why-history. Clone it and ask why any
part of Selvedge changed:

```bash
git clone https://github.com/masondelan/selvedge
cd selvedge
selvedge status                       # recent changes to Selvedge itself
selvedge search "telemetry"           # why the opt-in heartbeat shipped
selvedge blame selvedge/semantic.py   # why semantic search was added
```

Every event was logged by the agents that built Selvedge — the same
`log_change` calls this README asks you to make in your own project.

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
| `log_change` | Record a change event with entity, diff, and reasoning. `rename_from` + `change_type="rename"` records the dual-event rename pattern; `change_type="supersede"` re-opens a reverted decision (append-only); optional `constraint` / `stale_when` keep the decision's principle and its invalidation condition queryable |
| `diff` | History for an entity or entity prefix, each row annotated with `superseded_by` |
| `blame` | Most recent change + context for an exact entity, plus the derived decision `status` (active / reverted / reopened) |
| `history` | Filtered history across all entities |
| `changeset` | All events grouped under a named feature/task slug |
| `search` | Full-text search across all events |
| `prior_attempts` | Prior change attempts on an entity + inferred outcome (tried → reverted → re-opened) — call it before editing. Optional `fuzzy` query adds semantically similar records (needs the `semantic` extra; falls back to substring) |
| `stale_decisions` | Decisions due for a revisit: past their `revisit_after` and still in active use (`flag="revisit_due"`), or whose `stale_when` condition matched a later change (`flag="review_suggested"`) |

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
selvedge prior-attempts ENTITY            Prior attempts + inferred outcome,
                       [--description T]   with the tried → reverted →
                       [--all]             re-opened trail + status line
                       [--window 7d]       (--all widens recall)
                       [--fuzzy TEXT]      add semantic matches (needs the
                                           semantic extra; substring fallback)
selvedge supersede ENTITY                 Re-open a reverted decision —
                  --reasoning TEXT         append-only, links the prior
                  [--constraint TEXT]      reverted event (or --supersedes ID)
                  [--stale-when TEXT]
                  [--supersedes ID]
selvedge index [--model NAME]             Build/update the optional semantic
              [--json]                     embeddings index (selvedge[semantic])
selvedge stale [--entity ENTITY]          Decisions due for a revisit: past
              [--project NAME]            revisit_after + still in use, or
              [--agent NAME]              stale_when matched by a later change
              [--json]                    ("review suggested")
selvedge stats [--since SINCE]            Tool call coverage report (per-tool, per-agent)
selvedge doctor [--json]                  Health check: DB path, schema, hook, MCP wiring
selvedge install-hook [--path PATH]       Install git post-commit hook
                     [--window MIN]       (default 60 minutes)
selvedge backfill-commit --hash HASH      Backfill git_commit on recent events
                        [--window MIN]    (default 60 minutes)
selvedge import PATH                      Import migrations (SQL / Alembic) or
              [--format auto|sql|         an Agent Trace file (agent-trace)
                 alembic|agent-trace]
              [--from-git]                or walk git history for reverts:
              [--since REF|DATE]          revert-message commits + deletions
              [--project NAME]            become change_type="revert" events
              [--dry-run]                 (idempotent on commit + entity)
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
             [--agent NAME]               index_add, index_remove, migrate,
             [--commit HASH]              revert, supersede
             [--project NAME]
             [--changeset CS]
             [--revisit-after WHEN]       ISO date or offset (e.g. 90d)
             [--rename-from OLD]          OLD path when CHANGE_TYPE is 'rename'
             [--constraint TEXT]          the principle behind the decision
             [--stale-when TEXT]          what would invalidate it
             [--supersedes ID]            with CHANGE_TYPE 'supersede'
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
| Hook watch globs | `.selvedge/config.toml` | `[hook]`<br>`watch_globs = ["**/migrations/**", "db/**/*.sql"]` — replaces the enforcement hook's default schema/migration globs |
| Hook bypass | `SELVEDGE_HOOK_DISABLE=1` | Disables the PreToolUse enforcement hook for the shell |
| Semantic extra | `pip install "selvedge[semantic]"` | Enables `selvedge index` + `prior-attempts --fuzzy` (local model2vec embeddings, ~30 MB; core never depends on it) |

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
