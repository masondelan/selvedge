# Show HN: Selvedge – capture the *why* behind AI code changes

**Title:** Show HN: Selvedge – MCP server that captures the reasoning behind AI code changes

---

**Post body:**

I've been using Claude Code heavily for the past year, and I kept running into the same wall: an agent would make a schema change, rename a function, or add a dependency — and six weeks later I had no idea why. `git blame` tells me *what* changed and *when*. It doesn't tell me *why*. The original prompt that drove the decision evaporated the moment the session ended.

Selvedge is an MCP server that fixes this. AI agents call it as they work to log structured change events, including the reasoning behind each decision. It stores everything locally in SQLite.

```bash
$ selvedge blame users.stripe_customer_id

  users.stripe_customer_id
  Changed     2026-03-14 11:22:01
  Agent       claude-code
  Commit      9c3f441
  Reasoning   User asked to add Stripe billing — needs customer ID to link
              accounts to subscriptions without storing card details locally.
```

Six months later, that reasoning is still there.

**What it does:**
- Runs as an MCP server — agents call `log_change` as they work
- CLI for querying history: `selvedge diff users`, `selvedge search "billing"`, `selvedge blame payments.amount`
- Git hook integration (`selvedge install-hook`) auto-links events to commits
- Migration file importer (`selvedge import ./migrations/`) to backfill schema history from existing SQL/Alembic files
- Changeset grouping — tag related changes under a slug like `"add-stripe-billing"` and query the full scope later
- Reasoning quality validator — warns if the agent logs something generic like "user request" or "done"

**Install:**
```bash
pip install selvedge
```

GitHub: https://github.com/masondelan/selvedge

It's MIT licensed, v0.2.1, and works today with Claude Code. Would love feedback — especially from anyone who's felt this pain with AI-generated codebases.
