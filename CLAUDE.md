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
- **SQLite first, always.** Don't reach for Postgres until Phase 3. SQLite with WAL handles concurrent reads fine.
- **`ChangeEvent` is a dataclass, not Pydantic.** Keep the core dependency-free. MCP serialization uses `to_dict()`.
- **Every public function has a docstring.** The MCP tool docstrings in `server.py` are user-facing — they appear in agent tool listings and propagate into `manifest.json`.
- **Tests use `tmp_path` fixtures and `SELVEDGE_DB` env var.** Never write to the real DB in tests.
- **Rich for all terminal output.** No bare `print()` in `cli.py`.
- **`--json` flag on every read command.** Machine-readable output is a first-class concern.
- **Type hints everywhere.** Python 3.10+ syntax (`X | Y`, `list[dict]`, etc.).

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

Never write to the real DB in tests — always set `SELVEDGE_DB` to a `tmp_path` fixture.

---

## Version bump checklist

When the user asks for a version bump:

1. Update `pyproject.toml` AND `selvedge/__init__.py` AND `manifest.json` (all three must match).
2. Tag the commit; the PyPI publish workflow runs on tag push (OIDC trusted publisher is pinned to the workflow filename — don't rename that file without updating PyPI config first).
3. For Smithery: hand-zip the bundle (NOT `mcpb pack` — there's an MCPB-vs-Smithery schema mismatch around per-tool `inputSchema`), then `smithery mcp publish`.
4. Add a "What's new in vX.Y.Z" section to `README.md`. Cap at 2 versions — oldest drops off stack-style (so v0.3.2 ship → v0.3.0 drops).

---

## Release notes

- Pull content from `CHANGELOG.md`. Group into Added / Changed / Fixed sections.
- The MCP tool docstrings in `server.py` are user-facing — keep them accurate after any tool changes.

---

## Phase plan maintenance

The phase plan lives in `docs/architecture.md`.

- Source of truth for shipped work is `CHANGELOG.md`, not the phase checkboxes.
- When asked to update the phase plan, read `CHANGELOG.md` and `selvedge/server.py` (for current tool count/names) and compare against the checkboxes. Mark anything shipped as done.

---

## Scheduled recurring tasks (managed by Cowork)

- **Weekly phase-plan drift check** — compare `CHANGELOG.md` against the phase plan in `docs/architecture.md` and flag any shipped items still showing as unchecked.
- **Weekly coverage report** — run `scripts/coverage_check.py` against the git log and report the `log_change` call ratio per commit.
