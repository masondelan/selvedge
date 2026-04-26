"""
Tests for selvedge.setup — first-run wizard.

The wizard touches user-owned config files, so adversarial coverage
matters more here than almost anywhere else in the codebase. Every test
runs in tmp_path with monkeypatched ``Path.home()`` and a tmp project
directory — the real ``~/.claude/`` and ``~/.cursor/`` are never touched.

Coverage focus:

  - **Detection**: agents that exist and ones that don't; partial
    install cases where only the prompt-target parent exists.
  - **install_mcp_entry happy path**: created → unchanged → matches
    the desired entry exactly.
  - **install_mcp_entry conflict path**: existing different entry
    triggers ``"conflict"`` without writing; ``--force`` (the
    ``overwrite_existing`` flag) writes and produces ``"updated"``.
  - **install_mcp_entry malformed JSON**: ``"error"`` action, no
    write, no ``.bak``.
  - **Backups**: ``.bak`` is written before any modification on every
    write path that touches existing content.
  - **Wizard idempotence**: re-running the wizard on a project that's
    already set up emits ``"noop"`` for every step.
  - **Wizard non-interactive paths**: ``confirm`` returning False
    short-circuits writes; the resulting ``WizardOutcome`` is
    deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from selvedge.setup import (
    detect_agents,
    install_mcp_entry,
    run_wizard,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    """A throwaway HOME directory to point detect_agents at."""
    home = tmp_path / "fake-home"
    home.mkdir()
    return home


@pytest.fixture
def fake_project(tmp_path: Path) -> Path:
    """A throwaway project directory that looks like a git repo."""
    project = tmp_path / "fake-project"
    project.mkdir()
    (project / ".git").mkdir()
    return project


# ---------------------------------------------------------------------------
# detect_agents
# ---------------------------------------------------------------------------


def test_detect_agents_returns_empty_when_nothing_installed(
    fake_home: Path, fake_project: Path
):
    detected = detect_agents(home=fake_home, project=fake_project)
    assert detected == []


def test_detect_agents_finds_claude_code_when_config_present(
    fake_home: Path, fake_project: Path
):
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "config.json").write_text("{}")

    detected = detect_agents(home=fake_home, project=fake_project)
    names = [a.name for a in detected]

    assert "claude-code" in names


def test_detect_agents_returns_deterministic_order(
    fake_home: Path, fake_project: Path
):
    """Every supported agent installed → claude-code, cursor, copilot."""
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "config.json").write_text("{}")
    (fake_home / ".cursor").mkdir()
    (fake_home / ".cursor" / "mcp.json").write_text("{}")
    (fake_project / ".github").mkdir()
    (fake_project / ".github" / "copilot-instructions.md").write_text("")

    detected = detect_agents(home=fake_home, project=fake_project)

    assert [a.name for a in detected] == ["claude-code", "cursor", "copilot"]


def test_detect_agents_treats_parent_dir_as_signal(
    fake_home: Path, fake_project: Path
):
    """A user with Claude Code installed but no config.json yet still counts."""
    (fake_home / ".claude").mkdir()  # but no config.json yet

    detected = detect_agents(home=fake_home, project=fake_project)
    names = [a.name for a in detected]

    assert "claude-code" in names


# ---------------------------------------------------------------------------
# install_mcp_entry — happy path
# ---------------------------------------------------------------------------


def test_install_mcp_entry_creates_when_missing(tmp_path: Path):
    target = tmp_path / "nested" / "config.json"

    result = install_mcp_entry(target)

    assert result.action == "created"
    assert result.backup_path is None
    assert target.exists()
    data = json.loads(target.read_text())
    assert data["mcpServers"]["selvedge"]["command"] == "selvedge-server"


def test_install_mcp_entry_adds_to_existing_config(tmp_path: Path):
    target = tmp_path / "config.json"
    original = json.dumps(
        {"mcpServers": {"someOtherServer": {"command": "other-server"}}},
        indent=2,
    ) + "\n"
    target.write_text(original)

    result = install_mcp_entry(target)

    assert result.action == "added"
    assert result.backup_path is not None
    assert result.backup_path.read_text() == original
    data = json.loads(target.read_text())
    # Other server preserved, ours added
    assert data["mcpServers"]["someOtherServer"]["command"] == "other-server"
    assert data["mcpServers"]["selvedge"]["command"] == "selvedge-server"


def test_install_mcp_entry_is_idempotent(tmp_path: Path):
    target = tmp_path / "config.json"

    # First call creates
    first = install_mcp_entry(target)
    assert first.action == "created"

    # Second call is a true no-op — no backup, no write
    second = install_mcp_entry(target)
    assert second.action == "unchanged"
    assert second.backup_path is None
    # No .bak file created on the unchanged path
    assert not (tmp_path / "config.json.bak").exists()


# ---------------------------------------------------------------------------
# install_mcp_entry — conflict path
# ---------------------------------------------------------------------------


def test_install_mcp_entry_conflict_when_existing_differs(tmp_path: Path):
    target = tmp_path / "config.json"
    target.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "selvedge": {"command": "/custom/path/to/selvedge-server"}
                }
            },
            indent=2,
        )
    )

    result = install_mcp_entry(target)

    assert result.action == "conflict"
    assert "differs" in result.detail
    # The user's config was NOT modified — that's the whole point of conflict
    data = json.loads(target.read_text())
    assert data["mcpServers"]["selvedge"]["command"] == "/custom/path/to/selvedge-server"
    # And we didn't write a .bak either, since we didn't write anything
    assert not (tmp_path / "config.json.bak").exists()


def test_install_mcp_entry_force_overwrites_existing(tmp_path: Path):
    target = tmp_path / "config.json"
    original = json.dumps(
        {"mcpServers": {"selvedge": {"command": "/custom/old"}}}, indent=2
    )
    target.write_text(original)

    result = install_mcp_entry(target, overwrite_existing=True)

    assert result.action == "updated"
    assert result.backup_path is not None
    assert result.backup_path.read_text() == original
    data = json.loads(target.read_text())
    assert data["mcpServers"]["selvedge"]["command"] == "selvedge-server"


# ---------------------------------------------------------------------------
# install_mcp_entry — error path
# ---------------------------------------------------------------------------


def test_install_mcp_entry_errors_on_malformed_json(tmp_path: Path):
    target = tmp_path / "config.json"
    target.write_text("{ not valid json at all")

    result = install_mcp_entry(target)

    assert result.action == "error"
    assert "valid JSON" in result.detail
    # We didn't write anything, including no .bak
    assert target.read_text() == "{ not valid json at all"
    assert not (tmp_path / "config.json.bak").exists()


def test_install_mcp_entry_errors_on_non_object_top_level(tmp_path: Path):
    target = tmp_path / "config.json"
    target.write_text(json.dumps([1, 2, 3]))

    result = install_mcp_entry(target)

    assert result.action == "error"
    assert "object" in result.detail


def test_install_mcp_entry_replaces_wrong_typed_mcp_servers(tmp_path: Path):
    """If mcpServers is something nonsensical, replace it cleanly."""
    target = tmp_path / "config.json"
    # mcpServers as a list (wrong type) — wizard should overwrite with a fresh dict
    target.write_text(json.dumps({"mcpServers": ["wrong", "type"]}))

    result = install_mcp_entry(target)

    assert result.action == "added"
    data = json.loads(target.read_text())
    assert "selvedge" in data["mcpServers"]


# ---------------------------------------------------------------------------
# Wizard orchestration
# ---------------------------------------------------------------------------


def _stub_init(project: Path) -> None:
    """Stub init that just creates .selvedge/ — no DB touches."""
    (project / ".selvedge").mkdir(exist_ok=True)


def _stub_install_hook(project: Path) -> None:
    """Stub hook installer that writes a fake post-commit file."""
    hooks_dir = project / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "post-commit").write_text("# selvedge hook stub\n")


def test_wizard_returns_skipped_when_no_agents_detected(
    fake_home: Path, fake_project: Path
):
    """No detected agents → graceful skip + no errors."""
    outcome = run_wizard(
        project=fake_project,
        home=fake_home,
        interactive=False,
        confirm=lambda *_: True,
        init_fn=_stub_init,
        install_hook_fn=_stub_install_hook,
    )

    detect_step = next(s for s in outcome.steps if s.label == "Detect AI tooling")
    assert detect_step.status == "skipped"
    # No error should set exit_code
    assert outcome.exit_code == 0


def test_wizard_full_install_with_yes_to_all(
    fake_home: Path, fake_project: Path, monkeypatch
):
    """Full install path — every step lands ok or noop, no errors."""
    # Fake Claude Code installed
    (fake_home / ".claude").mkdir()
    claude_config = fake_home / ".claude" / "config.json"
    claude_config.write_text("{}")

    outcome = run_wizard(
        project=fake_project,
        home=fake_home,
        interactive=False,
        confirm=lambda *_: True,
        init_fn=_stub_init,
        install_hook_fn=_stub_install_hook,
    )

    statuses = {s.label: s.status for s in outcome.steps}

    # MCP entry installed
    assert statuses["Claude Code MCP entry"] == "ok"
    # Prompt block installed
    assert statuses["Claude Code prompt block"] == "ok"
    # Project initialized
    assert statuses["Initialize project"] == "ok"
    # Hook installed
    assert statuses["Install git hook"] == "ok"

    # And the actual MCP entry is in the config now
    data = json.loads(claude_config.read_text())
    assert "selvedge" in data["mcpServers"]


def test_wizard_is_idempotent(fake_home: Path, fake_project: Path):
    """Re-running setup on a project that's already set up = noop everywhere."""
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "config.json").write_text("{}")

    # First run — installs everything
    run_wizard(
        project=fake_project,
        home=fake_home,
        interactive=False,
        confirm=lambda *_: True,
        init_fn=_stub_init,
        install_hook_fn=_stub_install_hook,
    )

    # Second run — should be noop / unchanged
    second = run_wizard(
        project=fake_project,
        home=fake_home,
        interactive=False,
        confirm=lambda *_: True,
        init_fn=_stub_init,
        install_hook_fn=_stub_install_hook,
    )

    statuses = {s.label: s.status for s in second.steps}
    assert statuses["Claude Code MCP entry"] == "noop"
    assert statuses["Claude Code prompt block"] == "noop"
    # Init is noop because .selvedge/ already exists
    assert statuses["Initialize project"] == "noop"
    assert second.exit_code == 0


def test_wizard_confirm_no_skips_step(
    fake_home: Path, fake_project: Path
):
    """A confirm callback returning False marks each step as skipped."""
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "config.json").write_text("{}")

    outcome = run_wizard(
        project=fake_project,
        home=fake_home,
        interactive=True,
        confirm=lambda *_: False,
        init_fn=_stub_init,
        install_hook_fn=_stub_install_hook,
    )

    statuses = [s.status for s in outcome.steps]
    # Every step should be skipped — no ok/error
    assert all(s == "skipped" for s in statuses)


def test_wizard_records_error_when_init_fn_raises(
    fake_home: Path, fake_project: Path
):
    """A failing init step should bubble into outcome.exit_code without aborting later steps."""
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "config.json").write_text("{}")

    def bad_init(project: Path) -> None:
        raise RuntimeError("boom")

    outcome = run_wizard(
        project=fake_project,
        home=fake_home,
        interactive=False,
        confirm=lambda *_: True,
        init_fn=bad_init,
        install_hook_fn=_stub_install_hook,
    )

    init_step = next(s for s in outcome.steps if s.label == "Initialize project")
    assert init_step.status == "error"
    assert "RuntimeError" in init_step.detail
    assert outcome.exit_code == 1

    # Hook step still ran (we don't bail on first error)
    hook_step = next(s for s in outcome.steps if s.label == "Install git hook")
    assert hook_step.status in {"ok", "skipped"}


def test_wizard_skips_hook_when_not_in_git_repo(
    fake_home: Path, tmp_path: Path
):
    """No .git/ → hook step is skipped, not errored."""
    project = tmp_path / "no-git-here"
    project.mkdir()

    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "config.json").write_text("{}")

    outcome = run_wizard(
        project=project,
        home=fake_home,
        interactive=False,
        confirm=lambda *_: True,
        init_fn=_stub_init,
        install_hook_fn=_stub_install_hook,
    )

    hook_step = next(s for s in outcome.steps if s.label == "Install git hook")
    assert hook_step.status == "skipped"
    assert "git" in hook_step.detail.lower()


def test_wizard_force_resolves_conflict(fake_home: Path, fake_project: Path):
    """With force=True, an existing different MCP entry is overwritten cleanly."""
    (fake_home / ".claude").mkdir()
    config = fake_home / ".claude" / "config.json"
    config.write_text(
        json.dumps({"mcpServers": {"selvedge": {"command": "/old/path"}}})
    )

    outcome = run_wizard(
        project=fake_project,
        home=fake_home,
        interactive=False,
        force=True,
        confirm=lambda *_: True,
        init_fn=_stub_init,
        install_hook_fn=_stub_install_hook,
    )

    mcp_step = next(s for s in outcome.steps if s.label == "Claude Code MCP entry")
    assert mcp_step.status == "ok"
    data = json.loads(config.read_text())
    assert data["mcpServers"]["selvedge"]["command"] == "selvedge-server"
