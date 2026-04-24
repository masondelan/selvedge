# Product Hunt Launch

**Note:** Product Hunt works best when launched Tuesday–Thursday. You'll want
to line up a few friends to upvote and leave early comments in the first hour —
the algorithm heavily weights early momentum. Schedule for 12:01am PT so you
have the full day.

---

## Tagline (max 60 chars)
Capture the *why* behind every AI code change

*(57 chars)*

---

## Description (shown on listing, ~260 chars recommended)

AI agents make great decisions — and then the session ends and the reasoning
is gone forever. Selvedge is an MCP server that captures intent in the moment,
so you can run `selvedge blame` months later and actually understand why a
change was made.

---

## Topics / Tags
- Developer Tools
- Open Source
- Artificial Intelligence
- MCP
- CLI

---

## Maker first comment
*(Post this yourself within the first 5 minutes of launch — it signals activity and gives voters context)*

Hey PH 👋 — I'm Mason, and I built Selvedge after spending way too long staring at `git blame` telling me "Claude Code" changed a column with a commit message that said "Update schema."

AI agents know *exactly* why they made each decision. They just don't tell anyone, because the session ends and that context evaporates. Six months later your team is reverse-engineering decisions that were perfectly intentional at the time.

Selvedge is an MCP server that agents call as they work. It logs structured change events — entity path, change type, diff, and the reasoning captured in the moment — to a local SQLite database. Then you query it with a CLI.

```bash
$ selvedge blame users.stripe_customer_id

Reasoning: User asked to add Stripe billing — needs customer ID to link
           accounts to subscriptions without storing card details locally.
```

It's MIT, free, `pip install selvedge`, no account needed. Works with Claude Code today.

Would love to hear what other MCP tools you're running alongside this — I'm particularly interested in how people are structuring their CLAUDE.md files for multi-agent workflows.

---

## Gallery images (you'll need to create these)

**Image 1 — Hero:** Terminal screenshot of `selvedge blame` output showing the reasoning field
**Image 2 — Problem:** Side-by-side of `git blame` (no context) vs `selvedge blame` (full reasoning)
**Image 3 — CLI overview:** `selvedge status` or `selvedge diff` showing the table output
**Image 4 — Setup:** The 3-step quickstart (init → MCP config → CLAUDE.md snippet)

*(Use a dark terminal theme — looks better in PH's gallery. Carbon.sh or just a raw screenshot works.)*
