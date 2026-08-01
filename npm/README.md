# selvedge-mcp

Long-term memory for AI-coded codebases — a `git blame` for AI agents, for the *why*, not just which line which model touched. Captured live, by the agent, as the change happens.

This package is a **thin npx shim** for the [Selvedge](https://selvedge.sh) MCP server, which is a Python package on PyPI ([`selvedge`](https://pypi.org/project/selvedge/)). It exists for the many MCP hosts and quickstart docs that assume `npx`-style one-liners — no Node code beyond ~150 lines of runner dispatch, **zero npm dependencies**.

> **Why `selvedge-mcp` and not `selvedge`?** The npm name `selvedge` is taken by an unrelated TypeScript prompt-DSL package (viksit/selvedge). The Python package, CLI, and MCP server name remain `selvedge` everywhere else.

## Usage

In your MCP client config (Claude Code, Claude Desktop, Cursor, etc.):

```json
{
  "mcpServers": {
    "selvedge": {
      "command": "npx",
      "args": ["-y", "selvedge-mcp"]
    }
  }
}
```

Or run it directly:

```bash
npx -y selvedge-mcp
```

## What it actually does

The shim never reimplements the server. It finds a Python runner on PATH and delegates, in this order:

1. **uvx** — `uvx --from selvedge==<version> selvedge-server`
2. **pipx** — `pipx run --spec selvedge==<version> selvedge-server`
3. **existing install** — a `selvedge-server` already on PATH (from `pip install selvedge`)

If none of those exist it exits 1 with install guidance on stderr. The shim writes nothing to stdout — stdout belongs to the MCP stdio protocol.

### Version pinning

The PyPI version is pinned by this package's `pypiVersion` field — **not** by its own npm `version`, which is an independent semver line (npm cannot express a four-segment PEP 440 version). So `npx selvedge-mcp` is reproducible and always fetches the pinned Selvedge release. Override with the `SELVEDGE_VERSION` env var:

```bash
SELVEDGE_VERSION=latest npx -y selvedge-mcp   # track the newest PyPI release
SELVEDGE_VERSION=0.3.8 npx -y selvedge-mcp    # pin an older one
```

(When falling back to a pre-installed `selvedge-server`, whatever version is installed runs — the pin only applies to uvx/pipx.)

## Requirements

- Node >= 18 (for the shim itself)
- Python >= 3.10 plus **one of**: [uv](https://docs.astral.sh/uv/), [pipx](https://pipx.pypa.io/), or a `pip install selvedge`

If you already have Python tooling set up, you don't need this package at all — put `selvedge-server` (or `uvx --from selvedge selvedge-server`) directly in your MCP config.

## Publishing (maintainer notes)

This package is **published manually by the maintainer**, not by CI:

```bash
cd npm
npm publish
```

Keep `version` in `package.json` in lockstep with the PyPI release it pins (the shim derives its default `selvedge==X.Y.Z` pin from it).

## Links

- Homepage: https://selvedge.sh
- Source: https://github.com/masondelan/selvedge (this package lives in `npm/`)
- PyPI: https://pypi.org/project/selvedge/
- Issues: https://github.com/masondelan/selvedge/issues

MIT © Mason Delan
