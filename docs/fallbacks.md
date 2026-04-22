# Fallback mechanisms for log_change coverage

The core risk with Selvedge is that agents forget to call `log_change`. The MCP
server only records what agents explicitly report — it has no visibility into
changes agents make silently.

Three fallback approaches can close the gap. Here's the tradeoff analysis.

---

## Option 1: Git post-commit hook (Phase 2, planned)

**How it works:** A `post-commit` hook runs after every `git commit`. It reads
`git rev-parse HEAD` to get the new commit hash, then backfills `git_commit` on
any Selvedge events whose `timestamp` falls within a short window before the
commit (e.g. within the last 10 minutes and have an empty `git_commit` field).

**What it solves:** Events that were logged correctly but lack a `git_commit`
hash get linked automatically. Makes `selvedge blame` answers more complete.

**What it doesn't solve:** Events the agent forgot to log entirely.

**Tradeoffs:**
- Low friction — runs silently in the background
- Works even if the agent never sets `git_commit` explicitly
- False-positive risk: events from a different project might match the window
  if you commit quickly across repos (mitigated by filtering on `project`)
- Does not create new events — only links existing ones

**Implementation complexity:** Low. ~20 lines of shell.

---

## Option 2: File watcher (not recommended for Phase 2)

**How it works:** A background daemon (e.g. using `watchdog`) monitors the
project directory for file modifications and auto-generates Selvedge events
when files change.

**What it solves:** Catches every file change, even ones the agent didn't log.

**What it doesn't solve:** The `reasoning` field — the entire value proposition
of Selvedge. A file watcher can record *what* changed but has no access to
*why*. The resulting events would be structurally valid but hollow.

**Tradeoffs:**
- High noise: every keystroke, save, formatter run, and temp file generates an
  event. The signal-to-noise ratio is terrible without heavy filtering.
- Reasoning is always empty. You've recreated `git log` at higher cost.
- Adds a persistent daemon as a dependency — bad for a zero-config tool.
- Platform-specific (macOS FSEvents vs Linux inotify vs Windows ReadDirectoryChangesW).

**Verdict:** Not worth it. The `reasoning` field is why Selvedge exists.
A file watcher produces events without reasoning — the exact problem Selvedge
is trying to solve.

---

## Option 3: Migration file parser (`selvedge import`, Phase 2, planned)

**How it works:** `selvedge import migrations/` parses Alembic, Liquibase, or
raw SQL migration files and creates ChangeEvents from the schema operations it
finds (CREATE TABLE, ADD COLUMN, DROP COLUMN, etc.).

**What it solves:** Backfills schema history for projects that existed before
Selvedge was introduced. Day-one coverage without retroactive manual logging.

**What it doesn't solve:** `reasoning` — migration files encode what changed,
not why. But for schema history the structured entity data (column names, types,
constraints) is still valuable even without reasoning. And you can add reasoning
manually with `selvedge log` for the migrations that matter.

**Tradeoffs:**
- One-time backfill, not ongoing coverage
- Alembic/Liquibase parsing is well-defined; raw SQL requires more robust parsing
- Works well in combination with git hooks: import fills history, hooks link commits

**Implementation complexity:** Medium. ~200 lines of Python + SQL parsing logic.

---

## Recommended approach for Phase 2

Implement (1) and (3) together:

1. **Git post-commit hook** for ongoing automated linking
2. **`selvedge import`** for backfilling schema history

Leave file watchers out entirely. Invest in better agent prompting instead —
a well-written CLAUDE.md instruction is worth more than any automated fallback
that bypasses the `reasoning` field.

---

## Improving agent compliance through prompting

The highest-leverage intervention is the system prompt. The current recommended
instruction (from `CLAUDE.md`) is:

```
Call selvedge.log_change immediately after adding, modifying, or removing
any DB column, table, function, API endpoint, dependency, or env variable.
Set `reasoning` to the user's original request or the problem being solved.
```

To increase compliance:
- Move `selvedge.log_change` to the **first bullet** in any instruction list
- Add explicit examples of what constitutes a "meaningful change"
- Use `selvedge stats` periodically to measure and report back to the agent on
  its own coverage ratio — agents respond well to self-monitoring prompts
- Frame it as a requirement, not a suggestion: *"You must call..."* not *"You should..."*
