# Telemetry

Selvedge is local-first. Nothing about your code, your repos, or your
agents' reasoning ever leaves your machine — and by default, **nothing
leaves your machine at all**.

Telemetry is strictly opt-in. Until you explicitly enable it, Selvedge
sends zero bytes. There is no phone-home on install, no first-run
prompt that defaults to yes, no "anonymous by default" fine print.

## What gets sent (after you opt in)

One heartbeat, at most once per 24 hours, from the CLI or the MCP
server — whichever runs first that day. The complete payload:

```json
{
  "schema": 1,
  "install_id": "0b6dcb1e-7c58-4a2e-9f39-2f6e3ac0d8a1",
  "version": "0.3.9.1",
  "python": "3.12",
  "os": "darwin",
  "arch": "arm64",
  "source": "cli"
}
```

That is the entire schema. There are no other fields, no batching of
extra events, and no per-command tracking.

- `install_id` is a random UUID minted on your machine when you run
  `selvedge telemetry enable`. It is not derived from hardware,
  hostname, username, MAC address, or anything else identifying.
  Disabling telemetry deletes it; re-enabling mints a fresh one, so
  nothing links usage across consent periods.
- `source` is `"cli"` or `"server"` — the only usage dimension collected.
- No IP addresses are stored by the receiver (see below).

The heartbeat exists to answer exactly one question — *how many
installs were active this week* — and is deliberately incapable of
answering any other.

## What is never sent

Repo names, file paths, entity paths, reasoning text, change events,
command arguments, environment variables, git metadata, timestamps of
your work, error messages, stack traces. The payload above is
allowlist-complete: if it isn't in that JSON block, it isn't sent.

## Controls

```
selvedge telemetry            # show status + the exact payload
selvedge telemetry enable     # opt in
selvedge telemetry disable    # opt out, delete the install ID
```

Environment variables (useful for fleets and containers):

| Variable | Effect |
|---|---|
| `SELVEDGE_TELEMETRY=1` | opt in without a consent file |
| `SELVEDGE_TELEMETRY=0` | kill switch — beats a recorded opt-in |
| `SELVEDGE_NO_TELEMETRY=1` | same kill switch, `NO_`-style |
| `SELVEDGE_TELEMETRY_URL` | point the heartbeat at your own receiver |
| `CI` | truthy `CI` always suppresses sending, even when opted in |

Dev installs (versions with `.dev`, `rc`, `a`, `b`, or local segments)
never send, even when opted in.

Consent and state live in `~/.selvedge/telemetry.json` — user-global,
never inside your repos, readable JSON you can inspect or delete.

## Reliability posture

The sender is `selvedge/telemetry.py`: stdlib-only, fire-and-forget on
a daemon thread, 2-second timeout, no retries, and it never raises and
never prints. A dead endpoint, an unwritable home directory, or a
blocked network can never affect the command you actually ran, and can
never write noise into the MCP stdio channel.

## The receiver

The endpoint is `https://telemetry.selvedge.sh/v1/ping` — a Cloudflare
Worker whose full source ships in this repo at
`infra/telemetry-worker/`, so the receiving side is exactly as
auditable as the sending side. It validates the schema, writes the
payload to Cloudflare Workers Analytics Engine (which retains data
points for ~90 days), and stores nothing else. IP addresses are not
written to the dataset. There are no third-party analytics vendors
involved.
