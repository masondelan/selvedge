"""
Tests for entity-path canonicalization (v0.3.7).

Covers the pure :func:`canonicalize_entity_path` function and its invariants,
plus the load-bearing property that it's the single write chokepoint — both
the storage write path and (by extension) the MCP/CLI writes store canonical
paths without each caller having to canonicalize.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage, canonicalize_entity_path


def test_strips_leading_dot_slash():
    assert canonicalize_entity_path("./src/auth.py::login") == "src/auth.py::login"
    # Repeated leading "./" segments all go.
    assert canonicalize_entity_path("././src") == "src"


def test_collapses_slashes_and_normalizes_separators():
    assert canonicalize_entity_path("src//auth.py") == "src/auth.py"
    assert canonicalize_entity_path("src\\auth.py") == "src/auth.py"
    assert canonicalize_entity_path("a///b\\\\c") == "a/b/c"


def test_trims_surrounding_whitespace():
    assert canonicalize_entity_path("  users.email  ") == "users.email"


def test_preserves_case_intentionally():
    # The cross-platform stance: do NOT lowercase. ``Users`` and ``users`` are
    # distinct entities on case-sensitive filesystems.
    assert canonicalize_entity_path("Users.Email") == "Users.Email"
    assert canonicalize_entity_path("./SRC/Auth.py::Login") == "SRC/Auth.py::Login"


def test_idempotent():
    for raw in ("./a//b", "Users.Email", "src\\x.py", "users"):
        once = canonicalize_entity_path(raw)
        assert canonicalize_entity_path(once) == once


def test_never_empties_a_nonempty_path():
    # "./" would strip to empty — fall back to the trimmed original instead of
    # writing an empty entity_path.
    assert canonicalize_entity_path("./") == "./"
    assert canonicalize_entity_path("   ./   ") == "./"


def test_write_path_is_the_single_chokepoint(tmp_path: Path):
    storage = SelvedgeStorage(tmp_path / "c.db")
    storage.log_event(ChangeEvent(entity_path="./src//auth.py", change_type="add"))
    # The stored value is canonical even though the caller passed a messy path.
    rows = storage.get_entity_history("src/auth.py")
    assert len(rows) == 1
    assert rows[0]["entity_path"] == "src/auth.py"


def test_distinct_spellings_resolve_to_one_entity(tmp_path: Path):
    storage = SelvedgeStorage(tmp_path / "c.db")
    storage.log_event(ChangeEvent(entity_path="./src/auth.py::login", change_type="add"))
    storage.log_event(ChangeEvent(entity_path="src//auth.py::login", change_type="modify"))
    # Both spellings collapse to the same canonical path — no silent split.
    rows = storage.get_blame("src/auth.py::login")
    assert rows is not None
    assert storage.count() == 2


def test_case_preserved_paths_surface_as_collisions(tmp_path: Path):
    # Case is preserved, so these stay distinct — but the case-collision watch
    # (the doctor should-warn pair) flags them rather than letting the
    # divergence hide.
    storage = SelvedgeStorage(tmp_path / "c.db")
    storage.log_event(ChangeEvent(entity_path="Users.email", change_type="add"))
    storage.log_event(ChangeEvent(entity_path="users.email", change_type="add"))
    collisions = storage.find_case_collisions()
    assert len(collisions) == 1
    assert collisions[0]["paths"] == ["Users.email", "users.email"]
    # No collision is reported when paths don't differ only by case.
    storage.log_event(ChangeEvent(entity_path="orders.total", change_type="add"))
    assert len(storage.find_case_collisions()) == 1


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
