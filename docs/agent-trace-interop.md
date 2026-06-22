# Selvedge ↔ Agent Trace interop — design doc

**Status:** Shipped in **v0.3.9** — pulled forward from the v0.4.0 plan as an
opt-in interop format (Postgres + HTTP remain the v0.4.0 markers).  Owner:
maintainer.

> *`selvedge export --format agent-trace` and `selvedge import --format
> agent-trace` exist as of v0.3.9. The shipped implementation conforms to the
> **real** Agent Trace v0.1.0 record shape
> (`files[].conversations[].ranges[]`, a `tool`/`vcs`/`metadata` envelope, and
> Selvedge data under the reverse-domain `metadata["dev.selvedge"]` namespace).
> That differs from this doc's **original draft mapping** below — the draft
> predated the published spec. `selvedge/exporters/agent_trace.py` is the
> source of truth; the corrected wire format and mapping table follow.*

## Why interop with Agent Trace at all

[Agent Trace](https://github.com/cursor/agent-trace) is the open RFC released
by Cursor on 2026-01-29, drafted by Cognition AI, and backed by Cloudflare,
Vercel, Google Jules, Amp, OpenCode, and git-ai.  It defines a JSON-based
"trace record" format for AI code attribution — file/line ranges tied to
contributors (human, AI, mixed, unknown), with a content hash for tracking
code movement.

It is **a wire format, not a tool.** The spec deliberately doesn't say where
traces live (local files, git notes, a database, anything). The point is for
"a compliant tool can read and write attribution data" — so an attribution
viewer, CI gate, or compliance scanner doesn't have to know about every
upstream attribution producer.

Selvedge is one such producer. It already captures everything Agent Trace
records, plus reasoning, plus entity-level (rather than purely line-level)
attribution. There is **no positional conflict** between the two — Agent
Trace tells the rest of the ecosystem how to read what Selvedge already
has.

Concretely, supporting `selvedge export --format agent-trace` means:

1. **Discoverability.** When the Agent Trace alliance publishes its list of
   "compatible producers," Selvedge is on it. That's a category-level marketing
   surface we don't otherwise have access to.
2. **Compliance posture.** If the EU AI Act / California AB 2013 push
   companies toward Agent Trace as the de-facto attribution audit format,
   shipping a Selvedge → Agent Trace exporter turns Selvedge into an
   audit-trail-of-record source rather than a parallel system.
3. **Selvedge's reasoning fits *inside* an Agent Trace record.**  Agent
   Trace defines `extensions` for vendor metadata. Reasoning is a Selvedge
   extension, not a replacement.

We are not adopting Agent Trace as our *internal* model. The Selvedge data
model stays entity-centric (DB column, env var, route, dep). Agent Trace
is purely an export format.

## Mapping: ChangeEvent → Agent Trace

Per the [v0.1.0 Agent Trace spec](https://github.com/cursor/agent-trace),
a trace record is JSON with this shape (line ranges live *inside* a file's
`conversations[]`, and vendor data lives in `metadata` under reverse-domain
keys — there is no top-level `contributors[]` or `extensions`):

```json
{
  "version": "0.1.0",
  "id": "<uuid>",
  "timestamp": "<rfc3339>",
  "vcs": { "type": "git", "revision": "<sha>" },
  "tool": { "name": "selvedge", "version": "<v>" },
  "files": [
    {
      "path": "<repo-relative path>",
      "conversations": [
        {
          "url": "<uri>",
          "contributor": { "type": "ai", "model_id": "<optional models.dev id>" },
          "ranges": [ { "start_line": 42, "end_line": 67, "content_hash": "<optional>" } ],
          "related": [ { "type": "<kind>", "url": "<uri>" } ]
        }
      ]
    }
  ],
  "metadata": { "dev.selvedge": { } }
}
```

The shipped mapping from a `ChangeEvent`:

| ChangeEvent field | Agent Trace target |
|---|---|
| `id` | top-level `id` (already a UUID; one record per event) |
| `timestamp` | top-level `timestamp` |
| `git_commit` | `vcs.revision` (`vcs.type = "git"`); omitted when empty |
| *(producer identity)* | `tool = { name: "selvedge", version }` |
| `agent` | `files[].conversations[].contributor.type` (`ai` if present, else `unknown`); the agent *name* → `metadata["dev.selvedge"].agent`. `model_id` is **not** fabricated — Selvedge stores an agent name, not a models.dev id |
| `entity_path` *(file-typed: file/function/class)* | `files[].path` (a `path::symbol` is stripped to the file) |
| `diff` | `files[].conversations[].ranges[]` via unified-diff hunk extraction |
| `entity_path` *(non-file: column, env, dep, route)* | `metadata["dev.selvedge"].entity` only; `files[]` is empty (no native Agent Trace concept) |
| `change_type` | `metadata["dev.selvedge"].change_type` |
| `reasoning` | `metadata["dev.selvedge"].reasoning` |
| `session_id` | `metadata["dev.selvedge"].session_id` (also the `conversations[].url` urn) |
| `changeset_id` | `metadata["dev.selvedge"].changeset_id` (also a `conversations[].related[]` urn) |
| `project` | `metadata["dev.selvedge"].project` |
| `metadata` | merged into `metadata["dev.selvedge"].metadata` |

> The prose further down this doc that says `extensions.selvedge.*` reflects the
> original draft; in the shipped format read it as `metadata["dev.selvedge"].*`.

### Handling non-file entities

Selvedge tracks entities that don't have a file path: `users.email` (DB
column), `env/STRIPE_SECRET_KEY` (env var), `deps/stripe` (dependency).
Agent Trace's `files[]` array doesn't model these natively. Two options:

1. **Drop them from `files[]` and surface in `extensions.selvedge.entities`.**
   Agent Trace consumers ignore them, Selvedge consumers see them. Simple,
   loses information for AT-only readers.
2. **Synthesize a virtual `path` like `selvedge://entity/users.email`.**
   Some AT consumers may treat it as a file and choke; others will pass
   through fine.

**Decision: option 1.** Lossless for Selvedge readers, valid for AT readers,
no surprise. Documented in the export's preamble.

### Multiple events per file

Agent Trace records line ranges within a single trace record. Selvedge
events are one-per-change; a single PR/session that touches one file four
times would emit four trace records. We keep that 1:1 mapping by default.

A `--collapse-by-session` flag could merge events with the same `session_id`
into one trace record with multiple `ranges[]` — that's a v0.4.1 follow-up
once we see real consumer behavior.

## CLI surface

```bash
# Export everything in the current project DB
selvedge export --format agent-trace --output trace.json

# Export a slice
selvedge export --format agent-trace \
  --since 30d \
  --entity users \
  --output users-30d-trace.json

# Stream NDJSON (one trace record per line) for large histories
selvedge export --format agent-trace --ndjson --output trace.ndjson

# Round-trip: re-import an Agent Trace file from another tool
selvedge import trace.json --format agent-trace
```

The `import` direction is **best-effort**. Other tools that emit Agent
Trace won't have populated `extensions.selvedge.*`, so Selvedge will fill in
defaults: `entity_path = files[].path`, `change_type = "modify"`,
`reasoning = ""` (and the reasoning-quality validator will warn). This is
fine — the import is for cross-tool history, not for re-creating Selvedge's
native richness.

## File-type entity bridging

For events whose `entity_path` *is* a file (`src/auth.py::login` or
`src/auth.py`), we should populate `files[].ranges[].lines` if the diff
makes that derivable. v0.4.0 will:

- Extract line ranges from unified-diff `diff` payloads (`@@ -X,Y +A,B @@`
  hunks) when available.
- Fall back to `lines: [1, 1]` with `extensions.selvedge.range_unknown: true`
  when the diff isn't a unified diff (e.g. raw SQL DDL events from
  `selvedge import`).

A line-range backfill from git is possible but out of scope for v0.4.0.

### Export preamble — explaining `range_unknown` to AT consumers

Every Selvedge AT export emits a preamble comment / extensions block
that names the producer (`Selvedge vX.Y.Z`) and explains the
fidelity profile up front. AT consumers seeing many records with
`extensions.selvedge.range_unknown: true` should not interpret that
as Selvedge being a low-fidelity producer — it's the *truthful*
representation of events imported from migration files (where line
ranges genuinely don't exist) and DB-column / env-var / dependency
events (where there is no line range to attribute). Selvedge prefers
to emit `range_unknown` rather than fabricate a `[1, 1]` placeholder
that looks line-perfect.

The README's AT-compatibility section carries the same framing so
buyers and downstream consumers see this before judging fidelity. The
v0.4.0 release notes call out the expected `range_unknown` ratio for
typical Selvedge stores.

## Test plan

A new `tests/test_agent_trace_export.py`:

1. Round-trip: log_event → export agent-trace → import agent-trace → assert
   semantic equality of the events that AT can express (entity_path stays
   if file-typed, otherwise lands in extensions).
2. Spec validation: every emitted trace record passes the
   [official validator](https://github.com/cursor/agent-trace/tree/main/validator)
   when present, or a vendored copy of the JSON schema when not.
3. Non-file entity preservation: a `users.email` ChangeEvent → AT export →
   AT import → ChangeEvent should equal the original by every field.
4. Multi-event session: 5 events sharing a `session_id` collapse correctly
   under `--collapse-by-session` and stay separate without it.
5. Reasoning quality: empty / weak reasoning passes through to
   `extensions.selvedge.reasoning` unmodified, with the same warning the
   validator currently emits at log time.

## Implementation status — shipped in v0.3.9

All of the originally-planned work landed together in v0.3.9:

1. **`selvedge.exporters.agent_trace`** — pure, dependency-free, no-LLM
   conversion both ways (`event_to_trace_record` / `trace_record_to_event`),
   plus `extract_line_ranges`, `events_to_trace_records`, `load_trace_records`,
   and a hand-rolled `validate_trace_record` (no `jsonschema` dependency).
2. **CLI** — `selvedge export --format agent-trace` with `--ndjson` and
   `--collapse-by-session`; `selvedge import --format agent-trace` (round-trip
   gated by tests).
3. **Diff-to-line-range extractor** for unified diffs; vendored AT v0.1.0 JSON
   Schema at `selvedge/exporters/agent_trace_schema.json`;
   `tests/test_agent_trace_export.py` (25 tests).

Pulled forward from the v0.4.0 plan; Postgres + HTTP remain the v0.4.0 markers.

## Open questions

- **Should we list under "compatible producers" on the AT side?**  Yes
  once PR 2 lands. Open a PR against `cursor/agent-trace` adding Selvedge
  to whatever registry list they keep.
- **`extensions.selvedge.*` namespace name.**  AT spec recommends reverse
  domain notation (`com.example.foo`). We could use `dev.selvedge.*` if
  we register the domain, otherwise `selvedge.*` is fine — multiple
  vendors are using flat namespaces in the wild.
- **Versioning.**  Pin to AT v0.1.0 for v0.4.0. When AT v0.2.0 lands,
  emit the newest spec version we know about; document the mapping per
  version in this file.

## What this does *not* change

- Selvedge's internal data model. ChangeEvent stays as it is.
- The MCP tool surface (`log_change`, `diff`, `blame`, `history`,
  `changeset`, `search`). All of those keep using the native model.
- Storage. Still SQLite (or PostgreSQL post-Phase-3). Agent Trace is
  purely an import/export format.

---

References:
- [Agent Trace repo (cursor/agent-trace)](https://github.com/cursor/agent-trace)
- [Cognition AI announcement post](https://cognition.ai/blog/agent-trace)
- [InfoQ summary of the RFC](https://www.infoq.com/news/2026/02/agent-trace-cursor/)
