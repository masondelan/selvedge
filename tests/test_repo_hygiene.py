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


def test_sdist_ships_exactly_the_enumerated_set():
    """The sdist include list must mean what its comment claims.

    Hatchling reads these patterns with gitignore semantics, where an
    unanchored pattern matches at any depth — so bare `selvedge/` also matched
    `features/src/selvedge/` and bare `README.md` matched every README in the
    tree. Six unlisted files were shipping to PyPI as a result. This asserts
    the artifact, not the config, so the guarantee survives a pattern edit.
    """
    import subprocess
    import sys
    import tarfile
    import tempfile

    repo_root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as out:
        proc = subprocess.run(
            [sys.executable, "-m", "build", "--sdist", "--outdir", out, str(repo_root)],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            pytest.skip(f"sdist build unavailable: {proc.stderr[-200:]}")
        tarball = next(Path(out).glob("*.tar.gz"))
        names = [n.split("/", 1)[1] for n in tarfile.open(tarball).getnames() if "/" in n]

    non_package = sorted(n for n in names if not n.startswith("selvedge/") and n)
    # `.gitignore` is added by hatchling's sdist builder itself and cannot be
    # excluded from the target — listing it here is describing the builder,
    # not endorsing an extra file.
    assert non_package == [
        ".gitignore",
        "LICENSE",
        "PKG-INFO",
        "README.md",
        "docs/coding-agents.md",
        "docs/getting-started.md",
        "docs/telemetry.md",
        "pyproject.toml",
    ], f"sdist contents drifted: {non_package}"


def test_action_inputs_never_interpolated_into_shell():
    """`action.yml` must pass inputs through `env:`, never into `run:` text.

    `${{ }}` expansion is textual and happens before the shell parses the
    line, so an input interpolated into a script is a command-injection hole
    in the *caller's* runner, with their GITHUB_TOKEN in scope. This action is
    published for third-party use, so it cannot trust its inputs.
    """
    root = Path(__file__).resolve().parent.parent
    lines = (root / "action.yml").read_text().splitlines()

    # No YAML parser here on purpose — PyYAML is not a declared dependency and
    # this does not need one. Track the `run:` blocks by indentation: a block
    # opens on a `run:` key and closes at the next line indented no further.
    offenders: list[str] = []
    run_indent: int | None = None
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if run_indent is not None and indent <= run_indent:
            run_indent = None
        if run_indent is not None and "${{" in line:
            offenders.append(f"action.yml:{i}: {line.strip()}")
        if re.match(r"\s*run:\s*[|>]?\s*$", line) or re.match(r"\s*run:\s+\S", line):
            if "${{" in line:
                offenders.append(f"action.yml:{i}: {line.strip()}")
            run_indent = indent
    assert not offenders, f"steps interpolate expressions into run:: {offenders}"


def test_third_party_actions_are_pinned_to_commit_shas():
    """Mutable tags in privileged jobs are a supply-chain hole.

    `publish.yml` holds `contents: write` and OIDC `id-token: write` for the
    MCP Registry namespace, and fires automatically on tag push.
    """
    root = Path(__file__).resolve().parent.parent
    unpinned: list[str] = []
    for wf in (root / ".github" / "workflows").glob("*.yml"):
        for i, line in enumerate(wf.read_text().splitlines(), 1):
            m = re.search(r"uses:\s*([\w.-]+/[\w.-]+)@(\S+)", line)
            if not m:
                continue
            owner, ref = m.group(1), m.group(2)
            if owner.startswith("actions/"):
                continue  # first-party, tag-pinned by convention
            if not re.fullmatch(r"[0-9a-f]{40}", ref):
                unpinned.append(f"{wf.name}:{i} {owner}@{ref}")
    assert not unpinned, f"third-party actions not pinned to a SHA: {unpinned}"


def test_mcp_publisher_download_is_pinned_and_verified():
    """The binary that logs in with OIDC must be pinned and checksummed."""
    root = Path(__file__).resolve().parent.parent
    publish = (root / ".github" / "workflows" / "publish.yml").read_text()
    assert "releases/latest/download" not in publish, "mcp-publisher is unpinned"
    assert "sha256sum -c" in publish, "mcp-publisher download is not checksum-verified"
