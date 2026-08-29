"""Claude Code SessionStart hook — deliver memory instead of waiting for a pull.

The PreToolUse gate (v0.3.9.1) covers the case where there is something to
veto. This covers the much more common case where there isn't: the agent is
about to start work and has no idea the store exists.

Why a push at all. Two independent 2026 papers measured pull-model memory
tools going unused — "Delivery, Not Storage" (arXiv 2607.20972) and PROJECTMEM
(arXiv 2606.12329) recorded zero voluntary memory operations across 114 turns
against a pre-seeded store, while deterministic injection landed every time.
An MCP tool an agent *may* call is not the same as context an agent *has*.

Wire protocol (Claude Code hooks):

  - stdin: one JSON object with ``session_id``, ``cwd``, ``source``
    (``startup`` / ``resume`` / ``clear`` / ``compact`` / ``fork``).
  - stdout: a JSON object; the string at
    ``hookSpecificOutput.additionalContext`` is what reaches the model.
    Plain stdout is NOT injected — it is ignored.
  - exit 0 always.

Design posture, inherited from the gate and from the injection-noise risk:

  - **Quiet by default.** Nothing due, nothing reverted, nothing recent →
    emit nothing at all and exit 0. "Why Git Is the Memory Solution"
    (arXiv 2607.14390) measured ungated episode injection *degrading* good
    answers, so the digest is relevance-gated rather than a store dump.
  - **Hard size cap** from ``digest_max_bytes``; 0 disables it outright.
  - **Read-only.** This hook never writes to the store.
  - **Fail open.** Any error emits nothing and exits 0. A broken hook must
    cost the user nothing — least of all a session that won't start.
  - **Templated.** No LLM anywhere in the path, same as every other read.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # heavy import stays lazy at runtime — see evaluate()
    from ..storage import SelvedgeStorage

EXIT_ALLOW = 0

#: Same bypass as the gate, so one env var silences every Selvedge hook.
DISABLE_ENV = "SELVEDGE_HOOK_DISABLE"

#: How many entries each section may contribute before it is summarized.
#: Small on purpose — this is a nudge toward the store, not a replacement
#: for querying it.
_MAX_PER_SECTION = 5


def build_digest(db_path: object, max_bytes: int) -> str:
    """Render the digest for a project, or ``""`` when there is nothing to say.

    Three relevance-gated sections, in decreasing order of "this will change
    what the agent does next":

      1. decisions due for revisit,
      2. entities whose standing verdict is *reverted* (the wedge — an agent
         about to re-implement one of these is the expensive failure),
      3. the most recent changesets, for orientation.

    **Selection order — a documented contract, not an accident.** Each
    section is capped at ``_MAX_PER_SECTION`` (5), so store growth changes
    *which* five surface, not *how many*:

      1. Revisit rows come from ``get_stale_decisions``: date-due rows
         first, **most overdue leading** (ascending due date), then
         condition-only matches by decision time.
      2. Reverted rows come from ``get_reverted_entities``: **most recent
         revert first** (descending timestamp of the standing verdict).
      3. Changesets: most recently active first.

    That order is deliberately dumb — overdue-ness and recency, nothing
    semantic, nothing prompt-conditioned. Pinned by
    ``test_selection_order_*`` in ``test_hooks_sessionstart.py``. Any
    smarter ranking waits on Phase 2.24's delivery-mode measurement.

    A reverted row whose ``stale_when`` condition has since matched a later
    change presents as *re-examine* instead of a bare warning — presentation
    only, reusing the v0.3.8 active-memory surfacing. The stored verdict
    never mutates; closing the loop still takes an explicit ``supersede``.

    Deterministic: same store, same string. Nothing here is generated.
    """
    from ..storage import SelvedgeStorage

    storage = SelvedgeStorage(db_path)  # type: ignore[arg-type]
    sections: list[str] = []

    due = storage.get_stale_decisions(limit=_MAX_PER_SECTION)
    if due:
        lines = ["Decisions due for a revisit:"]
        for row in due:
            entity = row["entity_path"]
            why = row["reasoning"] or "(no reasoning recorded)"
            lines.append(f"  - {entity}: {why}")
        sections.append("\n".join(lines))

    reverted = storage.get_reverted_entities(limit=_MAX_PER_SECTION)
    if reverted:
        lines = ["Tried before and REVERTED — check prior_attempts before touching:"]
        for row in reverted:
            why = row["reasoning"] or "(no reasoning recorded)"
            if _stale_condition_matched(storage, row["entity_path"]):
                lines.append(
                    f"  - {row['entity_path']} [re-examine — a later change "
                    f"matched its stale_when condition]: {why}"
                )
            else:
                lines.append(f"  - {row['entity_path']}: {why}")
        sections.append("\n".join(lines))

    # `list_changesets` has no limit parameter — it is a grouped summary, and
    # slicing here keeps that method's contract untouched.
    recent = storage.list_changesets()[:_MAX_PER_SECTION]
    names = ", ".join(c["changeset_id"] for c in recent if c.get("changeset_id"))
    if names:
        sections.append(f"Recent changesets: {names}")

    if not sections:
        return ""

    header = (
        "Selvedge — captured decisions for this repo. This is a summary; "
        "call the prior_attempts tool before changing any entity below."
    )
    digest = header + "\n\n" + "\n\n".join(sections)
    return _cap(digest, max_bytes)


def _stale_condition_matched(storage: SelvedgeStorage, entity_path: str) -> bool:
    """Whether a later change has matched this entity's ``stale_when`` condition.

    Reuses the v0.3.8 active-memory surfacing — ``get_stale_decisions``
    rule 2's deterministic keyword overlap — rather than re-implementing any
    matching here. Read-only, presentation input only: a ``True`` changes how
    a reverted row is *worded* in the digest, never what the store says.
    """
    rows = storage.get_stale_decisions(entity_path=entity_path)
    return any("stale_when_match" in r["active_use_signals"] for r in rows)


def _cap(text: str, max_bytes: int) -> str:
    """Clip the digest to the configured byte budget.

    Cuts on a line boundary rather than mid-sentence: a digest that ends
    halfway through an entity path reads as corruption, and this text goes
    straight into a model's context.
    """
    if max_bytes <= 0:
        return ""
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    kept: list[str] = []
    used = 0
    for line in text.splitlines():
        cost = len(line.encode("utf-8")) + 1
        if used + cost > max_bytes:
            break
        kept.append(line)
        used += cost
    return "\n".join(kept).rstrip()


def evaluate(payload: dict) -> str:
    """The digest for this payload, or ``""`` to stay silent.

    Pure-ish core shared by the entry point and the tests — no stdin, no exit
    codes. Every unexpected shape resolves to silence.
    """
    import os

    if os.environ.get(DISABLE_ENV) == "1":
        return ""

    # Heavy imports stay below this point, after the cheap bail-outs, for the
    # same reason as in the gate: this runs in a fresh process and must not
    # tax a session that has nothing to be told.
    from pathlib import Path

    from ..config import get_setting
    from .pretooluse import _resolve_db_path

    max_bytes = get_setting("digest_max_bytes")
    if max_bytes <= 0:
        return ""

    cwd = Path(payload.get("cwd") or os.getcwd())
    db_path = _resolve_db_path(cwd)
    if db_path is None:
        return ""  # no project store — nothing to deliver

    return build_digest(db_path, max_bytes)


def run(argv: list[str] | None = None, stdin: str | None = None) -> int:
    """Read the payload, emit the digest as hook JSON, return the exit code.

    Always returns 0. ``--dry-run`` prints the raw digest text instead of the
    hook envelope, for a human checking what would be injected.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in argv

    try:
        raw = stdin if stdin is not None else sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        digest = evaluate(payload)
    except Exception:  # noqa: BLE001 — silence-on-miss is the contract
        digest = ""

    if dry_run:
        print(digest)
        return EXIT_ALLOW

    if digest:
        # Plain stdout is ignored by the harness; only the string at
        # hookSpecificOutput.additionalContext reaches the model.
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": digest,
            }
        }))
    return EXIT_ALLOW


def main() -> None:  # pragma: no cover — exercised via the hooks CLI
    sys.exit(run())
