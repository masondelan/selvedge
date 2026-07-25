---
name: selvedge
description: Use before editing a tracked entity (a DB column, table, function, API endpoint, dependency, or env var) and after any substantive change. Call prior_attempts first to learn whether the change was tried and reverted before, then log_change to record what changed and why. Selvedge is this project's change memory — a local MCP server and CLI, no LLM calls.
---

## Selvedge — change tracking

You have access to Selvedge (MCP server: `selvedge`) for change tracking.

**Rules:**

- Call `selvedge.log_change` immediately after adding, modifying, or
  removing any DB column, table, function, API endpoint, dependency,
  or env variable.
- Set `reasoning` to the user's original request or the problem being
  solved. Write at least one full sentence — the server will warn on
  empty, very short, or generic values like "user request" or "done".
  Good example: "User asked to add 2FA — needs phone number to send
  SMS verification codes."
- Set `agent` to the tool you're using, e.g. "claude-code", "cursor",
  or "codex".
- Set `session_id` if you have access to the current session/conversation ID.
- Set `git_commit` to the commit hash once you know it.
- For multi-entity changes (e.g. adding a whole feature), set a shared
  `changeset_id` on all related `log_change` calls — use a short slug
  like `add-stripe-billing`. This lets anyone query the full scope of
  the change with `selvedge.changeset()`.
- Before editing an entity, call `selvedge.prior_attempts` on it — if the
  same change was tried before and reverted, you'll see the prior
  reasoning and why it was rejected, and can change your plan instead of
  repeating a rejected approach. (If the Selvedge PreToolUse hook is
  installed, this check is enforced: schema/migration edits are blocked
  until prior_attempts has been consulted this session.)
- A reverted decision is not a permanent ban. If the constraint that
  killed it no longer holds, re-open it explicitly with
  `change_type="supersede"` (never re-apply a reverted change without
  superseding it first). Use `change_type="revert"` when you roll a
  change back — clearer than a plain remove.
- Then call `selvedge.diff` or `selvedge.blame` for the entity's broader
  history before conflicting with past decisions.

**The same operations are on your shell.** Selvedge is MCP-first, but the
identical local store is also a CLI (`selvedge` is on your PATH after
install). When the MCP server isn't loaded, you're in a shell-only subagent,
or you just want to keep context light, use the equivalents:

- Check an entity first: `selvedge prior-attempts <entity>` (was it tried and
  reverted before?), then `selvedge blame <entity>` / `selvedge diff <entity>`
  for its broader history.
- Log a change: `selvedge log <entity> <change_type> --reasoning "<why>"`
  (change_type: add, remove, modify, rename, retype, create, delete,
  index_add, index_remove, migrate, revert, supersede; for a rename add
  `--rename-from <old>`).
- Re-open a reverted decision: `selvedge supersede <entity> --reasoning "<why>"`.
- Find things: `selvedge search "<query>"`, `selvedge history --since 7d`,
  `selvedge stale` (decisions now due for a revisit).

Add `--json` to any read command; `selvedge <command> --help` gives detail on
demand.
