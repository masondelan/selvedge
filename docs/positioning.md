# Positioning — canonical

**Status:** current as of 2026-08-29 (prior-art fold-in — Zero-Mem and the
pull-model-memory evidence, per `docs/prior-art.md`; OpenLore purge scoping
amended 2026-08-06 against the 2026-08-05 source-verified brief). Supersedes
the differentiator framing in
`README.md` §"How Selvedge compares", `docs/comparison.html`, and
`docs/index.html`. This file is the source of truth; if copy anywhere disagrees
with this doc, the copy is wrong.

---

## The one-line claim

> Selvedge is the only AI-code memory layer that tells an agent what was
> **already tried and rejected** — and does it without a second LLM anywhere in
> the path.

Lead with **rejected paths**. Support with **determinism**. Do not lead with
liveness, and do not lead with the `git blame` metaphor.

---

## What changed, and why

The differentiator has moved. **"Live capture" is no longer defensible on its
own.** The category has filled in around it — Git AI is at 2.4k stars, and there
are now at least two distinct projects shipping under the AgentDiff name. Every
one of them describes its capture as some flavor of "as you work." The word
"live" no longer separates anything; a reader can't price the difference between
four tools that all claim it.

What survives is the compound: **live + zero-LLM + entity-granular +
append-only**. And within that compound, only one property is expensive for a
funded competitor to copy.

- **Live capture** — copyable. Anyone can add a hook or a checkpoint call.
- **Entity granularity** — copyable, but it's a real schema migration for a
  line-oriented store. Months, not weeks.
- **Append-only** — copyable. A design choice, not a moat.
- **Zero-LLM determinism** — **not copyable without re-architecting.** A
  competitor whose reasoning is produced by an inference call cannot make it
  deterministic by adding a flag. The LLM is load-bearing in their pipeline. To
  match Selvedge they would have to move capture into the producing agent's
  context and delete the model call that is currently their product.

That last line is the whole strategy. **Determinism, not liveness, is the wedge.**

### Update 2026-08-06 — OpenLore scopes the determinism claim

The paragraph above needs a scope qualifier as of this date.
[`clay-good/OpenLore`](https://github.com/clay-good/OpenLore) (258★, v2.1.8,
MIT, actively shipping) is a **deterministic, local-first,
zero-LLM-in-the-hot-path MCP server** — its tagline is nearly word-for-word our
determinism claim. Mechanism: tree-sitter static analysis, one-time repo
indexing into a call graph (application code + IaC + decisions as shared
node/edge primitives), SQLite store under `.openlore/`, 18 languages + 12 IaC
ecosystems, 73 MCP tools across three presets (13 in the default "substrate"
preset, including `record_decision`, `recall`, `verify_claim`). Its tool surface
includes env-var, route, and schema inventories — our three illustrative entity
types, as first-class graph nodes.

So: **"zero-LLM determinism" is no longer unique, even in the unfunded tier.**
The "not copyable without re-architecting" argument stays true — but only
against the funded competitors whose pipelines have an LLM as the load-bearing
part. Against OpenLore, determinism is shared ground, and the separation is
elsewhere:

- **Testimony vs. derivation.** OpenLore *derives* memory from code state — a
  re-derivable index, refreshed by re-running the analysis. Selvedge *captures
  testimony* — what the agent said at change time, in the context that had the
  reasoning — which nothing can regenerate after the fact. (This is the same
  re-derivable-vs-testimony distinction the architecture doc's trust-tier work
  formalizes; OpenLore's entire store sits in the re-derivable tier.)
- **They purge rejected decisions; we keep them — verified at source.**
  `purgeInactiveDecisions` (`src/core/decisions/store.ts`, docstring: "Drop all
  inactive decisions — their content is already in ADRs / spec.md") runs after
  every decision sync (`src/core/decisions/syncer.ts`), and `INACTIVE_STATUSES`
  includes `rejected`. Precision matters here, and it cuts two ways
  (re-verified at file/line 2026-08-05 — see
  `launch/v0.3.10-press-run/competitive-brief-2026-08-05.md` § OpenLore):
  a decision **approved and synced, then later rejected** leaves its content
  as an annotation in the synced spec/ADR markdown — the **queryable record**
  is what disappears. A decision **rejected without ever being approved**
  leaves nothing durable at all by default: `handleRejectDecision` writes
  nothing to spec/ADR, the syncer syncs only approved/auto-approved decisions,
  and the `decision_rejected` telemetry emit is opt-in (`OPENLORE_TELEMETRY`).
  The purge docstring's "their content is already in ADRs / spec.md" is true
  for synced decisions only. Nothing in OpenLore can answer `prior_attempts`'
  question in either case. This is a consequence of derivation, not an
  oversight: re-derivable memory has no reason to retain what the code no
  longer contains.
- **Economics.** 73 tools (13 in the default preset) vs. Selvedge's 8 at a
  CI-verified 3,705-token schema tax. State both numbers precisely; don't
  round theirs up for effect.
- **The AST fork is now occupied, which validates the non-goal.** OpenLore took
  the parser path (tree-sitter, 18 grammars, 12 IaC ecosystems — the dependency
  surface Selvedge refuses). Derive-state vs. capture-intent is now a real fork
  with a product on each side. Say so plainly; don't chase their inventories.

One genuine convergence: `verify_claim(decision-current)` is functionally
`stale_decisions` — two products independently shipping decision-currency
checking is validation for the staleness bet, not a loss.

### Update 2026-08-29 — the literature arrives: Zero-Mem and the delivery evidence

`docs/prior-art.md` (committed 2026-08-11) is the companion source of truth
for what the literature says — five papers, every quoted string verified at
source. This update folds its consequences into the positioning argument; if
a post cites a paper in a way that file does not support, the post is wrong.

**Determinism is scoped a second time, now from the literature.** The
2026-08-06 update above scoped "zero-LLM determinism" against products
(OpenLore). Zero-Mem (arXiv:2607.29377, submitted 2026-07-31) scopes it
against research: a published memory pipeline in which "no step outside final
question answering invokes an LLM or consumes LLM input or output tokens."
That is the strongest external validation the determinism claim has — a
deterministic, zero-generative-call memory architecture is now a published
result, not a Selvedge idiosyncrasy — and it is also the final word on
determinism-as-differentiator: shared ground in the unfunded product tier
(OpenLore), shared ground in the literature (Zero-Mem). It builds nothing in
our lane — long-context QA over conversation logs, no notion of a rejected
path — so the separation stays where the OpenLore update put it: append-only
testimony, rejected paths retained and queryable. One precision rule travels
with any citation: Zero-Mem's "zero-token" means zero *generative* calls —
encoders still run, and the paper says so plainly. The precise paraphrase
("no generative model call in the memory path") also happens to describe
Selvedge, so precision costs nothing and the loose version is falsifiable in
one click.

**The delivery-vs-storage evidence joins the argument — cited with its
tension stated.** "Delivery, Not Storage" (arXiv:2607.20972) measured
agent-voluntary memory use against a pre-seeded store at "0 memory operations
in 114 turns," while deterministic injection delivered in every seeded run.
That is the measured justification for the v0.3.10 SessionStart/PreCompact
hooks: memory as a harness property, not an agent choice. But `log_change`
is an agent-invoked tool — the paper's failure column contains our own write
path — so citing it as unqualified support for Selvedge is a positioning
error. The defensible framing: this is *why* capture and delivery are pushed
at the agent by the harness (prompt block, hooks, capture-time nudges)
rather than left to its discretion. And it is **one** paper, not two:
nothing else in `docs/prior-art.md` measures pull-model memory going unused
(PROJECTMEM is convergent design, not an adoption measurement). The "two
2026 papers" phrasing in the v0.3.10 changelog and README overcounts; the
changelog stands as an append-only record, the README line is a live
correction to make (`docs/outreach/determinism-copy-changes.md` § R3).

**Rejected paths now has prior art in the literature — cite it as prior art,
never as evidence.** MERIT (arXiv:2608.05906) stores "observed unsuccessful
directions" alongside verified corrections in text-to-SQL repair — and its
own ablations are substantially negative: "negative memory contributes
modestly," the typed memory is not reliably separated from untyped
retrieval, and a Reflexion baseline beats it on one of its two benchmarks
(at substantially higher inference cost). PROJECTMEM (arXiv:2606.12329) is
the nearest architectural neighbor — local-first, append-only, typed events
over MCP, with a deterministic pre-action gate that "warns an agent before
it repeats a previously failed fix or edits a known-fragile file" — and it
is evaluated only as a self-study by its own authors. Two consequences.
First, "nobody else surfaces rejected paths" is a claim about the
line-attribution *competitors* — where it remains true, and structural (see
the next section) — not a claim to make about the literature. Second,
Selvedge's version of the bet is a *different* claim from MERIT's:
agent-stated rejections retained in the codebase they belong to, versus
model-generated failure records on a QA-style benchmark. Different, and
untested — say untested; Phase 2.24 exists to change that. PAST-Bench
(arXiv:2608.04003) is the standing reason we ship demo transcripts instead
of a benchmark table: a headline gain is not evidence the memory pathway
caused it.

## Why "rejected paths" leads, not the blame metaphor

`git blame` is a *retrospective* frame — it answers "who did this to me." It has
carried Selvedge this far, but it puts Selvedge inside a category ("git blame for
AI agents") that now has five entrants and a naming convention. Competing on
being a better blame is competing on the axis everyone else picked.

Tried-and-rejected paths is a *prospective* frame — it answers "what should I
not do next." Nobody else surfaces it. `docs/comparison.html` already contains
the sharpest statement of this, and it is currently buried as the fourth of four
"why X matters" sections, behind "why captured live":

> "None of the line-attribution competitors surface rejected paths at all."

That sentence should be promoted to the top of every comparison surface. Line
attribution answers *who wrote this and roughly why*. It structurally cannot
answer *has this been tried, and how did it turn out* — because a line-oriented
store has no notion of an entity that persisted across a try/revert/retry cycle.
This is not a feature gap the competitors can close in a release. It is a
consequence of their data model.

## The third-party support

The strongest external validation is not a review of Selvedge. It is a
practitioner's objection to a competing approach.

On the Hacker News thread for **Contextual Commits** (an open standard for
capturing the "why" in Git history), user **0x457** wrote:

> "I'm 99% sure that grep won't find your commit because you rejected
> 'oauth-library' and grepping for 'auth' rejection. Given that LLM will make up
> category name, it will just get worse unless there is deterministic
> enforcement."
>
> — <https://news.ycombinator.com/item?id=47354263>

Why this quote is worth more than a favorable review:

1. It is **unprompted and adversarial** — a skeptic attacking someone else's
   product, not a fan praising ours.
2. It **names both halves of the wedge in one breath**: rejected paths ("you
   rejected 'oauth-library'") *and* the determinism requirement ("unless there is
   deterministic enforcement").
3. It states the failure mode as **inevitable** — "it will just get worse" —
   which is the argument that LLM-generated categories degrade a corpus over
   time rather than merely being imperfect at the margin.

Use it as the epigraph of the release post and the comparison page. Attribute it
properly, link the thread, and do not paraphrase it into something tidier — the
roughness is what makes it read as real.

The broader thread sentiment is skeptical of the standard overall (commenters
argued Linux/FreeBSD-style prose commit bodies already solve this, and that a
"no new tools" standard contradicts its own need for validation). That is
context worth knowing before citing the thread: **we are borrowing one
commenter's objection, not the thread's endorsement.** If someone reads the
whole thread expecting support for structured capture, they will not find it.
Frame the citation as "even the critics of structured capture agree the failure
mode is nondeterministic labels."

---

## Approved phrasing

**Lead claims — use these**

- "Knows what was already tried and rejected."
- "No LLM in the path. The same query returns the same answer today and in two
  years."
- "A templated query over reasoning the agent wrote itself — not a second model's
  guess about a diff."
- "Line attribution tells you who wrote it. Selvedge tells you what not to write
  next."
- "The deterministic newcomer proves the category — and proves the gap: it
  purges rejected decisions; Selvedge keeps them."

**Supporting claims — fine, but not the headline**

- Entity-granular (`users.email`, `env/STRIPE_SECRET_KEY`, `deps/stripe`).
- Append-only; `supersede` re-opens a reverted decision without rewriting it.
- Changesets across many entities.
- Captured live, in the producing context.

**Retire or demote**

- "Captured live" as the *primary* differentiator. Keep it as a mechanism
  explaining *why* the reasoning is trustworthy — it is the reason there is no
  LLM in the path — but stop asking it to carry the separation.
- "A `git blame` for AI agents" as the opening line. It is a fine second
  sentence, and a good SEO surface, but it frames Selvedge as an entrant in
  someone else's category.

**Never claim**

- That Selvedge is the only tool capturing live. It isn't, and the claim is
  cheap to disprove.
- That competitors' reasoning is always wrong. The accurate claim is that it is
  *unverifiable and nonreproducible* — a second LLM that never saw the original
  prompt is producing paraphrase, and re-running it can produce different
  categories.
- That determinism alone separates Selvedge (as of 2026-08: OpenLore is
  deterministic-native). The compound that separates is append-only testimony —
  captured stated reasoning, rejected paths retained and queryable.
- That OpenLore uses an LLM (it doesn't), or that it "deletes all record" of
  rejections *unconditionally*. The precise claim (scoping amended 2026-08-06;
  file/line evidence in the 2026-08-05 competitive brief): rejected decisions
  are purged from the queryable store after every sync
  (`purgeInactiveDecisions`). A decision approved and synced before rejection
  leaves an annotation in the synced spec markdown; a decision rejected
  without ever being approved leaves nothing durable by default —
  `handleRejectDecision` writes nothing to spec/ADR, and the syncer syncs
  approved decisions only. The "no durable record at all" claim may be made
  **only** with that never-approved scoping attached; unscoped, keep the
  annotation-survives phrasing.
- That "two 2026 papers" recorded pull-model memory tools going unused
  (added 2026-08-29). Only one — "Delivery, Not Storage" (arXiv:2607.20972)
  — measures that; PROJECTMEM is convergent design, not an adoption
  measurement. Name the one paper or drop the count.
- That research shows storing rejected paths / negative memory improves
  agent performance (added 2026-08-29). MERIT's own ablations contradict
  it. Approved form: "an idea the literature has begun testing, with mixed
  results in text-to-SQL repair" — prior art on the idea, never evidence
  the idea works. The same rule caps the Selvedge claim itself: retained
  agent-stated rejections are a different, *untested* claim, and we say
  untested until Phase 2.24 gives it a control.
- That Zero-Mem — or Selvedge — involves "no neural computation," "no
  model," or "no inference" (added 2026-08-29). Zero-Mem's encoders run
  and its paper concedes so explicitly; Selvedge's opt-in fuzzy ranking is
  a local embedding model. The accurate claim for both: no *generative*
  model call in the memory path.
- That PROJECTMEM is evidence that event-sourced agent memory works (added
  2026-08-29). It is a self-study with no control condition, no baseline,
  no external users. Cite it as independent arrival at the same
  architecture — nothing more.
- A benchmark number for Selvedge's memory effect (added 2026-08-29).
  PAST-Bench's finding — agents with the same headline gain differ in
  whether the intended pathway caused it — is the reason. Transcripts
  until the bench exists.

---

## Accuracy corrections to make before shipping

These are errors in current published copy. They matter because the whole
positioning rests on being the precise party in the argument.

1. **Git AI does not use git hooks.** `README.md` line 218 lists Git AI's
   mechanism as "Git hook + Agent Trace alliance." Git AI's own README states it
   "does not rely on Git Hooks (slow + difficult to set up in every repo) and it
   does not wrap the Git binary." Its actual model is agent-invoked
   checkpointing — the agent calls `git-ai checkpoint`, and attribution lands in
   Git notes at commit time. Correct this to "agent-invoked checkpoint → Git
   notes at commit." Leaving it wrong hands a competitor an easy correction that
   discredits the rest of the table.

2. **Git AI's checkpoint model is a sharper contrast than "hook vs MCP."** It
   requires the agent to cooperate by calling checkpoint, and it records at
   commit time — so it is closer to "cooperative snapshotting" than to
   continuous capture. This is a *better* argument for Selvedge than the one
   currently in the README, and it's true.

3. **"AgentDiff" is ambiguous — there are at least two.** `sunilmallya/agentdiff`
   ("git blame for coding agents") and `codeprakhar25/agentdiff` (ed25519-signed
   cross-agent provenance), plus a `getagentdiff.com` marketing surface. The
   comparison table names one "AgentDiff" and attributes Claude Haiku post-hoc
   inference to it. Disambiguate by owner/URL or the table is unfalsifiable.

4. **Star count.** 2.4k, not 2.3k, as of this writing. If a number appears in a
   post, date-stamp it.

---

## Objection handling

**"Isn't determinism just a euphemism for 'no AI features'?"**
No — the agent supplying the reasoning is an LLM. The claim is that the *storage
and retrieval path* has no model in it. The intelligence goes in at write time,
from the context that had it; nothing downstream re-interprets. That's why the
same query is stable across years and model versions.

**"Doesn't the optional semantic extra break the zero-LLM claim?"**
It is opt-in, local (model2vec embeddings, ~30 MB), off by default, and the core
never depends on it. State it plainly rather than hiding it — `prior-attempts
--fuzzy` is a ranking aid over records that were already written deterministically;
it never generates reasoning. The claim is "no LLM writes or rewrites your
history," not "no vectors exist anywhere."

**"Why should I care about rejected paths?"**
Because the expensive failure isn't forgetting why a column exists. It's an
agent confidently re-implementing something the team already killed for a good
reason, six months after everyone who knew that left the context window. See
`docs/demos/prior-attempts.md` — the auth-token-column transcript is the
canonical illustration and should be linked from every post.

---

## Where this shows up

| Surface | Change |
|---|---|
| `README.md` §"How Selvedge compares" | Reorder: prior attempts first, determinism second, entity third, live fourth. Fix the Git AI mechanism row. |
| `README.md` lines 21–23 (hero) | Demote the blame metaphor below the rejected-paths claim. |
| `docs/comparison.html` | Move "Why 'prior attempts' matters" above "Why 'captured live' matters". Add the 0x457 epigraph. |
| `docs/index.html` §"captured live, by the agent" | Retitle around determinism; keep the `blame` terminal demo, add a `prior-attempts` one. |
| `selvedge-site/src/content/docs/compare/*` | Same reorder; `selvedge-vs-agentdiff.md` needs the two-AgentDiffs disambiguation. |
| Next release post | Open with the 0x457 quote; make rejected paths the spine. |
| README comparison table | OpenLore row added 2026-08-06 (purge claim source-verified — see Update above). Keep the row's wording aligned with the "Never claim" precision rules. |
| `README.md` lines ~135–138 ("two 2026 papers") | Name the one paper ("Delivery, Not Storage") or drop the count — see Update 2026-08-29 and `docs/outreach/determinism-copy-changes.md` § R3. The v0.3.10 changelog entry stays as written (append-only record). |
