# Turning feedback into product decisions

Report a concrete problem through [GitHub issues](https://github.com/masondelan/selvedge/issues), or link an existing discussion. Include your Selvedge version, coding agent, expected behavior, actual behavior and a minimal example. Remove secrets and private project content. A synthetic example is enough; sharing a project database is unnecessary.

## Intake and review

Maintainers track substantive questions and reports from issues and public discussions. A comment is evidence of a question or problem, not proof that the commenter has installed the tool or that a proposed feature improves outcomes.

Each item records:

- Source link and date, the problem and affected workflow, and what was actually observed.
- Current released behavior, reproduction or uncertainty, and any existing feature, workaround or related issue.
- Classification: explanation/documentation, bug, or feature hypothesis; an owner and the next action.
- Decision: investigate, accept, defer or decline, with a reason and a condition that would reopen the decision.
- Implementation and release links when available, plus whether the original discussion has received a response.

Check for an existing item before creating another. Reproduce bugs against a released version using temporary data. Separate the requested solution from the underlying need. Evaluate frequency, severity, affected users, available workarounds, maintenance cost and fit with local storage, deterministic processing and explicit telemetry consent. One well-supported correctness bug can justify a fix; a popular suggestion still needs a concrete use case.

For features, define the smallest experiment and acceptance criteria before implementation. A deferred request stays visible with its reason and revisit condition. An accepted idea is not a release promise. Close the loop after shipping, with the actual version and any remaining limitations.

## Publishing and responding

Agent-assisted work follows a review loop: collect context, draft where permitted, independently verify claims against released code and documentation, check the destination's current rules, and publish through an authorized account. Routine review belongs to the project coordinator under the account owner's standing authorization. Ask the owner only for an unresolved decision or a required access, spending or agreement step.

Read the full discussion before replying. Answer the question before linking the product; identify project affiliation, use concrete examples and ask for specific missing evidence. Record the destination and publication receipt, and check for an existing send before retrying. Follow new questions and promised updates; avoid empty bumps, duplicate promotion or artificial engagement.

Use `CHANGELOG.md` and the released tag as the source for shipped claims. Describe recorded decisions as recorded, and `confidence: exact` as provenance rather than correctness. Automatic lifecycle hooks are Claude Code-specific. The log is **tamper-evident** against casual or accidental modification, not tamper-proof against someone controlling the file. Do not claim exclusive capabilities, research-proven benefits or measured improvements without evidence.

Platform eligibility is separate from technical review. Follow current [DEV AI guidelines](https://dev.to/guidelines-for-ai-assisted-articles-on-dev), [X automation rules](https://help.x.com/en/rules-and-policies/x-automation), and [Reddit app rules](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy), plus community-specific requirements. If a channel prohibits generated replies, retain a factual internal brief and track the unresolved question. Review approval does not override a platform restriction.

## Correcting a recorded decision

An explicit rejection can still be wrong. `confidence: exact` means the outcome was stated in the log; it does not certify the decision's truth.

Use the existing `supersede` operation to reopen a mistaken rejection or revert, preserving the original and recording why it no longer stands:

```sh
selvedge prior-attempts users.api_key --json
selvedge supersede users.api_key --supersedes <original-event-id> \
  -r "The original rejection was based on an incorrect requirement."
selvedge prior-attempts users.api_key --json
```

Replace the placeholder with the original event ID. The resulting trail reports `reopened` and links the superseding event. It does not rewrite the original row. `expires_when` surfaces a need to re-examine a decision; it does not automatically supersede it.

From v0.3.13, the Claude Code session-start summary includes expiry and manual-review context wherever the affected decision appears. Superseded decisions leave the revisit list while unrelated same-path decisions remain eligible. `selvedge stale --json` provides the structured flags; `selvedge verify` checks log integrity, not whether the advice remains correct. External anchoring is a separate design question, not a shipped guarantee.
