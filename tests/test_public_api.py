"""
Tests pinning the public ``selvedge`` package API.

These tests guard against accidentally removing or renaming a symbol that
downstream library users depend on. To intentionally change the surface,
update the test AND the CHANGELOG entry in the same commit.
"""

from __future__ import annotations

import selvedge

# ---------------------------------------------------------------------------
# Frozen public surface
# ---------------------------------------------------------------------------


_EXPECTED_PUBLIC_API: frozenset[str] = frozenset({
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
})


def test_all_matches_expected_surface():
    assert frozenset(selvedge.__all__) == _EXPECTED_PUBLIC_API


def test_every_all_entry_is_importable_from_package():
    """Each name in ``__all__`` must be reachable as a package attribute."""
    for name in selvedge.__all__:
        assert hasattr(selvedge, name), f"selvedge.{name} not importable"


def test_top_level_import_does_not_pull_in_mcp_server():
    """`import selvedge` must not import the heavyweight FastMCP runtime —
    library users on environments without the MCP package installed should
    still be able to use the storage layer.

    Uses a subprocess so the in-process module cache (which already has
    selvedge.server loaded by other tests) doesn't pollute the check.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, selvedge; "
            "print('selvedge.server' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False", (
        f"`import selvedge` pulled in selvedge.server transitively\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Symbol identity — exports point at the canonical implementation
# ---------------------------------------------------------------------------


def test_change_event_export_is_dataclass():
    from selvedge.models import ChangeEvent as ModelEvent
    assert selvedge.ChangeEvent is ModelEvent


def test_storage_export_is_class():
    from selvedge.storage import SelvedgeStorage as StorageClass
    assert selvedge.SelvedgeStorage is StorageClass


def test_check_reasoning_quality_export_is_callable():
    from selvedge.validation import check_reasoning_quality as canonical
    assert selvedge.check_reasoning_quality is canonical
    assert callable(selvedge.check_reasoning_quality)


def test_version_is_string():
    assert isinstance(selvedge.__version__, str)
    assert selvedge.__version__.count(".") >= 1
