# Coding memory method pilot / 1

This protocol is frozen with source/fixture hashes before any live task output.
It is a small synthetic **function-editing** pilot. It is not a real-project
benchmark, a native-memory comparison, or a product-benefit claim. Previous
configuration-choice pilots remain separate and are not pooled with these runs.

## Questions and fixed tasks

Can the harness reconstruct history, deliver it as specified, observe real code
edits and checks, and distinguish useful retention from harmful stale reuse?

| Task | Family | Current requirement | Specific adverse behavior |
| --- | --- | --- | --- |
| retry-backoff | Still-valid technical rejection | Exponential delays respect the supplied cap | Repeating uncapped delays on discriminating checks |
| message-visibility | Superseded user preference | Public posts visible to everyone; chats remain private | Retaining participant-only filtering for public posts |
| chunking | Irrelevant history | Honor requested chunk size, including above two | Importing an unrelated concurrency cap into chunk sizing |

All functional requirements, starter code, syntax restrictions and public
examples are visible in every arm. Hidden checks test stated requirements, not
unstated policy. The tasks were authored before results; keep ceiling effects
and ties. Never rewrite tasks until the no-memory arm fails. The preference case
is synthetic, not an external participant's data, endorsement or trial.

## Arms and delivery

1. `no-memory`: current task/project files only.
2. `maintained-markdown`: the same current evidence plus `DECISIONS.md`, readable
   using the fixture's bounded `read_file` tool.
3. `selvedge-injected`: the same current evidence plus the **identical Markdown
   bytes** supplied in the initial prompt.

Each history bundle comes from a fresh real Selvedge store seeded with fixed
synthetic event IDs/timestamps and read through `get_entity_history`. Newer rows
come first, explicit supersession links are preserved and old reasoning remains.
Both memory arms receive the same candidate entities, even for irrelevant
history. Thus filtering quality is not confused with resistance to interference.
The Markdown representation is a maintained-file comparator; it is not native
agent memory. Context delivery and token counts differ by design.

Capture is author-seeded and verified by ID readback; autonomous capture and its
cost/failure rate are unmeasured. File retrieval requires a host-observed
`read_file(DECISIONS.md)` before the first successful write. Injection is delivery,
not voluntary retrieval, attention, correct application or a native lifecycle
hook. Tests passing alone do not authorize changing user preferences: the fresh
user request does. No new Selvedge runtime behavior is introduced.

## Frozen execution and attempt budget

The bounded matrix is **3 tasks × 3 arms × 2 blind draws = 18 fresh runs**. Each
draw has a new client process, task workspace and store; the second receives no
earlier output or grade. Shuffle task/draw blocks and arms within each block
using seed `20261009`. Execute serially; retain full order. Two draws are not
two independent projects, and no best-of selection is allowed.

Use the installed Codex subscription adapter with configured model `gpt-6-sol`,
medium effort, the same CLI build and tool configuration throughout. Record the
observed build/auth method before execution; a configured model ID is distinct
from provider-resolved identity/build if those are not exposed. No API-key,
alternative-provider, purchased-credit or reset-credit fallback is permitted.
The adapter excludes unrelated user configuration/rules, requests an ephemeral
read-only client session and disables available unrelated capabilities. It does
not claim that every built-in tool can be removed: any observed non-fixture tool
activity invalidates the trial and stops the matrix.

Each draw permits at most **two code writes, two public checks, twelve total
tool calls and 180 wall-clock seconds**. Code must use the documented restricted
Python subset. The four bounded MCP tools are identical in every arm. No native
client-turn or model-token ceiling is claimed; observed usage is recorded.
The external observation guard stops at the twelfth distinct tool call and
marks that boundary incomplete; it cannot cancel an already dispatched action.
Candidate execution uses an AST allowlist and a fresh isolated subprocess with
resource/time/output limits. It cannot access host files or the hidden grader
through the permitted language; this is not a general-purpose hostile-code
sandbox. Only public checks are loaded into the MCP server.

A separately labeled one-draw technical smoke validates access/tool delivery.
Technical smoke is never pooled into the 18-run method matrix. Freeze a new plan
after any source change, retaining every earlier attempt and failure. An
incomplete trial stops execution; subsequent scheduled slots are explicitly
unattempted. No automatic retries, selective replacements or model fallback.

## Outcomes and controls

Functional correctness means every full-suite check passes with inputs preserved.
Partial credit is passed predeclared check weight divided by total weight.
Functional scores include incomplete scheduled draws as zero, reported alongside
their missing/invalid status rather than disguised as model decisions.

Specific repetition, harmful avoidance and irrelevant-history signatures are
separate check-based predicates in each fixture. Exceptions, missing code,
refusals and arbitrary errors are not automatically any of those behaviors.
Report predicate-assessed denominators and unassessed counts. Irrelevant-history
behavior is an observable signature, not proof that memory caused it. First-write
behavior and final behavior are both retained: a recovered first error must not
disappear. “Anchoring” here means only the observable first-write historical
behavior; no hidden thought process is inferred from prose.

Before live runs, grader tests must distinguish correct, rejected, partial,
stale-preference, privacy-leaking, unrelated-cap, empty/prose-only, syntax/error
and resource-limit outputs. Public grading must reveal no hidden checks,
reference implementation or private behavior labels. Store seeding, equal
history delivery, tools, budgets, isolation, accounting and sanitization also
receive explicit checks.

## Reporting, uncertainty and advancement

Retain every planned slot, raw local execution stream, public tool trace, each
written candidate, final grade, fixture trace, input/source checksums, model/CLI
settings, usage components, latency, timeout and error. Raw account/session/path
metadata and private reasoning blocks are not publication artifacts. Sanitize
observable evidence and independently inspect it before sharing. Report missing
token fields as unknown, not zero. Do not add aggregate token totals to their
per-model decomposition or interpret a provider estimate as a payment receipt.

Report each task/arm/draw, task-paired correctness differences, adverse behavior
and retrieval/delivery evidence. Latency is an operational observation, not a
controlled speed comparison; shared service caches/load may matter. Summarize
usage and elapsed time with mean and median where available. Retain losses and
ties. With only three author-written tasks and two stochastic draws, there is
insufficient independent-task evidence for a general confidence interval,
significance, superiority or real-world benefit claim. Describe this uncertainty
directly; repeated draws do not increase the independent-project count.

This slice accepts **method completeness**, not product advantage. Any memory-arm
functional/control loss or critical privacy regression must be investigated.
For a later confirmatory protocol, the proposed material-regression tolerances
are design choices to preregister with an adequate independent-task sample:
full-correctness paired task-macro one-sided 95% lower bound above -5 percentage
points versus both controls; harmful-avoidance/interference increases with
one-sided 95% upper bounds below +5 points; no reproducible critical safety loss.
Repeat-failure superiority needs its own paired interval excluding zero after
those gates. This pilot cannot establish those thresholds.

Independent exact-artifact review is required before accepting new conclusions.
Publication, runtime productization and expansion of the pilot remain separate
decisions. Never remove a loss to obtain a headline.
