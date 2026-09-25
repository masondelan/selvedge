"""Translate documented client hook payloads to Selvedge's shared checks.

These adapters do not execute tool arguments, read transcripts, or infer intent
from model prose. Unknown payloads fail open. Compaction notifications are user
messages where the client does not support injecting model context at that event.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from . import precompact, pretooluse, sessionstart

AGENTS = ("codex", "cursor", "copilot", "gemini", "windsurf")


def _text(value: object) -> str:
    """Accept strings without coercing arbitrary payload values."""
    return value if isinstance(value, str) else ""


def _base(agent: str, payload: dict) -> dict:
    """Resolve client identity and project directory without reading transcripts."""
    cwd = _text(payload.get("cwd"))
    session = _text(payload.get("session_id"))
    if agent == "cursor":
        session = session or _text(payload.get("conversation_id"))
        roots = payload.get("workspace_roots")
        if not cwd and isinstance(roots, list) and len(roots) == 1:
            cwd = _text(roots[0])
        # A multi-root payload without a cwd is ambiguous; don't use another repo.
        if not cwd and isinstance(roots, list) and len(roots) > 1:
            raise ValueError("ambiguous workspace")
    if agent == "windsurf":
        session = _text(payload.get("trajectory_id"))
        info = payload.get("tool_info")
        if isinstance(info, dict):
            cwd = _text(info.get("cwd")) or cwd
        # Cascade documents workspace-root execution for project hooks.
    return {"cwd": cwd or os.getcwd(), "session_id": session}


def _patch_edits(patch: str) -> list[dict]:
    """Split supported apply_patch headers, keeping each file's change text local."""
    edits: list[dict] = []
    current: dict | None = None
    for line in patch.splitlines():
        match = re.match(r"^\*\*\* (?:Add File|Update File|Delete File): (.+)$", line)
        if match:
            current = {"file_path": match.group(1), "content": ""}
            edits.append(current)
        elif line.startswith("*** Move to: ") and current is not None:
            current = {"file_path": line[len("*** Move to: ") :], "content": ""}
            edits.append(current)
        elif current is not None:
            current["content"] += line + "\n"
        if len(edits) > 200:
            raise ValueError("oversized patch")
    return edits


def normalize_edits(agent: str, payload: dict) -> list[dict]:
    """Normalize known mutating tools; read tools and unfamiliar schemas are ignored."""
    base = _base(agent, payload)
    name = _text(payload.get("tool_name"))
    args = payload.get("tool_input")
    if agent == "windsurf":
        args = payload.get("tool_info")
        name = _text(payload.get("agent_action_name"))
    if not isinstance(args, dict):
        return []
    tool = ""
    inputs: list[dict] = []
    if (
        (agent == "codex" and name == "Bash")
        or (agent == "cursor" and name == "Shell")
        or (agent == "gemini" and name == "run_shell_command")
        or (agent == "copilot" and name == "run_in_terminal")
        or (agent == "windsurf" and name == "pre_run_command")
    ):
        tool = "Bash"
        inputs = [
            {"command": args.get("command_line") if agent == "windsurf" else args.get("command")}
        ]
        working = _text(args.get("working_directory")) or _text(args.get("directory"))
        if working:
            base["cwd"] = str(Path(base["cwd"]) / working)
    elif agent in ("codex", "copilot") and name == "apply_patch":
        tool = "Write"
        inputs = _patch_edits(_text(args.get("command") if agent == "codex" else args.get("input")))
    elif (
        (agent == "cursor" and name == "Write")
        or (agent == "gemini" and name in ("write_file", "replace"))
        or (agent == "windsurf" and name == "pre_write_code")
    ):
        tool = "Write"
        inputs = [args]
    elif agent == "copilot" and name in (
        "create_file",
        "replace_string_in_file",
        "multi_replace_string_in_file",
    ):
        tool = "Write"
        replacements = (
            args.get("replacements") if name == "multi_replace_string_in_file" else [args]
        )
        if isinstance(replacements, list):
            inputs = [
                {
                    "file_path": r.get("filePath"),
                    "content": r.get("content"),
                    "old_string": r.get("oldString"),
                    "new_string": r.get("newString"),
                }
                for r in replacements
                if isinstance(r, dict)
            ]
    if len(inputs) > 200:
        raise ValueError("oversized edit batch")
    # Relative paths in tool arguments are relative to the tool's cwd. The
    # shared gate compares absolute paths to the actual database project root.
    result = []
    for args in inputs:
        args = dict(args)
        if tool == "Bash":
            # The shared shell detector distinguishes writes from reads. Make
            # its path candidates absolute against this command's cwd before
            # applying the project-relative watch globs.
            for candidate in pretooluse._candidate_paths("Bash", args):
                result.append(
                    {
                        **base,
                        "tool_name": "Write",
                        "tool_input": {
                            "file_path": str(Path(base["cwd"]) / candidate),
                            "content": _text(args.get("command")),
                        },
                    }
                )
            continue
        path = _text(args.get("file_path"))
        if path:
            args["file_path"] = str(Path(base["cwd"]) / path)
        result.append({**base, "tool_name": tool, "tool_input": args})
    return result


def evaluate_gate(agent: str, payload: dict) -> pretooluse.Decision:
    """Block if any normalized edit has an unchecked recorded rejection."""
    decisions = [pretooluse.evaluate(edit) for edit in normalize_edits(agent, payload)]
    blocked = [d for d in decisions if d.action == "block"]
    if not blocked:
        return pretooluse.Decision("allow")
    return pretooluse.Decision(
        "block",
        "\n\n".join(d.reason for d in blocked[:3]),
        [e for d in blocked for e in d.blocked_entities],
    )


def response(agent: str, event: str, payload: dict) -> tuple[dict, str, int]:
    """Produce only fields the selected client's event actually supports."""
    if agent not in AGENTS:
        raise ValueError("unknown agent")
    if event == "pretooluse":
        decision = evaluate_gate(agent, payload)
        denied = decision.action == "block"
        if agent == "windsurf":
            return {}, decision.reason if denied else "", 2 if denied else 0
        if agent == "cursor":
            output: dict = {"permission": "deny" if denied else "allow"}
            if denied:
                output.update(agent_message=decision.reason, user_message=decision.reason)
            return output, "", 0
        if agent == "gemini":
            return {"decision": "deny" if denied else "allow", "reason": decision.reason}, "", 0
        # Omit an allow decision, leaving the client's normal permissions intact.
        output = (
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": decision.reason,
                }
            }
            if denied
            else {}
        )
        return output, "", 0
    base = _base(agent, payload)
    if event == "sessionstart" and agent != "windsurf":
        context = sessionstart.evaluate(base)
        if not context:
            return {}, "", 0
        if agent == "cursor":
            return {"additional_context": context}, "", 0
        return (
            {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}},
            "",
            0,
        )
    if event == "precompact" and agent != "windsurf":
        reminder = precompact.evaluate(base).replace(
            "were edited this session", "were involved in attempted edits this session"
        )
        if reminder:
            return {"user_message" if agent == "cursor" else "systemMessage": reminder}, "", 0
        return {}, "", 0
    raise ValueError("unsupported event")


def run(agent: str, event: str, argv: list[str] | None = None, stdin: str | None = None) -> int:
    """Read JSON, emit the native hook response, and fail open on malformed input."""
    dry_run = "--dry-run" in (argv or [])
    try:
        raw = sys.stdin.read() if stdin is None else stdin
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("expected object")
        output, error, code = response(agent, event, payload)
    except Exception:  # noqa: BLE001 — unknown hook input must not break a workflow
        output, error, code = (
            ({"permission": "allow"} if agent == "cursor" and event == "pretooluse" else {}),
            "",
            0,
        )
    if dry_run:
        print(json.dumps({"output": output, "stderr": error, "exit_code": code}))
        return 0
    if output:
        print(json.dumps(output))
    if error:
        print(error, file=sys.stderr)
    return code
