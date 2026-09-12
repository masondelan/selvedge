"""Protect user configurations and verify the first-use path across agents."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from selvedge.cli import cli
from selvedge.setup import install_codex_entry, run_wizard


@pytest.mark.parametrize("agent,relative,key", [
    ("gemini", ".gemini/settings.json", "mcpServers"),
    ("copilot", ".vscode/mcp.json", "servers"),
    ("windsurf", "home/.codeium/windsurf/mcp_config.json", "mcpServers"),
])
def test_explicit_agent_config_preserves_other_servers(tmp_path, agent, relative, key):
    """Explicit setup works without detection and preserves unrelated entries."""
    home = tmp_path / "home"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    original = json.dumps({key: {"other": {"command": "keep-me"}}, "extra": True})
    path.write_text(original)
    def run():
        return run_wizard(project=tmp_path, home=home, selected_agents=(agent,),
                          confirm=lambda *_: True, install_hook=False, init_project_dir=False)
    assert run().exit_code == 0
    data = json.loads(path.read_text())
    assert data[key]["other"] == {"command": "keep-me"}
    assert data["extra"] is True
    assert data[key]["selvedge"]["command"] == "selvedge-server"
    if agent == "copilot":
        assert data[key]["selvedge"]["type"] == "stdio"
        assert "mcpServers" not in data
    assert any(p.read_text() == original for p in path.parent.glob(path.name + ".bak*"))
    assert all(step.status == "noop" for step in run().steps)


def test_codex_preserves_toml_and_existing_server(tmp_path):
    """Appending leaves comments and unrelated tables byte-for-byte intact."""
    path = tmp_path / "config.toml"
    original = '# keep this comment\nmodel = "test"\n[mcp_servers.other]\ncommand = "other"\n'
    path.write_text(original)
    result = install_codex_entry(path)
    assert result.action == "added"
    assert path.read_text().startswith(original)
    assert result.backup_path.read_text() == original
    assert install_codex_entry(path).action == "unchanged"


@pytest.mark.parametrize("original", [
    '[broken', 'mcp_servers = 42\n',
    '[mcp_servers.selvedge]\ncommand = "custom"\nenabled = false\n',
    'mcp_servers = { other = { command = "keep" } }\n',
])
def test_codex_rejects_unsafe_toml_changes(tmp_path, original):
    """Malformed, conflicting and inline-table configs are never rewritten."""
    path = tmp_path / "config.toml"
    path.write_text(original)
    assert install_codex_entry(path).action in ("error", "conflict")
    assert path.read_text() == original
    assert list(tmp_path.glob("*.bak*")) == []


def test_codex_cli_selects_only_requested_agent(tmp_path, monkeypatch):
    """The real CLI wires project TOML + AGENTS and preserves other instructions."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("# Team rules\nKeep this.\n")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("SELVEDGE_DB", str(tmp_path / "unused.db"))
    result = CliRunner().invoke(cli, ["setup", "--agent", "codex", "--path", str(project),
                                      "--non-interactive", "--yes", "--skip-hook"])
    assert result.exit_code == 0, result.output
    assert '[mcp_servers.selvedge]' in (project / ".codex/config.toml").read_text()
    assert (project / "AGENTS.md").read_text().startswith("# Team rules\nKeep this.")
    assert not (project / ".mcp.json").exists()
    assert not (project / ".claude").exists()


def test_explicit_setup_dry_run_writes_nothing(tmp_path):
    """Selecting agents does not override the existing dry-run behavior."""
    outcome = run_wizard(project=tmp_path, home=tmp_path / "home",
                         selected_agents=("codex", "gemini", "copilot", "windsurf"),
                         confirm=lambda *_: False)
    assert outcome.exit_code == 0
    assert list(tmp_path.iterdir()) == []


def test_demo_uses_isolated_store_and_removes_it(tmp_path, monkeypatch):
    """A configured real DB and project are untouched by the demo command."""
    from tempfile import TemporaryDirectory

    import selvedge.demo as demo_module
    existing = tmp_path / "real.db"
    existing.write_bytes(b"do not touch this database")
    monkeypatch.setenv("SELVEDGE_DB", str(existing))
    monkeypatch.setenv("SELVEDGE_NO_TELEMETRY", "1")
    monkeypatch.setenv("SELVEDGE_NO_UPDATE_CHECK", "1")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(demo_module, "TemporaryDirectory", lambda **kw: TemporaryDirectory(dir=tmp_path, **kw))
    result = CliRunner().invoke(cli, ["demo", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["outcome"] == "rejected"
    assert data["confidence"] == "exact"
    assert data["isolated"] is True
    assert existing.read_bytes() == b"do not touch this database"
    assert list(tmp_path.iterdir()) == [existing]
