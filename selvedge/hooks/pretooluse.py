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
from pathlib import Path, PurePosixPath

# SQL DDL extractors are reused so the hook derives the SAME dotted
# table.column / table entities the migration importer stores from git
# history — otherwise a raw `ALTER TABLE users ADD COLUMN sso_token` edit
# (no literal dot) yields no entity and the hook can't gate the very
# scenario it exists for: re-adding a reverted DB column.
from ..importers import (
    _SQL_ADD_COLUMN,
    _SQL_CREATE_TABLE,
    _SQL_DROP_COLUMN,
    _SQL_DROP_TABLE,
)
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


# Commands that write to a path given as an argument. Anything not listed is
# assumed read-only, which is the safe default here: a miss allows the edit,
# and this module accepts misses (see the precision-over-recall note above).
# A false BLOCK is the failure mode this list exists to prevent.
_WRITE_COMMANDS = frozenset({
    "tee", "cp", "mv", "rm", "install", "truncate", "dd", "patch", "touch",
    "ln", "mkdir", "chmod", "chown", "alembic", "psql", "sqlite3", "mysql",
})

# `git <subcommand>` that can modify the working tree.
_GIT_WRITE_SUBCOMMANDS = frozenset({
    "checkout", "restore", "apply", "rm", "mv", "clean", "reset", "stash",
    "switch", "revert", "cherry-pick", "merge", "rebase", "pull",
})

# Shell operators that separate one command from the next.
_SEGMENT_SEPARATORS = frozenset({";", "&&", "||", "|", "&"})


def _has_write_intent(tokens: list[str]) -> bool:
    """Whether a tokenized Bash command plausibly writes to a file.

    Redirection anywhere counts. Otherwise each pipeline segment is judged by
    its executable: a known writer, `sed -i`, or a mutating `git` subcommand.
    A `selvedge` invocation never counts — it is the tool the block message
    tells the agent to run, and gating it makes the remediation unreachable.
    """
    if any(t in (">", ">>") for t in tokens):
        return True

    segment: list[str] = []
    for token in [*tokens, ";"]:
        if token in _SEGMENT_SEPARATORS:
            if segment:
                # Skip leading VAR=value assignments to find the executable.
                argv = list(segment)
                while argv and "=" in argv[0] and not argv[0].startswith("-"):
                    argv.pop(0)
                if argv:
                    exe = PurePosixPath(argv[0]).name
                    if exe in _WRITE_COMMANDS:
                        return True
                    if exe == "sed" and any(a.startswith("-i") for a in argv[1:]):
                        return True
                    if exe == "git":
                        for arg in argv[1:]:
                            if arg.startswith("-"):
                                continue
                            if arg in _GIT_WRITE_SUBCOMMANDS:
                                return True
                            break
            segment = []
        else:
            segment.append(token)
    return False


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
        # A Bash call only counts as touching a path if it plausibly WRITES
        # to one. Without this, `cat`/`git diff`/`pytest`/`ruff check` on a
        # watched file all blocked — false positives, which this design
        # forbids — and so did `selvedge prior-attempts '<path>'`, the very
        # command the block message prints as the way out. That was a
        # livelock with no CLI-only escape.
        if not _has_write_intent(tokens):
            return []
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

    def _add(token: str) -> None:
        if token not in seen and len(seen) < _MAX_ENTITY_CANDIDATES:
            seen.append(token)

    # SQL DDL first — highest precision, and the case the importer records
    # from git history (ADD/DROP COLUMN → table.column; CREATE/DROP TABLE →
    # table). Matching the importer's parsers keeps hook and store in step.
    for m in _SQL_ADD_COLUMN.finditer(text):
        _add(f"{m.group(1)}.{m.group(2)}")
    for m in _SQL_DROP_COLUMN.finditer(text):
        _add(f"{m.group(1)}.{m.group(2)}")
    for m in _SQL_CREATE_TABLE.finditer(text):
        _add(m.group(1))
    for m in _SQL_DROP_TABLE.finditer(text):
        _add(m.group(1))
    # Then any already-dotted table.column literals elsewhere in the diff.
    for m in _ENTITY_TOKEN_RE.finditer(text):
        left, right = m.group(1), m.group(2)
        if right in _FILE_EXTENSIONS:
            continue
        _add(f"{left}.{right}")
        if len(seen) >= _MAX_ENTITY_CANDIDATES:
            break
    return seen


def _project_root(db_path: Path, cwd: Path) -> Path:
    """The directory that entity paths are recorded relative to.

    Entities are stored project-root-relative (git-import records
    ``git show``'s repo-root-relative paths; agents log the same shape). The
    root is the directory that CONTAINS ``.selvedge/`` — i.e.
    ``<root>/.selvedge/selvedge.db`` → ``<root>``. Relativizing against the
    payload ``cwd`` instead silently breaks enforcement for any session
    opened in a subdirectory: an absolute ``…/backend/migrations/x.sql``
    would become ``migrations/x.sql`` and never match the recorded
    ``backend/migrations/x.sql``. When ``SELVEDGE_DB`` points somewhere
    non-standard (no ``.selvedge`` parent), fall back to ``cwd`` — there is
    no better guess.
    """
    if db_path.parent.name == ".selvedge":
        return db_path.parent.parent.resolve()
    return cwd.resolve()


def _relative_to(path_str: str, root: Path) -> str:
    """Best-effort ``root``-relative form of ``path_str`` for glob matching.

    Returns ``""`` — which matches no watch glob — when the path resolves
    outside ``root``. A path in another checkout is not this project's entity,
    and letting it through meant an edit elsewhere could be gated against this
    project's history.

    Note this deliberately does not use ``lstrip("./")``: ``str.lstrip`` strips
    any leading run of the given *characters*, not a prefix, so ``../x`` became
    ``x`` and ``.hidden/schema.sql`` became ``hidden/schema.sql`` — a different
    entity from the one ``canonicalize_entity_path`` stores, which made
    enforcement silently inert for dotfile paths.
    """
    try:
        p = Path(path_str)
        if p.is_absolute():
            resolved = p.resolve()
            root_resolved = root.resolve()
            if resolved == root_resolved or root_resolved in resolved.parents:
                return str(resolved.relative_to(root_resolved))
            return ""
    except (ValueError, OSError):
        return ""

    # Relative path: normalize, then reject anything that escapes the root.
    normalized = os.path.normpath(path_str)
    if normalized == ".." or normalized.startswith(f"..{os.sep}"):
        return ""
    return "" if normalized == "." else normalized


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

    # Relativize touched paths against the PROJECT ROOT (the dir holding
    # .selvedge/), not the payload cwd — otherwise a session opened in a
    # subdirectory sees entity paths that never match the recorded,
    # root-relative ones, and enforcement goes silently inert.
    root = _project_root(db_path, cwd)
    globs = [_glob_to_regex(g) for g in _load_watch_globs(db_path.parent)]
    touched = [
        p
        for p in _candidate_paths(tool_name, tool_input)
        if any(rx.match(_relative_to(p, root)) for rx in globs)
    ]
    if not touched:
        return Decision("allow", reason="no watched path touched")

    # Entities: the touched files themselves + table.column tokens from the
    # change text. Heuristic extraction; the reverted-status check below is
    # the actual filter.
    entities = [_relative_to(p, root) for p in touched]
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
