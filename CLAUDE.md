# CLAUDE.md — Selvedge agent instructions

This file is auto-loaded by Claude Code and Cowork on every session. It's the rules and conventions agents follow when working on Selvedge.

For everything else:

- **[`README.md`](README.md)** — user-facing docs (install, quickstart, comparison, "what's new", CLI reference)
- **[`docs/architecture.md`](docs/architecture.md)** — internal architecture, data model, MCP tool reference, CLI reference, full phase plan, non-goals
- **[`CHANGELOG.md`](CHANGELOG.md)** — source of truth for what's actually shipped

---

## Sources of truth

- **What's shipped** → `CHANGELOG.md`. The phase-plan checkboxes in `docs/architecture.md` can drift; trust the changelog when they disagree.
- **Current MCP tool count and shape** → `selvedge/server.py`. Don't infer from `manifest.json` — the bundle can lag the live server.
- **Version string** → `pyproject.toml` AND `selvedge/__init__.py` AND `manifest.json` must all match.

---

## Code conventions

- **No external dependencies beyond the declared ones.** Keep the install footprint small.
- **No LLM calls inside Selvedge core.** Templated, deterministic output only. When a feature design is tempted toward an LLM hop, the PR description must explain how the templated output covers the user need; reviewers reject "we'll add an LLM later if needed." See `docs/architecture.md` cross-cutting risk register for the full rationale.
- **SQLite first, always.** Don't reach for Postgres until Phase 3. SQLite with WAL handles concurrent reads fine.
- **`ChangeEvent` is a dataclass, not Pydantic.** Keep the core dependency-free. MCP serialization uses `to_dict()`.
- **New TypedDict result types must justify themselves.** Before introducing a new MCP tool result shape, check whether an existing one (`LogChangeResult`, `BlameResult`, the auto-generated list-shapes) extends to fit. Prefer extending an existing type to introducing a new one. Every field always populated, never `null` — same convention as `LogChangeResult` / `BlameResult` (empty string / empty list / empty dict for "absent").
- **Every public function has a docstring.** The MCP tool docstrings in `server.py` are user-facing — they appear in agent tool listings and propagate into `manifest.json`.
- **Tests use `tmp_path` fixtures and `SELVEDGE_DB` env var.** Never write to the real DB in tests.
- **Rich for all terminal output.** No bare `print()` in `cli.py`.
- **`--json` flag on every read command.** Machine-readable output is a first-class concern.
- **Type hints everywhere.** Python 3.10+ syntax (`X | Y`, `list[dict]`, etc.).
- **Destructive actions require both interactive consent AND environment-level opt-in.** Any command that can delete events from the store (e.g. `selvedge prune --include-events`, landing in v0.3.10 alongside `.selvedge/config.toml`) must require BOTH a confirmation prompt AND `SELVEDGE_DESTRUCTIVE=1` in the environment. Defends against the cron / non-interactive `--yes` footgun.

---

## Test suite

Tests live in `tests/`. Run with `pytest` from the repo root.

- `test_storage.py` — storage layer
- `test_server.py` — MCP tools (in-process)
- `test_cli.py` — CLI commands
- `test_importers.py` — migration parsers (SQL DDL + Alembic)
- `test_adversarial.py` — locks in the v0.3.0 correctness fixes
- `test_concurrency.py` — multi-threaded writers
- `test_public_api.py` — frozen `__init__.py` surface
- `test_mcp_protocol.py` — boots real `selvedge-server` subprocess and round-trips every tool over stdio

Each phase has a soft test-budget target (see the cross-cutting risk register in `docs/architecture.md`). When a phase exceeds its budget, the release notes call out *why* — typically a perf-regression suite or a new protocol smoke test. The HTTP layer in v0.4.0 must ship with `test_http_protocol.py` parallel to `test_mcp_protocol.py` — release-blocker, not optional.

Never write to the real DB in tests — always set `SELVEDGE_DB` to a `tmp_path` fixture.

---

## Version bump checklist

When the user asks for a version bump:

1. Update `pyproject.toml` AND `selvedge/__init__.py` AND `manifest.json` AND `server.json` AND `.claude-plugin/plugin.json` (all five must match — `server.json` is the official MCP Registry manifest (2025-12-11 schema) and carries the version **twice**: top-level `version` and `packages[0].version`, bump both; it missed v0.3.3 entirely, see CHANGELOG; the CI registry job also syncs both fields from the tag as a backstop. `.claude-plugin/plugin.json` is the Claude Code plugin descriptor added in v0.3.7). Two more version surfaces the plugin added in v0.3.9.2: the launcher pin `SELVEDGE_PLUGIN_PIN` in `bin/selvedge-resolve` must equal that same version, and `npm/package.json`'s **`pypiVersion`** field (NOT its own npm `version`, which is an independent semver line — npm can't hold a four-segment PEP 440 version) pins what `npx selvedge-mcp` fetches. `tests/test_plugin.py::test_version_sync_across_manifests` / `test_launcher_pin_matches_plugin_version` / `test_npm_pin_tracks_server_version` enforce all of this — run them. Then regenerate the bundled skill so it can't drift from `prompt.py`: `selvedge prompt --format skill > skills/selvedge/SKILL.md` (a hygiene test also checks this).
2. **Run `pytest`, `ruff check`, AND `mypy selvedge/` locally** — the GitHub Actions lint job runs all three, and a CI-only mypy failure means your CI badge goes red post-publish (happened on v0.3.4 with the `ToolAnnotations` dict→model issue). Catch it on your machine first.
3. Tag the commit; the PyPI publish workflow runs on tag push (OIDC trusted publisher is pinned to the workflow filename — don't rename that file without updating PyPI config first). After the PyPI job succeeds, the `publish-mcp-registry` job in the same workflow publishes/updates the listing on the official MCP Registry (`registry.modelcontextprotocol.io`) — no manual step.
4. For Smithery: hand-zip the bundle (NOT `mcpb pack` — there's an MCPB-vs-Smithery schema mismatch around per-tool `inputSchema`), then `smithery mcp publish`. The npm shim (`selvedge-mcp`, in `npm/`) is a separate manual `npm publish` — only needed when its code or `pypiVersion` changes; it is currently **unpublished**, so its first publish is what makes `npx selvedge-mcp` work.
5. Add a "What's new in vX.Y.Z" section to `README.md`. Cap at 2 versions — oldest drops off stack-style (so v0.3.2 ship → v0.3.0 drops).

---

## Release notes

- Pull content from `CHANGELOG.md`. Group into Added / Changed / Fixed sections.
- The MCP tool docstrings in `server.py` are user-facing — keep them accurate after any tool changes.

---

## Phase plan maintenance

The phase plan lives in `docs/architecture.md`.

- Source of truth for shipped work is `CHANGELOG.md`, not the phase checkboxes.
- When asked to update the phase plan, read `CHANGELOG.md` and `selvedge/server.py` (for current tool count/names) and compare against the checkboxes. Mark anything shipped as done.
