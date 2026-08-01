"""
Tests for the Claude Code plugin surface (v0.3.9.2).

The plugin is the repo itself (``marketplace.json`` source ``"./"``). What
this file locks down:

  - **Launcher resolution.** ``bin/selvedge-resolve`` picks PATH → uvx → pipx
    → guidance in that order, pins the server version, honors
    ``SELVEDGE_VERSION`` / ``latest``, and never writes to stdout on the
    failure branch (stdout is the MCP stdio channel). Exercised with fake
    executables on a controlled PATH — no network, no real uvx call.
  - **Version sync.** Every manifest that carries the version agrees, the
    launcher's pin matches, and the npm shim's decoupled ``pypiVersion`` pin
    tracks it (npm's own semver ``version`` can't hold four segments).
  - **Skill/prompt sync.** ``skills/selvedge/SKILL.md`` is byte-for-byte
    ``prompt.render_skill()`` and its body is the canonical ``PROMPT_BLOCK``,
    so the bundled skill can't drift from the hand-installed prompt.
  - **Hook wiring.** ``hooks/hooks.json`` matcher stays locked to the
    ``selvedge setup`` installer, and double-registration (plugin +
    ``selvedge setup``) is harmless because the gate is idempotent.
  - **Commands.** The four slash commands are thin ``selvedge-cli`` wrappers.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

import selvedge
from selvedge.cli import cli
from selvedge.hooks import pretooluse as hook
from selvedge.models import ChangeEvent
from selvedge.prompt import PROMPT_BLOCK, SKILL_DESCRIPTION, render_skill
from selvedge.setup import HOOK_MATCHER
from selvedge.storage import SelvedgeStorage

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BIN = _REPO_ROOT / "bin"
_SKILL = _REPO_ROOT / "skills" / "selvedge" / "SKILL.md"
_COMMANDS = _REPO_ROOT / "commands"
_VERSION = selvedge.__version__


# ---------------------------------------------------------------------------
# Launcher — helpers
# ---------------------------------------------------------------------------


def _fake(directory: Path, name: str) -> Path:
    """A stub executable that echoes ``FAKE:<name> <args...>`` and exits 0."""
    p = directory / name
    p.write_text(f'#!/bin/sh\necho "FAKE:{name} $*"\n')
    p.chmod(0o755)
    return p


def _run(script: Path, args, path: str, env_extra: dict | None = None):
    """Run a launcher script with a controlled PATH (and nothing else).

    A bare ``PATH`` env keeps the resolution deterministic — no inherited
    ``SELVEDGE_VERSION``, no ambient uvx/pipx unless the test puts them on
    ``path`` itself.
    """
    env = {"PATH": path}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(script), *args], capture_output=True, text=True, env=env
    )


# ---------------------------------------------------------------------------
# Launcher — resolution order
# ---------------------------------------------------------------------------


def test_resolve_prefers_existing_path_install(tmp_path):
    """A real install on PATH wins over uvx — exact version, fast start."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake(bindir, "selvedge-server")
    _fake(bindir, "uvx")  # present, but must not be chosen
    r = _run(_BIN / "selvedge-resolve", ["selvedge-server"], str(bindir))
    assert r.returncode == 0
    assert r.stdout.strip() == "FAKE:selvedge-server"
    assert "uvx" not in r.stdout


def test_resolve_falls_back_to_uvx_with_pin(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake(bindir, "uvx")
    r = _run(_BIN / "selvedge-resolve", ["selvedge-server"], str(bindir))
    assert r.returncode == 0
    assert r.stdout.strip() == f"FAKE:uvx --from selvedge=={_VERSION} selvedge-server"


def test_resolve_falls_back_to_pipx_with_pin(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake(bindir, "pipx")
    r = _run(_BIN / "selvedge-resolve", ["selvedge-server"], str(bindir))
    assert r.returncode == 0
    assert (
        r.stdout.strip()
        == f"FAKE:pipx run --spec selvedge=={_VERSION} selvedge-server"
    )


def test_resolve_nothing_found_exits_1_stderr_only(tmp_path):
    """The stdout-is-sacred invariant: on the failure branch the resolver
    writes guidance to stderr and NOTHING to stdout (an MCP host would
    otherwise read the guidance as a protocol frame)."""
    empty = tmp_path / "empty"
    empty.mkdir()
    r = _run(_BIN / "selvedge-resolve", ["selvedge-server"], str(empty))
    assert r.returncode == 1
    assert r.stdout == ""
    assert "could not find" in r.stderr
    assert f"selvedge=={_VERSION}" in r.stderr


def test_resolve_version_override(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake(bindir, "uvx")
    r = _run(
        _BIN / "selvedge-resolve",
        ["selvedge-server"],
        str(bindir),
        {"SELVEDGE_VERSION": "9.9.9"},
    )
    assert r.stdout.strip() == "FAKE:uvx --from selvedge==9.9.9 selvedge-server"


def test_resolve_latest_unpins(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake(bindir, "uvx")
    r = _run(
        _BIN / "selvedge-resolve",
        ["selvedge-server"],
        str(bindir),
        {"SELVEDGE_VERSION": "latest"},
    )
    # "latest" drops the == pin entirely.
    assert r.stdout.strip() == "FAKE:uvx --from selvedge selvedge-server"


def test_resolve_passes_through_subcommand_args(tmp_path):
    """The hook entrypoint takes a subcommand — it must survive the hop."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake(bindir, "uvx")
    r = _run(_BIN / "selvedge-resolve", ["selvedge-hook", "pretooluse"], str(bindir))
    assert (
        r.stdout.strip()
        == f"FAKE:uvx --from selvedge=={_VERSION} selvedge-hook pretooluse"
    )


# ---------------------------------------------------------------------------
# Launcher — wrappers
# ---------------------------------------------------------------------------


def test_launcher_scripts_are_executable():
    for name in (
        "selvedge-resolve",
        "selvedge-plugin-server",
        "selvedge-plugin-hook",
        "selvedge-cli",
    ):
        p = _BIN / name
        assert p.exists(), f"missing {name}"
        assert os.access(p, os.X_OK), f"{name} must be executable"


@pytest.mark.parametrize(
    ("wrapper", "entrypoint"),
    [
        ("selvedge-plugin-server", "selvedge-server"),
        ("selvedge-plugin-hook", "selvedge-hook"),
        ("selvedge-cli", "selvedge"),
    ],
)
def test_wrappers_delegate_to_resolver(wrapper, entrypoint):
    text = (_BIN / wrapper).read_text()
    assert "selvedge-resolve" in text
    assert entrypoint in text


def test_plugin_server_wrapper_delegates(tmp_path):
    """End-to-end: the .mcp.json launcher finds and runs selvedge-server.

    Fake install is *prepended* to the real PATH, so the resolver's PATH-first
    branch picks it before any real uvx — deterministic regardless of host.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake(bindir, "selvedge-server")
    path = f"{bindir}{os.pathsep}{os.environ['PATH']}"
    r = _run(_BIN / "selvedge-plugin-server", [], path)
    assert r.returncode == 0
    assert "FAKE:selvedge-server" in r.stdout


def test_plugin_hook_wrapper_passes_subcommand(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake(bindir, "selvedge-hook")
    path = f"{bindir}{os.pathsep}{os.environ['PATH']}"
    r = _run(_BIN / "selvedge-plugin-hook", ["pretooluse"], path)
    assert r.returncode == 0
    assert "FAKE:selvedge-hook pretooluse" in r.stdout


def test_cli_wrapper_delegates(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake(bindir, "selvedge")
    path = f"{bindir}{os.pathsep}{os.environ['PATH']}"
    r = _run(_BIN / "selvedge-cli", ["status"], path)
    assert r.returncode == 0
    assert "FAKE:selvedge status" in r.stdout


# ---------------------------------------------------------------------------
# Version sync
# ---------------------------------------------------------------------------


def test_version_sync_across_manifests():
    """pyproject == __init__ == plugin.json == server.json (x2) == manifest."""
    assert f'version = "{_VERSION}"' in (_REPO_ROOT / "pyproject.toml").read_text()

    plugin = json.loads((_REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["version"] == _VERSION

    server = json.loads((_REPO_ROOT / "server.json").read_text())
    assert server["version"] == _VERSION
    assert server["packages"][0]["version"] == _VERSION

    manifest = json.loads((_REPO_ROOT / "manifest.json").read_text())
    assert manifest["version"] == _VERSION


def test_launcher_pin_matches_plugin_version():
    text = (_BIN / "selvedge-resolve").read_text()
    m = re.search(r'SELVEDGE_PLUGIN_PIN="([^"]+)"', text)
    assert m is not None, "SELVEDGE_PLUGIN_PIN not found in resolver"
    assert m.group(1) == _VERSION


def test_npm_pin_tracks_server_version():
    """npm's semver ``version`` can't hold a four-segment PEP 440 version, so
    the shim pins the server through a separate ``pypiVersion`` field. That
    field must track the release; the npm ``version`` is its own semver line."""
    pkg = json.loads((_REPO_ROOT / "npm" / "package.json").read_text())
    assert pkg["pypiVersion"] == _VERSION
    assert re.fullmatch(r"\d+\.\d+\.\d+", pkg["version"]), (
        "npm version must stay valid 3-segment semver"
    )


# ---------------------------------------------------------------------------
# Skill / prompt sync
# ---------------------------------------------------------------------------


def test_skill_file_matches_render_skill():
    assert _SKILL.read_text() == render_skill()


def test_skill_body_is_the_canonical_prompt_block():
    assert PROMPT_BLOCK.strip() in _SKILL.read_text()


def test_skill_frontmatter_says_when_to_use():
    text = _SKILL.read_text()
    assert text.startswith("---\nname: selvedge\ndescription: ")
    # The description must say WHEN to reach for Selvedge, not just what it is.
    assert "before editing" in SKILL_DESCRIPTION
    assert "after any substantive change" in SKILL_DESCRIPTION


def test_skill_description_is_yaml_safe():
    # Unquoted plain scalar: a ": " in the value would start a nested mapping.
    assert ": " not in SKILL_DESCRIPTION


def test_cli_prompt_skill_matches_render_skill():
    """The regeneration path (`selvedge prompt --format skill`) == the file."""
    res = CliRunner().invoke(cli, ["prompt", "--format", "skill"])
    assert res.exit_code == 0
    assert res.output == render_skill()


def test_cli_prompt_skill_install_is_rejected():
    res = CliRunner().invoke(
        cli, ["prompt", "--format", "skill", "--install", "SKILL.md"]
    )
    assert res.exit_code != 0
    assert "only applies to --format block" in res.output


# ---------------------------------------------------------------------------
# Hook wiring + double-registration
# ---------------------------------------------------------------------------


def test_hooks_json_matcher_locked_to_setup_installer():
    data = json.loads((_REPO_ROOT / "hooks" / "hooks.json").read_text())
    entries = data["hooks"]["PreToolUse"]
    assert len(entries) == 1
    entry = entries[0]
    # The plugin hook and the `selvedge setup` hook must watch the same tools.
    assert entry["matcher"] == HOOK_MATCHER
    cmd = entry["hooks"][0]
    assert cmd["type"] == "command"
    assert "${CLAUDE_PLUGIN_ROOT}" in cmd["command"]
    assert "selvedge-plugin-hook" in cmd["command"]
    assert "pretooluse" in cmd["command"]


def test_hook_double_registration_is_idempotent(tmp_path, monkeypatch):
    """The plugin (hooks/hooks.json) and `selvedge setup` (.claude/
    settings.json) can both register the gate. Two identical fires on one
    payload must yield the identical decision, so a user who has both installed
    is only slowed, never given conflicting verdicts — the gate is read-only
    and its block message is deterministic."""
    monkeypatch.delenv("SELVEDGE_DB", raising=False)
    monkeypatch.delenv(hook.DISABLE_ENV, raising=False)
    proj = tmp_path / "proj"
    (proj / ".selvedge").mkdir(parents=True)
    storage = SelvedgeStorage(proj / ".selvedge" / "selvedge.db")
    storage.log_event(
        ChangeEvent(
            entity_path="users.auth_token",
            change_type="add",
            timestamp="2026-01-01T00:00:00Z",
            reasoning="Tried a stored per-user auth token.",
        )
    )
    storage.log_event(
        ChangeEvent(
            entity_path="users.auth_token",
            change_type="revert",
            timestamp="2026-01-02T00:00:00Z",
            reasoning="Reverted: tokens must be short-lived JWTs, not stored.",
        )
    )
    payload = {
        "session_id": "sess-double",
        "cwd": str(proj),
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "migrations/0002_readd.sql",
            "old_string": "",
            "new_string": "users.auth_token",
        },
    }
    first = hook.evaluate(payload)
    second = hook.evaluate(payload)
    assert first.action == "block"
    assert first.to_dict() == second.to_dict()


# ---------------------------------------------------------------------------
# Commands + .mcp.json + marketplace identity
# ---------------------------------------------------------------------------


def test_commands_are_thin_cli_wrappers():
    expected = {"status.md", "blame.md", "history.md", "prior-attempts.md"}
    present = {p.name for p in _COMMANDS.glob("*.md")}
    assert expected <= present
    for name in expected:
        text = (_COMMANDS / name).read_text()
        assert text.startswith("---\n")
        assert "allowed-tools: Bash(selvedge-cli:*)" in text
        assert "selvedge-cli" in text
    # The entity/filter commands pass their argument through.
    for name in ("blame.md", "prior-attempts.md", "history.md"):
        assert "$ARGUMENTS" in (_COMMANDS / name).read_text()


def test_mcp_json_points_at_plugin_launcher():
    data = json.loads((_REPO_ROOT / ".mcp.json").read_text())
    cmd = data["mcpServers"]["selvedge"]["command"]
    assert cmd == "${CLAUDE_PLUGIN_ROOT}/bin/selvedge-plugin-server"


def test_marketplace_lists_plugin_at_repo_root():
    mp = json.loads((_REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    plugin = json.loads((_REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())
    entry = next(p for p in mp["plugins"] if p["name"] == plugin["name"])
    assert entry["source"] == "./"


# ---------------------------------------------------------------------------
# Packaging surfaces that had no test (review issue #25)
# ---------------------------------------------------------------------------


def test_server_version_flag_exits_without_serving():
    """`selvedge-server --version` must print and exit, not start serving.

    Checking that a pinned install resolved is the natural reason to run this,
    and it used to silently start the MCP server — which reads as a hang.
    """
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.argv=['selvedge-server','--version'];"
         " from selvedge.server import main; main()"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    # stdout is the MCP frame channel and must stay empty.
    assert result.stdout == ""
    assert _VERSION in result.stderr


def test_server_rejects_unknown_arguments():
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.argv=['selvedge-server','--nope'];"
         " from selvedge.server import main; main()"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2
    assert result.stdout == ""


def test_npm_readme_documents_pypi_version_field():
    """The npmjs.com landing page must describe the field the shim reads."""
    readme = (_REPO_ROOT / "npm" / "README.md").read_text()
    assert "pypiVersion" in readme
    assert "pinned to this npm package's own version" not in readme


def test_mcpbignore_does_not_exclude_the_manifest_icon():
    """manifest.json references docs/icon.png, so docs/ can't be blanket-excluded."""
    import json as _json

    manifest = _json.loads((_REPO_ROOT / "manifest.json").read_text())
    icon = manifest.get("icon", "")
    assert icon, "manifest declares no icon"
    ignore = (_REPO_ROOT / ".mcpbignore").read_text().splitlines()
    assert f"!{icon}" in ignore, f"{icon} is referenced by manifest.json but not un-ignored"
    assert "docs/" not in ignore, "blanket docs/ exclusion would drop the icon"
