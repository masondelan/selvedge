"""
Tests for the PreToolUse enforcement hook (v0.3.9.1).

Layer order: glob translation, entity-token extraction, the evaluate() core
(allow paths, block paths, session unblocking, supersede awareness, config
override), the run() wire behavior (exit codes, --dry-run, fail-open), the
`.claude/settings.json` installer, and the setup-wizard step.

The hook's contract under test throughout: precision over recall — every
miss, error, or unknown shape must ALLOW; only an unacknowledged standing
revert may block.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from selvedge.hooks import pretooluse as hook
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage

# ---------------------------------------------------------------------------
# Glob translation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("**/migrations/**", "migrations/0001_add.sql", True),
        ("**/migrations/**", "db/migrations/0001_add.py", True),
        ("**/migrations/**", "src/main.py", False),
        ("**/migrations/**", "migrationsx/0001.sql", False),
        ("**/schema*", "schema.sql", True),
        ("**/schema*", "db/schema_v2.sql", True),
        ("**/schema*", "db/old_schema.sql", False),
        ("**/*model*", "app/models.py", True),
        ("**/*model*", "models/user.py", False),  # dir itself isn't the match
        ("**/*.sql", "any/depth/file.sql", True),
        ("**/*.sql", "file.sql", True),
        ("**/*.sql", "file.sqlite", False),
        ("**/alembic/**", "alembic/versions/abc.py", True),
    ],
)
def test_glob_to_regex(pattern, path, expected):
    assert bool(hook._glob_to_regex(pattern).match(path)) is expected


# ---------------------------------------------------------------------------
# Entity-token extraction
# ---------------------------------------------------------------------------


def test_entity_tokens_extracts_table_column():
    assert hook._entity_tokens("add users.email plus payments.amount") == [
        "users.email",
        "payments.amount",
    ]


def test_entity_tokens_skips_file_extensions_and_dedupes():
    text = "import auth.py and schema.sql but users.token users.token"
    assert hook._entity_tokens(text) == ["users.token"]


def test_entity_tokens_capped():
    """Asserts the literal bound, not the constant.

    Comparing the output against `_MAX_ENTITY_CANDIDATES` compared the result
    to the very thing that produced it, so *any* value passed — dropping the
    cap to 4 left the suite green while the hook silently stopped considering
    36 of every 40 candidate entities.
    """
    assert hook._MAX_ENTITY_CANDIDATES == 40
    text = " ".join(f"table_{i}.col_{i}" for i in range(100))
    assert len(hook._entity_tokens(text)) == 40


def test_entity_tokens_extracts_sql_ddl():
    """Regression: raw SQL DDL has no literal 'table.column' dot — the hook
    must parse ADD/DROP COLUMN and CREATE/DROP TABLE the same way the
    importer does, or it can't gate the re-add of a reverted DB column (the
    feature's headline scenario)."""
    assert "users.sso_token" in hook._entity_tokens(
        "ALTER TABLE users ADD COLUMN sso_token TEXT;"
    )
    assert "orders.total" in hook._entity_tokens(
        "ALTER TABLE orders DROP COLUMN total;"
    )
    assert "payments" in hook._entity_tokens(
        "CREATE TABLE payments (id INT, amount DECIMAL);"
    )


def test_block_on_raw_sql_ddl_readd(project):
    """End-to-end regression for the extraction gap: a reverted column
    (stored dotted, as git-import records it) must block a raw
    `ALTER TABLE ... ADD COLUMN` edit that contains no literal dotted token."""
    storage = _storage(project)
    storage.log_event(ChangeEvent(
        entity_path="users.sso_token", change_type="add",
        timestamp="2026-01-01T00:00:00Z", reasoning="Tried an SSO token column.",
    ))
    storage.log_event(ChangeEvent(
        entity_path="users.sso_token", change_type="revert",
        timestamp="2026-01-02T00:00:00Z", reasoning="Reverted: SSO moved to JWTs.",
    ))
    payload = _edit_payload(
        project, "migrations/003_readd.sql",
        "ALTER TABLE users ADD COLUMN sso_token TEXT;",
    )
    decision = hook.evaluate(payload)
    assert decision.action == "block"
    assert decision.blocked_entities == ["users.sso_token"]


# ---------------------------------------------------------------------------
# evaluate() — fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project dir with a live .selvedge DB; SELVEDGE_DB unset."""
    monkeypatch.delenv("SELVEDGE_DB", raising=False)
    monkeypatch.delenv(hook.DISABLE_ENV, raising=False)
    proj = tmp_path / "proj"
    (proj / ".selvedge").mkdir(parents=True)
    SelvedgeStorage(proj / ".selvedge" / "selvedge.db")
    return proj


def _storage(project: Path) -> SelvedgeStorage:
    return SelvedgeStorage(project / ".selvedge" / "selvedge.db")


def _seed_reverted(storage, path):
    storage.log_event(ChangeEvent(
        entity_path=path,
        change_type="add",
        timestamp="2026-01-01T00:00:00Z",
        reasoning="Tried adding a per-user auth token column.",
    ))
    storage.log_event(ChangeEvent(
        entity_path=path,
        change_type="remove",
        timestamp="2026-01-02T00:00:00Z",
        reasoning="Reverted: tokens must be short-lived JWTs, not stored.",
    ))


def _edit_payload(project, file_path, new_string="", session_id="sess-1"):
    return {
        "session_id": session_id,
        "cwd": str(project),
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {
            "file_path": file_path,
            "old_string": "",
            "new_string": new_string,
        },
    }


# ---------------------------------------------------------------------------
# evaluate() — allow paths
# ---------------------------------------------------------------------------


def test_allow_unwatched_tool(project):
    payload = {"tool_name": "Read", "tool_input": {"file_path": "migrations/x.sql"}}
    assert hook.evaluate(payload).action == "allow"


def test_allow_unwatched_path(project):
    _seed_reverted(_storage(project), "users.auth_token")
    payload = _edit_payload(project, "src/main.py", "users.auth_token")
    assert hook.evaluate(payload).action == "allow"


def test_allow_when_no_project_db(tmp_path, monkeypatch):
    monkeypatch.delenv("SELVEDGE_DB", raising=False)
    bare = tmp_path / "bare"
    bare.mkdir()
    payload = _edit_payload(bare, "migrations/0001.sql", "users.auth_token")
    decision = hook.evaluate(payload)
    assert decision.action == "allow"
    assert "no project Selvedge DB" in decision.reason


def test_allow_when_disabled(project, monkeypatch):
    _seed_reverted(_storage(project), "users.auth_token")
    monkeypatch.setenv(hook.DISABLE_ENV, "1")
    payload = _edit_payload(project, "migrations/0001.sql", "users.auth_token")
    assert hook.evaluate(payload).action == "allow"


def test_allow_when_no_reverted_entities(project):
    payload = _edit_payload(project, "migrations/0001.sql", "users.email")
    decision = hook.evaluate(payload)
    assert decision.action == "allow"
    assert "no reverted decisions" in decision.reason


def test_allow_active_entity(project):
    storage = _storage(project)
    storage.log_event(ChangeEvent(
        entity_path="users.email", change_type="add",
        reasoning="Active column, never reverted.",
    ))
    payload = _edit_payload(project, "migrations/0001.sql", "users.email")
    assert hook.evaluate(payload).action == "allow"


def test_allow_reopened_entity_does_not_block(project):
    storage = _storage(project)
    _seed_reverted(storage, "users.auth_token")
    storage.log_supersede(
        "users.auth_token",
        reasoning="IdP now requires stored tokens — constraint gone.",
    )
    payload = _edit_payload(project, "migrations/0003.sql", "users.auth_token")
    decision = hook.evaluate(payload)
    assert decision.action == "allow"


# ---------------------------------------------------------------------------
# evaluate() — block + session unblocking
# ---------------------------------------------------------------------------


def test_block_on_unacknowledged_revert(project):
    _seed_reverted(_storage(project), "users.auth_token")
    payload = _edit_payload(project, "migrations/0002_readd.sql", "users.auth_token")
    decision = hook.evaluate(payload)
    assert decision.action == "block"
    assert decision.blocked_entities == ["users.auth_token"]
    # The prior reasoning rides along in the block message — context for free.
    assert "short-lived JWTs" in decision.reason
    assert "Tried adding a per-user auth token" in decision.reason
    assert "prior-attempts" in decision.reason
    assert "supersede" in decision.reason
    assert "SELVEDGE_HOOK_DISABLE=1" in decision.reason


def test_block_on_reverted_file_entity(project):
    _seed_reverted(_storage(project), "db/schema.sql")
    payload = _edit_payload(project, "db/schema.sql")
    decision = hook.evaluate(payload)
    assert decision.action == "block"
    assert decision.blocked_entities == ["db/schema.sql"]


def test_query_then_retry_unblocks(project):
    storage = _storage(project)
    _seed_reverted(storage, "users.auth_token")
    payload = _edit_payload(project, "migrations/0002.sql", "users.auth_token")
    assert hook.evaluate(payload).action == "block"

    # The documented acknowledgement: query prior_attempts (either surface
    # records the same tool_calls row), then retry.
    storage.record_tool_call("prior_attempts", entity_path="users.auth_token")
    retry = hook.evaluate(payload)
    assert retry.action == "allow"
    assert "checked this session" in retry.reason


def test_prefix_query_unblocks(project):
    storage = _storage(project)
    _seed_reverted(storage, "users.auth_token")
    payload = _edit_payload(project, "migrations/0002.sql", "users.auth_token")
    assert hook.evaluate(payload).action == "block"

    storage.record_tool_call("prior_attempts", entity_path="users")
    assert hook.evaluate(payload).action == "allow"


def test_stale_query_outside_window_still_blocks(project):
    import sqlite3
    import uuid

    storage = _storage(project)
    _seed_reverted(storage, "users.auth_token")
    # A prior_attempts query from two days ago — before any plausible
    # session window.
    conn = sqlite3.connect(str(storage.db_path))
    conn.execute(
        "INSERT INTO tool_calls (id, timestamp, tool_name, entity_path) "
        "VALUES (?, '2026-01-05T00:00:00Z', 'prior_attempts', 'users.auth_token')",
        (str(uuid.uuid4()),),
    )
    conn.commit()
    conn.close()

    payload = _edit_payload(project, "migrations/0002.sql", "users.auth_token")
    assert hook.evaluate(payload).action == "block"


def test_block_with_absolute_path_from_subdirectory(project):
    """Regression: real Claude Code payloads carry ABSOLUTE file paths and
    the session may be rooted in a subdirectory. Entities are recorded
    project-root-relative (git-import / agents), so the hook must relativize
    against the project root — the dir holding .selvedge/ — not the payload
    cwd. Relativizing against cwd made enforcement silently inert here."""
    storage = _storage(project)
    # Recorded the way git-import / an agent would: repo-root-relative.
    storage.log_event(ChangeEvent(
        entity_path="backend/migrations/0002_orders.sql", change_type="add",
        timestamp="2026-01-01T00:00:00Z", reasoning="Tried an orders table.",
    ))
    storage.log_event(ChangeEvent(
        entity_path="backend/migrations/0002_orders.sql", change_type="remove",
        timestamp="2026-01-02T00:00:00Z", reasoning="Reverted: orders live in the OMS.",
    ))
    # Session opened in the subdirectory; the edit carries an absolute path.
    abs_file = str(project / "backend" / "migrations" / "0002_orders.sql")
    payload = {
        "session_id": "sess-sub",
        "cwd": str(project / "backend"),
        "tool_name": "Edit",
        "tool_input": {"file_path": abs_file, "old_string": "", "new_string": ""},
    }
    decision = hook.evaluate(payload)
    assert decision.action == "block"
    assert decision.blocked_entities == ["backend/migrations/0002_orders.sql"]
    assert "orders live in the OMS" in decision.reason


def test_bash_command_touching_watched_path_blocks(project):
    _seed_reverted(_storage(project), "migrations/0001_add_tokens.sql")
    payload = {
        "session_id": "sess-bash",
        "cwd": str(project),
        "tool_name": "Bash",
        "tool_input": {"command": "git checkout HEAD~1 -- migrations/0001_add_tokens.sql"},
    }
    decision = hook.evaluate(payload)
    assert decision.action == "block"
    assert decision.blocked_entities == ["migrations/0001_add_tokens.sql"]


def test_config_toml_overrides_watch_globs(project):
    storage = _storage(project)
    _seed_reverted(storage, "users.auth_token")
    (project / ".selvedge" / "config.toml").write_text(
        '[hook]\nwatch_globs = ["docs/**"]\n'
    )
    # migrations/ is no longer watched under the override.
    payload = _edit_payload(project, "migrations/0002.sql", "users.auth_token")
    assert hook.evaluate(payload).action == "allow"


def test_malformed_config_toml_falls_back_to_defaults(project):
    _seed_reverted(_storage(project), "users.auth_token")
    (project / ".selvedge" / "config.toml").write_text("not [ valid toml ===")
    payload = _edit_payload(project, "migrations/0002.sql", "users.auth_token")
    assert hook.evaluate(payload).action == "block"


def test_session_state_file_is_created_and_pruned(project):
    _seed_reverted(_storage(project), "users.auth_token")
    state_dir = project / ".selvedge" / "hook_sessions"

    # An ancient session file gets pruned on the next evaluation.
    state_dir.mkdir(parents=True)
    old = state_dir / "old-session.json"
    old.write_text("{}")
    import os
    ancient = 1_600_000_000  # 2020 — far past the max age
    os.utime(old, (ancient, ancient))

    payload = _edit_payload(project, "migrations/0002.sql", "users.auth_token")
    hook.evaluate(payload)
    assert (state_dir / "sess-1.json").is_file()
    assert not old.exists()


# ---------------------------------------------------------------------------
# run() — wire behavior
# ---------------------------------------------------------------------------


def test_run_blocks_with_exit_2_and_stderr(project, capsys):
    _seed_reverted(_storage(project), "users.auth_token")
    payload = _edit_payload(project, "migrations/0002.sql", "users.auth_token")
    code = hook.run([], stdin=json.dumps(payload))
    assert code == hook.EXIT_BLOCK
    captured = capsys.readouterr()
    assert "short-lived JWTs" in captured.err


def test_wire_stdout_is_never_a_json_object(project, capsys):
    """The gate must never put a ``{…}`` object on stdout, on either verdict.

    Claude Code v2.1.248 made a non-parseable stdout ``{…}`` a hook error
    rather than a plain-text fallback. The gate sidesteps that entirely: its
    block message — which embeds arbitrary stored reasoning — rides on STDERR
    with exit 2, and the allow path is silent, so stdout stays empty and can
    never be mistaken for a malformed JSON object however hostile the reasoning.
    """
    hostile = 'REVERTED }{ "q" {"decision":"block","x":[1,2]}'
    storage = _storage(project)
    storage.log_event(ChangeEvent(
        entity_path="users.sso_token", change_type="add",
        timestamp="2026-01-01T00:00:00Z", reasoning="Tried an SSO token column.",
    ))
    storage.log_event(ChangeEvent(
        entity_path="users.sso_token", change_type="remove",
        timestamp="2026-01-02T00:00:00Z", reasoning=hostile,
    ))

    # Block path: watched re-add of the reverted entity, unacknowledged.
    code = hook.run([], stdin=json.dumps(_edit_payload(
        project, "migrations/003_readd.sql",
        "ALTER TABLE users ADD COLUMN sso_token TEXT;",
    )))
    captured = capsys.readouterr()
    assert code == hook.EXIT_BLOCK
    assert captured.out == "", "the gate must never write to stdout"
    assert hostile in captured.err  # the reasoning rides on stderr, safely

    # Allow path: an unwatched tool leaves stdout empty too.
    code = hook.run([], stdin=json.dumps({
        "tool_name": "Read", "tool_input": {"file_path": "migrations/x.sql"},
        "cwd": str(project), "session_id": "sess-1",
    }))
    captured = capsys.readouterr()
    assert code == hook.EXIT_ALLOW
    assert captured.out == ""


def test_run_allows_with_exit_0(project, capsys):
    payload = _edit_payload(project, "src/main.py")
    assert hook.run([], stdin=json.dumps(payload)) == hook.EXIT_ALLOW


def test_run_dry_run_prints_decision_and_exits_0(project, capsys):
    _seed_reverted(_storage(project), "users.auth_token")
    payload = _edit_payload(project, "migrations/0002.sql", "users.auth_token")
    code = hook.run(["--dry-run"], stdin=json.dumps(payload))
    assert code == hook.EXIT_ALLOW
    decision = json.loads(capsys.readouterr().out)
    assert decision["action"] == "block"
    assert decision["blocked_entities"] == ["users.auth_token"]


def test_run_fails_open_on_garbage_stdin(project):
    assert hook.run([], stdin="this is not json {") == hook.EXIT_ALLOW
    assert hook.run([], stdin="") == hook.EXIT_ALLOW
    assert hook.run([], stdin='["a", "list"]') == hook.EXIT_ALLOW


def test_console_entry_end_to_end(project):
    """The real subprocess path: python -m selvedge.hooks.cli pretooluse."""
    import os

    _seed_reverted(_storage(project), "users.auth_token")
    payload = _edit_payload(project, "migrations/0002.sql", "users.auth_token")
    env = {k: v for k, v in os.environ.items() if k != "SELVEDGE_DB"}
    env["SELVEDGE_QUIET"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "selvedge.hooks.cli", "pretooluse"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == hook.EXIT_BLOCK
    assert "short-lived JWTs" in result.stderr


# ---------------------------------------------------------------------------
# install_pretooluse_hook — the .claude/settings.json installer
# ---------------------------------------------------------------------------


def test_installer_creates_settings_file(tmp_path):
    from selvedge.setup import HOOK_COMMAND, install_pretooluse_hook

    settings = tmp_path / ".claude" / "settings.json"
    result = install_pretooluse_hook(settings)
    assert result.action == "created"
    data = json.loads(settings.read_text())
    entry = data["hooks"]["PreToolUse"][0]
    assert entry["hooks"][0]["command"] == HOOK_COMMAND
    assert "Edit" in entry["matcher"] and "Bash" in entry["matcher"]


def test_installer_appends_preserving_existing_hooks(tmp_path):
    from selvedge.setup import install_pretooluse_hook

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "other-hook"}]}
            ],
            "PostToolUse": [{"matcher": "*", "hooks": []}],
        },
    }))
    result = install_pretooluse_hook(settings)
    assert result.action == "added"
    assert result.backup_path is not None and result.backup_path.exists()
    data = json.loads(settings.read_text())
    # Existing content untouched, ours appended.
    assert data["permissions"] == {"allow": ["Bash(ls:*)"]}
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "other-hook"
    assert len(data["hooks"]["PreToolUse"]) == 2
    assert "PostToolUse" in data["hooks"]


def test_installer_is_idempotent(tmp_path):
    from selvedge.setup import install_pretooluse_hook

    settings = tmp_path / "settings.json"
    assert install_pretooluse_hook(settings).action == "created"
    assert install_pretooluse_hook(settings).action == "unchanged"
    data = json.loads(settings.read_text())
    assert len(data["hooks"]["PreToolUse"]) == 1


def test_installer_errors_on_malformed_json(tmp_path):
    from selvedge.setup import install_pretooluse_hook

    settings = tmp_path / "settings.json"
    settings.write_text("{not json")
    before = settings.read_text()
    result = install_pretooluse_hook(settings)
    assert result.action == "error"
    assert settings.read_text() == before  # no write on error


# ---------------------------------------------------------------------------
# Wizard step
# ---------------------------------------------------------------------------


def _wizard_env(tmp_path):
    """A home with Claude Code detected + an empty project."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "config.json").write_text("{}")
    project = tmp_path / "project"
    project.mkdir()
    return home, project


def test_wizard_installs_enforcement_hook(tmp_path):
    from selvedge.setup import run_wizard

    home, project = _wizard_env(tmp_path)
    outcome = run_wizard(
        project=project,
        home=home,
        confirm=lambda *_: True,
        init_fn=lambda p: None,
        install_hook_fn=lambda p: None,
    )
    labels = {s.label: s.status for s in outcome.steps}
    assert labels.get("PreToolUse enforcement hook") == "ok"
    assert (project / ".claude" / "settings.json").is_file()


def test_wizard_enforcement_hook_declined(tmp_path):
    from selvedge.setup import run_wizard

    home, project = _wizard_env(tmp_path)
    outcome = run_wizard(
        project=project,
        home=home,
        confirm=lambda *_: False,
        init_fn=lambda p: None,
        install_hook_fn=lambda p: None,
    )
    labels = {s.label: s.status for s in outcome.steps}
    assert labels.get("PreToolUse enforcement hook") == "skipped"
    assert not (project / ".claude" / "settings.json").exists()


def test_wizard_enforcement_hook_skippable(tmp_path):
    from selvedge.setup import run_wizard

    home, project = _wizard_env(tmp_path)
    outcome = run_wizard(
        project=project,
        home=home,
        install_enforcement_hook=False,
        confirm=lambda *_: True,
        init_fn=lambda p: None,
        install_hook_fn=lambda p: None,
    )
    labels = {s.label for s in outcome.steps}
    assert "PreToolUse enforcement hook" not in labels


def test_wizard_no_claude_code_no_hook_step(tmp_path):
    """Cursor-only machine: the Claude-specific hook step must not appear."""
    from selvedge.setup import run_wizard

    home = tmp_path / "home"
    (home / ".cursor").mkdir(parents=True)
    (home / ".cursor" / "mcp.json").write_text("{}")
    project = tmp_path / "project"
    project.mkdir()
    outcome = run_wizard(
        project=project,
        home=home,
        confirm=lambda *_: True,
        init_fn=lambda p: None,
        install_hook_fn=lambda p: None,
    )
    labels = {s.label for s in outcome.steps}
    assert "PreToolUse enforcement hook" not in labels


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])


# ---------------------------------------------------------------------------
# The Bash gate must require write intent
#
# The gate treated every path-shaped token in a Bash command as "touched", so
# reading, linting or testing a watched file was blocked. That is a false
# BLOCK, which the module docstring forbids — this design accepts misses, not
# false positives. Worse, the block message's own remediation
# (`selvedge prior-attempts '<path>'`) is itself a Bash command containing the
# path, so following the instruction produced the same block: a livelock with
# no CLI-only escape.
# ---------------------------------------------------------------------------

_WATCHED = "migrations/0001_add_tokens.sql"


def _bash_payload(project, command, session_id="sess-bash-intent"):
    return {
        "session_id": session_id,
        "cwd": str(project),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


@pytest.fixture
def reverted(project):
    _seed_reverted(_storage(project), _WATCHED)
    return project


@pytest.mark.parametrize("command", [
    f"cat {_WATCHED}",
    f"less {_WATCHED}",
    f"head -50 {_WATCHED}",
    f"wc -l {_WATCHED}",
    f"grep -n token {_WATCHED}",
    f"git diff {_WATCHED}",
    f"git log --oneline -- {_WATCHED}",
    f"git show HEAD -- {_WATCHED}",
    f"ruff check {_WATCHED}",
    f"pytest {_WATCHED}",
])
def test_read_only_bash_command_allows(reverted, command):
    decision = hook.evaluate(_bash_payload(reverted, command))
    assert decision.action == "allow", f"read-only command blocked: {command}"


@pytest.mark.parametrize("command", [
    f"git checkout HEAD~1 -- {_WATCHED}",
    f"echo x > {_WATCHED}",
    f"cat tmpl >> {_WATCHED}",
    f"sed -i '' s/a/b/ {_WATCHED}",
    f"cp other.sql {_WATCHED}",
    f"mv other.sql {_WATCHED}",
    f"rm {_WATCHED}",
    f"curl -s http://x | tee {_WATCHED}",
    f"git apply p.patch && cat {_WATCHED}",
])
def test_write_bash_command_still_blocks(reverted, command):
    decision = hook.evaluate(_bash_payload(reverted, command))
    assert decision.action == "block", f"write command slipped through: {command}"


@pytest.mark.parametrize("command", [
    f"selvedge prior-attempts '{_WATCHED}'",
    f"selvedge blame {_WATCHED}",
    f"selvedge supersede '{_WATCHED}' --reasoning x",
])
def test_selvedge_remediation_command_is_not_blocked(reverted, command):
    """The command the block message tells the agent to run must be runnable."""
    decision = hook.evaluate(_bash_payload(reverted, command))
    assert decision.action == "allow", f"remediation self-blocked: {command}"


# ---------------------------------------------------------------------------
# Path resolution must stay inside the project
#
# `_relative_to` ended in `path_str.lstrip("./")`, which strips any leading run
# of `.` and `/` CHARACTERS rather than a `./` prefix — so `../x` and even an
# absolute path in another checkout were rewritten into project-relative-
# looking strings that then matched the watch globs. It also disagreed with
# `canonicalize_entity_path` on dotfiles, making enforcement silently inert
# for an entity stored as `.hidden/schema.sql`.
# ---------------------------------------------------------------------------


def _matches_default_globs(path: str) -> bool:
    return any(
        hook._glob_to_regex(g).match(path) for g in hook.DEFAULT_WATCH_GLOBS
    )


def test_relative_to_strips_only_a_leading_dot_slash(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert hook._relative_to("./migrations/x.sql", root) == "migrations/x.sql"


def test_relative_to_preserves_dotfile_paths(tmp_path):
    """`.hidden/x` must not become `hidden/x` — that is a different entity."""
    root = tmp_path / "proj"
    root.mkdir()
    assert hook._relative_to(".hidden/schema.sql", root) == ".hidden/schema.sql"


@pytest.mark.parametrize("outside", [
    "../other/migrations/x.sql",
    "../../etc/migrations/x.sql",
])
def test_relative_to_does_not_pull_parent_paths_into_the_project(tmp_path, outside):
    root = tmp_path / "proj"
    root.mkdir()
    result = hook._relative_to(outside, root)
    assert not _matches_default_globs(result), (
        f"{outside!r} -> {result!r} still matches a watch glob"
    )


def test_absolute_path_in_another_checkout_is_not_project_relative(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    other = tmp_path / "repoB" / "migrations" / "003.sql"
    other.parent.mkdir(parents=True)
    other.write_text("-- x\n")
    result = hook._relative_to(str(other), root)
    assert not _matches_default_globs(result), (
        f"out-of-root absolute path {result!r} matched a watch glob"
    )


# ---------------------------------------------------------------------------
# Import cost
#
# The hook spawns a fresh process on every gated tool call, and in the ~99%
# case decides "nothing to do" in under a millisecond. Wall-clock assertions
# on a shared CI runner are too noisy to be a gate, so the durable guard is
# structural: assert which modules the common path is allowed to load.
# ---------------------------------------------------------------------------


def _modules_after(snippet: str) -> set[str]:
    """Run `snippet` in a fresh interpreter, return its loaded selvedge modules."""
    result = subprocess.run(
        [sys.executable, "-c", snippet + "\nimport sys, json\n"
         "print(json.dumps([m for m in sys.modules if m.startswith('selvedge')]))"],
        capture_output=True, text=True, check=True,
        env={**os.environ, "SELVEDGE_QUIET": "1"},
    )
    return set(json.loads(result.stdout.strip().splitlines()[-1]))


def test_allow_path_never_imports_the_ddl_parsers(tmp_path):
    """`..importers` is only needed once a watched path has been touched.

    It sat at module scope, so every unwatched edit — the overwhelming
    majority — paid for the SQL DDL extractors it would never call.
    """
    loaded = _modules_after(
        "from selvedge.hooks.pretooluse import evaluate\n"
        "evaluate({'tool_name': 'Edit', 'tool_input': {'file_path': 'src/app.py'},\n"
        f"          'cwd': {str(tmp_path)!r}, 'session_id': 's'}})"
    )
    assert "selvedge.importers" not in loaded, (
        "the unwatched-edit path imported the SQL DDL parsers; they belong "
        f"below the `if not touched:` early return. loaded={sorted(loaded)}"
    )


def test_decision_is_not_a_dataclass():
    """`@dataclass` costs `dataclasses` → `inspect` — ~2 ms, for nine lines.

    Asserting the decorator's absence rather than `inspect not in sys.modules`,
    because `inspect` legitimately arrives via other modules on the block path;
    what must not happen is `Decision` being the thing that requires it.
    """
    import dataclasses

    assert not dataclasses.is_dataclass(hook.Decision), (
        "Decision is a dataclass again — that decorator pulls dataclasses → "
        "inspect on every gated tool call, to save nine lines"
    )
    # The hand-written replacement has to keep behaving like one.
    d = hook.Decision("allow", reason="x")
    assert d.to_dict() == {"action": "allow", "reason": "x", "blocked_entities": []}
    assert d == hook.Decision("allow", reason="x")
    assert d != hook.Decision("block", reason="x")
    assert hook.Decision("allow").blocked_entities == []
    # Default must not be shared between instances.
    hook.Decision("allow").blocked_entities.append("leaked")
    assert hook.Decision("allow").blocked_entities == []


def test_disable_env_short_circuits_before_importing_the_hook():
    """The documented bypass must skip the imports, not just the logic.

    It used to be checked inside `evaluate()`, after every import had already
    run — so `SELVEDGE_HOOK_DISABLE=1` measured the same as not setting it.
    """
    loaded = _modules_after(
        "import os, sys\n"
        "os.environ['SELVEDGE_HOOK_DISABLE'] = '1'\n"
        "sys.argv = ['selvedge-hook', 'pretooluse']\n"
        "import selvedge.hooks.cli as c\n"
        "try:\n"
        "    c.main()\n"
        "except SystemExit as e:\n"
        "    assert e.code == 0, e.code\n"
    )
    assert "selvedge.hooks.pretooluse" not in loaded, (
        "the bypass imported the hook module it exists to skip; the check "
        f"must run before the import. loaded={sorted(loaded)}"
    )


def test_disable_env_name_matches_pretooluse():
    """`hooks/cli.py` duplicates the env-var name to avoid importing it."""
    from selvedge.hooks import cli as hook_cli

    assert hook_cli._DISABLE_ENV == hook.DISABLE_ENV


def test_dry_run_still_reports_the_decision_when_disabled(tmp_path):
    """The bypass short-circuit must not swallow `--dry-run` output."""
    proc = subprocess.run(
        [sys.executable, "-m", "selvedge.hooks.cli", "pretooluse", "--dry-run"],
        input=json.dumps({
            "tool_name": "Edit", "cwd": str(tmp_path), "session_id": "s",
            "tool_input": {"file_path": "src/app.py"},
        }),
        capture_output=True, text=True,
        env={**os.environ, "SELVEDGE_HOOK_DISABLE": "1", "SELVEDGE_QUIET": "1"},
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["action"] == "allow"


# ---------------------------------------------------------------------------
# Deliberate guards
#
# A mutation pass found these executed on every run but asserted by nothing:
# each could be deleted or inverted with the whole suite green. They cluster
# in the hook because it is the newest surface, it runs on every gated tool
# call, and its contract is "never block on a miss" — so the mutations most
# likely to arise from ordinary refactoring turn it into a false-positive
# blocker while CI stays green.
# ---------------------------------------------------------------------------


def test_session_back_slack_is_thirty_minutes():
    """The only guard stopping the hook from blocking a *compliant* agent.

    The documented order is "query prior_attempts, then edit". Without the
    back-slack, the session window starts at the first gated edit, so a query
    made moments earlier falls outside it and the compliant agent is blocked.
    """
    from datetime import timedelta

    assert hook._SESSION_BACK_SLACK == timedelta(minutes=30)


def test_back_slack_allows_a_query_made_just_before_the_first_edit(tmp_path, monkeypatch):
    """Pins the slack's magnitude, not just its sign.

    The `prior_attempts` call is recorded BEFORE the first `evaluate()` — i.e.
    before any session window exists — which is exactly the compliant order.
    Setting the slack to zero makes this block.
    """
    from datetime import datetime, timedelta, timezone

    db = tmp_path / ".selvedge" / "selvedge.db"
    db.parent.mkdir(parents=True)
    storage = SelvedgeStorage(db)
    storage.log_event(ChangeEvent(entity_path="users.sso_token", change_type="add",
                                  reasoning="Tried a dedicated SSO token column."))
    storage.log_event(ChangeEvent(entity_path="users.sso_token", change_type="remove",
                                  reasoning="Reverted: tokens belong in sessions."))

    # `record_tool_call` always stamps "now", so the 20-minute gap is created
    # by advancing the edit instead of backdating the query. Same thing from
    # the window's point of view, and it needs no test-only writer.
    storage.record_tool_call("prior_attempts", entity_path="users.sso_token", agent="a")
    edit_time = datetime.now(timezone.utc) + timedelta(minutes=20)

    decision = hook.evaluate({
        "session_id": "fresh-session-never-seen-before",
        "cwd": str(tmp_path),
        "tool_name": "Edit",
        "tool_input": {"file_path": "migrations/0003.sql",
                       "new_string": "ALTER TABLE users ADD COLUMN sso_token TEXT;"},
    }, now=edit_time)

    # Pins magnitude, not sign: this passes only while the slack exceeds the
    # 20-minute gap, so shrinking 30min → 0 (or → 5min) fails it.
    assert decision.action == "allow", (
        "a compliant agent that queried 20 minutes before editing was blocked "
        f"— the 30-minute back-slack is not in effect. reason={decision.reason!r}"
    )


def test_unknown_subcommand_exits_1_not_2():
    """Exit 2 is Claude Code's *block* code — 1 must never become 2.

    A stale `.claude/settings.json` entry pointing at a renamed subcommand
    would then block every Edit/Write/Bash in the session, with a usage dump
    delivered to the agent as the blocking reason.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "selvedge.hooks.cli", "definitely-not-a-hook"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 1, (
        f"unknown subcommand exited {proc.returncode}; 2 would block every "
        "gated tool call in the session"
    )


def test_session_id_cannot_escape_the_state_directory(tmp_path):
    """`session_id` is payload-supplied, so it is untrusted filename input."""
    assert hook._sanitize_session_id("../../PWNED") == ".._.._PWNED"
    assert hook._sanitize_session_id("a/b/../c") == "a_b_.._c"
    assert "/" not in hook._sanitize_session_id("x/y")
    assert hook._sanitize_session_id("") == "unknown-session"
    assert hook._sanitize_session_id(None) == "unknown-session"
    assert hook._sanitize_session_id(12345) == "unknown-session"
    assert len(hook._sanitize_session_id("z" * 500)) == 128


def test_traversal_session_id_writes_inside_the_state_dir(tmp_path):
    """The behavioural half: state must land under hook_sessions/, always."""
    from datetime import datetime, timezone

    state_dir = tmp_path / "hook_sessions"
    hook._session_window_start(
        state_dir, hook._sanitize_session_id("../../PWNED"),
        datetime.now(timezone.utc),
    )
    assert not (tmp_path.parent.parent / "PWNED.json").exists()
    written = list(state_dir.glob("*.json"))
    assert len(written) == 1
    assert written[0].parent == state_dir


def test_block_message_truncates_and_reports_the_remainder():
    """The `(+N more entities)` branch was never executed by any test."""
    assert hook._MAX_BLOCKED_REPORTED == 3
    blocked = [
        {"entity": f"t{i}.c{i}", "tried_reasoning": f"tried {i}",
         "reverted_reasoning": f"reverted {i}"}
        for i in range(5)
    ]
    message = hook._block_message(blocked)

    assert "t0.c0" in message and "t2.c2" in message
    assert "t3.c3" not in message and "t4.c4" not in message
    assert "(+2 more entities)" in message
