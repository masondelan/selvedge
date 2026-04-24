# Reddit Launch Posts

---

## r/ClaudeAI

**Title:** I built an MCP server that captures the *why* behind every Claude Code change – so you're not debugging mystery decisions 6 months later

**Post:**

If you use Claude Code heavily, you've probably hit this: the agent makes a schema change or adds a dependency, you move on, and weeks later you have no idea why that decision was made. Git blame tells you it was Claude. The commit message says "Update schema." The session that actually knew the reasoning is gone.

I built **Selvedge** to fix this. It's an MCP server that Claude Code calls as it works to log structured change events — what changed, when, and importantly: *why*, captured from the agent's context before the session ends.

After setup, you get this:

```bash
$ selvedge blame users.stripe_customer_id

  users.stripe_customer_id
  Changed     2026-03-14 11:22:01
  Agent       claude-code
  Reasoning   User asked to add Stripe billing — needs customer ID to link
              accounts to subscriptions without storing card details locally.
```

**Setup is quick:**

1. `pip install selvedge && selvedge init`
2. Add `selvedge-server` to your Claude Code MCP config
3. Add ~3 lines to your project's CLAUDE.md telling the agent to log changes

Then you get a full queryable history:
- `selvedge diff users --since 30d` — everything that changed in a table
- `selvedge blame payments.amount` — last change + why
- `selvedge search "billing"` — full-text search across all reasoning
- `selvedge stats` — how often your agent is actually logging

It also has git hook integration to auto-link events to commits, and a migration file importer to backfill existing schema history.

MIT, v0.2.1, on PyPI: `pip install selvedge`

GitHub: https://github.com/masondelan/selvedge

Would love to hear if others have felt this pain — and whether the CLAUDE.md instructions I include work well in your setup or need tweaking.

---

## r/cursor

**Title:** Built an open-source MCP server for capturing AI agent reasoning – sick of "why did the agent do this?" 6 months later

**Post:**

Problem I kept hitting: AI agent makes a change, session ends, reasoning evaporates. `git blame` gives you the commit. The commit message gives you nothing.

Built **Selvedge** — an MCP server that agents call as they work to log structured change events, including the *why* behind each decision captured in the moment.

```bash
$ selvedge blame payments.amount

  payments.amount
  Changed     2026-02-28 14:05:44
  Agent       claude-code
  Commit      7f2c019
  Reasoning   Switched from integer cents to DECIMAL(10,2) after user reported
              rounding errors on large international transactions.
```

Works with any MCP-compatible agent. CLI for querying: `selvedge diff`, `selvedge blame`, `selvedge search`, `selvedge history`.

`pip install selvedge` — MIT, local SQLite, no account needed.

GitHub: https://github.com/masondelan/selvedge

If anyone's using this with Cursor, I'd love to know if the MCP config works cleanly — still figuring out the best CLAUDE.md equivalent for Cursor projects.

---

## r/LocalLLaMA

**Title:** Selvedge – open source MCP server for capturing AI agent change reasoning (the "why" that evaporates when sessions end)

**Post:**

Built something I think this community will find useful: **Selvedge**, an MCP server for change tracking in AI-era codebases.

The problem it solves: AI agents make intentional, reasoned decisions as they write code. When the session ends, that reasoning is gone. Six months later you're staring at `git blame` pointing at your agent with a commit message that says "refactor."

Selvedge runs as an MCP server. Agents call `log_change` as they work, logging entity path, change type, diff, and reasoning. Everything goes into a local SQLite database. You query it with a CLI.

**Why it matters for local/self-hosted setups specifically:** all data stays local. No telemetry, no cloud, no account. The DB lives in your project directory (`.selvedge/selvedge.db`) or globally at `~/.selvedge/`. Works offline, works air-gapped, works with any MCP-compatible agent.

The MCP tools: `log_change`, `diff`, `blame`, `history`, `search`, `changeset`.

MIT licensed, on PyPI: `pip install selvedge`

GitHub: https://github.com/masondelan/selvedge

Phase 3 roadmap includes a PostgreSQL backend option for teams — but the local SQLite version is fully functional today.
