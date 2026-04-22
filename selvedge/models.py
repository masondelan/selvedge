"""Core data models for Selvedge."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import uuid


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
    """
    entity_path: str
    change_type: str

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    entity_type: str = EntityType.OTHER
    diff: str = ""
    reasoning: str = ""
    agent: str = ""
    session_id: str = ""
    git_commit: str = ""
    project: str = ""
    metadata: str = "{}"  # JSON string for extensibility

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
            "metadata": self.metadata,
        }
