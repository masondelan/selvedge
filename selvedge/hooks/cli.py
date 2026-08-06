"""
``selvedge-hook`` — the console entry point agent harnesses invoke.

Subcommand dispatcher kept deliberately tiny and dependency-free (no Click):
this binary runs on EVERY gated tool call inside an agent loop, so import
cost is latency the user feels. Today's only subcommand:

    selvedge-hook pretooluse [--dry-run]

Reads the Claude Code PreToolUse payload on stdin; exits 0 to allow, 2 to
block (reason on stderr). See :mod:`selvedge.hooks.pretooluse`.
"""

from __future__ import annotations

import os
import sys

# Duplicated from `.pretooluse.DISABLE_ENV` rather than imported, because
# importing it would load the module this check exists to avoid loading.
# `test_disable_env_name_matches_pretooluse` pins the two together.
_DISABLE_ENV = "SELVEDGE_HOOK_DISABLE"

_USAGE = """\
usage: selvedge-hook pretooluse [--dry-run]

Claude Code PreToolUse gate: blocks Edit/Write/Bash calls that touch
schema/migration paths until prior_attempts has been queried for the
affected entities this session. Reads the hook payload on stdin.

  --dry-run   evaluate and print the decision as JSON; always exit 0

Bypass with SELVEDGE_HOOK_DISABLE=1. Install via `selvedge setup`.
"""


def main() -> None:
    """Dispatch to the named hook. Unknown/missing subcommands print usage."""
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(_USAGE)
        sys.exit(0)
    if argv[0] == "pretooluse":
        # The bypass has to short-circuit HERE, not inside `evaluate()`. The
        # documented escape hatch used to be checked after every import had
        # already run, so setting it saved nothing measurable — the whole cost
        # is import, not logic. `--dry-run` deliberately falls through so it
        # still reports the decision the hook would have made.
        if os.environ.get(_DISABLE_ENV) == "1" and "--dry-run" not in argv:
            sys.exit(0)

        from .pretooluse import run

        sys.exit(run(argv[1:]))
    print(_USAGE, file=sys.stderr)
    print(f"error: unknown hook {argv[0]!r}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
