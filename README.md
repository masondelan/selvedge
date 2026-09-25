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

**Persistent decision memory for AI coding agents.**

Selvedge records why code changed, which approaches were rejected, and when
decisions deserve another look, so future sessions can retrieve that context
before editing. Query a function, database column, API route or dependency
instead of reconstructing its history from an old conversation.

Claude Code, Codex, Cursor, Copilot, Gemini CLI and Windsurf can connect through
MCP. The CLI works independently. Decisions live in a local SQLite store, with
no required hosted account and no LLM in the core storage or retrieval path.
See the [compatibility and capability reference](https://selvedge.sh/reference/compatibility/)
for setup targets, released capabilities, dependencies and privacy boundaries.
The [agent hook guide](docs/agent-hooks.md) documents native lifecycle adapters
and distinguishes protocol-tested behavior from client activation.

A saved explanation is testimony supplied by a person or agent. Selvedge does
not extract hidden model reasoning, verify that an explanation is true, or
guarantee that an agent will consult it. The useful test is whether the next
session retrieves the right decision and applies it appropriately.

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

This illustrative record shows **rationale logged while the context was available**.
Real records are only as informative as the explanations supplied. Imported
history may contain inferred or missing rationale; retain that distinction.

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

Both human and AI coding workflows can leave intent scattered across commits,
PRs and conversations. When a session ends, a later agent may see the code
without the constraint or rejected approach that explains it.

Six months later, your team is debugging a schema decision with no trail.
`git blame` tells you *what* changed and *when*. It can't tell you *why*.

**Selvedge captures the why — live, by the agent itself, as the change is
made.** The diff is git's job. The why is Selvedge's.

---

## What's new in v0.3.15

**Native lifecycle adapters for all six setup targets.**

Setup now offers hooks for Codex, Cursor, VS Code Copilot Local, Gemini CLI and
Windsurf/Cascade alongside Claude Code. Startup context and watched-edit checks
are available where the client supports them; compaction notifications are
advisory. Windsurf provides edit and command checks only. Existing configuration
is preserved, with backups and visible conflicts for customized hooks.

See [capabilities, activation and verification](docs/agent-hooks.md). The new
adapters have protocol and subprocess coverage; client versions and harnesses
still matter. No new dependencies, MCP tools, migrations or default telemetry.

The [configuration pilot](bench/decision_memory/results/2026-09-25/) publishes
all 48 measured trials and controls, including failures. It does not establish
an advantage over a maintained file or the same information in a prompt.

---

## What's new in v0.3.14

**Explicit seven-day lookups work as documented.**

The MCP `prior_attempts` tool now accepts `window_minutes=10080`, matching its
seven-day default. Previously, sending that value explicitly failed validation
because the time window incorrectly shared the 1,000-result pagination cap.
The allowed window is 1–10,080 minutes; result limits remain capped at 1,000.
No new dependencies, migrations or MCP tools.

---

## Where Selvedge fits

<p align="center">
  <img src="docs/ecosystem.svg" alt="Where Selvedge fits in the broader AI-coded-codebase tooling stack" width="720">
</p>

AI agents call Selvedge as they work. Selvedge stores explicitly supplied
rationale and makes it queryable later. Export formats support downstream
workflows, including [Agent Trace interchange](docs/agent-trace-interop.md).
A provenance record is evidence of what was recorded, not a certification
of correctness or compliance.

Selvedge does **not** replace `git` (line-level what/when), PR review
tools (review-time quality), agent observability (LLM call traces),
or general-purpose code-host AI features. It sits between them — the
provenance-as-first-class-citizen layer that everything else
references.

---

## How Selvedge compares

Choose the simplest memory mechanism that fits your workflow:

| Need | Useful starting point | What Selvedge adds |
| --- | --- | --- |
| Standing project rules | Maintained agent instruction files | Queryable decisions attached to particular entities |
| Reviewed architecture choices | Architecture decision records (ADRs) | Structured outcomes, rejected approaches and revisit signals |
| Who changed a line and when | Git history and attribution tools | The stated reason and earlier approaches for an entity |
| Carry recorded decisions into another session or client | Shared project documentation | MCP and CLI retrieval from the same local database |

These approaches can be used together. Read the [source-linked comparison](https://selvedge.sh/compare/)
and [instructions versus ADRs versus decision memory](https://selvedge.sh/compare/instructions-and-adrs/)
for specific selection criteria and limitations.

**Why entity history matters.** `users.email`, `env/STRIPE_SECRET_KEY`,
`api/v1/checkout` and `deps/stripe` remain useful query targets when their code
moves. Explicit rejection and superseding records let a later session inspect
what was tried and whether the original constraint still applies.

**Why capture time matters.** Recording a reason while the context is available
can preserve information a diff does not contain. It does not make the reason
infallible. Retrieval uses stored records; the coding agent remains responsible
for checking applicability and testing its change.

**Why changesets matter.** Tag events with a shared changeset such as
`add-stripe-billing` to retrieve a task's decisions across tables, environment
variables, routes and functions.

**Selvedge ↔ Agent Trace.** [Agent Trace](https://agent-trace.dev/) is an
open AI code-attribution wire format published by Cursor (RFC, Jan 2026). Its
original GitHub home went 404 in August 2026 and the multi-vendor momentum
behind it has faded, but the spec and schema still resolve at agent-trace.dev,
frozen at v0.1.0. Since **v0.3.9**, `selvedge export --format agent-trace`
emits Agent Trace v0.1.0 records and `selvedge import --format agent-trace`
reads them back — a portable, documented interchange format for file/line AI
attribution, with reasoning and entity-level provenance carried in each
record's `dev.selvedge` metadata. The mapping is in
[`docs/agent-trace-interop.md`](docs/agent-trace-interop.md); Selvedge vendors
the schema and has no runtime dependency on the upstream project.

---

## Quickstart

### Claude Code — install the plugin (recommended)

Two commands, inside Claude Code. No prior `pip install` — the plugin
bootstraps the server itself via `uvx` (or `pipx`):

```
/plugin marketplace add masondelan/selvedge
/plugin install selvedge@selvedge
```

That's the whole agent-facing surface in one step:

- the **MCP server** — 8 tools (`log_change`, `prior_attempts`, `blame`,
  `diff`, `history`, `changeset`, `search`, `stale_decisions`);
- a **skill** that tells the agent *when* to call them — before editing a
  tracked entity, after any substantive change;
- the **PreToolUse enforcement hook** — schema/migration edits are blocked
  until `prior_attempts` has been checked this session, with the prior
  reasoning in the block message;
- **slash commands** — `/selvedge:status`, `/selvedge:blame <entity>`,
  `/selvedge:history`, `/selvedge:prior-attempts <entity>`.

The store (`.selvedge/selvedge.db`) creates itself on the first logged change.
Two optional extras stay CLI-side: the post-commit hook that stamps each event
with its commit hash (`selvedge install-hook`), and — if you want the
`selvedge` command on your own shell `PATH` — `pip install selvedge`, which the
launcher then prefers over `uvx` for an exact pinned version.

> **Plugin or `selvedge setup` for Claude Code? Pick one.** Both wire the MCP
> server; running both registers it twice. The plugin is the lighter path and
> the one that updates itself. If you're on the plugin and only want the
> post-commit commit-hash stamping, run `selvedge install-hook` on its own.

### Choose your coding agent

With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed:

```bash
uv tool install --upgrade selvedge
selvedge demo
cd your-project
selvedge setup --agent codex
```

Use `codex`, `claude-code`, `cursor`, `copilot`, `gemini` or `windsurf`.
Repeat `--agent` for multiple tools, or omit it to detect installed agents.
Prefer pip? Use `python -m pip install --upgrade selvedge` in a virtual environment.
The `selvedge-server` executable must be on your editor's PATH; launch the editor
from that environment or use the executable's absolute path in its MCP config.

Setup asks before changing files, backs up existing content, installs MCP and
agent instructions, initializes the project, and offers a Git post-commit hook.
For Codex it writes `.codex/config.toml` and `AGENTS.md`; Gemini CLI gets
`.gemini/settings.json` and `GEMINI.md`; Copilot gets `.vscode/mcp.json` and
`.github/copilot-instructions.md`. Custom Codex TOML entries require manual
reconciliation, even with `--force`.

Restart your agent in the project and approve Selvedge's tools if prompted.
Codex must trust the project to load project-scoped configuration. Ask the agent:

> Use Selvedge to record one real approach we considered and rejected in this project.
> Include the entity, why, and what would change our mind. Do not invent a decision.
> Show the saved entity path and record ID.

Start a new session and look up the same entity to verify that the decision carries
forward. Follow the [first-decision verification guide](https://selvedge.sh/guides/verify-first-decision/)
and [cross-agent handoff guide](https://selvedge.sh/guides/share-memory-between-agents/)
for observable checks. MCP access does not automatically capture every decision: the installed
instructions guide the agent to use it. Native lifecycle adapters cover supported client events; startup delivery,
watched-edit checks and compaction notifications vary by harness. See the
[agent hook guide](docs/agent-hooks.md) before enabling them.

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
selvedge stats                         # observed tool calls and explanation quality
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
                 markdown|agent-trace]      Agent Trace v0.1.0 records;
                                            markdown = reviewable digest)
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
| Project settings | `.selvedge/config.toml` | See the key list below — retention, size bounds, redaction patterns |
| Global settings | `~/.selvedge/config.toml` | Same keys; the project file wins where both set one |
| Hook bypass | `SELVEDGE_HOOK_DISABLE=1` | Disables the PreToolUse enforcement hook for the shell |
| Semantic extra | `pip install "selvedge[semantic]"` | Enables `selvedge index` + `prior-attempts --fuzzy` (local model2vec embeddings, ~30 MB; core never depends on it) |

### `.selvedge/config.toml`

Every key is optional; a missing file means the defaults below. Precedence is
**CLI flag → env var → project `.selvedge/config.toml` → global
`~/.selvedge/config.toml` → default**. `SELVEDGE_DB` is the one exception: it
always wins for database resolution, because the config file is found *by*
resolving that path. `selvedge doctor` prints the effective value and the step
that produced it for every setting.

```toml
retention_days_events     = 0       # 0 = never delete events (the default)
retention_days_tool_calls = 90      # local telemetry retention
backup_keep_last          = 7
diff_bytes                = 65536   # truncate oversized diffs at log time
reasoning_bytes           = 32768   # truncate oversized reasoning
db_size_warn_mb           = 500     # doctor warns above this
stale_days                = 0       # 0 = off
digest_max_bytes          = 4096    # cap on the session-start digest
redaction_patterns        = []      # extra secret shapes to warn about

[hook]
watch_globs = ["**/migrations/**", "db/**/*.sql"]
```

Every key also has an env override (`SELVEDGE_DIFF_BYTES`,
`SELVEDGE_RETENTION_DAYS_EVENTS`, …).

---

## Reviewing captured intent in a pull request

`.selvedge/selvedge.db` is a SQLite file, so the reasoning inside it doesn't
show up in a diff. Export a Markdown digest next to it and commit both:

```bash
selvedge export --format markdown -o .selvedge/DECISIONS.md
git add .selvedge/
```

The digest is grouped by entity with **reverted decisions first**, and it is
deterministic — regenerating with no new events produces a zero-line diff, so
it stays reviewable instead of becoming noise everyone learns to skip. Heading
anchors derive from the entity path, so links into it keep working as it
grows. Regenerate it in the same commit as the code, or from a pre-commit
hook.

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
      - uses: masondelan/selvedge@v0.3.15   # pin to a release tag (or @main for latest)
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

Read the [feedback and review process](docs/community-feedback.md) for reporting problems, evaluating feature requests and following up on discussions.

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
