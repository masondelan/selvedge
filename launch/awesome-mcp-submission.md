# awesome-mcp List Submissions

Submit these as GitHub issues or PRs to the respective lists.
The biggest lists to target are listed first.

---

## 1. punkpeye/awesome-mcp-servers (most starred)
https://github.com/punkpeye/awesome-mcp-servers

**PR title:** Add Selvedge — change tracking MCP server for AI-era codebases

**PR body / issue description:**

Hi! Requesting addition of **Selvedge**, an MCP server for capturing structured change events and reasoning from AI coding agents.

**Name:** Selvedge
**Link:** https://github.com/masondelan/selvedge
**Category:** Developer Tools (or: Code/Codebase Management)
**Description:** Captures the *why* behind AI code changes — agents call `log_change` as they work, storing entity path, diff, and reasoning in local SQLite before the session ends.

**Suggested entry:**
```
- [Selvedge](https://github.com/masondelan/selvedge) - Change tracking for AI-era codebases. Captures structured change events and agent reasoning to local SQLite; query with `selvedge blame`, `selvedge diff`, `selvedge search`.
```

**Details:**
- MIT licensed
- Python, available on PyPI (`pip install selvedge`)
- Exposes 6 MCP tools: `log_change`, `diff`, `blame`, `history`, `search`, `changeset`
- Local SQLite storage, no account or cloud required
- Works with Claude Code; any MCP-compatible agent supported

---

## 2. appcypher/awesome-mcp-servers
https://github.com/appcypher/awesome-mcp-servers

**PR title:** Add Selvedge — codebase change tracking MCP server

**PR / issue body:**

Requesting addition of Selvedge to the list.

```markdown
| [Selvedge](https://github.com/masondelan/selvedge) | Captures AI agent change reasoning to local SQLite before sessions end. Query with `blame`, `diff`, `search`. | Python | MIT |
```

- MCP tools: `log_change`, `diff`, `blame`, `history`, `search`, `changeset`
- `pip install selvedge`
- No cloud dependency — fully local

---

## 3. wong2/awesome-mcp-servers
https://github.com/wong2/awesome-mcp-servers

**PR title:** Add Selvedge (change tracking / developer tools)

**Suggested entry:**
```
- [selvedge](https://github.com/masondelan/selvedge) - Change tracking MCP server. Logs AI agent reasoning before sessions end; query with blame, diff, and search.
```

---

## 4. modelcontextprotocol/servers (official Anthropic list)
https://github.com/modelcontextprotocol/servers

**Issue title:** Community server submission: Selvedge (change tracking)

**Issue body:**

Submitting Selvedge for inclusion in the community servers list.

**Name:** Selvedge
**GitHub:** https://github.com/masondelan/selvedge
**PyPI:** https://pypi.org/project/selvedge/
**Category:** Developer Tools

**Description:**
Selvedge is an MCP server for change tracking in AI-era codebases. AI agents call `log_change` as they work to log structured change events — entity path, change type, diff, and reasoning — to a local SQLite database. The reasoning field captures *why* a change was made, directly from the agent's context, before the session ends.

**MCP tools exposed:**
- `log_change` — record a change with entity, diff, and reasoning
- `diff` — history for an entity or entity prefix
- `blame` — most recent change + context for an exact entity
- `history` — filtered history across all entities
- `search` — full-text search across all events
- `changeset` — retrieve all events in a named feature/task group

**Install:** `pip install selvedge`
**License:** MIT
**Language:** Python 3.10+
