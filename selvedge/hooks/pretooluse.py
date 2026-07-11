"""
Claude Code PreToolUse enforcement hook (v0.3.9.1).

The CLAUDE.md instruction "call prior_attempts before editing an entity" is
probabilistic — agents skip it a meaningful fraction of the time (dev.to
launch-thread feedback: René Zander, Tae Kim). This hook makes the check
deterministic: it intercepts Edit/Write/Bash calls that touch schema or
migration paths and blocks them until ``prior_attempts`` has been queried
for the affected entities in this session. The block message carries the
prior reasoning, so the agent gets the context for free — and querying
``prior_attempts`` (which is the documented way to acknowledge) unblocks
the retry.

Wire protocol (Claude Code hooks):

  - stdin: one JSON object with ``session_id``, ``cwd``, ``tool_name``,
    ``tool_input`` (and more we ignore).
  - exit 0: allow the tool call.
  - exit 2: block; stderr is fed back to the agent as the reason.

Design posture — **precision over recall, never block on a miss**:

  - any internal error, unparseable stdin, missing DB, or unknown tool
    resolves to *allow*;
  - only entities whose standing verdict is ``reverted`` gate (a decision
    re-opened via ``supersede`` does not block);
  - ``SELVEDGE_HOOK_DISABLE=1`` bypasses the hook entirely;
  - ``selvedge-hook pretooluse --dry-run`` evaluates and prints the decision
    as JSON but always exits 0.

Session tracking: the MCP server can't see Claude's session id, so "queried
this session" is derived locally. On first sight of a ``session_id`` the
hook writes ``<db-dir>/hook_sessions/<id>.json`` recording a window start
(now minus a 30-minute back-slack, so a query made just before the first
gated edit still counts). ``prior_attempts`` tool-calls recorded at/after
that window on a related entity satisfy the check. The slack errs
permissive by construction — a stale query from a previous session within
the window allows rather than blocks. Session files older than 7 days are
pruned opportunistically.

Watched paths default to migration/schema shapes and can be replaced via
``.selvedge/config.toml``::

    [hook]
    watch_globs = ["**/migrations/**", "db/**/*.sql"]

(TOML parsing uses stdlib ``tomllib`` on Python 3.11+; on 3.10 the optional
``tomli`` package is used when present, otherwise the defaults apply.)
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..storage import SelvedgeStorage, _paths_related, canonicalize_entity_path
from ..timeutil import normalize_timestamp

DISABLE_ENV = "SELVEDGE_HOOK_DISABLE"

EXIT_ALLOW = 0
EXIT_BLOCK = 2

#: Tools whose calls can touch files. Bash is scanned for path tokens.
_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_WATCHED_TOOLS = _EDIT_TOOLS | frozenset({"Bash"})

#: Default globs for "this edit touches schema/migration territory".
#: Deliberately narrow — the hook must never fire on ordinary source edits.
DEFAULT_WATCH_GLOBS: tuple[str, ...] = (
    "**/migrations/**",
    "**/alembic/**",
    "**/schema*",
    "**/*model*",
    "**/*.sql",
)

#: Backdate applied when a session file is first created, so prior_attempts
#: queries made moments before the first gated edit still count.
_SESSION_BACK_SLACK = timedelta(minutes=30)

#: Session files older than this are pruned on sight.
_SESSION_FILE_MAX_AGE = timedelta(days=7)

#: Caps that bound the hook's work per invocation.
_MAX_ENTITY_CANDIDATES = 40
_MAX_BLOCKED_REPORTED = 3

# table.column-shaped tokens in diff text. Lowercase-only on purpose (DB
# naming convention); the right-hand side excludes file extensions so
# "auth.py" or "schema.sql" never becomes a phantom column entity.
_ENTITY_TOKEN_RE = re.compile(r"\b([a-z][a-z0-9_]{2,})\.([a-z][a-z0-9_]{2,})\b")
_FILE_EXTENSIONS = frozenset({
    "cfg", "conf", "csv", "html", "ini", "js", "json", "jsx", "md",
    "py", "rb", "rs", "sh", "sql", "toml", "ts", "tsx", "txt", "xml",
    "yaml", "yml",
})


@dataclass
class Decision:
    """The hook's verdict for one tool call, testable without a process."""

    action: str  # "allow" | "block"
    reason: str = ""  # stderr text on block; short note on allow
    blocked_entities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """JSON shape printed by ``--dry-run``. Every field always present."""
        return {
            "action": self.action,
            "reason": self.reason,
            "blocked_entities": list(self.blocked_entities),
        }


# ---------------------------------------------------------------------------
# Glob matching — fnmatch treats '*' as crossing '/' and has no '**', so a
# small translator gives the globs their documented gitignore-style meaning.
# ---------------------------------------------------------------------------


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a watch glob into a regex with gitignore-style semantics.

    ``**`` crosses directory separators, ``*`` and ``?`` do not. A leading
    ``**/`` also matches paths with no directory component at all, so
    ``**/migrations/**`` covers ``migrations/0001.sql``.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern[i : i + 3] == "**/":
                out.append(r"(?:.*/)?")
                i += 3
                continue
            if pattern[i : i + 2] == "**":
                out.append(r".*")
                i += 2
                continue
            out.append(r"[^/]*")
        elif ch == "?":
            out.append(r"[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("".join(out) + r"\Z")


def _load_watch_globs(db_dir: Path) -> tuple[str, ...]:
    """Return the configured watch globs, or the defaults.

    Reads ``[hook] watch_globs`` from ``<db_dir>/config.toml`` when the file
    exists and a TOML parser is available. A present-and-valid list REPLACES
    the defaults (so users can narrow as well as widen). Every failure mode
    falls back to the defaults — the hook must keep working on a mangled
    config rather than start blocking or crashing.
    """
    config_path = db_dir / "config.toml"
    if not config_path.is_file():
        return DEFAULT_WATCH_GLOBS
    try:
        import tomllib  # Python 3.11+
    except ImportError:  # pragma: no cover - exercised only on 3.10
        try:
            import tomli as tomllib
        except ImportError:
            return DEFAULT_WATCH_GLOBS
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_WATCH_GLOBS
    globs = data.get("hook", {}).get("watch_globs") if isinstance(data, dict) else None
    if (
        isinstance(globs, list)
        and globs
        and all(isinstance(g, str) and g.strip() for g in globs)
    ):
        return tuple(g.strip() for g in globs)
    return DEFAULT_WATCH_GLOBS


def _resolve_db_path(cwd: Path) -> Path | None:
    """Resolve the Selvedge DB for the hooked project, or None.

    ``SELVEDGE_DB`` wins; otherwise walk up from the hook payload's ``cwd``
    looking for an existing project-local DB. Deliberately NO global-DB
    fallback — enforcement against an unrelated global store would be all
    noise, and "no project DB" simply means "nothing to enforce".
    """
    if env_path := os.environ.get("SELVEDGE_DB"):
        p = Path(env_path).expanduser()
        return p if p.is_file() else None
    cwd = cwd.resolve()
    for directory in [cwd, *cwd.parents]:
        candidate = directory / ".selvedge" / "selvedge.db"
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Payload dissection
# ---------------------------------------------------------------------------


def _candidate_paths(tool_name: str, tool_input: dict) -> list[str]:
    """File paths this tool call touches, best-effort."""
    if tool_name in _EDIT_TOOLS:
        file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
        return [file_path] if isinstance(file_path, str) and file_path else []
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str) or not command:
            return []
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        # Path-shaped tokens only; option flags and bare words are noise.
        return [t for t in tokens if "/" in t or "." in t][:200]
    return []


def _diff_text(tool_name: str, tool_input: dict) -> str:
    """The textual change content, for entity-token extraction."""
    parts: list[str] = []
    for key in ("new_string", "old_string", "content", "command"):
        value = tool_input.get(key)
        if isinstance(value, str):
            parts.append(value)
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict):
                for key in ("new_string", "old_string"):
                    value = edit.get(key)
                    if isinstance(value, str):
                        parts.append(value)
    return "\n".join(parts)


def _entity_tokens(text: str) -> list[str]:
    """Extract table.column-shaped entity candidates from diff text.

    Heuristic on purpose (regex over the change text) and biased to
    precision: lowercase snake_case pairs only, file-extension lookalikes
    dropped, capped. A missed entity never blocks — the DB check downstream
    is the real filter.
    """
    seen: list[str] = []
    for m in _ENTITY_TOKEN_RE.finditer(text):
        left, right = m.group(1), m.group(2)
        if right in _FILE_EXTENSIONS:
            continue
        token = f"{left}.{right}"
        if token not in seen:
            seen.append(token)
        if len(seen) >= _MAX_ENTITY_CANDIDATES:
            break
    return seen


def _relative_to(path_str: str, cwd: Path) -> str:
    """Best-effort project-relative form of ``path_str`` for glob matching."""
    try:
        p = Path(path_str)
        if p.is_absolute():
            return str(p.resolve().relative_to(cwd.resolve()))
    except (ValueError, OSError):
        pass
    return path_str.lstrip("./")


# ---------------------------------------------------------------------------
# Session tracking
# ---------------------------------------------------------------------------


def _session_window_start(state_dir: Path, session_id: str, now: datetime) -> datetime:
    """Return the query-window start for this session, creating state on first sight.

    Also prunes session files past their max age — the directory stays a
    handful of small JSON files, even under heavy agent use.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - _SESSION_FILE_MAX_AGE.total_seconds()
    for stale in state_dir.glob("*.json"):
        try:
            if stale.stat().st_mtime < cutoff:
                stale.unlink()
        except OSError:
            continue

    session_file = state_dir / f"{session_id}.json"
    if session_file.is_file():
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            return datetime.fromisoformat(data["window_start"])
        except (OSError, ValueError, KeyError, TypeError):
            pass  # unreadable state — fall through and rewrite it

    window_start = now - _SESSION_BACK_SLACK
    try:
        session_file.write_text(
            json.dumps({"session_id": session_id, "window_start": window_start.isoformat()}),
            encoding="utf-8",
        )
    except OSError:
        pass  # state is an optimization; failing to persist must not block
    return window_start


def _queried_this_session(
    storage: SelvedgeStorage, entities: list[str], window_start: datetime
) -> bool:
    """True if prior_attempts was queried for any related entity in-window."""
    since = normalize_timestamp(window_start.astimezone(timezone.utc).isoformat())
    queried = storage.get_tool_query_paths("prior_attempts", since=since)
    return any(
        _paths_related(q, canonicalize_entity_path(entity))
        for q in queried
        for entity in entities
    )


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _sanitize_session_id(session_id: object) -> str:
    """Session id as a safe filename component (it comes from the payload)."""
    if not isinstance(session_id, str) or not session_id:
        return "unknown-session"
    return re.sub(r"[^A-Za-z0-9._-]", "_", session_id)[:128]


def _block_message(blocked: list[dict]) -> str:
    """Render the block reason with the prior reasoning inlined.

    Templated and deterministic — this is exactly the context the agent
    skipped by not calling prior_attempts, so the block itself teaches the
    fix.
    """
    lines = [
        "Selvedge: blocked — this edit touches entity(ies) that were tried "
        "before and REVERTED, and prior_attempts has not been checked this "
        "session.",
        "",
    ]
    for item in blocked[:_MAX_BLOCKED_REPORTED]:
        lines.append(f"  entity: {item['entity']}")
        if item["tried_reasoning"]:
            lines.append(f"    tried:    {item['tried_reasoning']}")
        lines.append(f"    reverted: {item['reverted_reasoning'] or '(no reasoning recorded)'}")
        lines.append("")
    if len(blocked) > _MAX_BLOCKED_REPORTED:
        lines.append(f"  (+{len(blocked) - _MAX_BLOCKED_REPORTED} more entities)")
        lines.append("")
    entity = blocked[0]["entity"]
    lines += [
        "Before repeating this change, check the prior attempts — call the "
        f"prior_attempts MCP tool (or run `selvedge prior-attempts '{entity}'`), "
        "then retry this edit; it will be allowed once the check is on record.",
        "If the old constraint no longer holds, re-open the decision "
        f"explicitly: `selvedge supersede '{entity}' --reasoning \"...\"`.",
        "(bypass for this shell: SELVEDGE_HOOK_DISABLE=1)",
    ]
    return "\n".join(lines)


def evaluate(payload: dict, *, now: datetime | None = None) -> Decision:
    """Evaluate one PreToolUse payload and return the hook's Decision.

    Pure-ish core shared by the real entry point and the test suite — no
    stdin, no exit codes. Every unexpected shape resolves to allow.
    """
    now = now or datetime.now(timezone.utc)

    if os.environ.get(DISABLE_ENV) == "1":
        return Decision("allow", reason=f"{DISABLE_ENV}=1")

    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if tool_name not in _WATCHED_TOOLS or not isinstance(tool_input, dict):
        return Decision("allow", reason="tool not watched")

    cwd = Path(payload.get("cwd") or os.getcwd())
    db_path = _resolve_db_path(cwd)
    if db_path is None:
        return Decision("allow", reason="no project Selvedge DB")

    globs = [_glob_to_regex(g) for g in _load_watch_globs(db_path.parent)]
    touched = [
        p
        for p in _candidate_paths(tool_name, tool_input)
        if any(rx.match(_relative_to(p, cwd)) for rx in globs)
    ]
    if not touched:
        return Decision("allow", reason="no watched path touched")

    # Entities: the touched files themselves + table.column tokens from the
    # change text. Heuristic extraction; the reverted-status check below is
    # the actual filter.
    entities = [_relative_to(p, cwd) for p in touched]
    entities += [t for t in _entity_tokens(_diff_text(tool_name, tool_input))]
    entities = entities[:_MAX_ENTITY_CANDIDATES]

    storage = SelvedgeStorage(db_path)
    blocked: list[dict] = []
    for entity in entities:
        decision = storage.get_decision_status(entity)
        if decision["status"] != "reverted":
            continue  # active / reopened / unknown — nothing to enforce
        trail = decision["trail"]
        reverted_event = trail[-1]
        tried_event = next(
            (e for e in reversed(trail[:-1]) if e["phase"] == "tried"), None
        )
        blocked.append(
            {
                "entity": decision["entity_path"],
                "reverted_reasoning": reverted_event["reasoning"],
                "tried_reasoning": tried_event["reasoning"] if tried_event else "",
            }
        )

    if not blocked:
        return Decision("allow", reason="no reverted decisions on touched entities")

    session_id = _sanitize_session_id(payload.get("session_id"))
    state_dir = db_path.parent / "hook_sessions"
    window_start = _session_window_start(state_dir, session_id, now)
    if _queried_this_session(
        storage, [b["entity"] for b in blocked], window_start
    ):
        return Decision("allow", reason="prior_attempts checked this session")

    return Decision(
        "block",
        reason=_block_message(blocked),
        blocked_entities=[b["entity"] for b in blocked],
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(argv: list[str] | None = None, stdin: str | None = None) -> int:
    """Run the hook: read the payload, evaluate, print, return the exit code.

    ``--dry-run`` prints the Decision as JSON to stdout and always returns
    0. Every internal failure returns 0 (allow) — the hook must never break
    an edit it can't understand.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in argv

    try:
        raw = stdin if stdin is not None else sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        decision = evaluate(payload)
    except Exception:  # noqa: BLE001 — allow-on-miss is the contract
        decision = Decision("allow", reason="hook error (fail-open)")

    if dry_run:
        print(json.dumps(decision.to_dict(), indent=2))
        return EXIT_ALLOW

    if decision.action == "block":
        print(decision.reason, file=sys.stderr)
        return EXIT_BLOCK
    return EXIT_ALLOW


def main() -> None:
    """Console entry for ``selvedge-hook pretooluse`` (via selvedge.hooks.cli)."""
    sys.exit(run())


if __name__ == "__main__":  # pragma: no cover
    main()
