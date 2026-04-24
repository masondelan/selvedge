"""
Shared validation utilities — used by both the MCP server's ``log_change``
tool and the CLI's ``selvedge log`` command so the rules stay consistent.

The reasoning-quality validator returns a list of warning strings rather
than raising, because the event itself is still logged — we want to nudge
agents toward better intent capture without dropping data on the floor.
"""

from __future__ import annotations

import re

# Patterns matching placeholder reasoning that an agent might log instead
# of describing actual intent. Matched case-insensitively against the
# stripped reasoning string.
#
# Verb forms use ``(?:ed)?`` so both the bare verb and its past tense match.
# (Earlier versions used ``ed?`` which made only the trailing 'd' optional —
# ``^fixed?$`` matched "fixe"/"fixed" but never the bare "fix" placeholder.)
GENERIC_REASONING_PATTERNS: tuple[str, ...] = (
    r"^user request$",
    r"^as requested$",
    r"^per request$",
    r"^done$",
    r"^update(?:d)?$",
    r"^change(?:d)?$",
    r"^fix(?:ed)?$",
    r"^add(?:ed)?$",
    r"^remove(?:d)?$",
    r"^n/?a$",
    r"^none$",
    r"^todo$",
    r"^see (?:diff|code|pr)$",
)

REASONING_MIN_LENGTH = 20

_GENERIC_REASONING_REGEX = re.compile(
    "|".join(f"(?:{pat})" for pat in GENERIC_REASONING_PATTERNS),
    re.IGNORECASE,
)


def check_reasoning_quality(reasoning: str) -> list[str]:
    """
    Return a list of human-readable warnings if ``reasoning`` looks low quality.

    Empty list means the reasoning looks fine. Warnings are advisory — the
    caller is expected to log the event regardless and surface these to
    the user / agent so the next call is better.
    """
    warnings: list[str] = []
    stripped = reasoning.strip()

    if not stripped:
        warnings.append(
            "reasoning is empty — log WHY this change was made, not just what. "
            "Include the user's request or the problem being solved."
        )
        # No point checking length / patterns if empty.
        return warnings

    if len(stripped) < REASONING_MIN_LENGTH:
        warnings.append(
            f"reasoning is very short ({len(stripped)} chars). "
            "Aim for at least a sentence describing the intent behind this change."
        )

    if _GENERIC_REASONING_REGEX.fullmatch(stripped):
        warnings.append(
            f"reasoning looks generic ({stripped!r}). "
            "Describe the actual intent: what problem this solves, what the "
            "user asked for, or why this approach was chosen."
        )

    return warnings
