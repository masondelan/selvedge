"""Shared result shaping for the CLI and the MCP server.

Both surfaces read the same storage layer, and both were assembling their own
defaults, error shapes and derived fields on top of it. Every time one grew a
rule the other had to be edited to match by hand — and the tell is that the
drifted code *asserts* parity in its comments: ``cli.py`` read "Mirror the MCP
blame tool ... so the two surfaces can't diverge" directly above a divergence.

Three drifts had accumulated by v0.3.9.3:

- ``metadata`` was a ``dict`` on ``blame`` and a JSON *string* on ``diff`` /
  ``history`` / ``search`` / ``changeset`` / ``prior_attempts``, so the
  documented rename-following idiom ``event["metadata"]["renamed_from"]``
  raised ``TypeError`` on five of six read surfaces. Fixed at the storage
  chokepoint (:func:`selvedge.storage._coalesce_event_nullables`) rather than
  here, since that is where nullability is already normalized.
- ``prior_attempts --fuzzy`` returned different lists: the tool prepended a
  bare ``{"note": ...}`` row with no ``entity_path``, the CLI sent the same
  message to stderr and returned one row fewer.
- An empty changeset was ``[]`` on the CLI and ``[{"error": ...}]`` on the
  tool.

The value here is not tidiness. It removes the *mechanism* by which those keep
appearing: one function per shared surface, called by both entry points, so
"the CLI mirrors the tool" is a fact about the code rather than a comment.

This module deliberately imports nothing from ``selvedge.server`` — it must
stay usable by the CLI without dragging in the FastMCP runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:  # pragma: no cover
    from .storage import SelvedgeStorage


class BlameResult(TypedDict):
    """The most recent change for an entity, or an error payload.

    On success: every event field is populated, ``error`` is the empty
    string. On miss: every event field is empty, ``error`` carries the
    "no history found" message.
    """

    id: str
    timestamp: str
    entity_type: str
    entity_path: str
    change_type: str
    diff: str
    reasoning: str
    agent: str
    session_id: str
    git_commit: str
    project: str
    changeset_id: str
    metadata: dict
    revisit_after: str
    expires_when: str
    supersedes: str
    constraint: str
    stale_when: str
    superseded_by: str
    status: str
    error: str


EMPTY_BLAME: BlameResult = {
    "id": "",
    "timestamp": "",
    "entity_type": "",
    "entity_path": "",
    "change_type": "",
    "diff": "",
    "reasoning": "",
    "agent": "",
    "session_id": "",
    "git_commit": "",
    "project": "",
    "changeset_id": "",
    "metadata": {},
    "revisit_after": "",
    "expires_when": "",
    "supersedes": "",
    "constraint": "",
    "stale_when": "",
    "superseded_by": "",
    "status": "",
    "error": "",
}


def blame_payload(storage: SelvedgeStorage, entity_path: str) -> BlameResult:
    """The `blame` result for an entity — the exact shape both surfaces emit.

    Always returns the full :class:`BlameResult` shape, miss included. The
    CLI used to emit only ``{"error": ...}`` on a miss, so a client written
    against the TypedDict and pointed at the CLI got a ``KeyError`` on every
    field — on the common case, and the one the shipped agent prompt tells
    agents to reach for.

    Does NOT record the tool call: the two surfaces label the caller
    differently (``agent="cli"`` vs the MCP default), so that stays with each
    entry point.
    """
    row = storage.get_blame(entity_path)
    if not row:
        miss: BlameResult = dict(EMPTY_BLAME)  # type: ignore[assignment]
        miss["error"] = f"No history found for '{entity_path}'"
        return miss

    # Merge over the empty template so every schema field is populated even
    # if the storage layer returns a slimmer dict.
    populated: BlameResult = dict(EMPTY_BLAME)  # type: ignore[assignment]
    populated.update(row)  # type: ignore[typeddict-item]

    # Derived decision state: entity-level status, plus whether THIS event has
    # been overridden by a later supersede.
    decision = storage.get_decision_status(entity_path)
    populated["status"] = decision["status"]
    populated["superseded_by"] = next(
        (e["superseded_by"] for e in decision["trail"] if e["id"] == populated["id"]),
        "",
    )
    populated["error"] = ""
    return populated


def changeset_payload(storage: SelvedgeStorage, changeset_id: str) -> list[dict]:
    """All events sharing a changeset id, or a one-element error list.

    The error element is what lets a caller tell "unknown changeset" from
    "changeset with no events". The CLI returned a bare ``[]`` for both.
    """
    events = storage.get_changeset(changeset_id)
    if not events:
        return [{"error": f"No events found for changeset '{changeset_id}'"}]
    return events


def prior_attempts_payload(
    storage: SelvedgeStorage,
    *,
    entity_path: str = "",
    description: str = "",
    fuzzy: str = "",
    min_confidence: str = "proximity_high",
    window_minutes: int = 10080,
    limit: int = 20,
) -> tuple[list[dict], list[str]]:
    """Prior attempts for an entity or description, optionally fuzzy-expanded.

    Returns ``(rows, notices)``. ``rows`` is the list BOTH surfaces emit
    verbatim; ``notices`` are human-facing lines the CLI prints to stderr and
    the MCP tool ignores (the rows already carry the same information in their
    ``note`` field).

    Every row carries a ``note`` key, empty on a normal row. The fallback used
    to be a bare ``{"note": ...}`` element with no other keys, so the obvious
    loop — ``for r in results: r["entity_path"]`` — raised ``KeyError`` on the
    first element whenever the optional ``[semantic]`` extra was absent, which
    is every default install. A row that doesn't match the shape of its
    siblings is worse than no row.
    """
    rows: list[dict] = []
    notices: list[str] = []

    if entity_path or description:
        rows = storage.get_prior_attempts(
            entity_path=entity_path,
            query=description,
            min_confidence=min_confidence,
            window_minutes=window_minutes,
            limit=limit,
        )

    if fuzzy:
        from . import semantic

        try:
            rows += semantic.fuzzy_prior_attempts(
                storage,
                fuzzy,
                min_confidence=min_confidence,
                window_minutes=window_minutes,
                limit=limit,
                exclude_paths={r["entity_path"] for r in rows},
                exclude_ids={r["id"] for r in rows},
            )
        except (semantic.SemanticUnavailable, semantic.IndexMissing) as e:
            existing_paths = {r["entity_path"] for r in rows}
            rows += [
                r
                for r in storage.get_prior_attempts(
                    query=fuzzy,
                    min_confidence=min_confidence,
                    window_minutes=window_minutes,
                    limit=limit,
                )
                if r["entity_path"] not in existing_paths
            ]
            note = f"fuzzy matching unavailable ({e}); fell back to substring"
            notices.append(note)
            rows = [_fallback_note_row(note), *rows]

    for row in rows:
        row.setdefault("note", "")
    return rows, notices


def _fallback_note_row(note: str) -> dict:
    """A note carried as a fully-shaped row, not a one-key stub.

    Uses the event shape with every field empty so a caller iterating rows and
    reading `entity_path` (or any other field) gets an empty string rather
    than an exception.
    """
    row = dict(EMPTY_BLAME)
    row.pop("status", None)
    row.pop("error", None)
    row.update(
        {
            "note": note,
            "outcome": "",
            "confidence": "",
            "outcome_reasoning": "",
            "supersede_reasoning": "",
            "current_status": "",
            "match_type": "note",
            "similarity": 0.0,
        }
    )
    return row
