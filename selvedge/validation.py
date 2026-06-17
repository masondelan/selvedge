"""
Shared validation utilities — used by both the MCP server's ``log_change``
tool and the CLI's ``selvedge log`` command so the rules stay consistent.

The reasoning-quality validator returns a list of warning strings rather
than raising, because the event itself is still logged — we want to nudge
agents toward better intent capture without dropping data on the floor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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


# ---------------------------------------------------------------------------
# Entity-path shape validation (v0.3.7)
#
# Per-``entity_type`` shape checks that WARN but never reject — the same
# nudge-not-gate posture as the reasoning-quality validator above. The
# patterns live here (not inline in the write path) so new entity types can
# be added by extending ``ENTITY_PATTERNS`` without touching ``log_change``.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityPattern:
    """The expected-shape regex for one entity type plus its nudge message.

    If ``expected`` does NOT match the path, ``hint`` is returned as a
    warning. The regex describes what a well-formed path *looks like*, so a
    missing match is the signal something is probably mis-shaped.
    """

    expected: re.Pattern[str]
    hint: str


#: Expected-shape rules keyed by ``entity_type``. Extend this dict to cover a
#: new type — the write path picks it up with no further changes.
ENTITY_PATTERNS: dict[str, EntityPattern] = {
    "function": EntityPattern(
        re.compile(r"::"),
        "function/method paths usually look like 'src/auth.py::login' "
        "(no '::' separator found between file and symbol).",
    ),
    "column": EntityPattern(
        re.compile(r"\."),
        "column paths usually look like 'table.column' "
        "(no '.' separator found between table and column).",
    ),
    "file": EntityPattern(
        # A file path normally has a directory separator OR a file extension.
        re.compile(r"/|\.[^./]+$"),
        "file paths usually have a directory separator or an extension, "
        "like 'src/auth.py' (none found).",
    ),
}


# ---------------------------------------------------------------------------
# Revisit-date nudge (v0.3.8, Phase 2.14)
#
# When an agent records an *architectural* add/modify/create/migrate, the
# decision is exactly the kind that tends to age out — so nudge it to set
# ``revisit_after``. Soft warning, never a rejection, same posture as the two
# validators above. Date-based active memory (the ``stale_decisions`` surface)
# is only as useful as the revisit dates agents actually set, and this is where
# they get prompted to set one.
# ---------------------------------------------------------------------------

#: change_types architectural enough to be worth a revisit date.
REVISIT_NUDGE_CHANGE_TYPES: frozenset[str] = frozenset(
    {"add", "modify", "create", "migrate"}
)

#: entity_types whose changes are load-bearing decisions, not leaf edits.
REVISIT_NUDGE_ENTITY_TYPES: frozenset[str] = frozenset(
    {"table", "schema", "dependency", "config"}
)


def check_revisit_nudge(
    change_type: str, entity_type: str, revisit_after: str
) -> list[str]:
    """Return a soft nudge to set ``revisit_after`` on an architectural change.

    Fires only when the change is an architectural add/modify/create/migrate
    (``REVISIT_NUDGE_CHANGE_TYPES`` × ``REVISIT_NUDGE_ENTITY_TYPES``) AND no
    ``revisit_after`` was provided. Advisory only — the event is logged
    regardless, exactly like :func:`check_reasoning_quality`. Empty list when
    the change isn't architectural or a revisit date is already set.
    """
    if revisit_after.strip():
        return []
    if change_type not in REVISIT_NUDGE_CHANGE_TYPES:
        return []
    if entity_type not in REVISIT_NUDGE_ENTITY_TYPES:
        return []
    return [
        f"consider setting revisit_after on this {entity_type} change — "
        "architectural decisions age out, and a revisit date lets "
        "`selvedge stale` / the stale_decisions tool surface it for review "
        "later (e.g. '90d', '6mo', or an ISO date)."
    ]


def check_entity_path_shape(entity_path: str, entity_type: str) -> list[str]:
    """Return soft warnings when ``entity_path`` doesn't fit ``entity_type``.

    Warn-never-reject — the event is still logged, exactly like
    :func:`check_reasoning_quality`. Returns an empty list when the type has
    no shape rule, the path is empty, or the path already matches the
    expected shape.
    """
    pattern = ENTITY_PATTERNS.get(entity_type)
    if pattern is None:
        return []
    stripped = entity_path.strip()
    if not stripped:
        return []
    if pattern.expected.search(stripped):
        return []
    return [pattern.hint]
