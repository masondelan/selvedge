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

Resolution is lazy (PEP 562). Every name below still imports exactly as it
always has; the difference is that ``import selvedge`` no longer eagerly
pulls `storage`, `models`, `validation`, `logging_config` and their
transitive `logging` / `traceback` / `dataclasses` / `inspect` chains. That
mattered because `selvedge-hook` runs in a fresh process on every gated tool
call, and importing *any* ``selvedge.*`` submodule executes this file first —
so the hook paid ~17 ms of import for a package surface it never touches.
Library users see no difference beyond the first attribute access, which is
cached into the module globals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

__version__ = "0.3.11"

# name -> submodule it lives in. Kept in step with `__all__` by
# `test_lazy_exports_covers_the_whole_surface` rather than by deriving one
# from the other: a literal `__all__` is what lets ruff and mypy see the
# re-exports below, and a derived one silently turned every TYPE_CHECKING
# import into an "unused import" error.
_LAZY_EXPORTS: dict[str, str] = {
    # Core data model
    "ChangeEvent": "models",
    "ChangeType": "models",
    "EntityType": "models",
    "VALID_CHANGE_TYPES": "models",
    "VALID_ENTITY_TYPES": "models",
    # Storage
    "SelvedgeStorage": "storage",
    # Configuration
    "get_db_path": "config",
    "get_selvedge_dir": "config",
    "init_project": "config",
    "configure_logging": "logging_config",
    # Time utilities
    "parse_time_string": "timeutil",
    "normalize_timestamp": "timeutil",
    "utc_now_iso": "timeutil",
    # Validation
    "check_reasoning_quality": "validation",
    "GENERIC_REASONING_PATTERNS": "validation",
    "REASONING_MIN_LENGTH": "validation",
}

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

# Type checkers and IDEs don't run `__getattr__`, so they need the real
# imports. This block never executes at runtime.
if TYPE_CHECKING:  # pragma: no cover
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


def __getattr__(name: str) -> object:
    """Resolve a public name on first access (PEP 562).

    Raises ``AttributeError`` for anything unknown, which is what lets
    ``from selvedge import server`` still fall through to normal submodule
    importing — the import system tries the attribute first and only imports
    the submodule when that lookup fails.
    """
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib import import_module

    value = getattr(import_module(f".{module_name}", __name__), name)
    # Cache into module globals so subsequent lookups skip __getattr__ and
    # cost exactly what an eager import would have.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Keep tab-completion and `dir(selvedge)` showing the full surface."""
    return sorted(__all__)
