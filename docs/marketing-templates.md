# Selvedge — Marketing Templates

Pre-written copy for community launches and future release announcements.
Voice: lowercase, direct, problem-first. No hype, no superlatives.

---

## Hacker News

### v0.1 — Show HN (initial launch)

**Title:**
```
Show HN: Selvedge – MCP server that captures the "why" before your AI agent's session ends
```

**Body:**
```
with claude code and cursor writing most of our code now, the intent behind
changes is disappearing. commit messages are auto-generated. pr descriptions
are sparse. the reasoning that used to leak into human-written commits is
just... gone.

selvedge is an mcp server that AI coding agents call as they work to log
structured change events — entity, diff, and reasoning — to a local sqlite
db. then you can ask things like:

  selvedge blame payments.amount
  → added by claude-code, commit a3f9c12
  → "stripe requires amount in cents per their v3 api change"

  selvedge diff users --since 30d
  → 12 changes across users.* in the last 30 days

it's open source, zero-config, and works with any MCP-compatible agent.
install takes 30 seconds. add selvedge.log_change to your system prompt and
it runs in the background.

pip install selvedge
github: github.com/masondelan/selvedge

would love feedback on the data model — especially the entity_path conventions
and whether the reasoning field is enough, or if more structure is needed.
```

---

### v0.2 — Show HN (git hook + migration parser release)

**Title:**
```
Show HN: Selvedge v0.2 – git hooks, migration importer, and CSV export for AI codebase history
```

**Body:**
```
a few months ago i posted selvedge — an mcp server for capturing why AI agents
make changes before the session disappears.

v0.2 adds three things:

1. git post-commit hook — one command installs it, then every commit
   automatically backfills git_commit on selvedge events from that session.
   no manual tagging ever.

   selvedge install-hook

2. selvedge import — point it at your alembic or raw sql migration files and
   it backfills schema history from before you started using selvedge.

   selvedge import alembic/versions/ --project my-api

3. selvedge export — dump your full history to json or csv for reporting,
   audits, or piping into other tools.

   selvedge export --format csv --since 30d > last-month.csv

the import command is the one i'm most excited about. if you have 2 years of
alembic migrations sitting there, you can backfill all of it in one command.

pip install selvedge==0.2.0
changelog: github.com/masondelan/selvedge/releases/tag/v0.2.0
```

---

## Reddit

### r/Python — v0.1 launch

**Title:**
```
I built an MCP server that logs why your AI coding agent made each change — before the session disappears
```

**Body:**
```
**the problem:** when I write code myself, intent leaks into commits and PR
descriptions. when claude code writes it, the reasoning lives in the prompt
and evaporates when the session closes.

**selvedge** is a lightweight MCP server (sqlite-backed, zero config) that AI
agents call as they work to log structured change events. think of it like git
blame, but for the *why*.

```bash
pip install selvedge
selvedge init
selvedge blame payments.amount
# → added 2025-03-14 by claude-code
# → "stripe requires amounts in cents per their v3 api change"
```

to activate it in claude code, add one line to your CLAUDE.md:

```
Call selvedge.log_change after any meaningful change.
Set reasoning to the original user request.
```

open source, MIT license. github.com/masondelan/selvedge
feedback welcome — especially from anyone doing serious AI-assisted dev work.
```

---

### r/Python — v0.2 launch

**Title:**
```
Selvedge v0.2: git hook + Alembic/Liquibase importer for AI codebase change tracking
```

**Body:**
```
v0.2 of selvedge is out. quick recap: selvedge is an MCP server that AI coding
agents use to log *why* they made each change before the session disappears.

new in v0.2:

**git post-commit hook** — after every commit, selvedge automatically finds
events logged in that session window and backfills the git hash.

**`selvedge import`** — parses alembic or raw SQL migration files and creates
ChangeEvents from them. if you're adding selvedge to an existing project,
this gets you caught up.

```bash
pip install selvedge==0.2.0
selvedge import migrations/
selvedge history --since 1y
```

changelog: github.com/masondelan/selvedge/releases/tag/v0.2.0
```

---

### r/ClaudeAI — v0.1 launch

**Title:**
```
built a tool that makes claude code log the why behind every change it makes
```

**Body:**
```
one thing that's been bugging me about using claude code heavily: the reasoning
behind changes lives in the conversation and disappears when the session ends.
six months later you're staring at a column called `user_tier_v2` with no idea
why it exists.

i built **selvedge** — an MCP server that claude code calls automatically to
log change events with reasoning to a local sqlite db.

setup is ~2 minutes:

```bash
pip install selvedge
selvedge init
```

add to `~/.claude/config.json`:
```json
{
  "mcpServers": {
    "selvedge": { "command": "selvedge-server" }
  }
}
```

add to your CLAUDE.md:
```
Call selvedge.log_change after any meaningful change.
Set reasoning to the original user request.
```

then:
```bash
selvedge blame payments.amount
selvedge diff users --since 30d
selvedge search "stripe"
```

open source, MIT. github.com/masondelan/selvedge
```

---

### r/ClaudeAI — v0.2 launch

**Title:**
```
selvedge v0.2 — git hooks and alembic importer so your AI change history goes back to day one
```

**Body:**
```
update on selvedge (the tool that makes claude code log why it makes changes):
v0.2 is out.

new:
- **git post-commit hook** — auto-backfills `git_commit` on events that match
  the commit timestamp window
- **`selvedge import`** — parses alembic / raw SQL migrations and creates
  change history from them

```bash
pip install selvedge==0.2.0
selvedge import migrations/
selvedge history --since 1y
```

changelog: github.com/masondelan/selvedge/releases/tag/v0.2.0
```

---

## Product Hunt

### Listing copy

**Name:** Selvedge

**Tagline:**
```
MCP server that logs why your AI agent made each code change
```

**Description:**
```
AI coding agents write your code. But when the session ends, the reasoning
disappears — and six months later, nobody knows why that column exists.

Selvedge is an open-source MCP server that captures the "why" in the moment.
Agents call it automatically as they work, logging structured change events
(entity, diff, reasoning, commit) to a local SQLite database.

Then you can ask:
  selvedge blame payments.amount
  → "stripe requires amounts in cents per v3 api change"

  selvedge diff users --since 30d
  → 12 schema changes with full reasoning

Zero config. Works with Claude Code, Cursor, or any MCP-compatible agent.
pip install selvedge to get started.
```

**Topics:** Developer Tools, AI, Open Source, Productivity

**First comment (post this yourself on launch day):**
```
hi PH — i built selvedge after noticing that every AI coding session I ran
left behind changes with no documented reasoning.

the core insight: with human-written code, intent leaks into commits. with
AI-written code, it evaporates. selvedge sits in the middle and catches it.

would love feedback on:
- the entity_path conventions (does the format feel natural?)
- whether the reasoning field needs more structure
- use cases I haven't thought of

github.com/masondelan/selvedge
```

---

## Blog post template

> **Before posting:** fill in the [SCENARIO] sections with a real change from
> your own work. The more specific, the better. Generic examples read like
> marketing; real ones read like engineering.

---

**Title:**
```
The column nobody could explain
```

**Body:**

Six months ago, [SCENARIO: describe the change — e.g. "claude code added a
column called `payment_tier_legacy` to our users table"]. The commit message
said "[SCENARIO: paste the actual commit message — probably something useless
like 'Update schema']."

Last week, we needed to modify it. Nobody on the team knew what it was for.
The agent session that created it was gone. The PR description was empty.
`git blame` showed the commit. `git log` showed the date. Neither showed
the why.

We had to read through a month of old code to reverse-engineer the intent.
It took two hours. We still weren't sure we got it right.

---

That's the problem Selvedge solves.

Selvedge is an MCP server that AI coding agents call as they work. Instead
of the reasoning dying with the session, it gets written to a local database
the moment the change is made:

```bash
$ selvedge blame [SCENARIO: your entity path]

  [SCENARIO: entity]
  Changed     [date]
  Agent       claude-code
  Reasoning   [SCENARIO: paste the actual captured reasoning]
```

Setup takes two minutes. You add `selvedge-server` to your Claude Code MCP
config, add one instruction to your `CLAUDE.md`, and it runs in the background
from that point forward.

---

The column problem is small. The bigger problem is architectural decisions
accumulating without context — auth middleware rewrites driven by compliance
requirements that nobody documented, API versioning choices made during an
incident, dependency upgrades prompted by a CVE that's now three major versions
behind.

With human developers, that context leaked everywhere — Slack threads, PR
comments, ADRs written after the fact. With AI developers, the only place it
ever existed was the conversation. And conversations end.

Selvedge is the thing that runs in the background and makes sure it doesn't
disappear.

[github.com/masondelan/selvedge](https://github.com/masondelan/selvedge) — open source, MIT license.

---

## Voice guidelines

- all lowercase in titles and body copy
- lead with the problem, not the product
- show terminal output — concrete beats abstract every time
- no superlatives ("best", "revolutionary", "game-changing")
- end community posts with a genuine question to invite replies
- "open source, MIT license" always included
- the word "why" appears in almost every piece of copy — that's the product

---

## Launch checklist (per release)

1. `git tag vX.Y.Z && git push --tags` → triggers publish.yml (PyPI + GitHub Release)
2. Verify live: `pip install selvedge==X.Y.Z`
3. Update version references in templates above
4. Post HN Show HN first (highest leverage, sets the narrative)
5. Post r/Python 24h later
6. Post r/ClaudeAI 24h after that
7. Product Hunt — launch on a Tuesday or Wednesday morning PT for best visibility
8. Space all posts 24–48h apart
