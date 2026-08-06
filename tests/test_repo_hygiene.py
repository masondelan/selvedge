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

import os
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


# --------------------------------------------------------------------------
# Docker build context
#
# `.gitignore` gets a hygiene guard above; `.dockerignore` needs the same one,
# and for a sharper reason. `.selvedge/` is git-*tracked* on purpose, so the
# gitignore guard structurally cannot catch it — it took `COPY . /app` plus
# `WORKDIR /app` to turn that into a container that resolved the maintainer's
# dogfood store as its own database.
#
# Asserting the `.dockerignore` *text* would be worthless (the original file
# looked fine and was wrong, because Docker's matcher is non-recursive and a
# bare `__pycache__` only matches at the context root). So this ports Docker's
# actual matcher — moby/patternmatcher — and asserts the resulting file set.
# --------------------------------------------------------------------------

# Characters `Pattern.compile` escapes rather than passing through to the
# regexp. `[` and `]` are deliberately absent: Docker passes those through so
# character classes keep working.
_REGEXP_META = set(".+()|{}^$\\")


class _DockerPattern:
    """One `.dockerignore` line, compiled the way Docker compiles it."""

    def __init__(self, line: str) -> None:
        self.exclusion = line.startswith("!")  # Docker's name for a `!` re-include
        raw = line[1:] if self.exclusion else line
        # Docker runs filepath.Clean on the pattern, which drops trailing
        # slashes — this is why `!selvedge/` and `!selvedge` behave alike.
        self.cleaned = raw.strip().rstrip("/") or "."
        self.regex = re.compile(self._to_regex(self.cleaned))

    @staticmethod
    def _to_regex(pattern: str) -> str:
        out = "^"
        i, n = 0, len(pattern)
        while i < n:
            ch = pattern[i]
            if ch == "*":
                if i + 1 < n and pattern[i + 1] == "*":
                    i += 1
                    if i + 1 < n and pattern[i + 1] == "/":
                        i += 1  # treat `**/` as `**` and eat the slash
                    if i + 1 == n:
                        out += ".*"  # trailing `**` accepts everything
                    else:
                        out += "(.*/)?"  # any number of path segments, incl. zero
                else:
                    out += "[^/]*"  # a single `*` never crosses a separator
            elif ch == "?":
                out += "[^/]"
            elif ch in _REGEXP_META:
                out += "\\" + ch
            else:
                out += ch
            i += 1
        return out + "$"

    def matches(self, path: str) -> bool:
        return self.regex.match(path) is not None


def _load_dockerignore() -> list[_DockerPattern]:
    text = (_REPO_ROOT / ".dockerignore").read_text()
    patterns = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        patterns.append(_DockerPattern(stripped))
    return patterns


def _is_excluded(path: str, patterns: list[_DockerPattern]) -> bool:
    """Port of ``patternmatcher.MatchesOrParentMatches``.

    Last match wins, and a pattern also matches a path when it matches any of
    that path's parent directories — which is what lets a bare `*` exclude a
    whole tree and a later `!selvedge` pull one subtree back out of it.
    """
    matched = False
    parent = path.rsplit("/", 1)[0] if "/" in path else "."
    parent_dirs = parent.split("/") if parent != "." else []

    for pattern in patterns:
        # Docker skips a pattern that cannot change the current verdict.
        if pattern.exclusion != matched:
            continue
        hit = pattern.matches(path)
        if not hit:
            for i in range(len(parent_dirs)):
                if pattern.matches("/".join(parent_dirs[: i + 1])):
                    hit = True
                    break
        if hit:
            matched = not pattern.exclusion
    return matched


def _build_context() -> set[str]:
    """Every path that `docker build .` would send from the current worktree.

    Walks the working tree rather than `git ls-files` on purpose: the leak this
    guards is that a *local* `docker build .` sweeps up untracked internal-ops
    files (`CLAUDE.local.md`, `internal/`) that a tracked-files view can't see.
    """
    patterns = _load_dockerignore()
    # Pruning an excluded directory is only safe when no `!` pattern could
    # reach inside it. Every exception in the file is a literal path, so a
    # prefix test settles it exactly.
    exception_roots = [p.cleaned for p in patterns if p.exclusion]

    included: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(_REPO_ROOT):
        rel_dir = os.path.relpath(dirpath, _REPO_ROOT).replace(os.sep, "/")
        for name in list(dirnames):
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            if _is_excluded(rel, patterns) and not any(
                root == rel or root.startswith(rel + "/") for root in exception_roots
            ):
                dirnames.remove(name)
        for name in filenames:
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            if not _is_excluded(rel, patterns):
                included.add(rel)
    return included


def test_docker_build_context_carries_no_store_and_nothing_internal():
    """The image must not ship a database, or any git-excluded internal file.

    The store is the sharp one: `.selvedge/selvedge.db` is tracked, so it
    reached the image through `COPY . /app` and then *won* DB resolution,
    because walk-up from the `WORKDIR` finds it before the `~/.selvedge/`
    fallback the Dockerfile's own comment promises.
    """
    context = _build_context()

    offenders = sorted(
        p
        for p in context
        if "/.selvedge/" in f"/{p}"
        or p.endswith((".db", ".db-journal", ".db-shm", ".db-wal"))
        or p.endswith(".docx")
        or p == "CLAUDE.local.md"
        or p == ".coverage"
        or p.startswith(("internal/", ".claude/", ".git/", "launch/", "tests/"))
        or _INTERNAL_RE.search(p)
    )
    assert not offenders, (
        "these would be copied into the Docker image — the build context must "
        f"carry neither a Selvedge store nor internal-only material: {offenders}"
    )


def test_docker_build_context_is_exactly_the_install_set():
    """Assert the artifact, not the config — the same shape as the sdist test.

    `.dockerignore` is an allowlist, so this is what makes it meaningful: the
    context is pinned to what `pip install .` actually reads, and anything new
    lands here as a failure rather than in the published image.
    """
    context = _build_context()

    non_package = sorted(p for p in context if not p.startswith("selvedge/"))
    assert non_package == ["LICENSE", "README.md", "pyproject.toml"], (
        f"docker build context drifted outside the install set: {non_package}"
    )

    # The package itself must arrive as source only — no caches, no store.
    package_junk = sorted(
        p
        for p in context
        if p.startswith("selvedge/") and not p.endswith((".py", ".md", ".json", ".toml"))
    )
    assert not package_junk, f"non-source files inside the shipped package: {package_junk}"


def test_dockerfile_pins_the_database_out_of_the_build_context():
    """Belt and braces: even a context leak must not become a wrong database.

    Without this, the failure mode is silent — the server starts healthy and
    serves whatever store happened to land under the WORKDIR.
    """
    dockerfile = (_REPO_ROOT / "Dockerfile").read_text()
    assert re.search(r"^ENV\s+SELVEDGE_DB=", dockerfile, re.MULTILINE), (
        "Dockerfile must pin SELVEDGE_DB so walk-up resolution can never "
        "select a database out of the build context"
    )
