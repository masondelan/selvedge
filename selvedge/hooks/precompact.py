"""Claude Code PreCompact hook — the last moment before session reasoning dies.

Compaction is exactly when Selvedge's premise becomes concrete: the *why*
behind this session's changes is about to stop existing anywhere except in
whatever the agent chose to write down. This hook fires immediately before
that and reminds the agent to `log_change` what it hasn't.

**Advisory only. This hook never blocks compaction.** The API allows a veto
two ways — exit code 2, and `{"decision": "block"}` on stdout — and Selvedge
uses neither. That is the same false-block discipline the PreToolUse gate
follows, and it matters more here: blocking compaction doesn't inconvenience
a tool call, it wedges the session. `test_never_blocks_compaction` asserts
both mechanisms stay unused, including the exit code, because 2 is a
one-character edit away from 0 and nothing else would catch it.

Wire protocol:

  - stdin: one JSON object with ``session_id``, ``cwd``, ``trigger``
    (``manual`` / ``auto``), ``custom_instructions``.
  - stdout: a JSON object; the string at
    ``hookSpecificOutput.additionalContext`` reaches the model.
  - exit 0, always and unconditionally.

Quiet by default, same as the session-start digest: with nothing unlogged to
point at, the reminder is noise arriving at a moment the agent is already
context-constrained.
"""

from __future__ import annotations

import json
import sys

EXIT_ALLOW = 0

DISABLE_ENV = "SELVEDGE_HOOK_DISABLE"

#: Cap on entities named in the reminder. The list is a prompt, not a report.
_MAX_ENTITIES = 8


def build_reminder(entities: list[str], logged_count: int) -> str:
    """Render the reminder, or ``""`` when there is nothing to say.

    ``entities`` are watched entities this session touched that have no event
    in the store. ``logged_count`` is how many events the session did log —
    used only to make the message accurate about what is already captured.
    """
    if not entities:
        return ""

    shown = entities[:_MAX_ENTITIES]
    more = len(entities) - len(shown)
    lines = [
        "Selvedge: context is about to be compacted, which destroys the "
        "reasoning behind this session's changes.",
        "",
        "These watched entities were edited this session but have no "
        "log_change recorded:",
    ]
    lines += [f"  - {e}" for e in shown]
    if more:
        lines.append(f"  (+{more} more)")
    lines += [
        "",
        "Call log_change for each one now, with the constraint or bug that "
        "prompted the change — not a description of the diff. After "
        "compaction that context is gone.",
    ]
    if logged_count:
        lines.append(f"({logged_count} change(s) from this session are already logged.)")
    return "\n".join(lines)


def evaluate(payload: dict) -> str:
    """The reminder for this payload, or ``""`` to stay silent.

    Never raises, never blocks — the caller turns any outcome into exit 0.
    """
    import os

    if os.environ.get(DISABLE_ENV) == "1":
        return ""

    from pathlib import Path

    from .pretooluse import _resolve_db_path, _sanitize_session_id, read_touched_entities

    cwd = Path(payload.get("cwd") or os.getcwd())
    db_path = _resolve_db_path(cwd)
    if db_path is None:
        return ""

    session_id = _sanitize_session_id(payload.get("session_id"))
    touched = read_touched_entities(db_path.parent / "hook_sessions", session_id)
    if not touched:
        return ""  # nothing the hook can point at — stay quiet

    from ..storage import SelvedgeStorage

    storage = SelvedgeStorage(db_path)
    logged = storage.get_session_logged_paths(session_id)
    unlogged = [e for e in touched if e not in logged]
    return build_reminder(unlogged, len(logged))


def run(argv: list[str] | None = None, stdin: str | None = None) -> int:
    """Read the payload, emit the reminder, return 0.

    The return value is a constant on every path. Compaction is never vetoed,
    so there is no branch that could produce another exit code.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in argv

    try:
        raw = stdin if stdin is not None else sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        reminder = evaluate(payload)
    except Exception:  # noqa: BLE001 — silence-on-miss is the contract
        reminder = ""

    if dry_run:
        print(reminder)
        return EXIT_ALLOW

    if reminder:
        # Deliberately no "decision" key. Its presence with the value "block"
        # is one of the two ways to veto compaction; the other is exit 2.
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreCompact",
                "additionalContext": reminder,
            }
        }))
    return EXIT_ALLOW


def main() -> None:  # pragma: no cover — exercised via the hooks CLI
    sys.exit(run())
