"""
Canonical agent-instructions block + idempotent installer.

The block is the single source of truth for what to put in a project's
``CLAUDE.md`` / ``.cursorrules`` so the agent knows when and how to call
Selvedge's MCP tools. Centralizing it here means:

  - users can fetch the current version with ``selvedge prompt``,
  - users can install / update it in their project file with
    ``selvedge prompt --install <file>`` and re-running picks up new
    revisions without any copy-paste drift,
  - the wizard in ``selvedge.setup`` reads the same constant when it
    drops the block into newly-set-up projects.

Idempotence comes from a pair of sentinel markers. The block is always
inserted between ``<!-- selvedge:start -->`` and ``<!-- selvedge:end -->``.
Re-running ``--install`` on a file that already has the markers updates
the content between them and leaves the rest of the file alone.
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Sentinel markers — anything between these two lines (exclusive) is
#: managed by Selvedge. Markers must stay byte-stable across releases so
#: blocks installed by one version are recognized by the next.
SENTINEL_START = "<!-- selvedge:start -->"
SENTINEL_END = "<!-- selvedge:end -->"

#: A pre-compiled regex that matches the existing block (including the
#: sentinels and any whitespace between them). DOTALL so ``.`` covers
#: newlines.
_BLOCK_RE = re.compile(
    re.escape(SENTINEL_START) + r".*?" + re.escape(SENTINEL_END),
    re.DOTALL,
)

#: The canonical agent-instructions block. Mirrors the prose in
#: ``docs/architecture.md``'s "System prompt / end-user agent
#: instructions" section. When you change the doc, change this — the
#: tests assert they're in lockstep.
PROMPT_BLOCK = """\
## Selvedge — change tracking

You have access to Selvedge (MCP server: `selvedge`) for change tracking.

**Rules:**

- Call `selvedge.log_change` immediately after adding, modifying, or
  removing any DB column, table, function, API endpoint, dependency,
  or env variable.
- Set `reasoning` to the user's original request or the problem being
  solved. Write at least one full sentence — the server will warn on
  empty, very short, or generic values like "user request" or "done".
  Good example: "User asked to add 2FA — needs phone number to send
  SMS verification codes."
- Set `agent` to "claude-code" (or whichever agent you are).
- Set `session_id` if you have access to the current session/conversation ID.
- Set `git_commit` to the commit hash once you know it.
- For multi-entity changes (e.g. adding a whole feature), set a shared
  `changeset_id` on all related `log_change` calls — use a short slug
  like `add-stripe-billing`. This lets anyone query the full scope of
  the change with `selvedge.changeset()`.
- Before modifying an entity, call `selvedge.diff` or `selvedge.blame`
  to understand its history and avoid conflicting with past decisions.
"""


# ---------------------------------------------------------------------------
# Block construction
# ---------------------------------------------------------------------------


def render_block() -> str:
    """Return the prompt block wrapped in sentinel markers.

    The output is what gets written to user files — sentinels first and
    last, prompt content in between, no trailing newline. Callers that
    need to append to existing content should add their own surrounding
    whitespace (see ``install_to_file``).
    """
    return f"{SENTINEL_START}\n{PROMPT_BLOCK.strip()}\n{SENTINEL_END}"


# ---------------------------------------------------------------------------
# File installation — idempotent, sentinel-bracketed
# ---------------------------------------------------------------------------


def install_to_file(path: Path, *, write_backup: bool = True) -> tuple[str, Path | None]:
    """Idempotently install the prompt block into ``path``.

    Returns a tuple of ``(action, backup_path)`` where ``action`` is one
    of:

      - ``"created"`` — file did not exist; created with just the block
      - ``"appended"`` — file existed but had no Selvedge block; block
        appended after a blank line
      - ``"updated"`` — file had an old Selvedge block; replaced
        in-place
      - ``"unchanged"`` — file already had the current block byte-for-byte

    ``backup_path`` is the path to the ``.bak`` file when one was
    written, ``None`` otherwise. ``.bak`` is written before any
    modification when ``write_backup`` is True (the default), even on
    "appended" and "updated". A "created" action never writes a backup
    because there is nothing to back up.

    The file's parent directory is created if it doesn't exist — same
    semantics as ``mkdir -p`` upstream of the write. This is what lets
    the wizard target ``~/.config/something/file.md`` even on a fresh
    machine where the parent doesn't exist yet.
    """
    new_block = render_block()

    if not path.exists():
        # Greenfield install — single block, no surrounding content.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_block + "\n")
        return ("created", None)

    existing = path.read_text()
    backup_path: Path | None = None

    # Existing Selvedge block? Update in place.
    match = _BLOCK_RE.search(existing)
    if match:
        if match.group(0) == new_block:
            return ("unchanged", None)
        if write_backup:
            backup_path = _write_backup(path, existing)
        updated = existing[: match.start()] + new_block + existing[match.end() :]
        path.write_text(updated)
        return ("updated", backup_path)

    # No block — append after the existing content with a blank-line
    # separator. Preserve the trailing newline convention of the source
    # file (most repos end .md files with one).
    if write_backup:
        backup_path = _write_backup(path, existing)
    separator = "\n\n" if existing and not existing.endswith("\n\n") else "\n"
    appended = existing.rstrip("\n") + separator + new_block + "\n"
    path.write_text(appended)
    return ("appended", backup_path)


def _write_backup(path: Path, contents: str) -> Path:
    """Write ``<path>.bak`` next to ``path`` containing ``contents``.

    If a backup file already exists at the target path we leave it
    alone and write to ``.bak.1``, ``.bak.2``, etc. — the goal is "the
    user can always undo" rather than "we always have exactly one
    backup", and overwriting the previous backup defeats the first.
    """
    candidate = path.with_suffix(path.suffix + ".bak")
    counter = 1
    while candidate.exists():
        candidate = path.with_suffix(f"{path.suffix}.bak.{counter}")
        counter += 1
    candidate.write_text(contents)
    return candidate
