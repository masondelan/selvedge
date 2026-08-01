# Security Policy

## Supported versions

Only the latest 0.3.x release receives security fixes. Older minor
versions are not patched — upgrade to the latest release before
reporting.

| Version | Supported |
|---------|-----------|
| latest 0.3.x | ✅ |
| < latest 0.3.x | ❌ |

## Reporting a vulnerability

Please report vulnerabilities privately — do not open a public issue.

- **Preferred:** [GitHub private vulnerability reporting](https://github.com/masondelan/selvedge/security/advisories/new)
  on `masondelan/selvedge`.
- **Email:** hello@selvedge.sh

You will receive an acknowledgement within 72 hours.

## Scope and threat model

Selvedge is a local-only tool:

- All data lives in a SQLite file under `.selvedge/` next to your code
  (or `~/.selvedge/` as a fallback). No code, paths, diffs, or reasoning text
  ever leaves your machine. Two outbound requests exist, neither carrying any
  of that: a version check against PyPI, on by default
  (`SELVEDGE_NO_UPDATE_CHECK=1` disables it), and an anonymous heartbeat that
  is off unless you enable it (see `docs/telemetry.md`).
- No network listeners. An opt-in HTTP layer is planned for v0.4.0;
  until then there is no remote attack surface.
- No LLM calls in core — output is templated and deterministic.
- Three runtime dependencies: `mcp`, `click`, `rich`.

The most interesting attack surface is input handling: SQL handling of
agent-supplied strings (everything an MCP client sends is untrusted)
and path canonicalization of entity paths. Reports in those areas are
especially welcome.
