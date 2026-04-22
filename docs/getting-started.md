# Getting started with Selvedge

## Install

```bash
pip install selvedge
```

## Initialize your project

```bash
cd your-project
selvedge init
```

This creates `.selvedge/selvedge.db` in your project root. Commit the `.selvedge/` directory to share change history with your team, or add it to `.gitignore` to keep it local.

## Connect to Claude Code

Add Selvedge to your MCP config at `~/.claude/config.json`:

```json
{
  "mcpServers": {
    "selvedge": {
      "command": "selvedge-server"
    }
  }
}
```

To use a project-specific database instead of the global fallback:

```json
{
  "mcpServers": {
    "selvedge": {
      "command": "selvedge-server",
      "env": {
        "SELVEDGE_DB": "/path/to/your/project/.selvedge/selvedge.db"
      }
    }
  }
}
```

## Tell your agent to log changes

Add this to your project's `CLAUDE.md`:

```
You have access to Selvedge (MCP server: selvedge) for change tracking.

Rules:
- Call selvedge.log_change immediately after adding, modifying, or removing
  any DB column, table, function, API endpoint, dependency, or env variable.
- Set `reasoning` to the user's original request or the problem being solved.
- Set `agent` to "claude-code" (or whichever agent you are).
- Set `session_id` if you have access to the current session ID.
- Set `git_commit` to the commit hash once you know it.
- Before modifying an entity, call selvedge.diff or selvedge.blame to understand
  its history and avoid conflicting with past decisions.
```

## Query your history

```bash
# See what's been logged
selvedge status

# Full change history for a DB table and all its columns
selvedge diff users

# Change history for a specific column
selvedge diff users.email

# Who changed this last, and why?
selvedge blame payments.stripe_customer_id

# Everything in the last 30 days
selvedge history --since 30d

# Search by keyword
selvedge search "stripe"
selvedge search "auth"
```

## Log a change manually

```bash
selvedge log users.phone add \
  --reasoning "Added phone number for 2FA" \
  --agent "me"
```

## All CLI commands

```
selvedge init [--path PATH]           Initialize in project directory
selvedge status                       Summary of recent activity
selvedge diff ENTITY [--limit N]      Change history (prefix matching)
selvedge blame ENTITY                 Most recent change + context
selvedge history [--since SINCE]      Browse all history with filters
              [--entity ENTITY]
              [--project PROJECT]
              [--limit N]
selvedge search QUERY [--limit N]     Full-text search
selvedge log ENTITY CHANGE_TYPE       Manually record a change
             [--diff TEXT]
             [--reasoning TEXT]
             [--entity-type TYPE]
             [--agent NAME]
             [--commit HASH]
             [--project NAME]
```

Add `--json` to any read command for machine-readable output.
