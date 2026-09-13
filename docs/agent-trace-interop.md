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

## Status note — 2026-08-10: the upstream repo is gone

`https://github.com/cursor/agent-trace` returns **HTTP 404**. Confirmed twice
on 2026-08-10 — once with an authenticated `gh` token and once with an
anonymous fetch. It is not a rename: GitHub answers renames and transfers with
a 301, so this is a deletion or a switch to private. The `cursor` org still
exists with exactly 10 public repos and none of them is `agent-trace`;
`anysphere`, `Cognition-ai`, and the `agent-trace` / `agenttrace` org names
hold no replacement either. The last archive.org capture of the repo root is
dated 2026-07-25 (status 200), so the removal happened between then and
2026-08-10. No deprecation notice, migration note, or announcement was found
anywhere.

**The spec itself survives.** <https://agent-trace.dev/> is live (HTTP 200) and
carries the complete v0.1.0 text, with a machine-readable schema at
<https://agent-trace.dev/schemas/v1/trace-record.json>. The README (554 lines,
blob capture 2026-06-29) and the `reference/` TypeScript files
(`trace-store.ts`, `trace-hook.ts`, last captures 2026-05-27) are recoverable
from archive.org.
Treat agent-trace.dev as the surviving publication home; treat every
`github.com/cursor/agent-trace` link in this repo as dead.

The decision memo (2026-08-10) weighed three options — keep the exporter and
re-document it against the frozen spec, republish our vendored schema as "the"
public reference, or deprecate the surface — and chose the first: carrying
cost is near zero, removal would break a shipped CLI surface for no gain, and
republishing would assert stewardship we don't have over a schema upstream
itself contradicts. The full forensics live in an internal note (not shipped
with the repo). **Nothing in Selvedge breaks** — see the *Resilience* section
below.

### Corrections to the text below

The shipped mapping table and the v0.1.0 record shape in this doc are still
correct. The prose errors listed here were **corrected inline on 2026-08-11**
(as part of the keep + demote decision); this list is kept as the record of
what was fixed and why:

- **"drafted by Cognition AI"** (opening paragraph of *Why interop with Agent
  Trace at all*) — not supported. Cognition's own
  post positions the company as *joining in support*, and
  [InfoQ](https://www.infoq.com/news/2026/02/agent-trace-cursor/) attributes
  the spec to Cursor. The spec's example records use a `dev.cursor` metadata
  namespace and `api.cursor.com` URLs. "backed by Cloudflare, Vercel, Google
  Jules, Amp, OpenCode, and git-ai" checks out as names on the partner list,
  but the upstream wording was "partners for helping shape Agent Trace" —
  a design-input credit, not a backing claim. That list also included
  Amplitude, Cline, and Tapes, which this doc dropped.
- **git-ai's README no longer mentions Agent Trace.** `git-ai-project/git-ai` is
  actively developed (last push 2026-08-11) and its README contains **zero**
  occurrences of "Agent Trace"; it promotes its own
  [Git AI standard](https://github.com/git-ai-project/git-ai/blob/main/specs/git_ai_standard_v3.0.0.md)
  (spec file `git_ai_standard_v3.0.0.md`) instead. Whether that is a withdrawal
  or a README that never carried the credit is **unverified** — claim the
  absence, not a departure. Per `docs/positioning.md` § "Never claim", a
  competitor claim we can't source is one they can cheaply correct.
- **The "official validator" at `/tree/main/validator`** (*Test plan*, item 2)
  — that path
  appears in no archive capture, and the README describes only `reference/`
  (`trace-store.ts`, `trace-hook.ts`). An official validator directory is
  **unverified** and probably never existed. Only the vendored path was ever
  built (see *Resilience*).
- **"25 tests"** (*Implementation status*, item 3) —
  `tests/test_agent_trace_export.py` now defines 35 `test_` functions with no
  parametrisation, and `pytest tests/test_agent_trace_export.py` reports
  `35 passed`. 25 was the v0.3.9 figure. Corrected to 35 here and in
  `docs/architecture.md`; `CHANGELOG.md:566` is left at 25 as an append-only
  record of the v0.3.9 release (accurate at ship time).
- **The Cognition announcement URL** (*References*, at the end) —
  `cognition.ai/blog/agent-trace` now 301s to `cognition.com/blog/agent-trace`.
  Stale but functional.

## Why interop with Agent Trace at all

[Agent Trace](https://agent-trace.dev/) is the open RFC published by Cursor on
2026-01-29, with design input from ten partners (Amp, Amplitude, Cline,
Cloudflare, Cognition, git-ai, Jules, OpenCode, Tapes, Vercel — "partners for
helping shape Agent Trace," per the upstream acknowledgements, not backers). It
defines a JSON-based "trace record" format for AI code attribution — file/line
ranges tied to contributors (human, AI, mixed, unknown), with a content hash
for tracking code movement. Its GitHub home is gone (404 as of 2026-08-10); the
spec now lives, frozen at v0.1.0, only at agent-trace.dev.

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

1. ~~**Discoverability.** When the Agent Trace alliance publishes its list of
   "compatible producers," Selvedge is on it.~~ **Void as of 2026-08-11:** no
   "compatible producers" registry ever existed (verified against the archived
   README and the live spec page — zero occurrences of *producer*, *registry*,
   *adopter*, or *compatible*; full forensics in an internal note), and the
   alliance has since scattered (upstream repo 404; git-ai left for its own
   standard). This discoverability rationale no longer applies.
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

Per the [v0.1.0 Agent Trace spec](https://agent-trace.dev/),
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
2. Spec validation: every emitted trace record passes Selvedge's hand-rolled
   `validate_trace_record` against a vendored copy of the v0.1.0 JSON schema.
   (No "official validator" was ever published — see *Corrections* above; the
   upstream repo described only a `reference/` directory.)
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
   `tests/test_agent_trace_export.py` (35 tests).

Pulled forward from the v0.4.0 plan; Postgres + HTTP remain the v0.4.0 markers.

## Resilience — what the upstream 404 costs us

Audited 2026-08-10, immediately after the repo went 404. **Verdict: nothing
breaks.** `pytest`, `ruff`, `mypy`, the wheel build, the CLI, and the MCP
server all behave identically with the upstream repo gone. There is zero
runtime, test, packaging, or CI dependency on `github.com/cursor/agent-trace`.
Four specific answers:

**1. `validate_trace_record` uses inline constants, not the vendored schema.**
`selvedge/exporters/agent_trace.py:490-538` (plus `_validate_file` at
`:541-589`) is a hand-rolled checker under the section banner "validation (no
third-party jsonschema dependency)". It compares against module-level
constants — `AGENT_TRACE_VERSION` (`:48`), the inline set
`{"git", "jj", "hg", "svn"}` (`:519`), `_CONTRIBUTOR_TYPES` (`:64`) — with no
file read anywhere. The module imports only `hashlib`, `json`, `re`, `uuid`,
`typing.Any`, and `..models.VALID_CHANGE_TYPES`. No `pathlib`, no `open()`.

**2. The vendored schema is never read at runtime — it is dead as code.**
Repo-wide, and setting aside the prose of this section itself,
`agent_trace_schema.json` has exactly five references and **none is
in `selvedge/`**: two tests
(`tests/test_agent_trace_export.py:153` and `:382`, which assert the vendored
file's `required` lists agree with the hand-rolled validator) and three prose
mentions (`CHANGELOG.md:553`, `docs/architecture.md:895`, and item 3 of
*Implementation status* above). The file ships in the wheel but nothing
imports it.

Being honest about what "vendored" bought us: not a runtime hedge — the
runtime never needed one. It bought a **documentation artifact** (a precise,
machine-readable record of the shape we target, which now outlives its source)
and a **drift anchor** for two consistency tests. That is still worth having
now that upstream is unreachable, but it is not the thing that made us
resilient. The thing that made us resilient was refusing the `jsonschema`
dependency and writing the checker by hand.

**3. No code path and no test reaches the network.** `selvedge/exporters/`
contains no `urllib` / `requests` / `httpx` / `urlopen` / `socket` reference at
all. `tests/test_agent_trace_export.py` imports only `json`, `uuid`,
`pathlib.Path`, `pytest`, `click.testing.CliRunner`, and Selvedge modules. The
only network-adjacent tests in the whole suite are `test_update_check.py` and
`test_telemetry.py`, both of which monkeypatch `urlopen` and both of whose
docstrings state that no test in the module hits the network.

**4. What is actually damaged is documentation credibility, not function.**
`cursor/agent-trace` appears in 9 tracked files. Three of them ship to PyPI
users: `selvedge/exporters/agent_trace.py:3` (module docstring),
`selvedge/cli.py:1971` (**user-facing** — it renders in
`selvedge export --help`), and `selvedge/exporters/agent_trace_schema.json:5`
(the `description` field; now carries a provenance header, see below). The
rest are `README.md`, `CHANGELOG.md`, `docs/architecture.md`,
`docs/comparison.html`, `docs/faq.html`, and this file. All are dead links;
`https://agent-trace.dev/` is the live replacement.

**One real external risk, and it predates the 404.** The schema *served* at
`https://agent-trace.dev/schemas/v1/trace-record.json` is a different artifact
from the README's inline schema we vendored — generated from the upstream zod
source, and using `definitions` under a top-level `$ref` rather than `$defs`.
(It declares the *same* draft we do, `https://json-schema.org/draft/2020-12/schema`;
`definitions` is a draft-07-era keyword that `zod-to-json-schema` still emits,
not a different draft.) Its `version` pattern is **two-segment**
(`"pattern": "^[0-9]+\\.[0-9]+$"`, described there as `"Agent Trace
specification version (e.g., '1.0')"`). Selvedge emits `"0.1.0"`
(`AGENT_TRACE_VERSION`, `agent_trace.py:48`), which passes the README schema and
**fails** the served one. Our vendored copy puts no `pattern` on `version` at
all, so no local check will ever surface this. Anyone validating Selvedge output
against that live URL gets a rejection.

**Do not "fix" this by emitting `"0.1"` without reading the next sentence.**
Upstream is not self-consistent, and the weight of the evidence is on our side.
The zod source the served schema is generated from —
`schemas.ts`, [archived 2026-06-23](https://web.archive.org/web/20260623013002id_/https://raw.githubusercontent.com/cursor/agent-trace/refs/heads/main/schemas.ts)
— defines `TraceRecordSchema.version` with a **three-segment** regex
(`^[0-9]+\.[0-9]+\.[0-9]+$`) carrying that same `(e.g., '1.0')` description, so
the served two-segment pattern disagrees with the repo source, with the README's
inline schema, and with the spec's own stated version string of `0.1.0`. The
served artifact is the outlier. Changing the emitted `version` is a wire-format
change either way and belongs to the maintainer, alongside the namespace
question below — but the defensible default is to keep `"0.1.0"` and document
the disagreement, which is what this file now does.

## Namespace ownership — `metadata["dev.selvedge"]`

Selvedge has emitted its data under `metadata["dev.selvedge"]` since v0.3.9
(`SELVEDGE_NS`, `selvedge/exporters/agent_trace.py:52`). Reverse-domain
notation for `dev.selvedge` implies ownership of **selvedge.dev**. The
project's domain is **selvedge.sh**, which would reverse to `sh.selvedge`.
Whether we hold selvedge.dev is **unverified** and was not checked here.

**Did the spec require domain ownership?** *Unverifiable from in-repo sources,
and not re-checked upstream.* The vendored schema constrains `metadata` only as
`{"type": "object"}` — no `propertyNames`, no key pattern, no ownership
language — so nothing we hold imposes a requirement. The claim in the
*namespace name* open question below ("AT spec recommends reverse domain
notation") is this repo's paraphrase of a **draft** that predated the published
v0.1.0 text, not a retrieved quote.
The normative sentence, if one exists, is no longer retrievable from
`github.com/cursor/agent-trace` (404 as of 2026-08-10); the surviving
publication at agent-trace.dev may contain it but was not re-read for this
question. Do not assert either way.

**The convention is loose in practice.** The spec's own example records use a
`dev.cursor` metadata namespace while Cursor's primary domain is cursor.com —
i.e. the format's own author did exactly what we did. Whatever the text says,
the reference usage does not treat the reverse-domain key as a proof of
ownership.

**Cost of changing the key later: it is a wire-format break.** Any third-party
consumer keying off `metadata["dev.selvedge"]` breaks, and so does our own
lossless round-trip — `selvedge import --format agent-trace` recovers entity,
change type, and reasoning from that exact key. Every Agent Trace file exported
by Selvedge since 2026-06-22 carries it. A rename would need a deprecation
window in which the importer reads both keys and the exporter writes only the
new one, plus a CHANGELOG breaking-change note. That is real work for a
cosmetic gain.

**Does upstream's disappearance make this moot or more urgent?** Mostly moot.
There is no registry, no conformance body, and no arbiter left to object to our
choice of key — the only party who could have ruled on it is gone. What
remains is a self-consistency question: a reader who resolves `selvedge.dev`
and finds nothing sees a small credibility scratch. That is a marketing-surface
concern, not an interop one, and it is strictly lower urgency than it was in
June.

**Recommendation: keep `dev.selvedge`. Do not change it.** The cost is a
breaking change to a shipped export format; the benefit is cosmetic alignment
with a convention whose own author didn't follow it and whose enforcement body
no longer exists. If the mismatch bothers us, the cheap defensible move is a
defensive registration of selvedge.dev pointing at selvedge.sh — that
retro-justifies the key without touching the wire. **This is the maintainer's
call and no change has been made.**

## Open questions

*(Answered 2026-08-10. Originals preserved; each carries its resolution.)*

- ~~**Should we list under "compatible producers" on the AT side?**  Yes
  once PR 2 lands. Open a PR against `cursor/agent-trace` adding Selvedge
  to whatever registry list they keep.~~
  **ANSWERED 2026-08-10 — no, and there never was one.** A "compatible
  producers" list never existed. Neither the archived README (554 lines,
  recovered in full) nor the live spec page contains a producers registry, an
  implementations list, or an adopters list. What exists is a ten-name
  *acknowledgements* list under `## Contributing`, introduced with "Thanks to
  the following partners for helping shape Agent Trace:" — Amp, Amplitude,
  Cline, Cloudflare, Cognition, git-ai, Jules, OpenCode, Tapes, Vercel. That
  is a design-input credit with no mechanism for adding an implementation.
  Keyword scan of the live page: *producer* 0 hits, *registry* 0, *adopter* 0,
  *compatible* 0. The PR target is 404 regardless. The **Discoverability**
  payoff claimed in *Why interop with Agent Trace at all* has no path to
  realization and should not be cited in positioning. The decision memo
  (2026-08-10) closed this as "keep the exporter, frozen" — the format stays a
  portable, documented export, not a live multi-vendor standard; the full
  forensics live in an internal note.
- ~~**`extensions.selvedge.*` namespace name.**  AT spec recommends reverse
  domain notation (`com.example.foo`). We could use `dev.selvedge.*` if
  we register the domain, otherwise `selvedge.*` is fine — multiple
  vendors are using flat namespaces in the wild.~~
  **ANSWERED 2026-08-10 — resolved in shipping as `metadata["dev.selvedge"]`;
  keep it.** See *Namespace ownership* above for the ownership analysis, the
  break cost, and the recommendation. No change made; a namespace rename is a
  breaking change to a shipped export format and is the maintainer's call.
- **Versioning.**  Pin to AT v0.1.0 for v0.4.0. When AT v0.2.0 lands,
  emit the newest spec version we know about; document the mapping per
  version in this file.
  **UPDATED 2026-08-10 — the pin is now permanent by default.** With the
  upstream repo gone and no v0.2.0 ever published, `AGENT_TRACE_VERSION`
  stays `"0.1.0"` and this contingency has no trigger. The live open item is
  not a *spec* version bump but the two-segment/three-segment `version`
  pattern disagreement between the README schema we vendored and the schema
  served at agent-trace.dev — see the last paragraph of *Resilience*.

## What this does *not* change

- Selvedge's internal data model. ChangeEvent stays as it is.
- The MCP tool surface (`log_change`, `diff`, `blame`, `history`,
  `changeset`, `search`). All of those keep using the native model.
- Storage. Still SQLite (or PostgreSQL post-Phase-3). Agent Trace is
  purely an import/export format.

---

References:
- [Agent Trace spec (agent-trace.dev)](https://agent-trace.dev/) — the surviving
  publication home; the original `github.com/cursor/agent-trace` repo is 404 as
  of 2026-08-10
- [Cognition announcement post](https://cognition.com/blog/agent-trace)
- [InfoQ summary of the RFC](https://www.infoq.com/news/2026/02/agent-trace-cursor/)
