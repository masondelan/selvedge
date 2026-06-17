# Using Selvedge with coding agents

Selvedge is an **MCP server first** — that's the default integration and what
`selvedge setup` wires up. It also exposes the *same* local store through a
`selvedge` CLI, and for an agent that has a shell, being able to reach for the
CLI directly is valuable. This page covers when an agent uses which.

## Two paths to the same store

- **MCP server (default).** The agent calls Selvedge's tools (`log_change`,
  `prior_attempts`, `diff`, `blame`, …). Best for discoverability and for hosts
  without a shell. This is what most users install.
- **CLI.** The agent runs `selvedge …` on its shell. Useful when the MCP server
  isn't loaded, in a shell-only subagent, or simply to keep context light: the
  CLI costs nothing in the context window until it's called, whereas the MCP
  tool definitions sit in context (~2.6–3.4k tokens) for the whole session
  whether used or not. Models are also fluent at shell invocations.

Neither replaces the other. MCP is the front door; the CLI is there so a
shell-having agent is never blocked and can choose the cheaper path.

> Measure the MCP footprint yourself: `python scripts/schema_tax.py` (add
> `--backend api` for the authoritative count via Anthropic's `count_tokens`).

## Letting an agent use the CLI

After `pip install selvedge` the `selvedge` command is on PATH, so any agent
with shell access can already call it. To make the agent actually *reach for
it*, the canonical agent-instructions block (installed by `selvedge setup` /
`selvedge prompt`) names the CLI equivalents alongside the MCP tools — see
[`agent-block-cli.md`](agent-block-cli.md). The two operations that matter:

**Check before editing a meaningful entity** (DB column, function contract, env
var, dependency, auth/session mechanism):

```bash
selvedge blame users.auth_token      # most recent change + the reasoning behind it
selvedge diff users.auth_token       # full change history for the entity
```

**Log the why after a meaningful change:**

```bash
selvedge log users.auth_token retype \
  --reasoning "Per-user DB token couldn't be revoked without a write; moved to short-lived JWTs."
```

`change_type` is one of `add, remove, modify, rename, retype, create, delete,
index_add, index_remove, migrate`; for a rename add `--rename-from OLD`. Add
`--json` to any read command for machine-readable output.

## Keeping the footprint honest

`scripts/schema_tax.py` doubles as a CI drift guard: run it with `--max <N>` to
fail the build if a new tool or a longer description pushes the MCP footprint
past a budget you set. Any context-cost figure Selvedge cites then stays a
verified fact, not a stale claim.
