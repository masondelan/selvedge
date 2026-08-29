# Demo transcript — `prior_attempts` changes an agent's plan

> Checked-in transcript for the v0.3.7 `prior_attempts` demo. The screen
> recording lives alongside it as `prior-attempts.mp4` (recorded separately;
> see [version note](#recording-notes)). This Markdown copy is the bit-rot
> defense — if the recording ages out or the tool's response shape changes,
> this transcript is the source of truth for what the demo *showed*.
>
> **The point in one line:** an agent about to re-implement something calls
> `prior_attempts` first, learns it was tried and reverted before, reads *why*,
> and changes its plan — instead of repeating a rejected approach.

---

## Setup

A repo that's been using Selvedge for a while. Months ago, a previous agent
session tried storing a long-lived per-user auth token in a database column,
then reverted it a couple of days later when the team moved to short-lived
JWTs. That history is in the Selvedge store:

```
$ selvedge diff users.auth_token
┌──────────┬─────────────┬─────────────┬──────────────────────────────────────────────┐
│ when     │ change_type │ entity_type │ reasoning                                      │
├──────────┼─────────────┼─────────────┼──────────────────────────────────────────────┤
│ Mar 14   │ remove      │ column      │ Reverted: tokens in the DB meant we couldn't   │
│          │             │             │ revoke without a write; moved to short-lived   │
│          │             │             │ JWTs verified statelessly. Column unused.      │
│ Mar 12   │ add         │ column      │ Store a per-user auth token so the mobile app  │
│          │             │             │ can stay signed in across restarts.            │
└──────────┴─────────────┴─────────────┴──────────────────────────────────────────────┘
```

## The session

A new task comes in. The user asks the agent to keep mobile users signed in
across app restarts. The agent's first instinct is the obvious one — add a
persistent token column.

> **User:** Mobile users get logged out every time they reopen the app. Can we
> keep them signed in across restarts?

> **Agent:** I'll add a persistent auth token column to `users` so the app can
> re-authenticate silently on launch. Before I write the migration, let me check
> whether this entity has any prior history worth knowing.

The agent calls `prior_attempts` on the entity it's about to change — **before**
writing anything:

```jsonc
// tool call
prior_attempts({ "entity_path": "users.auth_token" })

// tool result  (default min_confidence = "proximity_high")
[
  {
    "entity_path": "users.auth_token",
    "change_type": "add",
    "timestamp": "2026-03-12T17:40:11Z",
    "reasoning": "Store a per-user auth token so the mobile app can stay signed in across restarts.",
    "outcome": "reverted",
    "confidence": "proximity_high",
    "outcome_reasoning": "Reverted: tokens in the DB meant we couldn't revoke without a write; moved to short-lived JWTs verified statelessly. Column unused."
  }
]
```

The result is a single high-confidence hit: this exact change was **tried before
and reverted** within two days. The `outcome_reasoning` carries *why* it was
rejected — DB-stored tokens couldn't be revoked cheaply, and the team chose
stateless short-lived JWTs instead.

> **Agent:** Heads up — `users.auth_token` was already tried and reverted (Mar
> 2026). The reason it was pulled: tokens in the database couldn't be revoked
> without a write, and the team standardized on short-lived JWTs verified
> statelessly. Re-adding the column would walk straight back into that.
>
> Instead, I'll keep the JWT approach and solve the "logged out on restart"
> problem the way that's compatible with it: issue a **refresh token** the app
> stores in the OS keychain (not a DB column), and add a stateless
> `/auth/refresh` endpoint that mints a new short-lived JWT. That keeps
> revocation cheap and doesn't reintroduce the rejected column.

The agent then logs its *own* decision so the next session inherits this
reasoning too:

```jsonc
log_change({
  "entity_path": "api/v1/auth/refresh",
  "change_type": "create",
  "entity_type": "endpoint",
  "reasoning": "Keep mobile signed in across restarts WITHOUT a DB token column — that was tried and reverted (revocation cost). App holds a refresh token in the OS keychain; this endpoint mints a fresh short-lived JWT. Stays on the stateless-JWT path.",
  "changeset_id": "mobile-persistent-login",
  "agent": "claude-code"
})
```

## Why this is the wedge

The line-attribution tools (AgentDiff, Origin, Git AI, BlamePrompt) can tell you
*who wrote line 42 and roughly why*. None of them can answer the question the
agent actually had: **"has this been tried before, and how did it turn out?"**
`prior_attempts` answers it from reasoning the agents wrote live, surfaces the
*rejected* path with the reason it was rejected, and does it conservatively —
the default only returns the clear "tried-then-reverted" signal, so an empty
result is a trustworthy "nothing to worry about" rather than noise.

No second LLM is involved anywhere in that result — it's a templated query over
the agents' own captured intent. The agent didn't repeat a mistake the team
already paid to learn.

## Recording notes

- The `.mp4` is version-tagged (`prior-attempts-v1.mp4`, etc.) so a re-record
  after a response-shape change is obvious.
- `prior_attempts` is **pull-only** in v0.3.7 — the agent chose to call it. The
  push-model variant (auto-warn on `log_change`) is deferred; see the roadmap.
- The `outcome` here is inferred from add→remove proximity (the v0.3.7
  heuristic), which is still what an implicit removal like this one gets.
  Since v0.3.11, explicit `reject` / `revert` change types report
  `confidence: "exact"` instead — stated outcome, not inference — without
  changing this transcript's shape.
