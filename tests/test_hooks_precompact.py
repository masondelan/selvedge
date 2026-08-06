"""PreCompact hook (v0.3.10) — advisory only, never a veto.

Compaction is the moment this session's reasoning stops existing anywhere
except in what the agent wrote down. The hook fires immediately before and
says so.

The load-bearing property is what it does NOT do. The API allows a veto two
ways — exit code 2, and `{"decision": "block"}` on stdout — and neither is
used, because blocking compaction doesn't inconvenience a tool call, it
wedges the session.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout

import pytest

from selvedge.hooks import precompact as hook
from selvedge.hooks import pretooluse
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("SELVEDGE_DB", raising=False)
    monkeypatch.delenv(hook.DISABLE_ENV, raising=False)
    monkeypatch.setenv("SELVEDGE_QUIET", "1")
    proj = tmp_path / "proj"
    (proj / ".selvedge").mkdir(parents=True)
    SelvedgeStorage(proj / ".selvedge" / "selvedge.db")
    return proj


def _payload(project, session_id="sess-1", trigger="auto"):
    return {
        "session_id": session_id,
        "cwd": str(project),
        "hook_event_name": "PreCompact",
        "trigger": trigger,
        "custom_instructions": "",
    }


def _touch(project, session_id, entities):
    """Simulate the gate having seen these watched entities this session."""
    pretooluse.record_touched_entities(
        project / ".selvedge" / "hook_sessions", session_id, entities
    )


# ---------------------------------------------------------------------------
# Advisory only — the property that matters most
# ---------------------------------------------------------------------------


def test_never_blocks_compaction(project):
    """Both veto mechanisms must stay unused, on every path.

    Exit 2 is the veto code and is one character away from 0, so the exit
    code is asserted explicitly rather than assumed. `decision` is the other
    mechanism and must never appear in the emitted JSON.
    """
    _touch(project, "sess-1", ["migrations/0001.sql"])

    for stdin in (
        json.dumps(_payload(project)),
        json.dumps(_payload(project, trigger="manual")),
        "",                     # empty payload
        "not json at all",      # unparseable
        "[]",                   # wrong type
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = hook.run([], stdin=stdin)
        assert code == 0, f"exit {code} for {stdin!r}; 2 would veto compaction"
        out = buf.getvalue().strip()
        if out:
            payload = json.loads(out)
            assert "decision" not in payload, "the hook emitted a veto decision"
            assert payload["hookSpecificOutput"]["hookEventName"] == "PreCompact"


def test_source_contains_no_veto_mechanism():
    """A grep-level guard against a well-meaning future edit.

    Both veto forms are cheap to reintroduce by accident — an `exit 2` copied
    from the gate, or a `decision` key copied from the hooks docs.
    """
    import pathlib

    source = pathlib.Path(hook.__file__).read_text()
    code_lines = [
        ln for ln in source.splitlines()
        if not ln.strip().startswith("#")
    ]
    body = "\n".join(code_lines)
    assert "EXIT_BLOCK" not in body
    assert 'return 2' not in body
    assert 'sys.exit(2)' not in body
    assert '"decision"' not in body.split('"""')[-1]


# ---------------------------------------------------------------------------
# Quiet by default
# ---------------------------------------------------------------------------


def test_silent_when_the_session_touched_nothing(project):
    assert hook.evaluate(_payload(project)) == ""


def test_silent_when_everything_touched_was_logged(project):
    """Nothing would be lost, so there is nothing to say."""
    _touch(project, "sess-1", ["migrations/0001.sql"])
    SelvedgeStorage(project / ".selvedge" / "selvedge.db").log_event(ChangeEvent(
        entity_path="migrations/0001.sql", change_type="modify",
        session_id="sess-1", reasoning="Added the index for the slow report query.",
    ))
    assert hook.evaluate(_payload(project)) == ""


def test_silent_with_no_project_db(tmp_path, monkeypatch):
    monkeypatch.delenv("SELVEDGE_DB", raising=False)
    bare = tmp_path / "bare"
    bare.mkdir()
    assert hook.evaluate({"cwd": str(bare), "session_id": "s"}) == ""


def test_disable_env_silences_it(project, monkeypatch):
    _touch(project, "sess-1", ["migrations/0001.sql"])
    monkeypatch.setenv(hook.DISABLE_ENV, "1")
    assert hook.evaluate(_payload(project)) == ""


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


def test_names_touched_but_unlogged_entities(project):
    _touch(project, "sess-1", ["migrations/0001.sql", "migrations/0002.sql"])
    reminder = hook.evaluate(_payload(project))
    assert "migrations/0001.sql" in reminder
    assert "migrations/0002.sql" in reminder
    assert "log_change" in reminder


def test_already_logged_entities_are_subtracted(project):
    _touch(project, "sess-1", ["migrations/0001.sql", "migrations/0002.sql"])
    SelvedgeStorage(project / ".selvedge" / "selvedge.db").log_event(ChangeEvent(
        entity_path="migrations/0001.sql", change_type="modify",
        session_id="sess-1", reasoning="Indexed the report query.",
    ))
    reminder = hook.evaluate(_payload(project))
    assert "migrations/0002.sql" in reminder
    assert "migrations/0001.sql" not in reminder


def test_long_lists_are_capped(project):
    _touch(project, "sess-1", [f"migrations/{i:04d}.sql" for i in range(30)])
    reminder = hook.evaluate(_payload(project))
    assert reminder.count("  - migrations/") == hook._MAX_ENTITIES
    assert "more)" in reminder


def test_session_state_is_scoped_per_session(project):
    """One session's touched entities must not leak into another's reminder."""
    _touch(project, "sess-1", ["migrations/0001.sql"])
    assert hook.evaluate(_payload(project, session_id="sess-2")) == ""


def test_reminder_is_deterministic(project):
    _touch(project, "sess-1", ["migrations/0001.sql"])
    assert hook.evaluate(_payload(project)) == hook.evaluate(_payload(project))


# ---------------------------------------------------------------------------
# Wire behaviour + latency
# ---------------------------------------------------------------------------


def test_end_to_end_through_the_hooks_cli(project):
    proc = subprocess.run(
        [sys.executable, "-m", "selvedge.hooks.cli", "precompact"],
        input=json.dumps(_payload(project)), capture_output=True, text=True,
        env={**os.environ, "SELVEDGE_QUIET": "1", "SELVEDGE_DB": ""},
        cwd=project,
    )
    assert proc.returncode == 0, "a non-zero exit from PreCompact can veto"
    assert proc.stdout.strip() == ""


def test_quiet_path_does_not_import_storage(project):
    """Same lazy-import discipline the gate adopted in #20."""
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys, json\n"
         "from selvedge.hooks.precompact import evaluate\n"
         "evaluate({'cwd': '/nonexistent-project-xyz', 'session_id': 's'})\n"
         "print(json.dumps(sorted(m for m in sys.modules if m.startswith('selvedge'))))"],
        capture_output=True, text=True, check=True,
        env={**os.environ, "SELVEDGE_QUIET": "1"},
    )
    loaded = set(json.loads(result.stdout.strip().splitlines()[-1]))
    assert "selvedge.storage" not in loaded, (
        f"the quiet path imported the storage layer: {sorted(loaded)}"
    )


def test_session_window_write_does_not_clobber_touched_entities(project):
    """Regression: the gate writes this file twice in one invocation.

    `record_touched_entities` runs on the watched path, then
    `_session_window_start` runs on the block path — and it used to write the
    file blind, erasing the entity list the PreCompact reminder depends on.
    Caught by an end-to-end run, not by either hook's own unit tests, because
    each was correct in isolation.
    """
    from datetime import datetime, timezone

    state_dir = project / ".selvedge" / "hook_sessions"
    pretooluse.record_touched_entities(state_dir, "sess-1", ["migrations/0001.sql"])
    pretooluse._session_window_start(state_dir, "sess-1", datetime.now(timezone.utc))

    assert pretooluse.read_touched_entities(state_dir, "sess-1") == [
        "migrations/0001.sql"
    ]


def test_gate_then_precompact_end_to_end(project):
    """The real sequence: a blocked edit, then compaction wants a reminder."""
    SelvedgeStorage(project / ".selvedge" / "selvedge.db").log_event_batch([
        ChangeEvent(entity_path="users.sso_token", change_type="add",
                    timestamp="2026-01-01T00:00:00Z", reasoning="Tried it."),
        ChangeEvent(entity_path="users.sso_token", change_type="remove",
                    timestamp="2026-01-02T00:00:00Z", reasoning="Reverted it."),
    ])

    decision = pretooluse.evaluate({
        "session_id": "sess-1", "cwd": str(project), "tool_name": "Edit",
        "tool_input": {"file_path": "migrations/003.sql",
                       "new_string": "ALTER TABLE users ADD COLUMN sso_token TEXT;"},
    })
    assert decision.action == "block"

    reminder = hook.evaluate(_payload(project))
    assert "migrations/003.sql" in reminder
