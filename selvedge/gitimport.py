"""
Git-history import for ``selvedge import --from-git`` (v0.3.9.1).

Reverts that predate Selvedge are invisible to ``prior_attempts`` — but git
already holds most of them (dev.to launch-thread feedback: Alexey Spinov).
This module walks the repository's history and seeds the store with
``change_type="revert"`` events so pre-Selvedge rejections gate and surface
exactly like live-captured ones.

Two deterministic sweeps, zero LLM, zero network beyond the local repo:

  1. **Revert-message commits** — ``git log -i --grep=revert``. For each
     hit: one event per touched file path, plus table/column entities parsed
     from ``DROP TABLE`` / ``DROP COLUMN`` statements in the patch's removed
     lines (the existing SQL entity conventions).
  2. **Deletion commits** — ``git log --diff-filter=D``: files the commit
     deleted become revert events even when the message never says "revert".

Event shape: ``change_type="revert"``, ``agent="git-import"``,
``reasoning=<commit subject + body>``, ``git_commit=<hash>``, timestamp from
the author date. Idempotent by construction: the importer keys on
``(git_commit, entity_path)`` and skips pairs already stored for the
``git-import`` agent.

Honest limits, stated up front:

  - **Reverts folded into unrelated commits** (no "revert" in the message,
    nothing deleted) are missed.
  - **Identity boundary.** git's identity is paths + hunks; a decision lives
    at the entity level. A DROP TABLE / DROP COLUMN in the revert diff is
    seeded at the entity level (``users.card_token``) and so matches a re-add
    in any file — but every other diff shape (ORM model columns, serializer
    fields, non-SQL code) is seeded file-level, so a reverted entity that
    resurfaces in a *different* file is missed. Widening this — extracting
    identifiers from any revert diff with enclosing-scope context, indexing
    on those — is the Phase 2.18 "git-import provenance + trust tiers" work
    in ``docs/architecture.md``.

Precision over recall — a seeded false "reverted" verdict would gate the
enforcement hook on innocent entities.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .importers import (
    _SQL_ADD_COLUMN,
    _SQL_CREATE_TABLE,
    _SQL_DROP_COLUMN,
    _SQL_DROP_TABLE,
)
from .models import ChangeEvent

#: The agent name stamped on every imported event — also the idempotency
#: namespace (see ``SelvedgeStorage.get_agent_event_keys``).
GIT_IMPORT_AGENT = "git-import"

#: Field separator for --format parsing. NUL never appears in messages.
_SEP = "%x00"
#: Record separator: NUL + Record Separator. A commit body CAN legally
#: contain a lone 0x1e, but never a NUL — so the two-byte sequence is
#: unforgeable by message content.
_RECORD_SEP = "%x00%x1e"
_RECORD_SEP_BYTES = "\x00\x1e"

#: Bound the entities emitted per commit — a 400-file revert commit is a
#: repo-wide rollback, not 400 individual decisions worth seeding.
_MAX_ENTITIES_PER_COMMIT = 50


class GitImportError(RuntimeError):
    """Raised when git is missing, the path isn't a repo, or git fails."""


def _run_git(repo: Path, args: list[str]) -> str:
    """Run a git command in ``repo`` and return stdout, or raise GitImportError."""
    try:
        # core.quotepath=false: without it, git C-quotes non-ASCII paths
        # ("r\303\251sum\303\251.md") and the stored entity would never
        # match the live-logged one.
        result = subprocess.run(
            ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise GitImportError("git executable not found on PATH") from e
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        raise GitImportError(
            f"git {' '.join(args[:2])} failed: {detail[0] if detail else 'unknown error'}"
        )
    return result.stdout


def _since_args(repo: Path, since: str) -> list[str]:
    """Translate ``--since REF|DATE`` into git-log arguments.

    A value that resolves as a commit-ish becomes the range ``REF..HEAD``;
    anything else is handed to ``git log --since`` as a date expression
    (git's own date parser is the authority on what's valid).
    """
    if not since:
        return []
    probe = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{since}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode == 0:
        return [f"{since}..HEAD"]
    # Not a commit-ish — hand it to git's date parser. Say so out loud:
    # git's approxidate is extremely lenient, so a typo'd TAG here would
    # otherwise silently drop the bound and walk the whole history.
    sys.stderr.write(
        f"selvedge: --since {since!r} does not resolve as a git ref; "
        "passing it to git as a date expression\n"
    )
    return [f"--since={since}"]


def _log_commits(repo: Path, extra_args: list[str], since: str) -> list[dict]:
    """Return commit records (hash, subject, body, author date) for a git-log query."""
    fmt = f"{_RECORD_SEP}%H{_SEP}%aI{_SEP}%s{_SEP}%b"
    out = _run_git(repo, ["log", f"--format={fmt}", *extra_args, *_since_args(repo, since)])
    commits: list[dict] = []
    for chunk in out.split(_RECORD_SEP_BYTES):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        parts = chunk.split("\x00", 3)
        if len(parts) < 3:
            continue
        sha, date, subject = parts[0], parts[1], parts[2]
        body = parts[3].strip() if len(parts) == 4 else ""
        commits.append(
            {"hash": sha, "date": date, "subject": subject.strip(), "body": body}
        )
    return commits


def _touched_paths(repo: Path, sha: str, statuses: str = "") -> list[str]:
    """File paths a commit touched, optionally filtered by status letters.

    Rename detection is forced ON (``-M``, matching the sweep queries) so a
    renamed file reports as ``R<score>\\told\\tnew`` — NOT as a delete+add —
    and therefore never lands in the deletion sweep as a false revert. For
    renames the new path is used.
    """
    args = ["show", sha, "-M", "--name-status", "--format="]
    out = _run_git(repo, args)
    paths: list[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0].strip(), parts[-1].strip()
        if statuses and status[:1] not in statuses:
            continue
        if path:
            paths.append(path)
    return paths


def _dropped_sql_entities(repo: Path, sha: str) -> list[tuple[str, str]]:
    """(entity_path, entity_type) for tables/columns a revert commit tears down.

    Reverts come in two diff shapes, and both are covered with the same DDL
    regexes the migration importer uses:

      - a ``git revert`` of the adding commit REMOVES the original
        ``ADD COLUMN`` / ``CREATE TABLE`` lines (``-`` side), and
      - a hand-written down-migration ADDS ``DROP COLUMN`` / ``DROP TABLE``
        lines (``+`` side).

    Either way the entity is what got torn down — so a revert that visibly
    dropped ``users.email`` seeds a column-level entity, not just a file.
    """
    out = _run_git(repo, ["show", sha, "-M", "--format=", "--unified=0"])
    removed_lines: list[str] = []
    added_lines: list[str] = []
    for line in out.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            removed_lines.append(line[1:])
        elif line.startswith("+") and not line.startswith("+++"):
            added_lines.append(line[1:])
    removed = "\n".join(removed_lines)
    added = "\n".join(added_lines)

    entities: list[tuple[str, str]] = []
    # Added DROP statements (down-migration shape).
    for m in _SQL_DROP_COLUMN.finditer(added):
        entities.append((f"{m.group(1)}.{m.group(2)}", "column"))
    for m in _SQL_DROP_TABLE.finditer(added):
        entities.append((m.group(1), "table"))
    # Removed ADD/CREATE statements (git-revert shape).
    for m in _SQL_ADD_COLUMN.finditer(removed):
        entities.append((f"{m.group(1)}.{m.group(2)}", "column"))
    for m in _SQL_CREATE_TABLE.finditer(removed):
        entities.append((m.group(1), "table"))
    return entities


_REVERT_RE = re.compile(r"revert", re.IGNORECASE)


def collect_revert_events(
    repo: Path,
    *,
    since: str = "",
    project: str = "",
) -> list[ChangeEvent]:
    """Walk git history and return revert ChangeEvents, oldest first.

    Pure collection — nothing is written; the CLI layer handles dedupe
    against the store and the actual insert. Events within one run are
    already unique on ``(git_commit, entity_path)``.
    """
    repo = repo.resolve()
    _run_git(repo, ["rev-parse", "--git-dir"])  # fail fast on non-repos

    # Sweep 1: commits whose message mentions "revert".
    message_hits = _log_commits(repo, ["-i", "--grep=revert"], since)
    # Sweep 2: commits that deleted files (regardless of message). -M forces
    # rename detection to match _touched_paths, so a pure rename is an R,
    # not a D, on both sides — independent of the user's diff.renames config.
    deletion_hits = _log_commits(repo, ["--diff-filter=D", "-M"], since)

    by_hash: dict[str, dict] = {}
    for commit in message_hits + deletion_hits:
        by_hash.setdefault(commit["hash"], commit)

    events: list[ChangeEvent] = []
    seen: set[tuple[str, str]] = set()
    # git log is newest-first; emit oldest-first so timestamps read forward.
    for sha in reversed(list(by_hash)):
        commit = by_hash[sha]
        reasoning = commit["subject"]
        if commit["body"]:
            reasoning += "\n\n" + commit["body"]

        candidates: list[tuple[str, str]] = []
        if _REVERT_RE.search(commit["subject"]) or _REVERT_RE.search(commit["body"]):
            # Message sweep: any table/column visibly dropped in the patch,
            # then every touched path. SQL entities lead so the per-commit
            # cap can never truncate the highest-value extractions away.
            candidates += _dropped_sql_entities(repo, sha)
            candidates += [(p, "file") for p in _touched_paths(repo, sha)]
        else:
            # Deletion sweep only: just the deleted files.
            candidates += [(p, "file") for p in _touched_paths(repo, sha, statuses="D")]

        for entity_path, entity_type in candidates[:_MAX_ENTITIES_PER_COMMIT]:
            key = (sha, entity_path)
            if key in seen:
                continue
            seen.add(key)
            events.append(
                ChangeEvent(
                    entity_path=entity_path,
                    change_type="revert",
                    entity_type=entity_type,
                    timestamp=commit["date"],
                    reasoning=reasoning,
                    agent=GIT_IMPORT_AGENT,
                    git_commit=sha,
                    project=project,
                )
            )
    # ChangeEvent normalized every timestamp to canonical UTC, so a plain
    # lexicographic sort is chronological.
    events.sort(key=lambda e: e.timestamp)
    return events
