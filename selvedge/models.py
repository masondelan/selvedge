"""Core data models for Selvedge."""

import uuid
from dataclasses import dataclass, field
from enum import Enum

from .timeutil import normalize_timestamp, utc_now_iso


class ChangeType(str, Enum):
    """The kind of change that occurred."""
    ADD = "add"
    REMOVE = "remove"
    MODIFY = "modify"
    RENAME = "rename"
    RETYPE = "retype"
    CREATE = "create"
    DELETE = "delete"
    INDEX_ADD = "index_add"
    INDEX_REMOVE = "index_remove"
    MIGRATE = "migrate"
    # A supersede event re-opens a previously reverted decision (v0.3.9.1).
    # It links back to the event it overrides via ChangeEvent.supersedes —
    # the record stays append-only; "re-opened" is a new fact, not an edit.
    SUPERSEDE = "supersede"


class EntityType(str, Enum):
    """The kind of entity that was changed."""
    COLUMN = "column"
    TABLE = "table"
    FILE = "file"
    FUNCTION = "function"
    CLASS = "class"
    ENDPOINT = "endpoint"
    DEPENDENCY = "dependency"
    ENV_VAR = "env_var"
    INDEX = "index"
    SCHEMA = "schema"
    CONFIG = "config"
    OTHER = "other"


VALID_CHANGE_TYPES: frozenset[str] = frozenset(ct.value for ct in ChangeType)
VALID_ENTITY_TYPES: frozenset[str] = frozenset(et.value for et in EntityType)


@dataclass
class ChangeEvent:
    """
    A single recorded change to a codebase entity.

    entity_path conventions:
      - DB column:   "users.email"
      - DB table:    "users"
      - Code symbol: "src/auth.py::login"
      - File:        "src/auth.py"
      - API route:   "api/v1/users"
      - Dependency:  "deps/stripe"
      - Env var:     "env/STRIPE_KEY"

    Validation runs in ``__post_init__``:

      - ``entity_path`` must be a non-empty string (raises ValueError).
      - ``change_type`` must be one of :class:`ChangeType` (raises ValueError);
        unknown values are rejected so typos and hallucinated types don't
        silently corrupt the dataset.
      - ``entity_type`` is coerced to ``"other"`` if not a known
        :class:`EntityType`. (Coerced rather than rejected because the
        entity type is descriptive, not load-bearing for queries.)
      - ``timestamp`` is normalized to canonical UTC (``...Z`` suffix) so
        lexicographic ordering matches chronological ordering across
        mixed timezones.
    """
    entity_path: str
    change_type: str

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=utc_now_iso)
    entity_type: str = EntityType.OTHER
    diff: str = ""
    reasoning: str = ""
    agent: str = ""
    session_id: str = ""
    git_commit: str = ""
    project: str = ""
    changeset_id: str = ""  # Groups related changes (e.g. all events from one feature)
    metadata: str = "{}"  # JSON string for extensibility

    # Active memory v1 (v0.3.8, Phase 2.14). Both default to "" (absent) and
    # follow the "every field always populated, never null" convention. The
    # underlying SQLite columns are nullable so pre-v3 rows read back as NULL;
    # new writes store "" rather than NULL.
    #   - revisit_after: an ISO-8601 date OR a relative offset from `timestamp`
    #     (e.g. "90d"), normalized at the write path the way `--since` is.
    #     Consumed by the `stale_decisions` surface.
    #   - expires_when: a closed-grammar predicate. The COLUMN ships in v0.3.8
    #     but the evaluator is deferred to v0.3.11 — populated by no write path
    #     yet, carried on the dataclass so the field exists from day one.
    revisit_after: str = ""
    expires_when: str = ""

    # Decision states + supersede flow (v0.3.9.1, migration v4). All three
    # default to "" (absent) and follow the same nullable-column / coalesce
    # convention as the v0.3.8 pair above.
    #   - supersedes: the id of a prior event this event overrides. Set on
    #     change_type="supersede" events; the linked event's verdict is then
    #     derived as no longer standing (status "superseded") without ever
    #     mutating it.
    #   - constraint: the testable principle that drove the decision (e.g.
    #     "card data in our own DB puts us in PCI scope") — split out of the
    #     free-text reasoning so it stays queryable.
    #   - stale_when: the evidence that would invalidate the verdict (e.g.
    #     "payment provider changed"). `stale_decisions` keyword-matches this
    #     against later change events and flags "review suggested".
    supersedes: str = ""
    constraint: str = ""
    stale_when: str = ""

    def __post_init__(self) -> None:
        # entity_path: must be non-empty
        if not isinstance(self.entity_path, str) or not self.entity_path.strip():
            raise ValueError("entity_path must be a non-empty string")
        self.entity_path = self.entity_path.strip()

        # change_type: must be a known ChangeType
        if isinstance(self.change_type, ChangeType):
            self.change_type = self.change_type.value
        if self.change_type not in VALID_CHANGE_TYPES:
            raise ValueError(
                f"invalid change_type {self.change_type!r}; "
                f"must be one of: {sorted(VALID_CHANGE_TYPES)}"
            )

        # entity_type: coerce unknown to 'other' (descriptive, not load-bearing)
        if isinstance(self.entity_type, EntityType):
            self.entity_type = self.entity_type.value
        if self.entity_type not in VALID_ENTITY_TYPES:
            self.entity_type = EntityType.OTHER.value

        # timestamp: normalize to canonical UTC
        try:
            self.timestamp = normalize_timestamp(self.timestamp)
        except (ValueError, TypeError):
            # Fall back to "now" if the caller assigned an unparseable value
            self.timestamp = utc_now_iso()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "entity_type": self.entity_type,
            "entity_path": self.entity_path,
            "change_type": self.change_type,
            "diff": self.diff,
            "reasoning": self.reasoning,
            "agent": self.agent,
            "session_id": self.session_id,
            "git_commit": self.git_commit,
            "project": self.project,
            "changeset_id": self.changeset_id,
            "metadata": self.metadata,
            "revisit_after": self.revisit_after,
            "expires_when": self.expires_when,
            "supersedes": self.supersedes,
            "constraint": self.constraint,
            "stale_when": self.stale_when,
        }
