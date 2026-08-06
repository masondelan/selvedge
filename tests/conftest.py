"""Suite-wide safety net for the "never touch the real database" rule.

`CLAUDE.md` requires every test to point `SELVEDGE_DB` at a `tmp_path`, and
until now that was enforced only by each test remembering to do it — there was
no `conftest.py` anywhere in the repo. The rule held in practice (a full audit
found no real-DB or real-HOME writes under any ordering), but nothing would
have caught the first test that forgot, and the failure mode is writing into
the maintainer's own dogfood store.

The autouse fixture below closes that. It runs before every test and points
`SELVEDGE_DB` at a per-test temporary file, so a test that forgets to set it
gets an isolated database instead of falling through to `get_db_path()`'s
walk-up — which, run from the repo root, resolves to `.selvedge/selvedge.db`.

Tests that need to exercise resolution itself still control their own
environment: `monkeypatch.delenv("SELVEDGE_DB")` in a test overrides this,
because monkeypatch unwinds in reverse order of application.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _never_touch_the_real_db(tmp_path, monkeypatch):
    """Point SELVEDGE_DB at a per-test temp path unless the test says otherwise."""
    monkeypatch.setenv("SELVEDGE_DB", str(tmp_path / "autouse-selvedge.db"))
    # Keep the global-fallback warning out of captured stderr; tests that
    # assert on that warning set it themselves.
    monkeypatch.setenv("SELVEDGE_QUIET", "1")
