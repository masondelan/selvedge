"""Doctor must distinguish project configuration from actual client execution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from selvedge.cli import cli
from selvedge.diagnostics import agent_hook_checks
from selvedge.hooks.install import hook_path, install_agent_hooks
from selvedge.setup import install_delivery_hooks, install_pretooluse_hook

AGENTS = ("claude-code", "codex", "cursor", "copilot", "gemini", "windsurf")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep config, database and environment checks away from the user's project."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SELVEDGE_DB", str(tmp_path / ".selvedge/selvedge.db"))
    monkeypatch.delenv("SELVEDGE_HOOK_DISABLE", raising=False)
    monkeypatch.setattr("selvedge.diagnostics.shutil.which", lambda _: "/fixture/selvedge-hook")
    return tmp_path


def install(agent: str, project: Path) -> Path:
    """Exercise the real installer instead of duplicating its desired format."""
    if agent == "claude-code":
        path = project / ".claude/settings.json"
        install_pretooluse_hook(path)
        install_delivery_hooks(path)
    else:
        install_agent_hooks(agent, project)
        path = hook_path(agent, project)
    return path


def by_label(rows: list[dict]) -> dict[str, dict]:
    """Index the existing plain-row contract for assertion messages."""
    return {row["label"]: row for row in rows}


@pytest.mark.parametrize("agent", AGENTS)
def test_installed_config_is_not_execution_evidence(agent: str, project: Path) -> None:
    path = install(agent, project)
    before = path.read_bytes()
    rows = agent_hook_checks(agent, project)
    events = [r for r in rows if "entry present" in r["detail"]]
    assert len(events) == (2 if agent == "windsurf" else 3)
    assert all(r["status"] == "PASS" for r in events)
    activation = by_label(rows)["Agent hook activation"]
    assert activation["status"] == "INFO" and "Unknown" in activation["detail"]
    assert path.read_bytes() == before
    assert not (project / ".selvedge").exists()


def test_missing_config_and_binary_warn_without_claiming_no_global_hooks(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("selvedge.diagnostics.shutil.which", lambda _: None)
    rows = by_label(agent_hook_checks("codex", project))
    assert rows["Agent hook configuration"]["status"] == "WARN"
    assert "another scope" in rows["Agent hook configuration"]["detail"]
    assert rows["Agent hook executable"]["status"] == "WARN"
    assert not (project / ".codex").exists()


@pytest.mark.parametrize("content", ["not json", "[]", '{"hooks": []}',
    '{"hooks": {"PreToolUse": {}}}', '{"hooks": {"PreToolUse": [{"hooks": null}]}}'])
def test_bad_config_fails_and_keeps_other_checks(project: Path, content: str) -> None:
    path = install("codex", project)
    path.write_text(content)
    result = CliRunner().invoke(cli, ["doctor", "--agent", "codex", "--json"])
    assert result.exit_code == 1, result.output
    rows = by_label(json.loads(result.output)["checks"])
    assert rows["Agent hook configuration"]["status"] == "FAIL"
    assert "Database path" in rows and "Agent hook activation" in rows
    assert path.read_text() == content


def test_partial_and_custom_entries_do_not_pass(project: Path) -> None:
    path = install("codex", project)
    data = json.loads(path.read_text())
    del data["hooks"]["SessionStart"]
    entry = data["hooks"]["PreToolUse"][0]
    entry["matcher"] = "Read"
    entry["hooks"][0]["command"] = "/custom/selvedge-hook pretooluse --agent codex"
    path.write_text(json.dumps(data))
    rows = by_label(agent_hook_checks("codex", project))
    assert rows["Agent hook PreToolUse"]["status"] == "INFO"
    assert "Customized" in rows["Agent hook PreToolUse"]["detail"]
    assert rows["Agent hook SessionStart"]["status"] == "WARN"
    assert rows["Agent hook PreCompact"]["status"] == "PASS"


def test_bypass_and_project_disable_are_visible(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = install("claude-code", project)
    data = json.loads(path.read_text())
    data["disableAllHooks"] = True
    path.write_text(json.dumps(data))
    monkeypatch.setenv("SELVEDGE_HOOK_DISABLE", "1")
    rows = by_label(agent_hook_checks("claude-code", project))
    assert rows["Agent hook settings"]["status"] == "WARN"
    assert rows["Agent hook bypass"]["status"] == "WARN"
    assert rows["Agent hook activation"]["status"] == "INFO"


def test_windsurf_inspects_preferred_file_without_falling_back(project: Path) -> None:
    legacy = install("windsurf", project)
    preferred = project / ".devin/hooks.json"
    preferred.parent.mkdir()
    preferred.write_text('{"hooks": {}}')
    rows = by_label(agent_hook_checks("windsurf", project))
    assert str(preferred) in rows["Agent hook scope"]["detail"]
    assert rows["Agent hook pre_write_code"]["status"] == "WARN"
    assert "selvedge-hook" in legacy.read_text()


def test_unsupported_cursor_schema_fails(project: Path) -> None:
    path = install("cursor", project)
    data = json.loads(path.read_text())
    data["version"] = True
    path.write_text(json.dumps(data))
    rows = by_label(agent_hook_checks("cursor", project))
    assert rows["Agent hook configuration"]["status"] == "FAIL"


def test_default_doctor_unchanged_and_missing_config_is_not_fatal(project: Path) -> None:
    runner = CliRunner()
    original = runner.invoke(cli, ["doctor", "--json"])
    assert original.exit_code == 0, original.output
    assert not any(r["label"].startswith("Agent hook") for r in json.loads(original.output)["checks"])
    scoped = runner.invoke(cli, ["doctor", "--agent", "gemini", "--json"])
    assert scoped.exit_code == 0, scoped.output
    assert set(json.loads(scoped.output)) == {"checks"}
    assert runner.invoke(cli, ["doctor", "--agent", "unknown"]).exit_code == 2
