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


# ---------------------------------------------------------------------------
# ChangeEvent field surface — guards the active-memory v1 columns (v0.3.8)
# ---------------------------------------------------------------------------


def test_change_event_has_active_memory_fields():
    """ChangeEvent gained revisit_after + expires_when in v0.3.8 — pin them so
    they can't be dropped without an intentional change + CHANGELOG note."""
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(selvedge.ChangeEvent)}
    assert "revisit_after" in field_names
    assert "expires_when" in field_names


def test_change_event_to_dict_includes_active_memory_fields():
    """to_dict() (the MCP serialization surface) carries both new fields,
    always populated (empty string when absent), never null."""
    d = selvedge.ChangeEvent(entity_path="users", change_type="add").to_dict()
    assert d["revisit_after"] == ""
    assert d["expires_when"] == ""


# ---------------------------------------------------------------------------
# Lazy resolution (PEP 562)
#
# The surface resolves through a module `__getattr__` so `import selvedge`
# doesn't pull `storage` / `models` / `validation` and their transitive
# `logging` → `traceback` and `dataclasses` → `inspect` chains. That is load
# -bearing for `selvedge-hook`, which runs in a fresh process on every gated
# tool call and executes this package's `__init__` before any submodule.
# ---------------------------------------------------------------------------


def test_lazy_exports_covers_the_whole_surface():
    """Every `__all__` entry except `__version__` must have a home module."""
    assert set(selvedge.__all__) - {"__version__"} == set(selvedge._LAZY_EXPORTS)


def test_importing_the_package_does_not_pull_the_heavy_submodules():
    """The point of the lazy surface, asserted where it can't quietly regress."""
    import json
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c",
         "import sys, selvedge, json; "
         "print(json.dumps(sorted(m for m in sys.modules if m.startswith('selvedge'))))"],
        capture_output=True, text=True, check=True,
    )
    loaded = set(json.loads(result.stdout.strip()))
    heavy = {"selvedge.storage", "selvedge.models", "selvedge.validation",
             "selvedge.logging_config", "selvedge.config", "selvedge.timeutil"}
    assert not (loaded & heavy), (
        "`import selvedge` eagerly imported "
        f"{sorted(loaded & heavy)} — the hook pays this on every gated tool call"
    )


def test_lazy_attribute_resolves_and_is_cached():
    """First access imports; after that it's an ordinary globals lookup.

    Runs in a subprocess. Checking this in-process would mean clearing
    `selvedge.*` out of `sys.modules`, and rebinding those modules mid-session
    breaks every other test holding a reference to one — the server's storage
    singleton and the monkeypatched module attributes both stop matching.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c",
         "import selvedge\n"
         "assert 'SelvedgeStorage' not in vars(selvedge), 'resolved eagerly'\n"
         "cls = selvedge.SelvedgeStorage\n"
         "assert cls.__name__ == 'SelvedgeStorage'\n"
         "assert vars(selvedge)['SelvedgeStorage'] is cls, 'not cached'\n"
         "print('ok')"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0 and result.stdout.strip() == "ok", (
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


def test_unknown_attribute_still_raises_attribute_error():
    """`from selvedge import server` relies on this failing cleanly.

    The import system tries the attribute first and only falls back to
    importing the submodule when the lookup raises AttributeError.
    """
    import pytest

    with pytest.raises(AttributeError, match="no attribute 'definitely_not_a_thing'"):
        _ = selvedge.definitely_not_a_thing

    from selvedge import storage as storage_mod  # submodule access still works
    assert storage_mod.__name__ == "selvedge.storage"


def test_dir_still_reports_the_public_surface():
    assert set(dir(selvedge)) >= set(selvedge.__all__)
