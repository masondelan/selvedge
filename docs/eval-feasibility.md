# Eval feasibility — can Selvedge produce PAST-Bench-style pathway evidence?

**Status:** assessment only, written 2026-08-10 against Selvedge v0.3.10. No
benchmark was built. Nothing here is a commitment; this is scoping input to
Phase 2.24 (SelvedgeBench, v0.3.18) in `docs/architecture.md`, which already
plans a superset of what is proposed below.

**Question this doc answers:** PAST-Bench asserts, as an independent
third-party benchmark, the thing Selvedge asserts in marketing — that an
outcome number does not prove the memory pathway fired. Selvedge's
append-only log is pathway evidence and `prior_attempts` is the retrieve step
made observable. Can that claim be converted into a measurement, and what is
the smallest honest version of it?

**Short answer:** the *protocol* transfers and is worth borrowing. The *task
substrate* does not transfer at all, and any table putting a Selvedge number
next to a PAST-Bench Δ would be dishonest. A 12-task, 3-arm in-repo experiment
is feasible today on v0.3.10 without touching the core package, and the single
most interesting metric it can report is not the repeat rate — it is the
fraction of avoided repeats that have a retrieval call on record *before* the
first edit.

---

## 0. Verification status

PAST-Bench verifies. Checked against the arXiv abstract page and the full HTML
text on 2026-08-10.

- <https://arxiv.org/abs/2608.04003> resolves, v1, submitted 2026-08-04,
  sole category cs.CL.
- Title: "PAST-Bench: Benchmarking the Foundations of Recursive
  Self-Improvement in Personal Agents".
- Authors: Shuhan Xue, Zixin Ding, Yichen Shen, Yinjie Wang, Zhenfei Yin,
  Yingcheng Wu, Yuxin Chen, Mengdi Wang, Ling Yang.
- The load-bearing sentence is verbatim in the abstract: "Agents with the same
  headline gain can differ markedly in whether that gain is supported by
  evidence of the intended pathway."
- Code released: <https://github.com/Gen-Verse/PAST-Bench> (HTTP 200,
  Apache-2.0 for original code per its README; the paper freezes the framework
  snapshots and adapters at revision `0b56a98`). Full text pulled from
  <https://arxiv.org/html/2608.04003v1>.

One correction to how this paper has been summarized internally: **it is not a
pure benchmark paper.** Roughly half of it is Hermes+, an intervention system
that extends the Hermes framework with five mechanisms (E1 Plan, E2 Render,
E3 Route, E4 Gate, E5 Close). The abstract's own verdict on those interventions
is hedged — "improvement is real but uneven across capabilities", and the
effect "remains capability- and model-dependent". Citing PAST-Bench as a
neutral third-party benchmark is fine; citing it without noting that the
authors also ship one of the systems it scores is not.

Be precise about *how well* Hermes+ does, because "the authors' system wins"
overstates the paper. Hermes+ **ties** base Hermes on Overall persistence-on
score (0.66); it raises Overall Δ from +0.13 to +0.15, reaches the best Update
score (0.74) and gap (+0.24), and its Procedural result "declines slightly".
On its strongest pairing, Hermes+ with GPT-5.4 "ties the benchmark's highest
configuration (Δ = +0.24, Mech 0.80)" rather than beating it. The conflict of
interest is real and worth disclosing; the margin is not large enough to
describe as dominance.

---

## 1. What PAST-Bench actually does

### Unit of evaluation

The unit is a **task family**: an ordered sequence of fresh-session episodes
that share a latent rule, a reusable artifact, a correction, or a pre-seeded
reference. 26 families, 204 episodes total, distributed as:

| Capability | Families | Episodes |
|---|---:|---:|
| Memory | 5 | 41 |
| Procedural reuse | 8 | 64 |
| Information gathering | 6 | 48 |
| Update | 7 | 51 |
| **Total** | **26** | **204** |

All 26 families and 204 episodes are synthetic. The paper states plainly: "All
26 task families and 204 episodes are synthetic. No task contains data from
real users." They were generated from hand-written rules using two model–agent
pairs (Codex with GPT-5.4, Claude Code with Claude Opus 4.6), then checked by
three authors against a six-point list, each family checked by at least one
author.

### Episode roles

Every family is an ordered sequence of fresh-session episodes playing one of
four roles:

- **Cold** — first-contact behavior before any persistence can exist. Reported
  for calibration and headroom, explicitly *not* the persistence-off baseline.
- **Learn** (and, in Update families, an **Update** episode) — deposits the
  target clause, procedure, or correction into the persistence substrate.
- **Evaluation** — probes reuse of that state in a later fresh session **with
  the trigger wording removed** from the prompt.
- **Control** — checks that any gain cannot be explained by prompt shortcuts,
  surface memorization, stale reuse, or writes to the wrong substrate. Four
  named control types: no-retention, distractor, stale, wrong-mechanism.

The framework's volatile context is wiped between episodes. The paper's stated
reason is that both existing regimes — long-context visibility and
uninterrupted sequential task streams — "conflate persistent learning with
in-context propagation". Under strict context clearing, any later-episode
improvement "must therefore flow through the persistent substrate ... not
through residual prompt overlap."

### The matched ablation

Persistence means "benchmark-managed access to state produced or modified by
earlier episodes in the same family: memory records, skills, profile entries,
session-history indices, saved artifacts, and home-state fixtures."

Each evaluation episode is graded twice:

- **w/o-evolve** — the runtime is denied any access to family-produced state.
- **w/-evolve** — access permitted.

The two runs share the same prompt, grader, tool stack, and seed. The paper is
careful about what this buys: "We treat this as a strong design control rather
than a causal proof."

### What is measured

**Task score, per episode** (Equation 1):

```
s_e = σ_e × (0.80 × c_e + 0.20 × r_e)
```

- `c_e` — completion in [0,1]. For action-oriented families, computed from
  audit data (correct recipients, correct tickets updated, correct facts
  output). For open-ended families, an LLM judge scores the final output
  against a rubric. Every family defines its own grader.
- `r_e` — robustness in [0,1], a tool-call error-recovery rate. `ρ` is the
  fraction of distinct erroring tool names that were later called
  successfully, with a floor of `min(success_ratio, 0.5)`; a clean run scores
  1.0.
- `σ_e` — binary safety gate. A safety violation zeros the whole score.

Each episode runs **three independent trials**. Missing or crashed trials
score 0.0.

**Aggregation.** Family score = arithmetic mean of task scores over
evaluation-bucket episodes. Capability score = macro-average over families.
Overall = mean of the four capability scores. The self-evolution gap is
`Δ_f = S^{w/}_f − S^{w/o}_f`, macro-averaged the same way. A gap is only
accepted as evidence of self-evolution when it clears the upper bound set by
that family's control episodes.

**Mechanism-evidence score (Mech)** — the part that matters for Selvedge.
Mech = 1 means the agent completed the full expected persistence cycle
(write → retrieve → correct application); Mech = 0 means the pathway was
entirely absent. Each episode carries an **expectation contract** in its family
YAML naming the expected artifact type (`memory`, `skill`, or `session_search`),
required keyword patterns, minimum write/read counts, and retrieval signals.
The scorer compares the actual trace against that contract.

Artifact quality `q_e` is the mean of the contract-specified components:

- **keyword hit rate** — fraction of expected rule keywords appearing in the
  saved artifact;
- **count-delta score** — `min(n_actual / n_expected, 1.0)` against the
  contract's `min_count_delta`.

Family-level Mech (Equation 2):

```
Mech_f = (1/5) × ( wp + ra + uc + rh + (1 − pr) )
```

- **wp** (write precision) — mean `q_e` over learn episodes. Did the agent
  write the right state?
- **ra** (recall accuracy) — mean over evaluation episodes of the
  grader-assigned content-correctness, but **an episode contributes 0 unless it
  fired the expected retrieval signal**. This is the save→retrieve link.
- **uc** (update correctness) — mean of stale-memory resistance (binary: does
  the artifact show updates/changes/additions) and content-correctness, over
  update episodes.
- **rh** (retention horizon) — `clamp(S_eval_far / S_eval_near, 0, 1)`. Does
  persisted state survive domain shift?
- **pr** (pollution rate) — fraction of entries written in learn episodes that
  are irrelevant or out of scope. Subtracted from 1.

### Scale of the run

Seven base models (GLM-5.1, Kimi K2.6, DeepSeek-V4-Pro, MiniMax-M2.7,
GPT-5.4, Claude Sonnet 4.6, Claude Opus 4.6) against a fixed Hermes framework;
four agent frameworks (Hermes, Agent-Zero, ZeroClaw, nanobot) against a fixed
MiniMax-M2.7 model, plus Hermes+ as the authors' own. Three runs per
configuration.

### The finding this doc is about

Two frameworks reach the same headline gap `Δ = +0.13` — nanobot and Hermes —
but nanobot earns it "from a single capability with no consistent write-then-read
trace", dropping its Mech to 0.57 against Hermes's 0.64. In the paper's words:
"The same headline Δ can hide two completely different ways of getting there."

### Reproducibility, honestly

Good, with three caveats.

1. **The graders are partly an LLM judge.** All runs use MiniMax-M2.7 as judge
   at temperature 0 with a maximum output of 8,192 tokens; the judge model and
   prompt are not varied. Validated against two authors' blinded human scores
   on 48 samples (12 per capability). The paper does not claim the judge
   substitutes for human judgment.
2. **The framework comparison is adapter-mediated, not out-of-the-box.** The
   adapters "select the common model, expose task tools, and implement the
   matched persistence control"; the paper states the comparison "preserves
   each framework's agent loop, but is not a byte-for-byte default deployment."
   Agent-Zero additionally runs at a 1200s per-task wall-clock budget, 4× the
   Hermes budget, reported as "a methodological footnote rather than a fairness
   adjustment."
3. **The authors state the ceiling on their own attribution.** "The current
   mechanism-evidence score measures consistency with an expected persistence
   pathway, rather than establishing causal necessity." They name the stronger
   design they did not run: deleting, replacing, or corrupting a candidate
   artifact and measuring the behavioral change.

---

## 2. Could Selvedge produce comparable pathway evidence?

### 2a. Running Selvedge *on* PAST-Bench: no. Do not attempt this.

The mismatch is fatal to a direct comparison, on three independent grounds.

**The task substrate is personal-assistant work, not repo work.** The 26
families are named things like Preference Adoption, Oncall Handoff Lookup,
Temporary Waiver Audit, Change Freeze Followup, SOP Bootstrap 01–06,
Recall-then-Modify. The worked examples in Appendix A.3 involve sharing meeting
notes with the correct recipients, recovering a "Phoenix freeze date", and
triaging helpdesk tickets against a saved SOP. Selvedge's data model is a
change event on a repo entity path (`users.email`, `env/STRIPE_SECRET_KEY`,
`src/auth.py::login`). There is no honest mapping from `users.auth_token` to
"Phoenix freeze date". Forcing one would produce a number about the adapter,
not about Selvedge.

**The four capabilities do not include Selvedge's wedge.** Memory, Procedural
Reuse, Information Gathering, Update. "Was this approach tried and rejected,
and should I therefore not do it" is not among them. The nearest neighbour is
Update — "can a second write override a first one without leaking the first" —
which is genuinely close to Selvedge's `supersede` semantics, but Update
families test fact correction and rule migration, not avoidance of a rejected
approach. Scoring Selvedge on Update would measure the wrong thing well.

**Selvedge has one artifact type and PAST-Bench's contracts expect three.**
Expectation contracts name `memory`, `skill`, or `session_search`. Selvedge
writes exactly one kind of thing (a change event) and has no skill store and no
session index. Two of three contract types are unrepresentable.

The right conclusion is not "Selvedge fails PAST-Bench". It is that PAST-Bench
is a benchmark for personal agents and Selvedge is not a personal agent. The
paper says as much about its own general-purpose-agent appendix: the Codex CLI
and Claude Code results "do not imply that the two systems are personal agents
or that the result covers every general-purpose agent."

### 2b. Borrowing the protocol: yes, and the paper explicitly invites it.

Appendix C.3, verbatim: "The matched protocol only requires a way to turn
access to retained state on and off. A black-box agent can therefore report
Task Score and Δ when this control is available. Mech requires observable
persistence events. If an agent does not expose these events, Mech is
unavailable. If it exposes memory, skill, or history events, a small adapter
can map them to the benchmark event types."

Appendix C.2 already ran the protocol on two coding agents — Codex CLI and
Claude Code, both with MiniMax-M2.7 fixed, three runs each — and reports
positive matched gaps on all four capabilities (Claude Code Overall
`0.70 / +0.16`; Codex CLI `0.63 / +0.10`). So the protocol demonstrably
survives being pointed at a coding agent. What does not survive is the task
set.

### 2c. What Selvedge already has that maps onto Mech

This is the part that makes the exercise worth doing at all. A store can
generally show you what it wrote; recording that something *read* it is the
rarer property. PAST-Bench's own framing is the citable version: "Mech requires
observable persistence events. If an agent does not expose these events, Mech
is unavailable." How many shipping memory systems record reads is
**unverified** — no survey was done. Do not upgrade this to "most competitors
can't" in any external copy without one.

| PAST-Bench signal | Selvedge equivalent | Where |
|---|---|---|
| write event / artifact | a row in `events` with `entity_path`, `change_type`, `reasoning`, `timestamp` | `selvedge/storage.py` schema, `CREATE TABLE events` |
| write precision `wp` (keyword hit rate, count delta) | keyword match against `reasoning`; row-count delta per episode | same table, plus `validation.py`'s reasoning-quality check |
| **retrieval signal fired** | a row in `tool_calls` with `tool_name='prior_attempts'` and the queried `entity_path` | `SelvedgeStorage.record_tool_call` at `selvedge/storage.py:829`; called from `prior_attempts` in `selvedge/server.py` |
| update correctness `uc` (stale resistance) | `supersede` events and `prior_attempts`' `outcome` / `superseded_by` / `current_status` fields | `selvedge/server.py` `prior_attempts` docstring; `supersedes` column, migration v4 |
| pollution rate `pr` | events written to unrelated entity paths in the same episode | `events.entity_path` |
| retention horizon `rh` | not represented — Selvedge has no near/far evaluation-episode concept | — |

The `tool_calls` table is the asset here. `prior_attempts` records its own
invocation (`storage.record_tool_call("prior_attempts", entity_path=entity_path)`),
so "the retrieve step fired, on this path, at this timestamp" is a two-column
SQL query rather than a transcript-parsing exercise. That is exactly the signal
`ra` needs and exactly the signal the paper says most agents do not expose.

Selvedge also has something the PAST-Bench frameworks do not: a **harness-level
delivery mechanism** that removes the retrieval decision from the agent.
`selvedge/hooks/pretooluse.py` intercepts Edit/Write/MultiEdit/NotebookEdit/Bash
calls on watched paths and blocks them until `prior_attempts` has been queried
for the affected entities in-session, inlining the prior reasoning in the block
message. `selvedge/hooks/sessionstart.py` injects a "Tried before and REVERTED"
digest via `hookSpecificOutput.additionalContext`. Both are toggleable
(`SELVEDGE_HOOK_DISABLE=1`, documented in `README.md`), which makes them a
clean ablation axis, not just a feature.

This matters because of arXiv 2607.20972 (<https://arxiv.org/abs/2607.20972>),
which measures agent-voluntary memory use in a coding harness at "0 memory
operations in 114 turns" even with a pre-seeded store. Selvedge's pull-only
mode — MCP tools present, hook off — *is* the pattern that paper measures at
zero. Anyone citing that paper as support for Selvedge without running the
hook-on/hook-off contrast is citing evidence against their own default. The
experiment below turns that from a positioning risk into a measured number.

### 2d. What would have to be built

Four things, in increasing order of cost.

1. **Per-episode store isolation.** `record_tool_call` stores
   `(id, timestamp, tool_name, entity_path, success, error_msg, agent)` — no
   `session_id`. Retrieval calls can therefore only be bucketed by timestamp,
   not by session. Cheapest fix requires no core change: give every episode its
   own `SELVEDGE_DB` path, which the test suite already does via `tmp_path`
   fixtures (`CLAUDE.md` § Test suite). One DB per episode makes bucketing
   exact and sidesteps the missing column entirely.

2. **A record of hook decisions.** `selvedge/hooks/pretooluse.py` never calls
   `record_tool_call`, so a block leaves no trace in the store; it is visible
   only in the agent transcript. For the delivery-mode arm you need to know
   *the gate fired* separately from *the agent complied*. Two options, both
   harness-side: run the gate via its existing `--dry-run` mode (documented in
   the module docstring as printing the decision as JSON and always exiting 0)
   and log that, or parse the transcript for the block message. Neither touches
   the core package. Do not add block-logging to the core just for the bench.

3. **A task substrate.** This is the bulk of the work and there is no shortcut.
   There is currently no set of repo tasks with a known-rejected prior approach
   and a deterministic check for whether the approach was repeated. Phase 2.24
   already names the intended source — "failed-attempt scenarios derived from
   real repository histories (revert-then-retry cycles; the 2.18 git-import
   trust-tier work supplies exactly these, re-derivably)" — but 2.18 has not
   shipped. For a 12-task experiment, hand-authored fixtures are acceptable and
   honest **provided the writeup says they were hand-authored by the tool's
   author**. PROJECTMEM (<https://arxiv.org/abs/2606.12329>) is the nearest
   cautionary case, but state it accurately: its abstract discloses the design
   plainly — "a two-month self-study across 10 projects comprising 207 logged
   events" — so the problem is not a missing disclosure but the design itself
   (no control condition, no baseline, no external users; see
   `docs/prior-art.md` § 3). Its evaluation is also observational usage rather
   than authored fixtures, which makes our position *weaker*, not stronger:
   fixtures built by the author to have a rejected prior need a louder
   disclosure than PROJECTMEM's did, not a quieter one.

4. **A grader.** PAST-Bench uses per-family graders plus an LLM judge for
   open-ended families. Selvedge should not need the judge: the whole point of
   a rejected-approach task is that "did the agent reintroduce the rejected
   artifact" is a string- or AST-level check with a binary answer. Keep it
   deterministic. `CLAUDE.md`'s no-LLM-in-core rule does not govern a bench
   harness, but a deterministic grader is also simply a stronger result, and it
   removes the one reproducibility caveat PAST-Bench carries.

**What does not have to be built:** anything in `selvedge/`. The experiment
below runs on v0.3.10 as shipped. It uses `prior_attempts`' add→remove
proximity inference rather than the explicit `reject` / `revert` change types
scheduled for v0.3.11, so it is not blocked on Phase 2.17.

---

## 3. The smallest honest experiment

### Shape

**12 tasks. One frozen model. One frozen prompt template. Three trials per
task per arm.** Binary outcome per trial. 12 × 3 × 3 arms = 108 runs for the
core design.

Each task is a repo fixture where:

- a specific approach was tried and reverted, recorded in Selvedge as an
  `add` event followed by a `remove` event on the same entity path, with the
  revert carrying the reason in `reasoning` — the shape
  `docs/demos/prior-attempts.md` already documents;
- a fresh-session task is then given whose most obvious solution *is* the
  reverted approach, with the trigger wording removed (PAST-Bench's rule:
  evaluation episodes "probe reuse of that state in a later fresh session with
  the trigger wording removed");
- a deterministic checker answers one question: **did the final working tree
  reintroduce the rejected artifact?** For the canonical
  `users.auth_token` case, that is "does a migration in the diff add that
  column back". Not a rubric. Not a judge. A grep or an AST predicate,
  committed alongside the task.

Context is cleared between episodes. Each arm gets its own `SELVEDGE_DB`.

### Arms

| Arm | Store | MCP tools | Hooks | What it isolates |
|---|---|---|---|---|
| **A** — no memory | empty | absent | off | floor. What the model does with no help. |
| **B** — pull-only | seeded | present | off | agent-voluntary retrieval. The 2607.20972 condition. |
| **C** — delivered | seeded | present | PreToolUse gate + SessionStart digest on | harness-enforced retrieval. |

A vs C is the "with and without Selvedge" comparison. B is what makes the
result interesting rather than merely favourable — it is the in-repo
replication of the delivery-vs-storage result that Phase 2.24 already lists as
a planned ablation, and the honest test of whether Selvedge's *default* posture
(tools available, hook optional) does anything.

### Metrics

**Primary — repeat rate.** Fraction of the 36 trials per arm in which the
final working tree reintroduces the rejected artifact. Report the 12 per-task
outcomes as a table (3 trials each, so each cell is 0/3 … 3/3), not as a single
mean. PAST-Bench itself reports σ over three runs and refuses to interpret a
+0.02 difference across them as stable; a 12-task binary experiment has less
resolution than that, not more.

**Secondary, and the actually novel one — pathway-evidenced avoidance.** For
every trial that did *not* repeat, query that trial's store:

```sql
SELECT 1 FROM tool_calls
WHERE tool_name = 'prior_attempts'
  AND timestamp < :first_edit_timestamp
  AND (entity_path = :entity OR entity_path LIKE :entity_prefix)
```

Every avoided repeat lands in one of two buckets:

- **avoided-with-pathway** — the retrieve step is on record before the first
  edit;
- **avoided-without-pathway** — the agent avoided the approach for some other
  reason (model prior, prompt phrasing, luck).

The reported number is `avoided-with-pathway / avoided`. This is the direct
analogue of PAST-Bench's `ra`, which contributes 0 for any evaluation episode
that did not fire the expected retrieval signal. **If this ratio is low in arm
B, that is the finding, and it publishes.** It would mean Selvedge's outcome
gain is not being produced by Selvedge's mechanism — precisely the
same-headline-different-pathway case the paper exists to expose.

**Reported alongside, not in an appendix:** tokens and wall-clock added per
episode by each arm. Phase 2.24 makes this a requirement; PAST-Bench models the
practice (Hermes+ costs ~2.5× the tokens of base Hermes, 31,859 vs 12,615 per
episode, for a wall-time increase of only 1.10×, 77.4s vs 70.5s). A memory
system that wins on avoidance while tripling context cost has not obviously
won.

### Pre-registration and falsifiers

Write these down before looking at any arm B or C data.

1. **Floor check — the task substrate must be hard.** If arm A avoids the
   rejected approach on its own in more than 4 of 12 tasks, the tasks are too
   easy and the experiment is void. Rewrite the tasks and rerun arm A before
   any other arm is scored. A tool author constructing tasks his own tool
   passes is the central Goodhart risk here and the floor check is the only
   real defense.
2. **Mechanism falsifier.** If `avoided-with-pathway / avoided` in arm B is
   below 0.5, the pull-only claim is not supported and the writeup says so in
   the summary, not the limitations section.
3. **Token-matched control.** Arms B and C put text in front of the model that
   arm A never sees. A fourth arm **D** — the same reverted-reasoning text
   pasted as a plain prompt preamble, no Selvedge, no tools, no hook — tests
   whether the *store* is doing the work or just the *text*. If D ≈ C, the
   defensible claim shrinks to "Selvedge is a durable, queryable way to get
   that text in front of the agent at the right moment", which is still a real
   claim and a different one. Phase 2.24 already sets a token-matched arm as
   the 2026 reviewer bar. **The A/B/C core is the minimum experiment; A/B/C/D
   is the minimum *publishable* experiment** (144 runs). Do not publish
   without D.
4. **Variance floor.** With 36 trials per arm, a difference of fewer than 3
   trials is not a result. State the threshold before running.

### What gets published

Per-task rows, all arms, all trials, plus the exact task fixtures, the
deterministic checkers, the seeded event pairs, the model and prompt, and the
raw `tool_calls` dumps. "We ran 12 tasks and here is exactly what happened,"
with the tasks attached so a reader can disagree with the tasks themselves.
Negative and mixed results ship with the same prominence as positive ones —
already the stated posture of Phase 2.24's risk register.

---

## 4. What a self-run experiment could and could not prove

### Could

- **That the pathway is observable in Selvedge at all.** This is a claim about
  the tool, not about the world, and it is fully defensible from the
  `tool_calls` table. PAST-Bench says Mech is simply unavailable for agents
  that do not expose persistence events, so being scoreable on `ra` at all is
  the claim. How many other systems clear that bar is **unverified** and should
  not be asserted. Demonstrating that Selvedge can be scored on `ra` is a
  genuine, small, checkable result.
- **That on these 12 tasks, with this model and this prompt, arm X repeated the
  rejected approach N times and arm Y repeated it M times** — with every
  fixture published. This is a narrow factual claim and it survives scrutiny
  because it does not generalize.
- **Whether Selvedge's default posture works.** The B-vs-C contrast answers a
  real product question: does pull-only retrieval happen, or does it need the
  gate? That answer changes the install docs regardless of which way it goes.
- **The cost of the win.** Tokens and latency per arm, reported next to the
  outcome.

### Could not

- **Causal necessity.** PAST-Bench's own limitation applies with more force at
  N=12: mechanism evidence "measures consistency with an expected persistence
  pathway, rather than establishing causal necessity." The stronger design the
  authors name — corrupt or delete the stored artifact and measure the
  behavioral change — is available to Selvedge cheaply (rewrite the seeded
  event's `reasoning` to something wrong and see whether the agent follows it)
  and would be a genuine upgrade if the first experiment is worth extending.
  It is not in the minimum design.
- **Anything about other models.** PAST-Bench's central model result is that
  gains concentrate on different capabilities per model — GPT-5.4 spreads its
  movement across Memory and Update, GLM-5.1 puts nearly half on Update,
  Kimi K2.6 nearly half on Memory. One model at N=12 says nothing about the
  next one.
- **A comparison to PAST-Bench numbers.** Different tasks, different
  capabilities, different graders, different domain. No table may place a
  Selvedge figure beside a PAST-Bench Δ or Mech value. The protocol is
  borrowed; the scoreboard is not shared.
- **Real-world benefit.** The tasks are constructed by the tool's author to
  have a rejected prior. That is the definition of a favourable sample. The
  floor check bounds it; it does not remove it. Say this in the body of the
  writeup.
- **Statistical significance in any conventional sense.** 12 tasks, binary
  outcome, 3 trials. This is a case series, and it should be titled and written
  as one.

### The one claim to avoid

"Selvedge measurably reduces repeated mistakes" is not what this experiment
produces, at any N reachable here. What it produces is: *on 12 published
fixtures, avoidance differed by this much between arms, and this fraction of
avoidances has the retrieve step on record.* The second half is the part
nobody else can currently report, and it is the part worth leading with.

---

## Cross-references

- `docs/architecture.md` § Phase 2.24 — SelvedgeBench (v0.3.18). Supersedes
  this doc as a plan; this doc is scoping input only. Note the sequencing
  constraint recorded there: the bench consumes v0.3.11's `reject`/`revert`
  events and v0.3.12's trust tiers. The 12-task experiment above is
  deliberately designed to run *before* those, using the existing add→remove
  proximity inference.
- `docs/demos/prior-attempts.md` — the `users.auth_token` transcript is the
  template for task fixture 1.
- `docs/positioning.md` — the claim this experiment would convert into a
  measurement is the one in § "The one-line claim".
- `selvedge/storage.py:829` (`record_tool_call`), `selvedge/hooks/pretooluse.py`,
  `selvedge/hooks/sessionstart.py` — the three surfaces the harness reads and
  toggles.
