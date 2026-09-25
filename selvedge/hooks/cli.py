"""
``selvedge-hook`` — the console entry point agent harnesses invoke.

Subcommand dispatcher kept deliberately tiny and dependency-free (no Click):
this binary runs on EVERY gated tool call inside an agent loop, so import
cost is latency the user feels. For example:

    selvedge-hook pretooluse [--dry-run]

Reads a native hook payload on stdin. Claude Code (the default) and Windsurf
deny with exit 2 and a stderr reason. Other clients selected through --agent
communicate decisions through JSON on stdout with exit 0.
"""

from __future__ import annotations

import os
import sys

# Duplicated from `.pretooluse.DISABLE_ENV` rather than imported, because
# importing it would load the module this check exists to avoid loading.
# `test_disable_env_name_matches_pretooluse` pins the two together.
_DISABLE_ENV = "SELVEDGE_HOOK_DISABLE"

_USAGE = """\
usage: selvedge-hook <pretooluse|sessionstart|precompact> [--agent CLIENT] [--dry-run]

  pretooluse    Gate. Blocks Edit/Write/Bash calls touching schema or
                migration paths until prior_attempts has been queried for
                the affected entities within the configured recent window.
  sessionstart  Delivery. Injects a compact digest at session start —
                decisions due for revisit, reverted entities, recent
                changesets. Silent when there is nothing to say.
  precompact    Advisory reminder about unsaved decisions before compaction.
                Client support varies; a user notification does not inject
                context into the model or automatically save decisions.

Each reads its hook payload on stdin.

  --agent     codex, cursor, copilot (VS Code Local), gemini, or windsurf.
              Omit for the existing Claude Code protocol. Client capabilities
              differ; compaction notifications for these clients are advisory.

  --dry-run   evaluate and print what would be emitted; always exit 0

Bypass with SELVEDGE_HOOK_DISABLE=1. Install via `selvedge setup`.
"""

#: Subcommand -> module holding its `run(argv)`. Imported lazily, one at a
#: time, so a hook never pays for its siblings' imports.
_HOOKS = ("pretooluse", "sessionstart", "precompact")


def main() -> None:
    """Dispatch to the named hook. Unknown/missing subcommands print usage."""
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(_USAGE)
        sys.exit(0)
    if argv[0] in _HOOKS:
        # Keep the documented bypass cheap for every client. Cursor expects an
        # explicit permission response even when no evaluation is performed.
        if os.environ.get(_DISABLE_ENV) == "1" and "--dry-run" not in argv:
            if "--agent" in argv:
                index = argv.index("--agent")
                if argv[index + 1 : index + 2] == ["cursor"] and argv[0] == "pretooluse":
                    print('{"permission": "allow"}')
            sys.exit(0)
        if "--agent" in argv:
            from .adapters import AGENTS, run

            index = argv.index("--agent")
            if index + 1 >= len(argv) or argv[index + 1] not in AGENTS:
                print("error: --agent requires a supported hook client", file=sys.stderr)
                sys.exit(1)
            sys.exit(run(argv[index + 1], argv[0], argv[1:]))
        # Imported by name, one hook per process, so each pays only for its
        # own module.
        from importlib import import_module

        module = import_module(f".{argv[0]}", __package__)
        sys.exit(module.run(argv[1:]))
    print(_USAGE, file=sys.stderr)
    print(f"error: unknown hook {argv[0]!r}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
