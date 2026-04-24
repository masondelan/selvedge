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
        }
