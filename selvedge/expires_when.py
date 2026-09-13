"""
The ``expires_when`` closed grammar and its local-only evaluator (v0.3.11).

The ``expires_when`` column shipped in v0.3.8 (schema migration v3) but no
write path populated it; this module lights it up. A decision can carry a
machine-checkable expiry condition, and ``stale_decisions`` evaluates it —
from local state only, no network, no LLM — so the decision surfaces the
moment its recorded invalidation condition fires.

**Closed grammar, on purpose.** Free-form conditions would fragment
instantly ("when django is new enough" vs "django >= 5" vs prose), and a
condition that can't be evaluated deterministically is a condition that
silently rots. v1 recognizes exactly four shapes, declared in
:data:`PATTERNS`; anything else is REJECTED at write time (the
:class:`~selvedge.models.ChangeEvent` constructor calls
:func:`validate_expires_when`, so every write path — MCP tool, CLI,
importers — enforces the grammar). The grammar grows deliberately,
versioned in ``docs/architecture.md``.

The four shapes, chosen because each is evaluable from local state:

  - ``library:NAME>=VERSION`` — revisit when the named dependency reaches
    a version. Evaluated against locally installed distribution metadata
    (``importlib.metadata``) when available; when the package isn't
    locally observable the evaluation *presents as manual review* rather
    than guessing.
  - ``entity:PATH:changes`` — revisit when the named entity has a change
    event AFTER the decision. Log-derived (entity events, not parsed
    code), per the non-goals.
  - ``date:ISO`` — revisit on a specific date. Compared to now.
  - ``manual:LABEL`` — an opaque label for human review. Never auto-fires.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from .timeutil import normalize_timestamp

# ---------------------------------------------------------------------------
# The closed grammar
# ---------------------------------------------------------------------------

#: The v1 grammar, keyed by pattern kind. A value is valid iff exactly one of
#: these regexes fullmatches it (the ``KIND:`` prefixes are disjoint, so at
#: most one can). Grows deliberately — additions are versioned in
#: ``docs/architecture.md``'s Phase 2.17 section.
PATTERNS: dict[str, re.Pattern[str]] = {
    # library:NAME>=VERSION — NAME is a PEP 503-ish distribution name,
    # VERSION is dotted-numeric only (comparable without a version library,
    # keeping the evaluator dependency-free).
    "library": re.compile(
        r"^library:(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
        r">=(?P<version>\d+(?:\.\d+)*)$"
    ),
    # entity:PATH:changes — PATH is any non-empty entity path. Greedy so a
    # path containing ':' (src/auth.py::login) keeps every colon except the
    # grammar's own delimiters.
    "entity": re.compile(r"^entity:(?P<path>.+):changes$"),
    # date:ISO — the payload must parse as an ISO 8601 date/datetime
    # (validated semantically in validate_expires_when, not just by shape).
    "date": re.compile(r"^date:(?P<when>\d{4}-\d{2}-\d{2}(?:[T ].+)?)$"),
    # manual:LABEL — an opaque, non-empty label. Never auto-fires.
    "manual": re.compile(r"^manual:(?P<label>.+)$"),
}

#: One-line, deterministic description of the accepted shapes — embedded in
#: the rejection error so the agent that mis-wrote a value can self-correct.
GRAMMAR_HINT = (
    "expected one of: 'library:NAME>=VERSION' (e.g. 'library:django>=5.0'), "
    "'entity:PATH:changes' (e.g. 'entity:users.email:changes'), "
    "'date:ISO' (e.g. 'date:2027-01-01'), or 'manual:LABEL' "
    "(e.g. 'manual:security-review')"
)


@dataclass(frozen=True)
class ExpiresEvaluation:
    """The result of evaluating one ``expires_when`` value.

    ``status`` is one of:

      - ``"expired"`` — the condition fired; the decision is due for review.
      - ``"pending"`` — evaluable, and the condition has not fired.
      - ``"manual_review"`` — not auto-evaluable here: a ``manual:`` label
        (never auto-fires by design), or a ``library:`` whose dependency
        state isn't locally observable (presented as manual review rather
        than guessed at).

    ``kind`` is the :data:`PATTERNS` key that matched; ``detail`` is a
    one-line templated explanation, deterministic for a given store state.
    Every field is always populated, never null.
    """

    status: str
    kind: str
    detail: str


def _parse(value: str) -> tuple[str, re.Match[str]]:
    """Match ``value`` against the closed grammar, returning (kind, match).

    Raises ``ValueError`` (with :data:`GRAMMAR_HINT`) when nothing matches.
    """
    for kind, pattern in PATTERNS.items():
        match = pattern.fullmatch(value)
        if match is not None:
            return kind, match
    raise ValueError(
        f"expires_when {value!r} does not match the closed grammar; {GRAMMAR_HINT}"
    )


def validate_expires_when(value: str) -> str:
    """Validate an ``expires_when`` value against the closed grammar.

    Returns the normalized value to store: ``""`` for empty input, the
    stripped value for ``library:`` / ``entity:`` / ``manual:``, and a
    ``date:`` value with its payload canonicalized to UTC (same posture as
    ``revisit_after`` normalization — canonical stored timestamps keep
    every later comparison trivial and deterministic).

    Raises ``ValueError`` on anything that doesn't match — non-matching
    values are rejected at write time, which is the closed grammar's whole
    defense against syntax fragmentation.
    """
    stripped = value.strip()
    if not stripped:
        return ""
    kind, match = _parse(stripped)
    if kind == "date":
        try:
            canonical = normalize_timestamp(match.group("when"))
        except ValueError as e:
            raise ValueError(
                f"expires_when {value!r} has an unparseable date; {GRAMMAR_HINT}"
            ) from e
        return f"date:{canonical}"
    return stripped


def _installed_version(name: str) -> str | None:
    """The locally installed version of distribution ``name``, or ``None``.

    The "cheap deterministic source" for ``library:`` evaluation —
    ``importlib.metadata`` reads installed dist metadata from disk, no
    network. ``None`` means the package isn't locally observable.
    """
    from importlib import metadata

    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


_NUMERIC_PREFIX_RE = re.compile(r"^(\d+(?:\.\d+)*)")


def _version_tuple(version: str) -> tuple[int, ...] | None:
    """The leading dotted-numeric part of ``version`` as an int tuple.

    ``"2.31.0.post1"`` → ``(2, 31, 0)``. Returns ``None`` when the string
    has no numeric prefix at all — the comparison is then not cheaply
    determinable and the caller degrades to manual review rather than
    guessing.
    """
    match = _NUMERIC_PREFIX_RE.match(version.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _version_suffix(version: str) -> str:
    """Whatever follows the dotted-numeric prefix of ``version``.

    ``"5.0rc1"`` → ``"rc1"``; ``"2.31.0.post1"`` → ``".post1"``; ``"5.0"``
    → ``""``. Returns the whole string when there is no numeric prefix.
    """
    stripped = version.strip()
    match = _NUMERIC_PREFIX_RE.match(stripped)
    if match is None:
        return stripped
    return stripped[match.end():]


def _compare_versions(installed: str, required: str) -> bool | None:
    """True/False when ``installed >= required`` is determinable, else None.

    Dotted-numeric comparison with right-padding (``5`` == ``5.0.0``), so
    the evaluator stays dependency-free. ``required`` is grammar-guaranteed
    numeric; ``installed`` may carry a suffix, which only decides when the
    numeric prefixes differ. When the prefixes are EQUAL and a suffix is
    present, the answer is not cheaply determinable — PEP 440 orders
    ``5.0rc1`` (and a1/b2/dev1) *before* ``5.0`` but ``5.0.post1`` *after*
    it — so this returns ``None`` and the caller degrades to manual review
    rather than guessing, per the module contract. In particular an expiry
    on ``>=5.0`` must not fire the day someone installs ``5.0rc1``.
    """
    installed_t = _version_tuple(installed)
    required_t = _version_tuple(required)
    if installed_t is None or required_t is None:
        return None
    width = max(len(installed_t), len(required_t))
    pad = (0,) * width
    installed_p = (installed_t + pad)[:width]
    required_p = (required_t + pad)[:width]
    if installed_p != required_p:
        return installed_p > required_p
    if _version_suffix(installed):
        return None
    return True


def evaluate_expires_when(
    value: str,
    *,
    decision_timestamp: str,
    now: datetime | None = None,
    entity_changed_after: Callable[[str, str], bool] | None = None,
    installed_version: Callable[[str], str | None] | None = None,
) -> ExpiresEvaluation:
    """Evaluate one stored ``expires_when`` value from local state only.

    No network, no LLM: ``date:`` compares against ``now``; ``entity:``
    asks the injected ``entity_changed_after(path, decision_timestamp)``
    callable (the storage layer wires this to the event log; ``None``
    presents as manual review since there is nothing to check against);
    ``library:`` compares against ``installed_version(name)`` (defaults to
    installed dist metadata) and presents as manual review when the
    dependency isn't locally observable; ``manual:`` never auto-fires.

    ``decision_timestamp`` is the timestamp of the event carrying the
    condition. Raises ``ValueError`` on a value outside the closed grammar
    (only reachable for rows written around the validator, e.g. raw SQL).
    Returns an :class:`ExpiresEvaluation` — deterministic for a given
    store/environment state.
    """
    kind, match = _parse(value.strip())
    now_dt = now if now is not None else datetime.now(timezone.utc)

    if kind == "manual":
        label = match.group("label").strip()
        return ExpiresEvaluation(
            status="manual_review",
            kind="manual",
            detail=f"manual condition '{label}' — never auto-fires",
        )

    if kind == "date":
        canonical = normalize_timestamp(match.group("when"))
        due = datetime.fromisoformat(canonical[:-1] + "+00:00")
        if now_dt >= due:
            return ExpiresEvaluation(
                status="expired",
                kind="date",
                detail=f"expiry date {canonical} has passed",
            )
        return ExpiresEvaluation(
            status="pending",
            kind="date",
            detail=f"expiry date {canonical} not yet reached",
        )

    if kind == "entity":
        path = match.group("path").strip()
        if entity_changed_after is None:
            return ExpiresEvaluation(
                status="manual_review",
                kind="entity",
                detail=f"entity '{path}' cannot be checked here — no event log available",
            )
        if entity_changed_after(path, decision_timestamp):
            return ExpiresEvaluation(
                status="expired",
                kind="entity",
                detail=f"entity '{path}' changed after this decision",
            )
        return ExpiresEvaluation(
            status="pending",
            kind="entity",
            detail=f"no change on entity '{path}' since this decision",
        )

    # kind == "library"
    name = match.group("name")
    required = match.group("version")
    lookup = installed_version if installed_version is not None else _installed_version
    installed = lookup(name)
    if installed is None:
        return ExpiresEvaluation(
            status="manual_review",
            kind="library",
            detail=(
                f"dependency '{name}' is not locally observable "
                "(no installed dist metadata) — review manually"
            ),
        )
    verdict = _compare_versions(installed, required)
    if verdict is None:
        return ExpiresEvaluation(
            status="manual_review",
            kind="library",
            detail=(
                f"dependency '{name}' version {installed!r} is not comparable "
                f"to {required!r} — review manually"
            ),
        )
    if verdict:
        return ExpiresEvaluation(
            status="expired",
            kind="library",
            detail=f"dependency '{name}' {installed} is installed (>= {required})",
        )
    return ExpiresEvaluation(
        status="pending",
        kind="library",
        detail=f"dependency '{name}' {installed} is installed (below {required})",
    )
