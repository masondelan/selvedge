"""
Selvedge — change tracking for AI-era codebases.

Public API: import the supported surface from the top-level ``selvedge``
package rather than reaching into internal submodules. Anything not in
``__all__`` is implementation detail and may change between minor releases.

    from selvedge import SelvedgeStorage, ChangeEvent, ChangeType, EntityType
    from selvedge import get_db_path, parse_time_string, normalize_timestamp

The ``selvedge.server`` MCP entry point and the ``selvedge.cli`` Click
application remain importable directly — those are entry points, not
library API.
"""

from .config import get_db_path, get_selvedge_dir, init_project
from .logging_config import configure_logging
from .models import (
    VALID_CHANGE_TYPES,
    VALID_ENTITY_TYPES,
    ChangeEvent,
    ChangeType,
    EntityType,
)
from .storage import SelvedgeStorage
from .timeutil import normalize_timestamp, parse_time_string, utc_now_iso
from .validation import (
    GENERIC_REASONING_PATTERNS,
    REASONING_MIN_LENGTH,
    check_reasoning_quality,
)

__version__ = "0.3.3"

__all__ = [
    # Version
    "__version__",
    # Core data model
    "ChangeEvent",
    "ChangeType",
    "EntityType",
    "VALID_CHANGE_TYPES",
    "VALID_ENTITY_TYPES",
    # Storage
    "SelvedgeStorage",
    # Configuration
    "get_db_path",
    "get_selvedge_dir",
    "init_project",
    "configure_logging",
    # Time utilities
    "parse_time_string",
    "normalize_timestamp",
    "utc_now_iso",
    # Validation
    "check_reasoning_quality",
    "GENERIC_REASONING_PATTERNS",
    "REASONING_MIN_LENGTH",
]
