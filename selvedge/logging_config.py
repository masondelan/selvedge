"""
Logging configuration for Selvedge.

Library modules should call ``logging.getLogger(__name__)`` directly and
never add handlers — the entry points (``selvedge`` CLI, ``selvedge-server``
MCP) call :func:`configure_logging` once at startup so user-facing output
goes to stderr at a level the user controls.

Verbosity is controlled by the ``SELVEDGE_LOG_LEVEL`` environment variable.
Accepted values (case-insensitive): ``DEBUG``, ``INFO``, ``WARNING`` (default),
``ERROR``, ``CRITICAL``.
"""

from __future__ import annotations

import logging
import os
import sys

_DEFAULT_LEVEL = "WARNING"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"
LOG_LEVEL_ENV = "SELVEDGE_LOG_LEVEL"

# Marker attribute on handlers we've installed — lets configure_logging
# stay idempotent without clobbering user-installed handlers.
_HANDLER_MARKER = "_selvedge_handler"


def _resolve_level(override: str | None = None) -> int:
    """
    Resolve the effective log level.

    Resolution order: explicit override → ``SELVEDGE_LOG_LEVEL`` env →
    ``WARNING`` default. Unknown level names fall back to ``WARNING`` so a
    typo'd env var never silences logging entirely.
    """
    raw = (override or os.environ.get(LOG_LEVEL_ENV) or _DEFAULT_LEVEL).upper().strip()
    level = getattr(logging, raw, None)
    if not isinstance(level, int):
        return logging.WARNING
    return level


def configure_logging(level: str | None = None) -> None:
    """
    Configure the ``selvedge`` logger hierarchy for an entry point.

    Idempotent — repeated calls swap out the Selvedge-installed handler
    with a fresh one matching the requested level. User-installed handlers
    on the ``selvedge`` logger are left untouched.

    Library code should NOT call this; only CLI and server entry points
    should configure logging for their process.
    """
    root = logging.getLogger("selvedge")

    # Remove any handler we previously installed (idempotency).
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(fmt=_FORMAT, datefmt=_DATE_FORMAT))
    setattr(handler, _HANDLER_MARKER, True)
    root.addHandler(handler)

    root.setLevel(_resolve_level(level))
    # Stop propagation to the root logger — we own our own pipeline.
    root.propagate = False
