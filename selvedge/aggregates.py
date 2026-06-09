"""
Aggregate digest helpers for Selvedge.

``summary()`` is a pure-Python **library** function — deliberately *not* an
MCP tool and *not* a CLI command in v0.3.7. It rolls a window of change
events up into a small, schema-versioned dataclass (changesets touched,
agents involved, top entities by activity) that v0.3.9's ``selvedge audit``
/ ``selvedge digest`` surface will consume directly.

Why a library function and not a tool:

  - An MCP ``summary`` tool would be a genuinely new aggregate shape, but the
    agent-facing need is thin — an agent calling ``prior_attempts`` already
    gets the relevant events and can roll up client-side. Promotion to MCP is
    a telemetry decision, not a roadmap commitment.
  - The output is templated, deterministic dataclass assembly — **no LLM
    call**. The generic shape is intentional so downstream consumers
    (``audit`` / ``digest``) own their own rendering. ``summary_version`` is
    stamped on every result so the shape can be lifted to MCP later without
    breaking the v0.3.9 CLI consumers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .storage import SelvedgeStorage

#: Schema version of the :class:`Summary` shape. Bump when fields change so
#: consumers (and a future MCP promotion) can detect the shape they got.
SUMMARY_VERSION = 1


@dataclass
class EntityActivity:
    """One entry in :attr:`Summary.top_entities` — an entity and its volume."""

    entity_path: str
    event_count: int


@dataclass
class Summary:
    """A templated, schema-versioned roll-up of change activity.

    Every field is always populated (empty list / zero for "absent"), the
    same never-``null`` convention the MCP result types follow, so consumers
    never have to guard for missing keys.
    """

    total_events: int
    changesets: list[str]
    agents: list[str]
    top_entities: list[EntityActivity]
    since: str = ""
    project: str = ""
    summary_version: int = field(default=SUMMARY_VERSION)

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict (for ``--json`` consumers / tests)."""
        return {
            "summary_version": self.summary_version,
            "since": self.since,
            "project": self.project,
            "total_events": self.total_events,
            "changesets": list(self.changesets),
            "agents": list(self.agents),
            "top_entities": [
                {"entity_path": e.entity_path, "event_count": e.event_count}
                for e in self.top_entities
            ],
        }


def summary(
    storage: SelvedgeStorage,
    *,
    since: str = "",
    project: str = "",
    top_n: int = 10,
) -> Summary:
    """Build a :class:`Summary` of change activity from ``storage``.

    Pure read, no side effects, no LLM call. ``since`` is a canonical UTC
    timestamp (resolve relative shorthand like ``"7d"`` upstream with
    :func:`selvedge.timeutil.parse_time_string`); ``project`` scopes to one
    repository; ``top_n`` caps the busiest-entities list.
    """
    raw = storage.summarize(since=since, project=project, top_n=top_n)
    return Summary(
        total_events=raw["total_events"],
        changesets=raw["changesets"],
        agents=raw["agents"],
        top_entities=[
            EntityActivity(entity_path=e["entity_path"], event_count=e["event_count"])
            for e in raw["top_entities"]
        ],
        since=since,
        project=project,
    )
