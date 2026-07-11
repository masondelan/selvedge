# Selvedge (dev container feature)

Long-term memory for AI-coded codebases — a `git blame` for AI agents, for the *why*, not just which line which model touched. Captured live, by the agent, as the change happens.

This feature installs the [`selvedge`](https://pypi.org/project/selvedge/) PyPI package into the container image at build time, giving you the `selvedge` CLI and the `selvedge-server` MCP command on PATH for every user.

> **Status: draft — not yet published.** The OCI reference below resolves only after the `publish-features.yml` workflow (workflow_dispatch, run by the maintainer) has pushed the feature to GHCR and the package has been flipped to public visibility.

## Usage

```jsonc
{
    "features": {
        "ghcr.io/devcontainers/features/python:1": {},
        "ghcr.io/masondelan/selvedge/selvedge:1": {}
    },
    // Wires Selvedge into the AI tools detected in the workspace
    // (MCP config entries, agent-instructions block, .selvedge/ init,
    // post-commit hook) without prompting. Documented pairing for
    // devcontainer bootstrap — see `selvedge setup --help`.
    "postCreateCommand": "selvedge setup --non-interactive --yes"
}
```

The `postCreateCommand` line is optional but recommended: image build installs the *binary*; `selvedge setup --non-interactive --yes` does the per-workspace wiring (it must run after the workspace is mounted, which is exactly what `postCreateCommand` is for). Skip it if you prefer to run `selvedge setup` interactively, or wire the MCP entry yourself:

```json
{
    "mcpServers": {
        "selvedge": {
            "command": "selvedge-server"
        }
    }
}
```

## Options

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `version` | string | `latest` | Version of the `selvedge` PyPI package to install (`latest` or an exact version such as `0.3.9`). |

Example with a pinned version:

```json
"features": {
    "ghcr.io/masondelan/selvedge/selvedge:1": { "version": "0.3.9" }
}
```

## Requirements

- `python3` >= 3.10 must exist in the image. Pair with `ghcr.io/devcontainers/features/python` (this feature declares `installsAfter` on it) or use a base image that ships Python.
- If `pipx` is present it is used (isolated venv, `/usr/local/bin` launchers); otherwise a plain `pip install` runs, with a PEP 668 `--break-system-packages` retry for externally-managed system Pythons.

## Feature versioning

The feature's own semver (`1.0.0` in `devcontainer-feature.json`) is independent of the Selvedge package version — it bumps when the *feature* (options, install script) changes. The `version` **option** controls which PyPI release gets installed.
