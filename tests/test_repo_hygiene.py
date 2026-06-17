"""
Regression guard: known-internal artifacts must never be *tracked* (committed).

Selvedge keeps strategy / launch / dashboard / competitive-intel material
local-only via the ``.gitignore`` "Internal" section. A file that slips back
into tracking would leak onto the public GitHub repo AND into the PyPI sdist.
This test fails fast if any tracked path matches a known-internal pattern, so
the leak is caught in CI rather than after a publish.

Skips cleanly when run outside a git checkout (e.g. from an unpacked sdist).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Mirrors the ``.gitignore`` "Internal" section. Matched against each tracked
# path; substrings are enough (these tokens don't appear in shipped product
# files).
_INTERNAL_RE = re.compile(
    r"(marketing-templates"
    r"|strategy-2026"
    r"|-teardown"
    r"|dashboard"
    r"|ideas-backlog"
    r"|top-priority"
    r"|engagement-strategy"
    r"|long-term-thesis)",
    re.IGNORECASE,
)


def _tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("git not available or not a git checkout")
    return [line for line in out.stdout.splitlines() if line.strip()]


def test_no_internal_artifacts_are_tracked():
    offenders = sorted(p for p in _tracked_files() if _INTERNAL_RE.search(p))
    assert not offenders, (
        "internal-only artifacts are tracked — they must be gitignored, not "
        f"committed (they would leak to GitHub and the PyPI sdist): {offenders}"
    )
