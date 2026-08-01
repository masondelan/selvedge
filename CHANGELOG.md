# Changelog

All notable changes to Selvedge are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Selvedge uses [semantic versioning](https://semver.org/).

---

## [0.3.9.3] — 2026-08-01

**Unbreaks `pip install selvedge`, plus a full code-quality pass.** `mcp` 2.0.0
(2026-07-28) removed `mcp.server.fastmcp`, and Selvedge declared `mcp>=1.0.0`
with no upper bound — so every fresh install since then resolved 2.0.0 and
`selvedge-server` died at import. That pin is the reason to take this release.
It arrives alongside a full code-quality pass. Nine parallel reviews across correctness,
concurrency, security, performance, API consistency, test quality, code health
and packaging, with every finding put through an adversarial verification pass
before it was acted on. Seventeen confirmed defects fixed; the rest filed. No
schema change and no tool-surface change — every fix below is behavioral or
internal, so this is a drop-in for anyone on 0.3.9.x.

### Fixed

- **`pip install selvedge` works again.** `mcp>=1.0.0` had no upper bound, so a
  fresh install resolved `mcp` 2.0.0 (released 2026-07-28), which removed
  `mcp.server.fastmcp` — `selvedge-server` failed at import. Pinned to `<2.0.0`;
  the 2.x migration is tracked separately.
- **`.selvedge/config.toml` is honored on Python 3.10.** `tomllib` is stdlib
  only from 3.11 and the `tomli` fallback was never declared, so a production
  install on the declared floor silently ignored the file — including a user
  narrowing the hook's watch globs, who then kept getting blocked on files they
  had explicitly opted out of.
- **Schema-qualified DDL records the right entity.** `CREATE TABLE public.users`
  recorded an entity named `public` and lost every column event;
  `ALTER TABLE public.users ADD COLUMN` matched nothing and was dropped
  silently. Both are the default spelling in Postgres dumps.
- **`CREATE INDEX` and `DROP INDEX` share one entity**, so an index add can
  finally be closed by its own removal — they were recorded against the table
  and the index name respectively, so `prior_attempts` reported a dropped index
  as still active.
- **The telemetry heartbeat actually sends.** It stamped its 24-hour rate limit
  *before* sending and the daemon thread died at interpreter exit first —
  measured, 1 run in 20 reached the socket while 20 in 20 wrote the stamp.
- **`.selvedge/hook_sessions/` is gitignored** (it carries agent session ids);
  only `backups/` was covered.
- **Oversized inputs return the documented error shape** instead of raising a
  raw `sqlite3.OperationalError` or `OverflowError`.
- **The hook no longer blocks read-only commands.** The Bash gate treated every
  path-shaped token as a write, so `cat`, `git diff`, `git log`, `pytest`,
  `ruff check` and `mypy` against a watched path all blocked. Worse, the
  remediation the block message prints — `selvedge prior-attempts '<path>'` —
  is itself a Bash command containing that path, so following the instruction
  produced the same block, with no CLI-only way out (the advertised
  `SELVEDGE_HOOK_DISABLE=1` does nothing when written into the command, since
  the hook reads its own process environment). A Bash call now counts as
  touching a path only when the command plausibly writes to one.
- **The hook stops treating other repositories' files as yours.** `_relative_to`
  ended in `lstrip("./")`, which strips leading *characters* rather than a
  prefix — so `../x` became `x`, an absolute path in another checkout became a
  relative-looking one, and `.hidden/schema.sql` became `hidden/schema.sql`.
  That last one made enforcement silently inert for dotfile paths, because the
  hook and `canonicalize_entity_path` disagreed about the entity's identity.
- **Commented-out DDL no longer creates phantom history.** The SQL importer
  split on bare `;` over raw text, so `-- ALTER TABLE users DROP COLUMN email;`
  in a comment produced a real `remove` event, and the column then read as
  `reverted` — which gates the hook. Same for `/* ... */` and for a semicolon
  inside a string literal.
- **`import --from-git` no longer invents rollbacks.** Any commit whose message
  merely contained "revert" seeded `revert` events on every file it touched, so
  `feat(ui): add a revert button to the toolbar` marked `app.py` reverted.
  Detection is now anchored on git's own conventions.
- **`blame`, `diff` and `history` find entities logged under a different
  spelling.** Writes canonicalized `entity_path` but these three reads compared
  the caller's raw string against the stored value, so the workflow Selvedge
  itself installs — `prior_attempts <entity>`, then `blame <entity>` — returned
  a hit followed by two misses on a `./`-prefixed path.
- **Concurrent upgrades no longer crash.** `apply_migrations` decided a
  migration was pending outside any write lock and used a deferred `BEGIN`, so
  N processes opening a database with the same migration pending all re-ran the
  `ALTER TABLE`; the losers died with `duplicate column name`, which is not a
  lock error and so escaped the retry ladder and the `SelvedgeStorage`
  constructor. Measured at 3 of 6 trials with 8 contenders before the fix.
- **An interrupted backup can no longer destroy your last good one.**
  `VACUUM INTO` wrote straight to the final `selvedge-*.db` name with no
  cleanup on failure, so a truncated snapshot was reported by `doctor` as a
  healthy recent backup and consumed a rotation slot — evicting the only
  restorable file.
- **Timestamps are fixed-width, so ordering is correct.** Omitting the
  fractional part at zero microseconds meant `...12:00:00Z` sorted *after*
  `...12:00:00.500000Z`, because `.` sorts below `Z`. Mixed precision is
  routine: git and Agent Trace imports are second-precision. The visible
  symptom was `blame` returning the older event and a re-opened decision
  reading back as `reverted`.
- **`selvedge setup` no longer deletes surrounding content from `CLAUDE.md`.**
  A stray `<!-- selvedge:start -->` earlier in the file — someone documenting
  the convention, or a merge that dropped an end marker — made the managed
  block match span back to it, silently removing everything in between.
  Duplicate blocks now converge instead of leaving a stale one in place.
- **`6M` no longer means six minutes.** Case-insensitive unit matching resolved
  the usual spelling of "six months" to minutes, so `--since 6M` showed six
  minutes and a `revisit_after` of `6M` fell due immediately and stayed
  overdue. `90D` still works; only the ambiguous bare `M` is rejected.
- **`blame --json` emits JSON on a miss** instead of Rich markup on stdout —
  it was the one command in the CLI whose `--json` output could fail to parse.
- **`selvedge log` and `selvedge supersede` count toward `selvedge stats`.**
  CLI reads fed the coverage denominator while CLI writes fed nothing, so a
  correctly-logged CLI session reported 0% coverage and the report advised the
  user to do what they had just done.
- **`search` covers the four columns it documents.** It also matched
  `change_type`, undocumented, so searching "revert" returned every
  revert-typed event rather than events discussing one.
- **`diff` and `history` describe `entity_path` accurately.** They called it a
  "path prefix"; it is a dotted-segment prefix, so `src/` matched nothing and
  `src/auth.py` did not cover `src/auth.py::login` — a confident empty answer
  to "what happened to this file?".
- **`SECURITY.md` no longer claims nothing leaves your machine.** The
  default-on PyPI version check made that false. The narrower claim — no code,
  paths, diffs, or reasoning text ever leaves, and the two outbound requests
  carry none of it — is both true and stronger.

### Changed

- **Performance: the entity-path read is indexed.** `entity_path = ? OR
  entity_path LIKE ?` — the query behind `prior_attempts`, `blame`, `diff`,
  `history` and the hook — was full-scanning `events`, because `idx_entity_path`
  is BINARY-collated and SQLite's default `LIKE` is case-insensitive, so the
  planner could never use it. A NOCASE-collated index gives it a MULTI-INDEX OR.
  Measured 7.409 ms → 0.347 ms per query at 100k events; end-to-end hook p50 had
  been ~4.9 s at 890k events. Installs itself on existing databases; no
  migration needed.
- Coverage `omit` no longer lists a file that does not exist, and the
  `Python :: 3.13` classifier matches what CI has been testing all along.

### Security

- **The published composite action no longer interpolates its inputs into
  shell scripts.** All six `${{ inputs.* }}` references sat directly in `run:`
  text, and `${{ }}` expansion is textual and pre-shell — so a consumer wiring
  a dynamic value in could inject commands that run in *their* runner with
  *their* `GITHUB_TOKEN` in scope. They now pass through `env:` as quoted
  shell variables.
- **`mcp-publisher` is pinned and checksum-verified.** It was fetched from a
  `releases/latest` URL and executed in a job holding OIDC `id-token: write`
  for the MCP Registry namespace, automatically on every tag push, with no
  human between the download and the privileged call. Third-party actions are
  pinned to commit SHAs, and `test.yml` declares least-privilege permissions.
- **The npm shim validates `SELVEDGE_VERSION`.** On Windows a `.cmd`/`.bat`
  runner is spawned with `shell: true`, and Node does not escape arguments in
  shell mode, so a value of `1.0 & calc` in a shared `.mcp.json` would execute
  the injected command.

### Added

- **`selvedge status --json`** — the last read command without it, while the
  docs and the shipped agent prompt both say to add `--json` to any read.
- `tests/` grew from 739 to 826, concentrated on guards a mutation pass showed
  were executed but unasserted, plus the first multi-writer test of the
  pending-migration path and the first structural (`EXPLAIN QUERY PLAN`) index
  guard.
- Two accepted risks written into the cross-cutting register with their
  reasoning: the hook's fail-open posture on unrecognized writes, and verbatim
  reasoning text living in a store that gets committed.

## [0.3.9.2] — 2026-07-23

**The Claude Code plugin, from scaffolding to first-class.** v0.3.7 registered
the repo as a self-hosted plugin marketplace but shipped only metadata — the
plugin assumed `selvedge-server` was already on your `PATH`, which is why the
README demoted it to "(alternative)" and kept `selvedge setup` as the
recommended path. This release makes `/plugin install selvedge@selvedge` a
complete, zero-prior-install setup: a bootstrapping launcher, the
agent-instructions as a bundled skill, the PreToolUse enforcement hook, and
slash commands. **No store, schema, or tool-surface change** — the MCP server is
byte-identical to 0.3.9.1. Drop-in for anyone on 0.3.9.x.

### Added

- **A launcher, so the plugin needs nothing preinstalled.**
  `bin/selvedge-resolve` (POSIX sh) resolves how to run Selvedge's console
  scripts in order: the entrypoint already on `PATH` → `uvx --from
  selvedge==<pin>` → `pipx run --spec selvedge==<pin>` → install guidance on
  stderr, exit 1. Three thin wrappers hang off it — `selvedge-plugin-server`
  (the MCP command in `.mcp.json`), `selvedge-plugin-hook` (the hook command),
  and `selvedge-cli` (what the slash commands call). It mirrors the npm shim's
  invariants exactly: nothing is ever written to stdout (that channel belongs to
  the MCP protocol), the version is pinned to `plugin.json`'s so installs are
  reproducible, and `SELVEDGE_VERSION` overrides the pin (`latest` unpins). The
  order is PATH-first on purpose — the reverse of the npm shim, which targets
  Node-only hosts that usually have no install — so a machine that already has
  Selvedge keeps its exact version and the hook's fast path.
- **A bundled skill.** `skills/selvedge/SKILL.md` ships the agent-instructions
  with the install instead of waiting for someone to write them into
  `CLAUDE.md`. Its frontmatter says *when* to reach for Selvedge (before editing
  a tracked entity, after any substantive change), so Claude loads it at the
  right moments. The body is generated from the same `PROMPT_BLOCK` constant
  `selvedge prompt` installs — new `selvedge prompt --format skill` emits the
  file, and a test asserts the committed `SKILL.md` never drifts from it.
- **The enforcement hook, wired by the plugin.** `hooks/hooks.json` runs the
  PreToolUse gate on `Edit|Write|MultiEdit|NotebookEdit|Bash` — the same matcher
  `selvedge setup` writes (a test locks the two together). Plugin users get the
  gate without running `selvedge setup`.
- **Four slash commands** — `/selvedge:status`, `/selvedge:blame <entity>`,
  `/selvedge:history`, `/selvedge:prior-attempts <entity>` — thin wrappers over
  the CLI, argument-passing only.
- **`tests/test_plugin.py`** (28 tests): launcher resolution order with fake
  runners and no network, the stdout-stays-empty-on-failure invariant,
  `SELVEDGE_VERSION`/`latest` handling, version parity across every manifest +
  the launcher pin + the npm shim's pin, skill↔`render_skill()` byte parity, and
  hook double-registration idempotence.

### Changed

- **`.mcp.json` points the plugin's MCP command at
  `${CLAUDE_PLUGIN_ROOT}/bin/selvedge-plugin-server`** instead of bare
  `selvedge-server`. This file was added to register the repo as a marketplace;
  repo self-development never relied on it loading (the CLI + post-commit hook do
  the dogfooding), so nothing there regresses.
- **`.claude-plugin/plugin.json` carries the version for real now**, joining
  `pyproject.toml` / `selvedge/__init__.py` / `server.json` / `manifest.json` as
  a must-match sibling — a test enforces the set, and the launcher's pin is held
  equal to it.
- **The npm shim pins the server through a new `pypiVersion` field**, separate
  from its own npm `version`. npm's semver can't hold a four-segment PEP 440
  version like `0.3.9.2`, so pinning `selvedge==<npm version>` could never track
  the real server; the decoupled field fixes it. (The npm package is still
  unpublished — this makes its first publish pin correctly.)

### Notes

- **Double-registration is harmless.** A user who installs the plugin *and* runs
  `selvedge setup` gets the PreToolUse gate twice. The gate is a read-only,
  fail-open evaluation with a deterministic block message, so two identical fires
  return the identical decision — proven by a test. The MCP server should be
  registered once, though: both the plugin and `selvedge setup` provide it, so
  pick one (the plugin is recommended for Claude Code).
- **Windows.** The launchers are POSIX `sh`; on Windows they need a POSIX shell
  (Git Bash / WSL), which Claude Code's Windows builds commonly run under. A
  single `.mcp.json` can't express a platform-specific command, and a
  full-path-with-no-extension spawn wouldn't pick up a `.cmd` twin, so a native
  Windows path is a tracked follow-up rather than a half-working shim.
- **Official-directory submission is still pending.** The community directory
  (`claude-plugins-community`) accepts plugins through a submission form at
  `clau.de/plugin-directory-submission`, not a pull request — PRs there are
  closed automatically. Submission materials are prepared; the form is a manual
  step.
- **Test budget:** 28 new tests, all in `test_plugin.py`. On the low side for a
  release because nothing in the store or MCP layer moved — the surface under
  test is packaging: one shell script, a generator, and four manifests.

---

## [0.3.9.1] — 2026-07-10

**The dev.to feedback release.** Every item here was publicly promised as
"planned for a following version" in the comment replies to the launch post
([*My AI agent tried to ship a mistake we'd already reverted*](https://dev.to/masondelan/my-ai-agent-tried-to-ship-a-mistake-wed-already-reverted-4737)).
Five improvements from five threads of community feedback: decision states +
an explicit supersede flow, a PreToolUse enforcement hook, structured
constraint / stale-condition fields, git-history backfill, and optional
semantic recall. The store stays append-only and the core stays zero-LLM /
zero-network throughout.

**Why a four-segment version:** PEP 440 allows it, and the release is a
direct patch-series on the v0.3.9 launch narrative — the schema migration
(v4) is three nullable, default-less `ADD COLUMN`s, the same metadata-only
class as v0.3.8's migration v3, instant at any DB size. Drop-in upgrade for
anyone on 0.3.9.

### Added

- **Supersede flow — "reverted" is no longer a permanent ban** (thread:
  Nazar Boyko, Armorer Labs, Mudassir Khan, Alex Shev). New
  `change_type="supersede"` re-opens a reverted decision by linking the
  prior event via the new `supersedes` field; records stay append-only —
  a re-open is a new fact, never an edit. `log_change` gained
  `supersedes` / `constraint` / `stale_when` parameters instead of a ninth
  MCP tool (mirrors the `rename_from` precedent; keeps the surface at
  **8 tools**). Auto-links the entity's most recent remove/delete when no
  explicit id is given; refuses to supersede nothing. New CLI:
  `selvedge supersede ENTITY --reasoning ... [--constraint] [--stale-when]
  [--supersedes ID]`. Explicit supersede only — **no automatic
  un-retiring, by design** ("I don't trust auto un-retire yet").
- **Derived decision states + full trail.** `prior_attempts` results gain
  `outcome="reopened"`, `superseded_by`, `supersede_reasoning`, and
  `current_status`; `blame` gains `status` + `superseded_by`; `diff` rows
  gain `superseded_by`. The CLI renders the arc with a clear status line:
  **tried → reverted → re-opened**. New storage surface
  `get_decision_status(entity)` returns the status + phase-annotated trail.
- **Structured decision fields** (thread: Armorer Labs, Raffaele Zarrelli).
  `constraint` — the testable principle behind a decision ("card data in
  our own DB = PCI scope") — and `stale_when` — the evidence that would
  invalidate it ("payment provider changed") — are now first-class,
  queryable columns alongside free-text reasoning. `stale_decisions` grew a
  second rule: a decision whose `stale_when` keyword-overlaps a *later*
  change event (≥2 meaningful tokens — dumb, deterministic overlap on
  purpose) is flagged `review_suggested`. Surfacing only; the follow-up is
  an explicit `supersede`. Results now carry `flag`, `matched_terms`,
  `matched_event_id`.
- **PreToolUse enforcement hook** (thread: René Zander, Tae Kim). The
  CLAUDE.md "check prior_attempts first" instruction was probabilistic;
  agents skip it. New `selvedge-hook pretooluse` console script intercepts
  Edit/Write/Bash calls touching schema/migration paths and blocks (exit 2)
  until `prior_attempts` has been queried for the affected entities this
  session — **with the prior reasoning in the block message**, so the agent
  gets the skipped context for free. Fail-open contract: every miss, error,
  or unknown shape allows; decisions re-opened via supersede never block.
  `--dry-run` prints the decision as JSON; `SELVEDGE_HOOK_DISABLE=1`
  bypasses. Watch globs configurable via `.selvedge/config.toml`
  (`[hook] watch_globs`). `selvedge setup` installs it into the project's
  `.claude/settings.json` (default-on step, idempotent, backup-first;
  `--skip-enforcement-hook` opts out).
- **Git-history import** (thread: Alexey Spinov). `selvedge import
  --from-git [PATH] [--since REF|DATE]` walks revert-message commits plus
  file-deletion commits (`--diff-filter=D`) and seeds
  `change_type="revert"` events (`agent="git-import"`, reasoning = commit
  subject + body) — so reverts that predate Selvedge gate `prior_attempts`
  and the hook like live-captured ones. Handles both revert diff shapes
  (a `git revert` removing `ADD COLUMN` lines, a down-migration adding
  `DROP` lines) to extract table/column entities. Idempotent on
  `(commit, entity)`. Honest limit, stated in the help: reverts folded
  into unrelated commits are missed.
- **New `change_type="revert"`** — pulled forward from the v0.3.11 plan for
  the importer; an explicit "we tried this and rolled it back" that counts
  as a removal for outcome inference. (`reject` stays on the v0.3.11
  roadmap.)
- **Optional semantic recall** (thread: Dipankar Sarkar, Raffaele
  Zarrelli). `pip install "selvedge[semantic]"` + `selvedge index` builds a
  local embeddings index over reasoning text; `prior_attempts --fuzzy
  "description"` (CLI and MCP) surfaces attempts on entities whose prior
  reasoning is *semantically* similar — recall that survives renames
  (`payment_token` vs `card_token`). Backend: **model2vec** static
  embeddings (`potion-base-8M`) over sentence-transformers — no torch
  stack, ~30 MB model, sub-second cold start; size and cold-start matter
  more than quality for one-sentence reasoning strings. Fuzzy rows are
  clearly labeled (`match_type="fuzzy"` + similarity); without the extra
  (or before `selvedge index`) the query falls back to substring matching
  with a one-line pointer. **Core never imports the backend** — pinned by
  a subprocess test; the one network touch is the model download on the
  first `selvedge index` run.

### Changed

- `prior_attempts` rows now always carry `match_type`
  (`exact`/`substring`/`fuzzy`) and `similarity` (0.0 unless fuzzy), plus
  the trail fields above — all always-populated, never null, per the
  standing result-shape convention.
- The MCP server instructions and `log_change` docstring now teach the
  supersede etiquette: *never re-apply a reverted change without
  superseding it first.*

### Notes

- **Migration v4** adds `supersedes`, `"constraint"` (quoted — SQL reserved
  word), and `stale_when` as nullable, default-less TEXT columns.
  Metadata-only ALTER, not a table rewrite; pre-v4 rows read back as `""`
  on every surface.
- **MCP schema footprint (called out):** the tool-definition core grows
  from ~3.1k to ~3.6k tokens — still 8 tools, but `log_change` gained the
  `supersedes` / `constraint` / `stale_when` params and `prior_attempts`
  gained `fuzzy` plus the trail-field documentation. Deliberate: the new
  surface is the feature. Descriptions were tightened in the same pass and
  the CI drift gate moved 3200 → 3800.
- **Git-import identity boundary (stated, not hidden).** A `DROP TABLE` /
  `DROP COLUMN` in a revert diff is seeded at the entity level
  (`users.card_token`) and matches a re-add in any file; every other diff
  shape (ORM columns, serializer fields, non-SQL code) is seeded file-level,
  so a reverted entity resurfacing in a *different* file is missed.
  Documented in `selvedge import --from-git` help and `gitimport.py`.
  Widening it — plus provenance-based trust tiers (re-derivable git slice vs
  live testimony) and a `--verify` re-derivation diff — is the "git-import
  provenance + trust tiers" cluster in the Phase 2.18 plan.
- **Test budget:** 114 new tests across `test_supersede.py` (35),
  `test_hook.py` (45), `test_gitimport.py` (18), `test_semantic.py` (16) —
  full suite 676. Large by design: four publicly-promised features, each
  with migration-upgrade, error-path, and both-surfaces (MCP + CLI)
  coverage.

---

## [0.3.9] — 2026-06-22

**Agent Trace export — Selvedge is a compatible producer.** New
`selvedge export --format agent-trace` emits
[Agent Trace](https://github.com/cursor/agent-trace) **v0.1.0** records (the
open AI code-attribution wire format from Cursor + Cognition AI), so Selvedge's
captured history travels to any tool that reads the standard. Selvedge's
reasoning and entity-level provenance ride along in each record's `metadata`
under the reverse-domain `dev.selvedge` namespace — Agent Trace is the wire
format, Selvedge is the live capture + query layer that emits it.

**Pulled forward, deliberately.** The export was planned for v0.4.0 (Phase 3).
It ships now, in the 0.3.x line, as an **opt-in, additive** interop format —
nothing about the native model, MCP surface, or SQLite storage changes.
Postgres and the tool rename remain the v0.4.0 markers (HTTP + auth ships in
v0.4.1); only the exporter moved up. **Drop-in upgrade for anyone on 0.3.8.**

### Added

- **`selvedge export --format agent-trace`** — one Agent Trace v0.1.0 record per
  change event. `--ndjson` streams one record per line for large histories;
  `--collapse-by-session` merges events sharing a `session_id` into a single
  record. The default JSON form is a self-describing bundle
  (`{agent_trace_version, producer, note, records: [...]}`).
- **`selvedge import --format agent-trace`** — round-trips a Selvedge export
  losslessly (entity, change type, and reasoning survive in `dev.selvedge`
  metadata) and ingests foreign producers best-effort (`change_type="modify"`,
  empty reasoning).
- **`selvedge.exporters.agent_trace`** — the pure, dependency-free, no-LLM
  converter behind both CLI directions (`event_to_trace_record`,
  `trace_record_to_event`, `extract_line_ranges`, `events_to_trace_records`,
  `validate_trace_record`). Plus a vendored v0.1.0 JSON Schema
  (`selvedge/exporters/agent_trace_schema.json`) for reference.

### Notes

- **Conforms to the real v0.1.0 spec.** Records use
  `files[].conversations[].ranges[]`, a `contributor` of type `ai`/`unknown`
  (no `model_id` is fabricated — Selvedge stores the agent name, not a
  models.dev id), `tool = {name: "selvedge", ...}`, and `vcs` from `git_commit`.
- **Honest fidelity.** Entity-level events (DB column, env var, dependency) and
  migration-imported events have no line range, so they carry
  `metadata.dev.selvedge.range_unknown: true` and an empty `files[]` rather than
  a fabricated `[1, 1]` placeholder. The export bundle's preamble explains this
  to consumers up front.
- **Test budget.** Adds `tests/test_agent_trace_export.py` (25 tests: round-trip,
  non-file entity preservation, line-range extraction, collapse-by-session,
  reasoning-quality passthrough, schema validation, and CLI integration). The
  `docs/agent-trace-interop.md` mapping was corrected to the real v0.1.0 shape
  (the earlier draft predated the published spec).

---

## [0.3.8] — 2026-06-16

**Active memory v1 (date-based).** Selvedge's append-only log learns to know
when its own data is stale. A decision can now carry a revisit date
(`revisit_after`), and a new **`stale_decisions`** tool surfaces decisions that
have aged out — but only the ones whose entity is *still in active use*, so an
old-but-correct decision nobody touches never nags. This is the date-based half
of the active-memory arc; the pattern-based half (`expires_when` grammar,
explicit `reject`/`revert` change types) lands in v0.3.11. **Drop-in upgrade
for anyone on 0.3.7.** Brings the MCP surface to **8 tools**.

This release also bundles two adjacent items: CLI parity for the v0.3.7 wedge
(`selvedge prior-attempts`, previously the only MCP tool with no CLI command)
and a CLI-awareness section in the agent-instructions block, so a shell-having
agent knows the same operations exist as `selvedge` commands.

**No-LLM guard.** `stale_decisions` is templated, deterministic assembly over
the reasoning and telemetry Selvedge already stores — no model hop anywhere in
core, by design.

**One-time migration cost (multi-million-event installs).** Schema migration
v3 adds two **nullable, default-less** TEXT columns (`revisit_after`,
`expires_when`) via `ALTER TABLE ADD COLUMN`. In SQLite that's a metadata-only
edit — the table is **not** rewritten — so even a multi-million-event database
migrates in well under a second on the next connection. `test_migrations_perf.py`
gates this at 10k / 100k / 1M events. (`expires_when` is added now but unused
until the v0.3.11 evaluator — both columns ship in one migration to avoid a
second migration two releases later.)

**Test-budget overage (called out).** Phase 2.14's soft budget is ≤25 new
tests; this release exceeds it. Two drivers, neither scope creep in the core
feature: (1) the two bundled items — the `prior-attempts` CLI command and the
CLI-awareness block, each with its own tests (incl. the lockstep test); and
(2) an adversarial multi-agent review pass that hardened edge cases the first
draft under-covered — NULL-coalescing on the pre-v3 read paths, the
most-overdue-first ordering and `limit` contracts, filter wiring at the
tool/CLI boundary, the `changeset_activity` active-use signal, and a
version-independent `PRAGMA page_count` structural guard on the v3 migration.

### Added

- **`stale_decisions` MCP tool — the 8th tool.** Returns events whose
  `revisit_after` has passed AND whose entity is still in active use. The
  required active-use signal is one of: the entity was queried
  (`blame` / `diff` / `prior_attempts`) at or after the decision was logged, or
  the decision's `changeset_id` saw later sibling activity. **Pure age alone
  never surfaces** — that's the noise defense against old-but-correct decisions.
  Each result carries `revisit_due`, `days_overdue`, `active_use_signals`, and a
  templated `stale_reason`. Filterable by `entity_path`, `project`, `agent`.
  No LLM.
- **`revisit_after` on `log_change` (MCP) and `selvedge log` (CLI).** An
  ISO-8601 date OR a relative offset from the event's timestamp (e.g. `90d`,
  `6mo`), normalized with the same grammar as `--since`. Stored on the event
  and consumed by `stale_decisions` / `selvedge stale`.
- **`selvedge stale` CLI command.** The same data surface as `stale_decisions`,
  Rich-formatted, with `--json` for cron / Slack / digest jobs. Filters by
  `--entity`, `--project`, `--agent`.
- **`selvedge prior-attempts` CLI command.** CLI parity for the v0.3.7
  `prior_attempts` wedge — a thin presenter over the same `get_prior_attempts`
  store, so `--json` emits the identical list the MCP tool returns and the two
  surfaces can't diverge. ENTITY (positional) xor `--description`; `--all`
  widens recall to `proximity_low`; `--window` (e.g. `7d`, `60m`) maps onto the
  proximity window. An empty result is the normal, good answer (exit 0). Records
  on the same coverage counter as the tool so `selvedge stats` reflects both
  surfaces.
- **CLI-awareness in the agent-instructions block.** `selvedge/prompt.py`'s
  `PROMPT_BLOCK` gains a short, MCP-first-preserving section telling a
  shell-having agent the same operations exist as `selvedge` commands — for when
  the MCP server isn't loaded, in a shell-only subagent, or to keep context
  light. Kept in lockstep with `docs/architecture.md`'s "System prompt /
  end-user agent instructions" section (asserted by a test).
- **Reasoning-quality revisit-date nudge.** When an architectural change
  (`add` / `modify` / `create` / `migrate` on a `table` / `schema` /
  `dependency` / `config`) is logged without a `revisit_after`, the validator
  soft-warns suggesting one. Advisory only — never blocks the write, same
  posture as the reasoning-quality and entity-path-shape validators.
- **`doctor` — stale-decisions row (INFO).** Surfaces how many dated decisions
  are due for a revisit, pointing at `selvedge stale`. Deliberately INFO-tier:
  a decision aging out is a nudge, not a fault.
- **`scripts/schema_tax.py` wired into CI.** The lint job now runs
  `schema_tax.py --max 3200`, failing the build if the MCP tool definitions'
  at-init context footprint balloons (today ~3.1k core for 8 tools). `tiktoken`
  was added as a **dev-only** dependency so the gate uses the real tokenizer
  that matches the figures Selvedge publishes.
- **`docs/coding-agents.md`** — end-user doc on the MCP-first / CLI-second
  story (when an agent reaches for which). Shipped in the sdist.

### Changed

- **Schema migration v3** adds `revisit_after` and `expires_when` to `events`
  (both nullable; existing rows backfill to NULL, new writes store `""`). The
  `revisit_after` index is created post-migration so it exists on both fresh and
  upgraded databases.
- **`BlameResult` extended** with `revisit_after` and `expires_when` (every
  field still always present, NULL coalesced to `""`) rather than introducing a
  new result shape — per the type-discipline convention.
- **`doctor` signal-to-noise pass.** Reviewed every existing check as the new
  row landed; the stale-decisions row is INFO-tier on purpose so the net WARN
  count does not grow with this release (the existing WARN rows each still fire
  usefully, so none were demoted to compensate).
- **CLI `diff` and `blame` now record on the coverage counter** (`agent="cli"`),
  matching the MCP tools and the `prior-attempts` CLI command. They now appear
  in `selvedge stats`, and a CLI review of an entity counts as active use for
  the stale-decisions weighting (closing a documented-contract gap).
- **PyPI sdist tightened.** `[tool.hatch.build.targets.sdist]` previously
  shipped all of `docs/` and `CLAUDE.md`, so website HTML, `og-image.png`
  (~697 KB), `icon.png` (~873 KB), `sitemap.xml`/`robots.txt`,
  `marketing-templates.md`, and the agent-instructions file all rode along to
  PyPI. Now ships only `selvedge/`, `README.md`, `LICENSE`, `pyproject.toml`,
  and two end-user docs (`getting-started.md`, `coding-agents.md`).

### Fixed

- **`docs/marketing-templates.md` was tracked** — public on GitHub and shipped
  in the sdist. Untracked (`git rm --cached`) and added to `.gitignore`'s
  Internal section. A new `tests/test_repo_hygiene.py` guard fails the build if
  any known-internal artifact (marketing-templates, strategy, dashboard,
  teardown, ideas-backlog, top-priority, …) is ever tracked again. (History
  caveat: the file remains reachable via old commits; a history rewrite is
  out of scope for this release.)

## [0.3.7] — 2026-06-08

The brand-defining release: the **`prior_attempts`** MCP tool — an agent
about to change an entity can ask *"was this tried before, and how did it
turn out?"* — plus the **entity-canonicalization foundation** it depends on.
A single new MCP tool (`prior_attempts`), bringing the surface to **7**.
**Drop-in upgrade for anyone on 0.3.6.** This release also folds in the icon
redesign, Claude Code plugin-marketplace scaffolding, and og-image work that
had accumulated under `[Unreleased]`.

**No-LLM guard.** Both `prior_attempts` and `aggregates.summary()` are
templated, deterministic assembly over reasoning the agents wrote live — no
model hop anywhere in Selvedge core, by design.

**Test-budget overrun (called out).** 47 new tests against the standard ≤30
per-phase soft budget. The ≤40 target was set for this phase because the
entity foundation and the wedge ship together; the final 7 over that came
from an adversarial review pass that surfaced acceptance-criteria coverage
gaps (the `doctor` case-collision / path-migration rows, the `column` / `file`
entity-path shape rules, and the `summary()` empty-DB never-null contract) —
all on already-correct code. Closing them was judged worth the overrun on the
brand-defining release.

### Added

- **`prior_attempts` MCP tool — the wedge (7th tool).** Given an
  `entity_path` or free-text `description`, returns prior change attempts on
  the entity, each annotated with an inferred `outcome` (`reverted` /
  `active`), a `confidence` tier (`proximity_high` / `proximity_low`), and
  `outcome_reasoning` (why a reverted attempt was rejected). Outcome is
  inferred from add→remove proximity within a configurable window — explicit
  `reject`/`revert` change types arrive in v0.3.11. Conservative-recall:
  `min_confidence` defaults to `proximity_high`, so an empty list is the
  preferred answer over a false positive. Templated, no LLM, pull-only.
- **`rename_from` parameter on `log_change`.** With `change_type="rename"`,
  records the dual-event rename pattern — a `rename` event on the old path
  plus a `create` event on the new path with `metadata.renamed_from` set —
  so blame / diff / `prior_attempts` on the new path keep the history.
  Rename is a `log_change` parameter, **not** a new tool, keeping the v0.3.7
  surface at +1. A worked rename example is in the `log_change` docstring.
- **`selvedge migrate-paths` — one-shot entity-path backfill.** Re-
  canonicalizes existing rows. **Dry-run is the default**; it prints a
  collisions report (pre-canonicalization paths that converge, i.e.
  histories that would merge) so you inspect before `--apply` writes.
  Idempotent on the event data; one audit row per `--apply` run goes to a
  new `path_migrations` table (visible to `doctor`). `--json` for machine
  output.
- **Soft entity-path shape validation in `log_change`.** Per-`entity_type`
  shape checks (a `function` path without `::`, a `column` without `.`, a
  `file` without a separator or extension) that **warn but never reject**,
  mirroring the v0.3.4 reasoning-quality validator. Patterns live in
  `selvedge.validation.ENTITY_PATTERNS` so new types extend without touching
  the write path.
- **`selvedge.aggregates.summary()` — library digest helper.** A pure-
  Python, schema-versioned (`summary_version`) roll-up — changesets touched,
  agents involved, top entities by activity — that v0.3.9's `selvedge audit`
  / `digest` will consume. Ships as a **library function, not an MCP tool and
  not a CLI command**: a deliberate tool-surface-discipline call.
- **`doctor` — case-collision watch + path-migration row.** A WARN row
  surfaces sibling entity paths that differ only by case (the pair to the
  intentional case-preservation stance), and an INFO row surfaces the last
  `migrate-paths --apply` run.
- **Claude Code plugin marketplace scaffolding.** Adds
  `.claude-plugin/marketplace.json`, `.claude-plugin/plugin.json`,
  and `.mcp.json` at the repo root, declaring this repo as a
  self-hosted Claude Code marketplace. After the next tag, users
  can install Selvedge into Claude Code with two commands:
  `/plugin marketplace add masondelan/selvedge`, then
  `/plugin install selvedge@selvedge`. No Anthropic gatekeeping
  required — Track 0 of the plugin-marketplace rollout. Submission
  to `claude-plugins-official` / `claude-plugins-community` is
  tracked separately. The version field in
  `.claude-plugin/plugin.json` is now a fifth sibling to
  `pyproject.toml` / `selvedge/__init__.py` / `manifest.json` /
  `server.json` — version-bump checklist in `CLAUDE.md` updated.
- **`docs/og-image.png` — wide wordmark banner for social previews.**
  2064×512 'selvedge' wordmark with the red selvedge edge stitch.
  Referenced from `og:image` and `twitter:image` meta tags on the
  three main site pages. Favicon and apple-touch-icon continue to
  use the square `docs/icon.png` mark — wide banner for share
  previews, tight square mark for tab and home-screen icons.

### Changed

- **Entity-path canonicalization on write (the foundation).** Every write —
  the MCP `log_change` tool, the `selvedge log` CLI, and the migration
  importers — now routes through a single
  `selvedge.storage.canonicalize_entity_path` chokepoint that strips leading
  `./`, collapses `//`, normalizes separators to `/`, and trims, so
  `src/auth.py::login` and `./src/auth.py::login` resolve to the same entity
  instead of silently splitting history. **Case is preserved on purpose** —
  filesystems differ (case-insensitive on macOS/Windows, case-sensitive on
  most Linux), and lowercasing would collapse genuinely distinct entities on
  case-sensitive hosts; collisions surface as a `doctor` warning instead.
- **Agent-guidance prompt leads with `prior_attempts`.** The canonical
  prompt block (`selvedge prompt`) and the architecture system-prompt section
  now tell agents to call `prior_attempts` before editing an entity, ahead of
  the existing `diff` / `blame` guidance.
- **Server icon redesigned.** The "stitched timeline" mark from
  v0.3.3 is replaced with a minimalist square mark — a navy 'S'
  beside a red selvedge edge stitch on cream. Lives at
  `docs/icon.png` (canonical tracked location), referenced by
  `manifest.json`, the selvedge.sh favicon / apple-touch-icon, and
  Smithery thumbnail. Verified to hold up from 16×16 favicon scale
  through Smithery thumbnail render sizes.
- **`manifest.json` icon path repointed from `assets/icon.png` to
  `docs/icon.png`.** `assets/` is now gitignored as internal-only,
  so a fresh clone no longer has `assets/icon.png` to bundle. The
  byte-identical-but-tracked copy at `docs/icon.png` becomes the
  single source — bundle, site, and social previews now build from
  the same file.
- **`twitter:card` upgraded from `summary` to `summary_large_image`**
  on `docs/index.html`, `docs/comparison.html`, and `docs/faq.html`.
  The new wide og-image banner would be center-cropped to a small
  square by the summary card; the large-image card renders the full
  banner.

### Fixed

- **Silent entity history-split.** Differently-spelled paths for the same
  entity (`./src/auth.py` vs `src//auth.py`) previously created separate
  histories. Canonicalization on write, plus `migrate-paths` for existing
  rows, closes the gap — the correctness fix that makes `prior_attempts`
  trustworthy.

### Internal

- **Explicit non-goal reaffirmed: no code parser / AST.** Selvedge stores
  the canonicalized entity events the agent supplies; it does not extract
  entities from source code (language-specific, dependency-dragging, and
  against the dependency-free-core rule). Documented in the release notes and
  added to the cross-cutting non-goals section so the boundary survives
  roadmap pressure.
- **Test-budget overrun (47 vs. the standard ≤30; the phase target was ≤40).**
  New tests across `test_entity_canonicalize.py`, `test_migrate_paths.py`,
  `test_prior_attempts.py`, `test_aggregates.py`, and extensions to
  `test_server.py` / `test_mcp_protocol.py` (entity foundation + wedge share
  the release), plus a review-driven round in `test_validation.py` and
  `test_doctor.py` covering the entity-path shape rules and the new doctor
  rows. Total suite: 450 tests, coverage 86.6% (gate is 85%).
- **Bulk `launch/` and `assets/` rules in `.gitignore`** (PR #8).
  Replaces six individual `launch/` sub-path entries with a single
  bulk rule (matches the `.mcpbignore` convention so MCPB builds
  are unaffected). Adds `assets/` and three new strategy-doc paths
  (`docs/strategy-2026-q3.md`, `docs/top-priority-actions.md`,
  `docs/seo-gsc-primer.md`) alongside the existing internal-docs
  block. No public-facing content change; existing local copies
  remain on disk.

---

## [0.3.6] — 2026-05-24

Two themes in one release — a one-time exception to the single-theme
cadence so the retention-basics work can ship without a renumbering
pass on the phase plan. v0.3.4 made install one command; v0.3.5 made
recovery possible; v0.3.6 closes the version-drift gap **and** bounds
the noise table so old `tool_calls` telemetry doesn't accumulate
forever. **Drop-in upgrade for anyone on 0.3.5.**

### Added — stay-current

- **Background PyPI version check in the CLI.** On every `selvedge`
  invocation, a daemon thread fetches the latest published version
  from PyPI's JSON endpoint and caches the result at
  `~/.selvedge/update_check.json` (user-global, not per-project — a
  user with ten Selvedge projects doesn't pay for ten checks). If a
  newer release exists, a one-line notice is printed to stderr on
  process exit:

  ```
  selvedge: v0.3.7 available (you're on 0.3.6) — https://selvedge.sh/upgrade
  ```

  The notice prints *after* the command's output (via `atexit`) so it
  never interleaves with `selvedge log`, `selvedge watch`, or `--json`
  pipelines. Cache TTL is 24h — matches what `gh` and `npm` use; `pip`
  uses 7 days but that's longer than most users want.

- **Generous suppression.** The check is disabled when any of
  `SELVEDGE_NO_UPDATE_CHECK=1`, `SELVEDGE_QUIET=1`, or `CI` is set in
  the environment; when stderr isn't a TTY (piping, agent stdio,
  redirected output); and on dev / editable installs
  (`pip install -e .` where the version contains `.dev` / `+` / `rc`).
  The TTY gate is also re-checked at print time, so even a cached
  notice can't pollute redirected output.

- **Soft-fail everywhere.** The fetch has a 1.5-second timeout and
  every code path in `selvedge/update_check.py` swallows exceptions —
  a network blip, an unwritable `$HOME`, or a malformed PyPI response
  can never affect the command the user invoked. The upgrade-URL is
  the only hard-coded copy; pointing at `selvedge.sh/upgrade` rather
  than inlining `pip install -U selvedge` keeps the notice correct
  across PyPI / Smithery / Glama users.

### Added — retention basics

- **`selvedge prune` — trim old `tool_calls` rows.** Hardcoded default
  of 90 days; `--days N` overrides. The 90-day default is long enough
  that the previous month's agents are still in the data, and is
  surfaced explicitly in `selvedge prune --help`. **Only `tool_calls`
  is pruned in this release** — the events-table prune path waits for
  `.selvedge/config.toml` in v0.3.10 and will require both
  `SELVEDGE_DESTRUCTIVE=1` and an interactive confirmation. `--json`
  for machine output.

- **`.selvedge/prune.log` audit trail.** Every prune appends a
  tab-separated one-liner — `<utc-iso>\t<count_pruned>\t<days_threshold>` —
  mirroring the format of the post-commit `.selvedge/hook.log`. The
  log is written even when 0 rows were deleted, so cadence is visible
  in the doctor row regardless of whether a given run had work to do.

- **Doctor — `Last prune` row.** Parses the tail of
  `.selvedge/prune.log` and surfaces the most recent timestamp,
  pruned-row count, and day threshold. INFO when present, INFO with
  a "run `selvedge prune`" nudge when the log doesn't exist yet.

- **Doctor — `tool_calls size` WARN at >100k rows.** A rough oversized-
  table signal that points users at `selvedge prune`. Threshold lives
  at `selvedge.prune.TOOL_CALLS_WARN_ROWS` and is revisitable once the
  v0.3.5/v0.3.6 telemetry has bedded in.

### Changed

- **`selvedge-server` stays silent.** The update check is deliberately
  wired into `selvedge/cli.py` only — not `server.py`. The MCP
  server's stdio is the JSON-RPC channel; a stray stderr write from a
  daemon thread would surface in the calling agent's logs as noise.

### Tests

- **`tests/test_update_check.py`** (24 tests) — covers env-var and TTY
  gating, dev-install detection, 24h TTL behavior, malformed-cache
  recovery, the network-error / timeout / unexpected-exception soft
  fails, the `packaging`-vs-fallback comparison paths, and the
  notice's once-per-process idempotency. **No test in the module hits
  the network** — `urlopen` is monkeypatched everywhere.
- **`tests/test_prune.py`** (10 tests) — covers old-row deletion,
  preserve-recent semantics, `--days` override, log-line shape,
  empty-table still-logs, append-on-subsequent-run, last-line parsing,
  missing-log handling, and the CLI `--json` shape.
- **`tests/test_doctor.py`** — +2 tests for the new `Last prune` row
  (parsed from the log) and the oversized-`tool_calls` WARN (stubbed
  via a monkeypatched count helper rather than inserting 100k rows).

### Note on cadence

This release combines two themes — stay-current (Phase 2.11 leftover,
deferred from v0.3.5) and retention basics (Phase 2.12). It is a
**one-time exception** to the single-theme-per-release discipline
locked in on 2026-05-10. Single-theme resumes at v0.3.7 (entity
foundation + `prior_attempts` wedge).

---

## [0.3.5] — 2026-05-11

The recovery-basics release. v0.3.1 made the runtime safe; v0.3.2 made
problems visible; v0.3.5 adds the *minimum viable* "what happens when
something has gone wrong" surface. Verify so you can detect corruption.
Backup so you have a known-good snapshot to fall back to. **Drop-in
upgrade for anyone on 0.3.4.**

### Added

- **`selvedge verify` — DB-correctness gate with two exit tiers.** Walks
  the store and reports each check as PASS / WARN / FAIL. Must-fail
  conditions (SQLite corruption from `PRAGMA integrity_check`, schema
  mismatch against the declared `MIGRATIONS` tuple, empty `entity_path`,
  unknown `change_type` in the store, unparseable timestamps, malformed
  `tool_calls` rows) exit non-zero. Should-warn conditions (singleton
  `changeset_id` groups, events past the 60-minute backfill window with
  no `git_commit`) print warnings but exit 0 by default. Pass `--strict`
  to escalate warnings to failures — `selvedge verify` is meant to drop
  into CI on day one without `|| true`. `--json` for machine output.
  Tier mapping is locked in by `selvedge.verify.CHECK_TIERS` and
  asserted by `tests/test_verify.py` — adding a check without a tier
  trips CI.
- **`selvedge backup` — online SQLite snapshot via VACUUM INTO.**
  Default destination
  `.selvedge/backups/selvedge-YYYYMMDD-HHMMSS.db`. Hardcoded
  `keep_last=7` for this release; the setting becomes
  `backup_keep_last` in `.selvedge/config.toml` when that file lands
  in v0.3.10. Two backups within the same second don't collide — the
  second one gets a `-1` suffix rather than clobbering the first.
  `--output <path>` overrides the default destination and is excluded
  from rotation. `--json` for scripting.
- **`.selvedge/backups/` added to the project `.gitignore`.**
  `selvedge init` writes it on fresh repos; the first `selvedge backup`
  run on an existing repo appends it the same way. Idempotent — safe
  to re-run.
- **Doctor — `Last backup` row.** INFO when the newest backup is ≤7
  days old, WARN when older, FAIL when no backups exist *and* the
  events table has ≥10,000 rows (the threshold where no-backups
  becomes a real data-loss exposure rather than a CI/scratch DB).
- **Doctor — `Schema version` now FAILs on downgrade.** When
  `schema_migrations` contains a version not declared in the current
  `MIGRATIONS` tuple, the row fails rather than silently appearing
  PASS — surfaces "this DB was last opened by a newer Selvedge" before
  any write attempts schema work it doesn't understand.

### Changed

- **`selvedge doctor` docstring** updated to describe the new rows and
  the downgrade-failure semantics.

### Tests

- **`tests/test_verify.py`** (13 tests) — covers tier locking, the
  happy path, every must-fail trigger, the warn-only paths, and the
  CLI surface including `--strict` escalation.
- **`tests/test_backup.py`** (7 tests) — covers snapshot validity,
  rotation, same-second collisions, missing-DB error path, and the
  `.gitignore` append idempotency.
- **`tests/test_doctor.py`** — 4 new tests for the `Last backup` row
  and the downgrade-detection branch.

---

## [0.3.4] — 2026-04-26

The first-run release. The install funnel was six manual steps with
three documentation lookups; v0.3.4 collapses it to one command and
makes the agent integration discoverable from inside the tool instead
of from the README. **Drop-in upgrade for anyone on 0.3.3.**

### Added

- **`selvedge setup` — interactive first-run wizard.** Detects the AI
  tooling already on your machine (Claude Code via
  `~/.claude/config.json`, Cursor via `~/.cursor/mcp.json` and
  `.cursorrules`, GitHub Copilot via `.github/copilot-instructions.md`)
  and walks through every install step in one pass: adds the Selvedge
  MCP entry to each tool's config, drops the canonical agent-instructions
  block into the project's prompt file (`CLAUDE.md` / `.cursorrules` /
  copilot-instructions.md), runs `selvedge init` if `.selvedge/`
  doesn't exist, and installs the post-commit hook. Every modified
  file gets a `.bak` written next to it before any change reaches
  disk. Re-running on an already-set-up project is a no-op
  (idempotent). Existing-but-different MCP entries trigger a conflict
  warning rather than silent overwrite — pass `--force` to overwrite,
  or update by hand. For CI / devcontainer `postCreateCommand`:
  `selvedge setup --non-interactive --yes`.
- **`selvedge prompt` — canonical agent instructions on tap.** Prints
  the recommended system-prompt block to stdout
  (pipe-friendly: `selvedge prompt | tee -a CLAUDE.md`) or installs it
  idempotently into a target file with `--install <file>`. The block
  is wrapped in `<!-- selvedge:start -->` / `<!-- selvedge:end -->`
  sentinel markers, so re-running `--install` updates the bracketed
  region without disturbing anything else in the file. The block
  source lives at `selvedge.prompt.PROMPT_BLOCK` — single source of
  truth, so it stays in lockstep with the docs.
- **`selvedge watch` — live tail of newly-logged events.** Polls the
  SQLite store at a configurable `--interval` (default 1s) and prints
  each new event as it lands, Rich-formatted. Filters mirror
  `selvedge history` exactly: `--since`, `--entity`, `--project`,
  `--agent`. `--json` emits one compact JSON object per line for
  piping into other tools. WAL mode means the polling SELECT never
  blocks the writer; the runtime cost is one indexed query per second
  while the command is running. Ctrl-C exits cleanly.
- **`selvedge.prompt.PROMPT_BLOCK` is now public.** Library users can
  import the canonical agent-instructions block as a constant for
  templating into their own onboarding flows.

### Changed

- **Better empty-state diagnosis in `selvedge status`.** Replaces the
  generic "No changes logged yet" with a decision-tree-driven hint:
    * MCP entry installed in some agent's config but no tool_calls
      received → "MCP entry installed but no tool_calls received yet…
      try restarting your agent" (with the config path printed)
    * MCP entry not detected anywhere → "Run `selvedge setup` to wire
      Selvedge into your AI tools"
    * Detection error → falls back to the setup nudge gracefully
  Detects "MCP entry installed but agent never reloaded" by reading
  the agent config files, comparing modification times against the
  current time. Five-minute grace window before the hint shifts from
  "restart your agent" to "run `selvedge doctor` for a full health
  check."
- **`selvedge doctor`'s "MCP wiring" check now points at `selvedge
  setup`.** Same diagnostic improvement, surfaced through the doctor
  table for users who hit that command first.
- **`server.json` (Glama / catalog descriptor) regenerated from live
  server.py.** Was still showing v0.3.2 tool descriptions through the
  v0.3.3 release; now in lockstep with `manifest.json`. Folded into
  the version-bump checklist so this can't drift again.

### Tests

- **`tests/test_setup.py`** — 18 tests covering detect-and-install for
  every agent type. Uses `tmp_path` + a fake home/project — never
  touches real `~/.claude/`, `~/.cursor/`, or `.git/`. Idempotent
  re-runs, malformed JSON handling, conflict detection, `--force`
  overwrites, errors don't abort later steps, hook step skipped when
  not in a git repo, prompt-block wired correctly.
- **`tests/test_prompt.py`** — 18 tests covering the prompt installer.
  Greenfield install, append, in-place update, idempotence, backup
  numbering (consecutive edits don't overwrite the first `.bak`),
  trailing-newline-convention preservation, sentinel-bracketed block
  detection survives whitespace around markers.
- **`tests/test_watch.py`** — 18 tests covering filter semantics
  (entity prefix-aware, project/agent exact-match), cursor advancement
  (no re-emission across polls), interval clamping, catch-up window
  emits chronologically before the loop starts, `--json` mode emits
  one compact line per event. Uses a `max_iterations` test seam so
  the loop exits deterministically without needing SIGINT delivery.

---

## [0.3.3] — 2026-04-26

A discoverability + ergonomics release. No new MCP tools, no behavior
changes that affect stored data — but the live tool schema is now
substantially richer for the agents that read it and the directories
that score it. **Drop-in upgrade for anyone on 0.3.2.**

### Added

- **Per-parameter descriptions on every MCP tool.** All 6 tools now
  declare each parameter via `Annotated[T, Field(description=...)]`,
  populating `inputSchema.properties.<param>.description` in the live
  tool listing. Previously each parameter shipped only `type` and
  `title`; the rich docstrings sat in the function body where agents
  couldn't see them at tool-call time. Agents picking which tool to
  call read these descriptions directly, so this is a DX win for
  Claude Code / Cursor / Copilot use, not just a directory-score
  improvement. Coverage went 0/21 → 21/21.
- **MCP tool annotations on every tool.** Each tool now declares
  `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`,
  and a human-friendly `title`. `log_change` is the only writer (not
  destructive — it's append-only — but not idempotent, since each call
  mints a new event). The five readers (`diff`, `blame`, `history`,
  `changeset`, `search`) are all read-only + idempotent. None are
  open-world. Lets MCP clients gate or surface tools appropriately.
- **`outputSchema` on `log_change` and `blame`.** New `LogChangeResult`
  and `BlameResult` TypedDicts (in `selvedge.server`) give the JSON-RPC
  layer something concrete to advertise. The four list-returning tools
  (`diff`, `history`, `changeset`, `search`) already had auto-generated
  schemas from their `list[dict]` annotation; this brings the dict
  returners in line so all 6 tools advertise their output.
- **Custom server icon.** A "stitched timeline" mark — a horizontal
  running stitch crossing the icon, where each visible stitch is a
  captured change event. Lives at `assets/icon.svg` and a 512×512
  `assets/icon.png`. Referenced from `manifest.json` so it ships with
  the Smithery bundle and renders in the directory's thumbnail.

### Changed

- **`log_change` always returns a complete result payload.** The
  result now always includes `id`, `timestamp`, `status`, `error`, and
  `warnings` keys (not just present-when-non-empty). On success,
  `error` is `""` and `warnings` is `[]` if reasoning passed the
  quality validator. On validation failure, `id`/`timestamp`/
  `warnings` are empty and `status` == "error". Required for the new
  `outputSchema` to validate cleanly. Tests updated to match.
- **`blame` returns a stable shape on miss.** Empty-history responses
  now populate every event field with the empty value of its type and
  set `error` to the "no history found" message. Previously returned
  the slim `{"error": "..."}`. Same `error`-key convention, fuller
  payload — easier for callers to type-check without branching.
- **Tool-level descriptions are dedented at startup.** Each tool's
  docstring is run through `inspect.cleandoc` once at import time so
  `tools/list` doesn't leak the function-body indent
  (`"\n    Get change..."` → `"Get change..."`). Cosmetic but visible
  in any directory that surfaces the raw description.

### Documentation

- **`CLAUDE.md` ↔ `docs/architecture.md` split.** `CLAUDE.md` is now a
  thin agent-instructions file (sources of truth, code conventions,
  version bump checklist, scheduled tasks). The architecture, data
  model, MCP tool reference, full CLI reference, phase plan, and
  non-goals all moved to `docs/architecture.md`. Reduces noise on
  every Claude Code / Cowork session boot and gives the architecture
  doc a stable home.
- **isError convention documented.** Empty-history cases (`blame` on
  an unknown entity, `changeset` with no events) intentionally return
  `{"error": "..."}` with protocol-level `isError: false`. Empty
  history isn't a protocol failure; the in-payload `error` key is
  the documented signal. Codified as a comment in `selvedge.server`
  module-level docstring.

### Fixed

- **Test helper handles all three FastMCP response shapes.**
  `tests/test_mcp_protocol.py::_payload` previously assumed
  `structuredContent={"result": ...}` for every tool. With v0.3.3's
  TypedDict returns, the structured content for `log_change` and
  `blame` is the dict itself with no `result` wrap. Helper now
  detects all three shapes (list-wrapped, dict-direct, content-only)
  and unwraps correctly.

---

## [0.3.2] — 2026-04-25

An observability-polish release. No new feature surface — the focus is
making existing functionality discoverable and debuggable, plus locking
in WAL/`busy_timeout` assumptions across SQLite versions in CI.
**Drop-in upgrade for anyone on 0.3.1.**

### Added

- **`selvedge doctor` command.** Walks the ambient state agents typically
  run into and reports each row PASS / WARN / FAIL / INFO:
    * which DB path is being resolved (and which precedence step matched —
      `SELVEDGE_DB`, walkup, or global fallback)
    * whether `.selvedge/` exists where you think it does
    * whether the schema is at the latest migration version
    * whether the post-commit hook is installed
    * whether the post-commit hook has been failing silently
    * last `tool_calls` entry timestamp (proxy for "is the agent wired up?")
    * whether `SELVEDGE_LOG_LEVEL` is set to a recognized value
  Exits 1 if any FAIL row is present so doctor can be wired into CI.
  Supports `--json` for machine-readable output.
- **Post-commit hook failure surfacing.** The previous hook silently died
  when `selvedge` wasn't on the shell PATH that git launched (a common
  symptom under macOS GUI git clients with stripped PATHs). The new hook
  appends a single line to `.selvedge/hook.log` on failure, and both
  `selvedge status` and `selvedge doctor` surface the most recent failure.
  Old hooks keep working — re-running `selvedge install-hook` is enough
  to upgrade.
- **`selvedge stats` upgrades:**
    * **Per-agent breakdown.** Catches the case where one agent (e.g.
      claude-code) is well-instrumented but another (e.g. cursor) is
      only querying history and never logging changes. Each agent shows
      total calls, log_change calls, and coverage ratio.
    * **Missing-reasoning count.** Counts events whose stored reasoning
      fails the quality validator (empty, too short, or generic
      placeholder). A non-zero count means an agent saw a warning at
      log time and shipped the event anyway.
- **`agent` column on `tool_calls` (migration v2).** The MCP server now
  passes the calling agent's name through to the telemetry table, so
  the per-agent stats break down correctly. v0.3.1 databases are
  migrated automatically; fresh DBs get the column from the create
  schema and the migration is recorded via the bootstrap path.
- **Public `selvedge.config.resolve_db_path()`.** Returns both the
  resolved path AND the precedence step that produced it (`env`,
  `walkup`, or `global`). Used by doctor; available for any tool that
  needs to know not just *which* DB is in effect but *why*.
- **Pinned-SQLite CI matrix.** A new `sqlite-matrix` job builds SQLite
  3.37.2, 3.42.0, and 3.45.3 from source and runs the suite against
  each via `LD_PRELOAD`. The implicit Python-bundled-SQLite matrix is
  also expanded with Python 3.13, and each row prints the active
  SQLite version so the matrix is visible in CI logs.

### Internal

- New tests: `test_doctor.py` (20), expanded `test_cli.py`,
  `test_storage.py`, and `test_migrations.py` for the v2 migration and
  the per-agent / missing-reasoning paths. Total suite is now 282 tests.
- `selvedge.cli.last_hook_failure()` and `selvedge.cli.hook_log_path()`
  expose the hook log to both status and doctor without duplication.
- `selvedge.migrations.latest_version()` so doctor can compare a DB's
  applied set against "what should be there" without knowing the
  migration list itself.

---

## [0.3.1] — 2026-04-23

A hardening release. No new feature surface — concurrency, observability,
schema-versioning, and developer-quality changes that take the codebase
from "works on my machine" to "safe to run in a long-lived agent pool."

### Added

- **Connection-with-retry on every storage write.** SQLite `database is locked`
  errors that escape the C-level `busy_timeout` (5s) now retry with exponential
  backoff (5 attempts, capped at 1s sleeps) before raising. Combined with WAL
  mode, this makes Selvedge safe under concurrent writers — `tests/test_concurrency.py`
  spawns 8 threads writing 25 events each and asserts all 200 land.
- **`PRAGMA busy_timeout = 5000` set on every connection** so SQLite's own
  retry handler covers the common contention case before Python ever sees it.
- **`schema_migrations` table.** Replaces the previous swallow-OperationalError
  ALTER pattern with an explicit, versioned migration runner. Every migration
  is recorded with version, name, and applied-at timestamp; partial failures
  roll back the DDL atomically. Pre-versioning databases (v0.2.1+ with
  `changeset_id` already present) are bootstrapped without re-running DDL
  that would error.
- **Structured logging (`selvedge.logging_config`).** All library modules now
  log under the `selvedge.*` namespace. Entry points (`selvedge` CLI,
  `selvedge-server` MCP) call `configure_logging()` once at startup. Set
  `SELVEDGE_LOG_LEVEL=DEBUG|INFO|WARNING|ERROR` to control verbosity.
- **Public API exports in `selvedge/__init__.py`.** Library users can now
  `from selvedge import SelvedgeStorage, ChangeEvent, parse_time_string`
  instead of reaching into internal modules. The frozen surface is locked
  in by `tests/test_public_api.py`.
- **Shared `selvedge.validation`.** The reasoning-quality validator moved
  out of `server.py` so the CLI's `selvedge log` command emits the same
  warnings as agent-driven `log_change` calls.
- **MCP protocol smoke tests (`tests/test_mcp_protocol.py`).** Boot the
  real `selvedge-server` subprocess and round-trip every tool over the
  actual JSON-RPC stdio transport. Catches contract drift the in-process
  tool tests miss.
- **CI gates: `ruff`, `mypy`, coverage ≥85%.** Added a separate `lint` job
  and `pytest-cov` to the test job. Current coverage is 92%.
- **`SelvedgeStorage._session()` context manager.** Yields a connection,
  commits on success, rolls back on error, ALWAYS closes — fixes a
  long-standing connection leak where `with self._connect()` managed the
  transaction but never closed the underlying socket.

### Fixed

- **Reasoning-quality regex bug.** Patterns like `^fixed?$` were intended
  to match both "fix" and "fixed" but actually matched "fixe"/"fixed" —
  the `?` only made the trailing `d` optional. Rewritten as `^fix(?:ed)?$`
  (and the same for `add`, `remove`, `update`, `change`, `see (...)`).
  Previously-uncaught placeholder reasonings now produce warnings.
- **Connection lifecycle.** Storage methods previously used
  `with self._connect() as conn:` which calls `Connection.__exit__()` for
  commit/rollback but never closes the connection — Python's GC eventually
  reclaimed it. All read/write methods now use `_session()` which closes
  explicitly. Affects long-running agent sessions where leaked connections
  could accumulate.

### Changed

- **`record_tool_call()` exception handling.** Still swallows so telemetry
  failures never crash the parent tool, but now routes through
  `logger.exception("…")` so the failure is visible at `SELVEDGE_LOG_LEVEL=DEBUG`.

### Internal

- New modules: `selvedge.migrations`, `selvedge.logging_config`,
  `selvedge.validation`. Imports are flat (no circular deps).
- New tests: `test_concurrency.py` (9), `test_migrations.py` (8),
  `test_logging_config.py` (11), `test_validation.py` (32),
  `test_public_api.py` (7), `test_mcp_protocol.py` (8). Total suite is
  now 244 tests.
- `pyproject.toml` configuration for ruff, mypy, and coverage.

---

## [0.3.0] — 2026-04-23

A correctness and data-quality release. No new feature surface — every
change here either prevents a wrong answer, prevents silent data loss,
or makes the import story actually work end-to-end.

### Fixed (correctness — high severity)

- **`5m` now means 5 minutes, not 5 months.** `_parse_relative_time` mapped
  `m` to months, contradicting every CLI convention (`sleep 5m`, `kubectl
  --since=5m`, Prometheus). New mapping: `m` = minutes, `mo`/`mon` = months.
  Users typing `selvedge history --since 5m` get the last 5 minutes as
  expected.
- **`search()` and prefix matching escape SQL `LIKE` wildcards.** Previously
  `selvedge search "stripe_customer_id"` matched `stripeXcustomerXid` and
  similar (because `_` is a `LIKE` wildcard). All five `LIKE` queries in
  `storage.py` now use `ESCAPE '\'` and escape `\`, `_`, `%` in user input.
- **Unparseable `--since` raises instead of silently returning empty.**
  `selvedge history --since yesterday` previously did `WHERE timestamp >=
  'yesterday'`, lexicographically matched nothing, and returned no error.
  Now both the CLI and MCP server validate the input and surface a clear
  error.
- **`CREATE TABLE` import emits a `column.add` event for every column.**
  Previously importing `CREATE TABLE users (id INT, email TEXT)` created
  one event for the table and zero for its columns, so `selvedge blame
  users.email` returned "no history" for any column defined only in the
  initial schema. The import story now works end-to-end.
- **All timestamps normalized to canonical UTC (`...Z` suffix) on write.**
  Previously a tz-aware timestamp like `09:00:00-08:00` (= 17:00 UTC)
  sorted lexicographically *before* `10:00:00+00:00` (because `-` < `+`
  in ASCII), even though the PST time is later. All stored timestamps
  are now converted to UTC and serialized with a fixed `Z` suffix so
  lexicographic and chronological order match.

### Fixed (data quality — medium severity)

- **`change_type` validated against the `ChangeType` enum.** Hallucinated
  types (`"banana"`) and typos (`"modifyed"`) are now rejected with a
  clear error rather than silently inserted, which kept stats grouping
  honest.
- **`entity_type` coerced to `"other"`** when not a known `EntityType`.
  Descriptive metadata, not load-bearing for queries — coerce rather
  than reject.
- **Empty `entity_path` rejected.** `ChangeEvent(entity_path="", ...)`
  used to insert orphan rows that broke prefix queries.
- **Alembic and SQL `RENAME TABLE` emit two events.** A `rename` event
  for the old name and a `create` event for the new name, so `selvedge
  blame` works under both names after a rename. Same pattern for
  `RENAME COLUMN` (column `add` event for the new name).
- **`get_db_path` requires the DB file to exist**, not just the
  `.selvedge/` directory. A stray empty `.selvedge/` upstream no longer
  hijacks resolution. Falling back to the global `~/.selvedge/`
  database now prints a one-time stderr warning so unintentional
  global use is visible. Suppress with `SELVEDGE_QUIET=1`.
- **`backfill_git_commit` window widened from 10 to 60 minutes** so
  longer agent sessions still get their events stamped after a commit.
  `selvedge status` now shows the count of events missing `git_commit`
  to nudge users toward installing the post-commit hook.

### Added

- **`storage.log_event_batch()`** — wraps multiple inserts in a single
  transaction. Used by `selvedge import` for orders-of-magnitude faster
  bulk imports of large Alembic histories, and makes the import atomic.
- **`storage.count_missing_git_commit()`** — surfaced in `selvedge status`.
- **`selvedge.timeutil`** — shared `parse_time_string()` and
  `normalize_timestamp()` helpers, deduplicating the relative-time
  parsing previously copy-pasted between `server.py` and `cli.py`.
- **`selvedge log` CLI** uses `click.Choice` for `change_type`, so
  invalid types are caught at the argument-parsing layer with the
  full list of valid choices.
- **Adversarial-input test suite** (`tests/test_adversarial.py`) with
  25 tests covering underscore-in-search, `--since yesterday`,
  `CREATE TABLE` blame for inline columns, mixed-tz ordering, and
  validation rejection paths.

---

## [0.2.1] — 2026-04-22

### Added

- **`changeset_id` field on `ChangeEvent`** — optional slug to group related changes
  under a named feature or task (e.g. `"add-stripe-billing"`). Indexed in SQLite.
- **`changeset` MCP tool** — retrieve all events belonging to a `changeset_id`,
  returned oldest-first so you can reconstruct the full scope of a feature.
- **`storage.list_changesets()`** — summary view of all changesets: id, event count,
  agent, and time range.
- **Reasoning quality validation in `log_change`** — the server now returns a
  `warnings` array if `reasoning` is empty, under 20 characters, or matches a
  generic placeholder (`"user request"`, `"done"`, `"n/a"`, etc.). Logged event
  is still written; warnings are advisory only.

---

## [0.2.0] — 2026-04-22

### Added

- **`selvedge install-hook`** — installs a git post-commit hook that automatically
  backfills `git_commit` on Selvedge events after each commit. Safe to run on repos
  with existing post-commit hooks (appends rather than overwrites). Idempotent.
- **`selvedge backfill-commit --hash HASH`** — manually backfill `git_commit` on
  recent events within a configurable time window. Called by the git hook automatically.
- **`selvedge import PATH`** — parse migration files and backfill schema history:
  - Raw SQL DDL: `CREATE TABLE`, `ALTER TABLE ADD/DROP/RENAME/ALTER COLUMN`,
    `DROP TABLE`, `CREATE/DROP INDEX`, `RENAME TABLE`
  - Alembic Python migrations: `op.add_column`, `op.drop_column`, `op.create_table`,
    `op.drop_table`, `op.alter_column`, `op.rename_table`, `op.create_index`,
    `op.drop_index`, `op.execute()` (with inline SQL parsing)
  - Supports `--dry-run` (preview without writing), `--json`, `--project`, `--format`
  - Directories walked recursively; files sorted by name for chronological order
- **`selvedge export`** — dump change history to JSON or CSV with full filter support
  (`--since`, `--entity`, `--project`, `--limit`, `--output`)

### Changed

- `selvedge stats` added in 0.1.0 now documented in CHANGELOG (was omitted)

---

## [0.1.0] — 2025-04-21

Initial release.

### Added

- **MCP server** (`selvedge-server`) with 5 tools: `log_change`, `diff`, `blame`, `history`, `search`
- **SQLite storage** with WAL mode and graceful fallback for mounted filesystems
- **DB path resolution**: `SELVEDGE_DB` env var → walk-up `.selvedge/` → `~/.selvedge/selvedge.db`
- **CLI** (`selvedge`) with commands: `init`, `status`, `diff`, `blame`, `history`, `search`, `log`, `stats`
- **`selvedge stats`** — tool call coverage report: shows log_change call ratio, per-tool breakdown, and recent call history. Answers "is my agent actually logging changes?"
- **Local tool call telemetry** — every MCP tool invocation is recorded to a `tool_calls` table (local only, never networked). Powers `selvedge stats` and `scripts/coverage_check.py`
- **`scripts/coverage_check.py`** — cross-references git log against Selvedge events to measure coverage ratio per commit
- `--json` flag on all read commands for machine-readable output
- Relative time support in `--since` flag (`7d`, `24h`, `3m`, `1y`)
- Rich terminal output with tables and styled panels
- Full test suite: storage, server, and CLI tests (57 tests)
- PyPI package with `selvedge` and `selvedge-server` entry points

### Entity types supported
`column`, `table`, `file`, `function`, `class`, `endpoint`, `dependency`, `env_var`, `index`, `schema`, `config`, `other`

### Change types supported
`add`, `remove`, `modify`, `rename`, `retype`, `create`, `delete`, `index_add`, `index_remove`, `migrate`

---

## Roadmap

### [0.4.0] — planned (Phase 3 — team features)
- PostgreSQL backend option (`SELVEDGE_BACKEND=postgresql://...`)
- HTTP REST API layer (FastAPI)
- Auth (API keys) for the HTTP layer

### [1.0.0] — planned
- Web dashboard
- Cross-repo queries
- Team / org management
- Webhook events on schema changes
