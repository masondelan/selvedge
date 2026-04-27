# Reddit Launch Posts

## Karma strategy (currently ~10 karma)

Most of the AI-focused subs (r/ClaudeAI, r/LocalLLaMA, r/MachineLearning,
r/Python) filter new accounts by karma and/or account age. r/ClaudeAI has
already blocked the post, so the other big ones likely will too.

**Recommended order:**

1. **Post to low-barrier subs first** (r/SideProject, r/coolgithubprojects,
   r/opensource, r/selfhosted). These don't gate on karma, accept launches
   openly, and each one that lands will move karma up meaningfully.

2. **Build karma passively** while those run. Spend 15–20 minutes over a
   couple of days leaving helpful comments in r/ClaudeAI, r/LocalLLaMA, and
   r/cursor — answer questions, share workflow tips, no links. A few
   upvoted comments typically clears 50–100 karma inside a week.

3. **Revisit the gated subs** once you're past ~100 karma and the account
   is a couple of weeks old. The three original posts are kept below.

Rule of thumb for every subreddit: open the sidebar, read the rules, and
check whether self-promotion needs a specific tag/flair or a designated
weekly thread. Getting removed by a mod for a rule violation burns the sub
for weeks — showing up compliant the first time doesn't.

---

# Low-barrier subs (post these first)

---

## r/SideProject

*Friendly to solo launches. No karma gate. Flair the post as "Show off".*

**Title:**

I built Selvedge — captures the "why" behind every AI code change, so future-you isn't reverse-engineering the agent's decisions six months later

**Post:**

Hey r/SideProject 👋

Been using Claude Code daily for the past year and kept running into the same wall: the agent makes a reasoned decision (add a column, rename a function, switch a type), the session ends, and six weeks later I'm staring at `git blame` with a commit message that says "Update schema." The reasoning is gone. The agent knew *exactly* why — it just never got written down anywhere durable.

Selvedge is my fix. It's an MCP server — agents call it as they work to log structured change events, including the reasoning captured from the agent's context before the session ends. Everything stored locally in SQLite. Query it later with a CLI:

```
$ selvedge blame users.stripe_customer_id

  users.stripe_customer_id
  Changed     2026-03-14 11:22:01
  Agent       claude-code
  Reasoning   User asked to add Stripe billing — needs customer ID to link
              accounts to subscriptions without storing card details locally.
```

It's MIT, free, `pip install selvedge`. Works with Claude Code today; any MCP-compatible agent should work.

GitHub: https://github.com/masondelan/selvedge

Solo project, just shipped v0.3.1. Would genuinely love feedback — especially from anyone who's felt this pain with AI-generated code.

---

## r/coolgithubprojects

*Literally exists for sharing GitHub repos. Low barrier, short format.*

**Title:**

Selvedge — MCP server that captures AI agent reasoning before sessions end (Python, MIT)

**Post:**

Open-source MCP server for change tracking in AI-era codebases. AI agents call `log_change` as they work to log what changed, when, which agent, and *why* — capturing intent from the agent's context before the session disappears. All stored in local SQLite, queryable with a CLI (`selvedge blame`, `selvedge diff`, `selvedge search`).

- MIT licensed
- `pip install selvedge`
- Works with Claude Code; any MCP-compatible agent supported
- No cloud, no account, no telemetry

https://github.com/masondelan/selvedge

---

## r/opensource

*OSS-focused framing. Emphasize no-telemetry, MIT, contributions welcome.*

**Title:**

Selvedge — open source MCP server for capturing AI agent change reasoning (Python, MIT, local-only)

**Post:**

I've been building Selvedge, an MCP server for tracking changes in codebases edited by AI agents. Releasing it today at v0.3.1.

**The problem:** AI agents make intentional, context-aware decisions while writing code. When the session ends, that reasoning is gone. `git blame` gives you the commit; the commit message usually gives you nothing. This bites harder the more AI-written code a project accumulates.

**What Selvedge does:** runs as an MCP server. Agents call `log_change` as they work, logging entity path, change type, diff, and *reasoning*. Everything goes into a local SQLite database. You query it with a CLI (`blame`, `diff`, `search`, `history`, `changeset`).

**Why it's worth your time if you care about OSS:**

- MIT licensed, no CLA
- Zero telemetry, zero network calls from the core tool (your change history is yours)
- Single Python dependency tree, PyPI install
- Full test suite (82 tests, including an adversarial-input test file)
- Simple, focused scope — core server + CLI + SQLite, nothing more for now

GitHub: https://github.com/masondelan/selvedge
Install: `pip install selvedge`

PRs and issues welcome. The Phase 3 roadmap (PostgreSQL backend, HTTP REST API for teams) is the obvious place to contribute if you're interested.

---

## r/selfhosted

*The local-first angle is the strongest fit here. Lead with "no cloud".*

**Title:**

Selvedge — local-only change tracker for AI-written code. SQLite, no cloud, no account, MIT

**Post:**

Built this for my own use and figured this sub is the right audience.

**What it is:** an MCP server that AI coding agents (Claude Code, any MCP-compatible agent) call as they work to log structured change events to a **local SQLite database**. Schema changes, function renames, dependency adds — all recorded with a `reasoning` field captured from the agent's context. You then query the history with a CLI months later.

**Why it fits r/selfhosted:**

- All data lives on your machine. The DB file is either in your project directory (`.selvedge/selvedge.db`) or `~/.selvedge/selvedge.db` — that's it.
- Zero network calls from the core tool. No telemetry, no cloud, no account, no API key.
- Works offline. Works air-gapped.
- MIT licensed, Python, `pip install selvedge`.
- If/when you want to share history across a team, the v0.4.0 roadmap has a PostgreSQL backend option — but the SQLite version is fully functional for one-machine use today.

Example:

```
$ selvedge blame payments.amount

  payments.amount
  Changed     2026-02-28 14:05:44
  Agent       claude-code
  Reasoning   Switched from integer cents to DECIMAL(10,2) after user reported
              rounding errors on large international transactions.
```

GitHub: https://github.com/masondelan/selvedge

Would love feedback from anyone self-hosting AI tooling — particularly around DB location, backup patterns, and whether the CLI `--json` output is usable for piping into your own dashboards.

---

# Higher-barrier subs (return once karma is ~100+ and account is a couple weeks old)

---

## r/ClaudeAI

*Blocked at 10 karma. Very likely has a karma + account-age gate.*

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

MIT, v0.3.1, on PyPI: `pip install selvedge`. v0.3.1 is a hardening release (concurrency-safe writes, explicit schema_migrations table, structured logging), right on the heels of v0.3.0 — a correctness pass that fixed a handful of silent-wrong-answer bugs (most notably `--since 5m` used to mean 5 months instead of 5 minutes, contradicting every other CLI convention).

GitHub: https://github.com/masondelan/selvedge

Would love to hear if others have felt this pain — and whether the CLAUDE.md instructions I include work well in your setup or need tweaking.

---

## r/cursor

*Smaller community than ClaudeAI. May let you through at lower karma. Worth trying after 50 karma.*

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

*Likely gated at 100+ karma and account-age. Worth circling back after the first four lower-barrier posts land.*

**Title:** Selvedge – open source MCP server for capturing AI agent change reasoning (the "why" that evaporates when sessions end)

**Post:**

Built something I think this community will find useful: **Selvedge**, an MCP server for change tracking in AI-era codebases.

The problem it solves: AI agents make intentional, reasoned decisions as they write code. When the session ends, that reasoning is gone. Six months later you're staring at `git blame` pointing at your agent with a commit message that says "refactor."

Selvedge runs as an MCP server. Agents call `log_change` as they work, logging entity path, change type, diff, and reasoning. Everything goes into a local SQLite database. You query it with a CLI.

**Why it matters for local/self-hosted setups specifically:** all data stays local. No telemetry, no cloud, no account. The DB lives in your project directory (`.selvedge/selvedge.db`) or globally at `~/.selvedge/`. Works offline, works air-gapped, works with any MCP-compatible agent.

The MCP tools: `log_change`, `diff`, `blame`, `history`, `search`, `changeset`.

MIT licensed, on PyPI: `pip install selvedge` (currently v0.3.1, a hardening release — SQLite-lock retry/backoff, an explicit schema_migrations table, structured logging, frozen public API. v0.3.0 immediately before was the correctness pass with 25 adversarial-input tests).

GitHub: https://github.com/masondelan/selvedge

Phase 3 roadmap includes a PostgreSQL backend option for teams — but the local SQLite version is fully functional today.
