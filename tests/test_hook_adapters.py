"""Native hook contracts must gate real stored decisions without clobbering config."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from selvedge.hooks.adapters import AGENTS, normalize_edits, response, run
from selvedge.hooks.install import hook_config, hook_path, install_agent_hooks
from selvedge.models import ChangeEvent
from selvedge.setup import run_wizard
from selvedge.storage import SelvedgeStorage


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, SelvedgeStorage]:
    """Use only an isolated project database and deterministic rejection."""
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.delenv("SELVEDGE_HOOK_DISABLE", raising=False)
    db = root / ".selvedge/selvedge.db"
    monkeypatch.setenv("SELVEDGE_DB", str(db))
    storage = SelvedgeStorage(db)
    storage.log_event(
        ChangeEvent(
            entity_path="schema.sql",
            change_type="reject",
            reasoning="Rejected dropping the compatibility column because the importer still requires it.",
        )
    )
    return root, storage


def payload_for(agent: str, root: Path) -> dict:
    """Use native field names and edit tools from each client's published protocol."""
    base = {"cwd": str(root), "session_id": "test-session"}
    return {
        **base,
        **{
            "codex": {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "*** Begin Patch\n*** Update File: schema.sql\n@@\n-old\n+new\n*** End Patch"
                },
            },
            "cursor": {
                "conversation_id": "test-session",
                "tool_name": "Write",
                "tool_input": {"file_path": str(root / "schema.sql"), "content": "new"},
            },
            "gemini": {
                "tool_name": "replace",
                "tool_input": {
                    "file_path": str(root / "schema.sql"),
                    "old_string": "old",
                    "new_string": "new",
                },
            },
            "copilot": {
                "tool_name": "replace_string_in_file",
                "tool_input": {
                    "filePath": str(root / "schema.sql"),
                    "oldString": "old",
                    "newString": "new",
                },
            },
            "windsurf": {
                "trajectory_id": "test-session",
                "agent_action_name": "pre_write_code",
                "tool_info": {
                    "file_path": str(root / "schema.sql"),
                    "edits": [{"old_string": "old", "new_string": "new"}],
                },
            },
        }[agent],
    }


def is_denied(result: tuple[dict, str, int]) -> bool:
    """Interpret the documented native responses rather than model prose."""
    output, _, code = result
    return (
        code == 2
        or output.get("permission") == "deny"
        or output.get("decision") == "deny"
        or output.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    )


@pytest.mark.parametrize("agent", AGENTS)
def test_rejection_blocks_until_real_lookup(
    agent: str, project: tuple[Path, SelvedgeStorage]
) -> None:
    """A stored rejection gates the edit; observed retrieval allows the retry."""
    root, storage = project
    payload = payload_for(agent, root)
    assert is_denied(response(agent, "pretooluse", payload))
    storage.record_tool_call("prior_attempts", entity_path="schema.sql")
    assert not is_denied(response(agent, "pretooluse", payload))


@pytest.mark.parametrize("agent", AGENTS)
def test_cli_emits_native_deny_and_dry_run_never_blocks(
    agent: str, project: tuple[Path, SelvedgeStorage]
) -> None:
    """Exercise actual process stdin/stdout/exit semantics for every adapter."""
    root, _ = project
    command = [sys.executable, "-m", "selvedge.hooks.cli", "pretooluse", "--agent", agent]
    result = subprocess.run(
        command,
        input=json.dumps(payload_for(agent, root)),
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        check=False,
    )
    output = json.loads(result.stdout) if result.stdout.strip() else {}
    assert is_denied((output, result.stderr, result.returncode))
    dry = subprocess.run(
        [*command, "--dry-run"],
        input=json.dumps(payload_for(agent, root)),
        capture_output=True,
        text=True,
        check=False,
    )
    assert dry.returncode == 0 and json.loads(dry.stdout)["output"] == output
    assert json.loads(dry.stdout)["exit_code"] == result.returncode
    assert json.loads(dry.stdout)["stderr"] == result.stderr.rstrip("\n")


@pytest.mark.parametrize("agent", AGENTS)
def test_fail_open_and_bypass(
    agent: str,
    project: tuple[Path, SelvedgeStorage],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Malformed payloads and the documented escape hatch never deny a tool."""
    root, _ = project
    assert run(agent, "pretooluse", stdin="not json") == 0
    capsys.readouterr()
    monkeypatch.setenv("SELVEDGE_HOOK_DISABLE", "1")
    assert not is_denied(response(agent, "pretooluse", payload_for(agent, root)))


@pytest.mark.parametrize("agent", ["codex", "cursor", "copilot", "gemini"])
def test_digest_and_compaction_are_advisory(
    agent: str, project: tuple[Path, SelvedgeStorage]
) -> None:
    """Deliver stored rationale at startup, and use only notification fields at compaction."""
    root, _ = project
    payload = payload_for(agent, root)
    output, _, code = response(agent, "sessionstart", payload)
    assert "compatibility column" in json.dumps(output) and code == 0
    if agent == "cursor":
        assert set(output) == {"additional_context"}
    else:
        assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert "compatibility column" in output["hookSpecificOutput"]["additionalContext"]
    response(agent, "pretooluse", payload)
    output, _, code = response(agent, "precompact", payload)
    assert "schema.sql" in json.dumps(output) and code == 0
    assert set(output) == ({"user_message"} if agent == "cursor" else {"systemMessage"})


def test_multi_file_patch_checks_each_target(project: tuple[Path, SelvedgeStorage]) -> None:
    """An unrelated first file or a rename must not hide the watched target."""
    root, _ = project
    payload = payload_for("codex", root)
    payload["tool_input"]["command"] = (
        "*** Begin Patch\n*** Update File: app.py\n@@\n-a\n+b\n*** Update File: old.txt\n*** Move to: schema.sql\n@@\n-a\n+b\n*** End Patch"
    )
    assert is_denied(response("codex", "pretooluse", payload))
    target = normalize_edits("codex", payload)[-1]["tool_input"]
    assert target["file_path"].endswith("schema.sql") and "+b" in target["content"]


def test_copilot_batch_checks_all_replacements(project: tuple[Path, SelvedgeStorage]) -> None:
    """Every replacement is independently checked against its entity history."""
    root, _ = project
    payload = {
        "cwd": str(root),
        "session_id": "batch",
        "tool_name": "multi_replace_string_in_file",
        "tool_input": {
            "replacements": [
                {"filePath": str(root / "app.py")},
                {"filePath": str(root / "schema.sql")},
            ]
        },
    }
    assert is_denied(response("copilot", "pretooluse", payload))


def test_read_tools_and_other_checkout_do_not_gate(project: tuple[Path, SelvedgeStorage]) -> None:
    """The gate must not block reads or apply this project's history elsewhere."""
    root, _ = project
    payload = payload_for("cursor", root)
    payload["tool_name"] = "Read"
    assert normalize_edits("cursor", payload) == []
    payload["tool_name"] = "Write"
    payload["tool_input"]["file_path"] = str(root.parent / "other/schema.sql")
    assert not is_denied(response("cursor", "pretooluse", payload))


@pytest.mark.parametrize("agent", AGENTS)
def test_installer_preserves_config_and_is_idempotent(agent: str, tmp_path: Path) -> None:
    """Merge with unrelated hooks/settings, back up exact original bytes, then no-op."""
    path = hook_path(agent, tmp_path)
    path.parent.mkdir(parents=True)
    original = json.dumps(
        {"keep": {"enabled": False}, "hooks": {"OtherEvent": [{"command": "echo keep"}]}}
    )
    path.write_text(original)
    first = install_agent_hooks(agent, tmp_path)
    assert first.action == "added" and first.backup_path.read_text() == original
    actual = json.loads(path.read_text())
    assert (
        actual["keep"] == {"enabled": False}
        and actual["hooks"]["OtherEvent"][0]["command"] == "echo keep"
    )
    assert all(actual["hooks"][k] == v for k, v in hook_config(agent)["hooks"].items())
    assert install_agent_hooks(agent, tmp_path).action == "unchanged"


@pytest.mark.parametrize(
    "bad", ["[]", '{"hooks": []}', '{"hooks":{"preToolUse":{}}}', "{bad", '{"version":2}']
)
def test_bad_configuration_is_not_overwritten(bad: str, tmp_path: Path) -> None:
    """Invalid config must produce a visible error without a backup or write."""
    path = hook_path("cursor", tmp_path)
    path.parent.mkdir()
    path.write_text(bad)
    assert install_agent_hooks("cursor", tmp_path).action == "error"
    assert path.read_text() == bad and not path.with_suffix(".json.bak").exists()


def test_customized_selvedge_entry_is_a_conflict(tmp_path: Path) -> None:
    """Do not claim success or add duplicate handlers for a customized existing entry."""
    path = hook_path("codex", tmp_path)
    path.parent.mkdir()
    data = hook_config("codex")
    data["hooks"]["PreToolUse"][0]["matcher"] = "^Bash$"
    original = json.dumps(data)
    path.write_text(original)
    assert install_agent_hooks("codex", tmp_path).action == "conflict"
    assert path.read_text() == original


def test_cascade_preferred_path_is_respected(tmp_path: Path) -> None:
    """A legacy path must not receive hooks that the current client will ignore."""
    preferred = tmp_path / ".devin/hooks.json"
    preferred.parent.mkdir()
    preferred.write_text("{}")
    assert install_agent_hooks("windsurf", tmp_path).path == preferred
    assert not (tmp_path / ".windsurf/hooks.json").exists()


def test_cascade_install_does_not_shadow_active_legacy_hooks(tmp_path: Path) -> None:
    """An empty preferred file leaves legacy hooks active until explicitly migrated."""
    preferred = tmp_path / ".devin/hooks.json"
    legacy = tmp_path / ".windsurf/hooks.json"
    preferred.parent.mkdir()
    legacy.parent.mkdir()
    preferred.write_text("{}")
    original = '{"hooks":{"pre_run_command":[{"command":"check-project"}]}}'
    legacy.write_text(original)
    assert install_agent_hooks("windsurf", tmp_path).action == "conflict"
    assert preferred.read_text() == "{}" and legacy.read_text() == original


@pytest.mark.parametrize("agent", AGENTS)
def test_wizard_installs_native_hooks_in_selected_project(agent: str, tmp_path: Path) -> None:
    """Explicit agent selection wires its hooks without changing user-wide hook policy."""
    root = tmp_path / "repo"
    root.mkdir()
    outcome = run_wizard(
        project=root,
        home=tmp_path / "home",
        selected_agents=(agent,),
        confirm=lambda *_: True,
        init_project_dir=False,
        install_hook=False,
    )
    assert hook_path(agent, root).exists()
    assert not any(s.status == "error" for s in outcome.steps)


@pytest.mark.parametrize(
    "agent,name,key",
    [
        ("codex", "Bash", "command"),
        ("cursor", "Shell", "command"),
        ("gemini", "run_shell_command", "command"),
        ("copilot", "run_in_terminal", "command"),
        ("windsurf", "pre_run_command", "command_line"),
    ],
)
def test_shell_write_and_read_from_subdirectory(
    agent: str, name: str, key: str, project: tuple[Path, SelvedgeStorage]
) -> None:
    """Relative shell paths use the actual command directory; reads stay allowed."""
    root, _ = project
    sub = root / "sub"
    sub.mkdir()
    args = {key: "echo new > ../schema.sql"}
    payload = {"cwd": str(sub), "session_id": "shell", "tool_name": name, "tool_input": args}
    if agent == "windsurf":
        payload.update(
            agent_action_name=name, trajectory_id="shell", tool_info={**args, "cwd": str(sub)}
        )
    assert is_denied(response(agent, "pretooluse", payload))
    args[key] = "cat ../schema.sql"
    if agent == "windsurf":
        payload["tool_info"][key] = args[key]
    assert not is_denied(response(agent, "pretooluse", payload))


def test_cursor_ambiguous_workspace_fails_open(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A multi-root workspace without cwd must not consult an arbitrary root."""
    payload = {
        "conversation_id": "ambiguous",
        "workspace_roots": [str(tmp_path / "one"), str(tmp_path / "two")],
        "tool_name": "Write",
        "tool_input": {"file_path": "schema.sql"},
    }
    assert run("cursor", "pretooluse", stdin=json.dumps(payload)) == 0
    assert json.loads(capsys.readouterr().out) == {"permission": "allow"}


def test_wizard_hook_opt_out_leaves_only_mcp(tmp_path: Path) -> None:
    """The existing skip flag suppresses new adapters too."""
    root = tmp_path / "repo"
    root.mkdir()
    run_wizard(
        project=root,
        home=tmp_path / "home",
        selected_agents=("codex",),
        confirm=lambda *_: True,
        init_project_dir=False,
        install_hook=False,
        install_enforcement_hook=False,
    )
    assert (root / ".codex/config.toml").exists()
    assert not (root / ".codex/hooks.json").exists()
