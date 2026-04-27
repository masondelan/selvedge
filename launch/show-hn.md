# Show HN: Selvedge

HN doesn't render bold, code fences, inline backticks, or bullet lists. It only renders
*italic* (single asterisks) and 2-space-indented monospace blocks. Everything below the
"POST BODY" marker is already formatted for HN — copy from that line down.

---

TITLE (paste into HN's title field):

Show HN: Selvedge – MCP server that captures the reasoning behind AI code changes

URL (paste into HN's URL field):

https://github.com/masondelan/selvedge

---

POST BODY (paste as the first comment after submitting):

I've been using Claude Code heavily for the past year, and I kept running into the same wall: an agent would make a schema change, rename a function, or add a dependency — and six weeks later I had no idea why. git blame tells me *what* changed and *when*. It doesn't tell me *why*. The original prompt that drove the decision evaporated the moment the session ended.

Selvedge is an MCP server that fixes this. AI agents call it as they work to log structured change events, including the reasoning behind each decision. It stores everything locally in SQLite.

  $ selvedge blame users.stripe_customer_id

    users.stripe_customer_id
    Changed     2026-03-14 11:22:01
    Agent       claude-code
    Commit      9c3f441
    Reasoning   User asked to add Stripe billing — needs customer ID to link
                accounts to subscriptions without storing card details locally.

Six months later, that reasoning is still there.

What it does:

- Runs as an MCP server — agents call log_change as they work.

- CLI for querying history: selvedge diff users, selvedge search "billing", selvedge blame payments.amount.

- Git hook integration (selvedge install-hook) auto-links events to commits.

- Migration file importer (selvedge import ./migrations/) to backfill schema history from existing SQL/Alembic files.

- Changeset grouping — tag related changes under a slug like "add-stripe-billing" and query the full scope later.

- Reasoning quality validator — warns if the agent logs something generic like "user request" or "done".

Install:

  pip install selvedge

GitHub: https://github.com/masondelan/selvedge

It's MIT licensed, v0.3.1, and works today with Claude Code. v0.3.1 is a hardening release — connection-with-retry on SQLite lock contention (tested against 8 threads writing at once), an explicit schema_migrations table, structured logging, frozen public API surface. v0.3.0 right before it was a correctness pass that caught bugs like --since 5m meaning 5 months instead of 5 minutes, SQL LIKE not escaping _ in search queries, and CREATE TABLE imports dropping every column-level event.

Would love feedback — especially from anyone who's felt this pain with AI-generated codebases.
