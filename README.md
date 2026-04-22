# Selvedge

**Change tracking for AI-era codebases.**

AI agents write your code now. But when they're done, the *why* disappears — the conversation that prompted the change, the reasoning behind the schema decision, the context that made the diff make sense. Selvedge captures all of it.

```bash
$ selvedge blame payments.amount

  payments.amount
  Changed     2025-11-03 14:22:01
  Type        add
  Agent       claude-code
  Commit      a3f9c12
  Diff        + amount DECIMAL(10,2) NOT NULL DEFAULT 0
  Reasoning   User requested Stripe billing integration. Added amount column
              to store transaction totals in cents per Stripe convention.
```

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

This creates `.selvedge/selvedge.db` in your project root. Commit it to share history with your team, or add `.selvedge/` to `.gitignore` to keep it local.

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

**4. Start querying**

```bash
selvedge status                        # recent activity
selvedge diff users                    # all changes to the users table
selvedge diff users.email              # changes to a specific column
selvedge blame payments.amount         # what changed last and why
selvedge history --since 30d           # last 30 days of changes
selvedge search "stripe"               # full-text search
```

---

## How it works

Selvedge runs as an MCP server. AI agents in tools like Claude Code call Selvedge's tools as they work — logging structured change events to a local SQLite database.

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

---

## MCP tools

When connected as an MCP server, Selvedge exposes:

| Tool | Description |
|------|-------------|
| `log_change` | Record a change event |
| `diff` | History for an entity or entity prefix |
| `blame` | Most recent change + context for an entity |
| `history` | Filtered history across all entities |
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
              [--limit N]
selvedge search QUERY [--limit N]         Full-text search
selvedge log ENTITY CHANGE_TYPE           Manually log a change
             [--diff TEXT]
             [--reasoning TEXT]
             [--agent NAME]
             [--commit HASH]
             [--project NAME]
```

All read commands support `--json` for machine-readable output.

**Relative time in `--since`:**
- `7d` → last 7 days
- `24h` → last 24 hours
- `3m` → last 3 months
- `1y` → last year

---

## Configuration

| Method | Format | Example |
|--------|--------|---------|
| Env var | `SELVEDGE_DB=/path/to/db` | Per-session override |
| Project init | `selvedge init` | Creates `.selvedge/selvedge.db` in CWD |
| Global fallback | `~/.selvedge/selvedge.db` | Used if no project DB found |

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

MIT
