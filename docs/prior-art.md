# Prior art — citable reference list

**Status:** current as of 2026-08-10. Companion to `docs/positioning.md`; that
file is the source of truth for what we *claim*, this one is the source of truth
for what the literature *says*. If a post cites a paper in a way this file does
not support, the post is wrong.

Scope: published research that bears on Selvedge's positioning — deterministic
memory, agent memory adoption, and rejected-path / negative-experience recall.
Competitor products are tracked separately, in `docs/positioning.md`.

---

## How these were verified

Metadata (title, submission date, categories, authors, comment field) was read
from the arXiv API Atom feed for all five IDs in one query:
<https://export.arxiv.org/api/query?id_list=2607.29377,2608.04003,2607.20972,2606.12329,2608.05906>

Every string presented below in quotation marks was confirmed as an **exact
substring** of the retrieved source — abstracts against the raw Atom feed, and
Zero-Mem's full-text passages against <https://arxiv.org/html/2607.29377v1>. No
quote in this file was produced by a summarizer. Where a number lives in the
full text rather than the abstract, the entry says so, because a reader checking
the abstract alone will not find it and will conclude we invented it.

Titles are reproduced exactly as retrieved, including the source's own
capitalization (PROJECTMEM is uppercase in the title and lowercase in its own
abstract; that is the paper's inconsistency, not a transcription error).

---

## 1. Zero-Mem — deterministic memory, externally validated

| | |
|---|---|
| **Title** | Zero-Mem: Zero-Token Memory Operations for LLM Agents |
| **ID / URL** | arXiv:2607.29377 — <https://arxiv.org/abs/2607.29377> (v1) |
| **Submitted** | 2026-07-31 |
| **Categories** | cs.CL (sole) |
| **Authors** | 11, Yilin Xiao first, Xiao Huang last |
| **Verification** | **Verified** 2026-08-10 — metadata, all quoted strings, and the full-text figures below confirmed at source |

**Finding.** A memory pipeline in which every operation except the final
question-answering read is performed without a generative model call: "no step
outside final question answering invokes an LLM or consumes LLM input or output
tokens", and "Only the final-QA reader invokes an LLM."

**Mechanism.** "An entity--context graph" (the LaTeX en-dash is the source's),
plus a deterministic evidence-calibration stage.

**Evaluation domain.** Long-memory and long-context **question answering** —
LoCoMo and HotpotQA. Not code, not coding agents.

**Relation to Selvedge.** This is the strongest external validation the
determinism claim has, and simultaneously the reason it stops being a
differentiator: a deterministic, zero-generative-call memory architecture is now
a published result rather than a Selvedge idiosyncrasy. It builds nothing in our
lane — QA over conversation logs is not code decisions, and it has no notion of
a rejected path.

**Numbers, with their correct provenance.** The abstract states only "57.6\%
relative to the fastest compared baseline" and names neither the baseline nor a
dataset. Everything specific is **full text, not abstract** — cite it that way:

> "Nevertheless, Zero-Mem requires only 334.77 seconds in total and 0.22 seconds
> per query, reducing memory-operation latency by 57.6% relative to LightMem,
> the fastest baseline."

Table 2 ("Efficiency comparison under a unified experimental configuration")
reports LightMem at `38.44 34.36 877,086 569.54 788.76 0.51` against Zero-Mem
(Ours) at `59.15 52.96 0 0 334.77 0.22`. So the latency comparison is against
the *fastest* baseline, not the most accurate one; Zero-Mem's own `F1 Score`
of 59.15 is nonetheless the highest of the four methods in that table. The
"+10.0%" is the paper's own `Relative Gain/Reduction` row, computed against
GAM.

**Honesty caveat — load-bearing, do not drop.** "Zero-token" means **zero
generative LLM calls**, not zero neural computation. The abstract concedes that
"encoder computation is accounted for separately", and the full text is explicit:

> "Zero-token operation does not imply zero computation, since encoder
> inference, memory organization, retrieval, and deterministic calibration still
> incur processing costs."

**Never paraphrase this paper as "no neural computation", "no model", or "no
inference."** Encoders run, and a deterministic calibration stage runs. The
accurate paraphrase is "no generative model call in the memory path" — which is
also the accurate description of Selvedge, so the precise version costs us
nothing and the loose version is falsifiable in one click.

**Other limits.** Code is unreleased: "After peer review, the code and
implementation details will be available at
https://github.com/TheMoon0815/Zero-mem" (repo existence **unverified**).

**Reception.** Posted to Hacker News as item 49178608 —
<https://news.ycombinator.com/item?id=49178608> — submitted by
`theanonymousone` at 2026-08-05T04:36:44Z, and standing at 101 points with 13
comments **when read on 2026-08-10**. Figures read from
<https://hacker-news.firebaseio.com/v0/item/49178608.json>; the HTML page
rate-limits automated fetches, so quote the Firebase JSON as the source. Score
and comment count are live values — re-read them before quoting, and always
carry the read date, or the post asserts a number that has since moved.

---

## 2. Delivery, Not Storage — the adoption problem, including ours

| | |
|---|---|
| **Title** | Delivery, Not Storage: Cue-Anchored Working Memory as a Harness Property for Coding Agents |
| **ID / URL** | arXiv:2607.20972 — <https://arxiv.org/abs/2607.20972> (v1) |
| **Submitted** | 2026-07-23 |
| **Categories** | cs.AI primary, cross-listed cs.SE — the only multi-category paper here |
| **Authors** | 1 — Swapnanil Saha |
| **Verification** | **Verified** 2026-08-10 — metadata and all quoted strings confirmed at source |

**Finding.** Agent-voluntary memory use collapses to nothing even when a store
is pre-seeded: "a controlled evaluation on a real coding task showing that
voluntary memory use is near zero even with a pre-seeded store (0 memory
operations in 114 turns), that deterministic injection delivered in every seeded
run with zero false alarms". Its thesis is that the second memory tier "must be
a harness property, not an agent choice."

**Evaluation domain.** Coding agents and agent harnesses — the only paper in
this list evaluated in our lane. Not a public benchmark: one real coding task
plus a repeated-compaction decay probe.

**Relation to Selvedge.** Already cited in `README.md` lines 135–138 ("two 2026
papers recorded pull-model memory tools going unused entirely…") as the measured
justification for the SessionStart / PreCompact hooks. That citation is sound,
and the hooks are the right response to it. **But the README says "two 2026
papers" and this file supports only one for that claim** — no other entry here
measures pull-model memory going unused. Either name a second source or drop the
count; see `docs/outreach/determinism-copy-changes.md` § R3.

**But cite it with the tension stated.** This paper's headline measurement is
that an *agent-invoked* memory tool goes unused at 0 operations in 114 turns.
`log_change` is an agent-invoked MCP tool. The paper cuts against the pull model
as much as it supports the hooks, and **citing it as unqualified support for
Selvedge is a positioning error** — a reader who opens it finds our own write
path in the failure column. The defensible framing is the one the README already
uses: this is why capture and injection are pushed by the harness rather than
left to the agent's discretion.

**Quote precisely.** "0 memory operations in 114 turns" is verbatim, in
parentheses, and the word "voluntary" sits directly before it. The phrase
*"facts surviving all 138 compact-resumes"* is **not** in the paper — it is a
paraphrase that has circulated in our own notes. The actual clause is "the same
facts injected from a harness-owned store arrive intact through all 138
compact-resumes as the final summary carries none." Use that, or use the
verbatim fragment "138 compact-resumes" alone.

**Other limits.** Single author, single task, no baseline system comparison. The
decay probe is "stay absent from 106 of 108 compactions" — two compactions did
retain the facts; do not round it to 108/108. The paper also asserts its trigger
vocabulary is "a composition no surveyed academic or shipped system provides",
which is a competitive claim we have not independently checked — **unverified**,
and not ours to repeat.

---

## 3. PROJECTMEM — convergent design, not evidence

| | |
|---|---|
| **Title** | PROJECTMEM: A Local-First, Event-Sourced Memory and Judgment Layer for AI Coding Agents |
| **ID / URL** | arXiv:2606.12329 — <https://arxiv.org/abs/2606.12329> (v1) |
| **Submitted** | 2026-06-10 |
| **Categories** | cs.AI (sole) |
| **Authors** | 2 — Ripon Chandra Malo, Tong Qiu |
| **Comment field** | "12 pages, 5 figures, 1 table. Code: https://github.com/riponcm/projectmem" (repo **unverified**) |
| **Verification** | **Verified** 2026-08-10 — metadata and all quoted strings confirmed at source |

**Finding.** A local-first, append-only typed event log served to coding agents
over MCP, with "a deterministic pre-action gate that warns an agent before it
repeats a previously failed fix or edits a known-fragile file." Five event types:
"issues, attempts, fixes, decisions, and notes". Framed as "Memory-as-Governance."

**Evaluation domain.** A self-study by the authors on their own tool: "a
two-month self-study across 10 projects comprising 207 logged events."

**Relation to Selvedge.** The nearest prior art in the literature — local-first,
append-only, typed events, deterministic projection, MCP-served, offline. Its
pre-action gate is the same instinct as `prior_attempts`. Notably it claims the
log serves as a provenance trail **without** making any tamper-evidence or
hash-chain claim, which matches Selvedge's actual posture: `selvedge verify`
asserts data correctness, not tamper-evidence, and there is no hash chain in the
codebase.

**Honesty caveat — load-bearing.** This is a **self-study, not a controlled
benchmark**: no control condition, no baseline, no external users, authors
evaluating their own system. **Cite it as convergent design, never as evidence
that the design works.** "An independent group arrived at the same architecture"
is supportable. "Research shows event-sourced memory reduces repeated failures"
is not, and this paper cannot carry it. The cost figure is hedged in the source
too — "estimated 5,000-20,000 tokens per session" — so quote the word
"estimated" if you quote the range.

**When quoting the pre-action gate, do not truncate it.** The sentence continues
"...or edits a known-fragile file." Dropping the second clause understates the
feature and reads as selective quotation if anyone checks.

---

## 4. PAST-Bench — headline gains are not evidence of mechanism

| | |
|---|---|
| **Title** | PAST-Bench: Benchmarking the Foundations of Recursive Self-Improvement in Personal Agents |
| **ID / URL** | arXiv:2608.04003 — <https://arxiv.org/abs/2608.04003> (v1) |
| **Submitted** | 2026-08-04 |
| **Categories** | cs.CL (sole) |
| **Authors** | 9, Shuhan Xue first, Ling Yang last |
| **Comment field** | "Code: https://github.com/Gen-Verse/PAST-Bench" (repo **unverified**) |
| **Verification** | **Verified** 2026-08-10 — metadata and all quoted strings confirmed at source |

**Finding.** Matched-condition evaluation (retained experience on vs. off) across
"26 scenarios", "204 episodes", seven base models and four agent frameworks,
finding that "Agents with the same headline gain can differ markedly in whether
that gain is supported by evidence of the intended pathway."

**Evaluation domain.** Personal AI agents — memory, procedural reuse,
information gathering, update. Not coding agents.

**Relation to Selvedge.** Methodological, not competitive. It is the citation for
*why we do not claim a benchmark number*: a headline improvement can be real and
still not be evidence that the memory pathway caused it. Useful when someone asks
why Selvedge ships demo transcripts instead of a benchmark table.

**Do not describe it as a pure benchmark paper.** It ships an intervention system
alongside the benchmark — **Hermes+**, extending Hermes with "five targeted
interventions across stages of the agent loop." The paper also reports that
"improvement is real but uneven across capabilities" and that Hermes+'s benefit
remains capability- and model-dependent. Calling it a benchmark-only paper
misreads half of it.

---

## 5. MERIT — prior art on the rejected-path idea, with negative results

| | |
|---|---|
| **Title** | Causal Episodic Memory for Feedback-Driven Agent Repair |
| **ID / URL** | arXiv:2608.05906 — <https://arxiv.org/abs/2608.05906> (v1) |
| **Submitted** | 2026-08-06 |
| **Categories** | cs.CL (sole) |
| **Authors** | 5, Khang Nhat Hoang Vo first, Tho Quan last |
| **Verification** | **Verified** 2026-08-10 — metadata and all quoted strings confirmed at source |

Note: **MERIT does not appear in the title.** The system is named only in the
abstract ("We introduce MERIT, a training-free agent"). Cite the title as
retrieved and gloss the name.

**Finding.** A training-free repair agent carrying "an online dual-polarity
memory of oracle-verified corrections and observed unsuccessful directions" —
i.e. it stores what failed, not only what worked.

**Evaluation domain.** Text-to-SQL, on Spider and BIRD, with Qwen2.5-7B-Instruct
frozen and identical initial predictions and repair budgets. Spider 66.34% →
69.79%; BIRD 47.35% → 48.44%.

**Relation to Selvedge.** **This is the closest published prior art to the
rejected-paths idea, and it must be listed as prior art on the idea — never as
evidence that the idea works.** The paper's own ablations are substantially
negative, and they are stated here inline so nobody has to go looking:

- "negative memory contributes modestly" — the failed-attempt half of the memory
  is the weaker half in their ablation.
- MERIT "is not reliably separated from untyped dynamic retrieval on either
  benchmark" — typing the memory by polarity did not reliably beat just
  retrieving recent context.
- "Reflexion-style memory reaches \(51.24\%\) on BIRD" against MERIT's 48.44% —
  a baseline beats it on one of its two benchmarks. (The escaped LaTeX is the
  source's.)

Two mitigations that must travel with those three, or the summary is unfair to
the paper: the Reflexion result comes "at substantially higher inference cost",
and the authors themselves report "Paired analyses provide clear evidence for the
Spider gain but weaker evidence on BIRD" — so the BIRD comparison is soft in both
directions. Their most consistent positive ablation is that "schema-local
experience provides the most consistent benefit."

**How to use it.** "Storing rejected approaches is an idea the literature has
begun testing, with mixed results in text-to-SQL repair" is accurate. "Research
shows negative memory improves agent performance" is **contradicted by this
paper's own ablations** and must never be written. If a reader raises MERIT as
counter-evidence against Selvedge, the honest answer is that MERIT tests
*model-generated* records of failed attempts on a QA-style benchmark, whereas
Selvedge retains *agent-stated* rejections in the codebase they belong to — a
different claim, still untested, and we should say untested.

---

## Summary

| Paper | ID | Domain | Cite it for | Never cite it for |
|---|---|---|---|---|
| Zero-Mem | 2607.29377 | Long-context QA | Determinism is now a validated architecture | "No neural computation"; anything in code/decisions |
| Delivery, Not Storage | 2607.20972 | Coding agents | Why memory is harness-pushed, not agent-pulled | Unqualified support — it measures our write path at zero |
| PROJECTMEM | 2606.12329 | AI coding agents (self-study) | Convergent design; independent arrival at the same architecture | Evidence that the architecture works |
| PAST-Bench | 2608.04003 | Personal agents | Headline gains ≠ evidence of mechanism | A benchmark-only paper; it ships Hermes+ too |
| MERIT | 2608.05906 | Text-to-SQL repair | Prior art on retaining failed attempts | Evidence that negative memory works |

**Standing rule.** Every citation from this file carries its arXiv URL and its
submission date. Any figure not present in this file has not been verified — do
not add one to a post without adding it here first, with its provenance
(abstract vs. full text) recorded.
