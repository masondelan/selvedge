"""SessionStart delivery hook (v0.3.10).

The counterpart to the PreToolUse gate: the gate covers the case where there
is something to veto, this covers the far more common case where there isn't.

Contract under test: quiet when there is nothing to say, hard size cap,
read-only, fail-open, and no LLM anywhere.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from selvedge.hooks import sessionstart as hook
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


def _storage(project):
    return SelvedgeStorage(project / ".selvedge" / "selvedge.db")


def _payload(project, source="startup"):
    return {
        "session_id": "sess-1",
        "cwd": str(project),
        "hook_event_name": "SessionStart",
        "source": source,
    }


def _seed_reverted(project, path="users.sso_token"):
    st = _storage(project)
    st.log_event(ChangeEvent(entity_path=path, change_type="add",
                             timestamp="2026-01-01T00:00:00Z",
                             reasoning="Tried a dedicated SSO token column."))
    st.log_event(ChangeEvent(entity_path=path, change_type="remove",
                             timestamp="2026-01-02T00:00:00Z",
                             reasoning="Reverted: tokens belong in sessions, not users."))


# ---------------------------------------------------------------------------
# Quiet by default
# ---------------------------------------------------------------------------


def test_empty_store_emits_nothing(project):
    assert hook.evaluate(_payload(project)) == ""


def test_no_project_db_emits_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("SELVEDGE_DB", raising=False)
    bare = tmp_path / "bare"
    bare.mkdir()
    assert hook.evaluate({"cwd": str(bare), "session_id": "s"}) == ""


def test_store_with_only_active_changes_emits_nothing(project):
    """Relevance-gated: a store full of ordinary history is not a digest.

    Ungated episode injection measurably degrades good answers
    (arXiv 2607.14390), so "we have data" is not a reason to inject it.
    """
    st = _storage(project)
    for i in range(20):
        st.log_event(ChangeEvent(entity_path=f"users.col{i}", change_type="add",
                                 reasoning=f"ordinary change {i}"))
    assert hook.evaluate(_payload(project)) == ""


def test_disable_env_silences_it(project, monkeypatch):
    _seed_reverted(project)
    monkeypatch.setenv(hook.DISABLE_ENV, "1")
    assert hook.evaluate(_payload(project)) == ""


def test_digest_max_bytes_zero_disables_it(project, monkeypatch):
    _seed_reverted(project)
    monkeypatch.setenv("SELVEDGE_DIGEST_MAX_BYTES", "0")
    assert hook.evaluate(_payload(project)) == ""


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


def test_reverted_entities_are_surfaced_with_their_reasoning(project):
    _seed_reverted(project)
    digest = hook.evaluate(_payload(project))
    assert "users.sso_token" in digest
    assert "tokens belong in sessions" in digest
    assert "prior_attempts" in digest, "the digest must point at the tool"


def test_a_superseded_revert_drops_off_the_digest(project):
    """Re-opened decisions are not warnings — the trail moved on."""
    _seed_reverted(project)
    _storage(project).log_event(ChangeEvent(
        entity_path="users.sso_token", change_type="supersede",
        timestamp="2026-03-01T00:00:00Z", reasoning="Constraint lifted."))
    digest = hook.evaluate(_payload(project))
    assert "users.sso_token" not in digest


def test_decisions_due_for_revisit_are_surfaced(project):
    st = _storage(project)
    st.log_event(ChangeEvent(
        entity_path="users.email", change_type="add", revisit_after="2020-01-01",
        reasoning="Chose one email column over a contacts table."))
    st.record_tool_call("prior_attempts", entity_path="users.email")
    digest = hook.evaluate(_payload(project))
    assert "users.email" in digest
    assert "revisit" in digest.lower()


def test_digest_is_deterministic(project):
    """Same store, same string — there is no model in this path."""
    _seed_reverted(project)
    assert hook.evaluate(_payload(project)) == hook.evaluate(_payload(project))


# ---------------------------------------------------------------------------
# Size cap
# ---------------------------------------------------------------------------


def test_digest_respects_the_byte_cap(project, monkeypatch):
    st = _storage(project)
    for i in range(40):
        st.log_event(ChangeEvent(entity_path=f"t{i}.c{i}", change_type="add",
                                 reasoning="x" * 400))
        st.log_event(ChangeEvent(entity_path=f"t{i}.c{i}", change_type="remove",
                                 reasoning="y" * 400))
    monkeypatch.setenv("SELVEDGE_DIGEST_MAX_BYTES", "500")
    digest = hook.evaluate(_payload(project))
    assert 0 < len(digest.encode("utf-8")) <= 500


def test_cap_cuts_on_a_line_boundary():
    """A digest ending mid-path reads as corruption in a model's context."""
    text = "alpha\nbravo\ncharlie\ndelta"
    assert hook._cap(text, 12) == "alpha\nbravo"


# ---------------------------------------------------------------------------
# Wire behaviour
# ---------------------------------------------------------------------------


def test_run_emits_the_hook_envelope_not_bare_text(project):
    """Plain stdout is ignored by the harness — only additionalContext lands."""
    _seed_reverted(project)
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = hook.run([], stdin=json.dumps(_payload(project)))

    assert code == 0
    payload = json.loads(buf.getvalue())
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "users.sso_token" in payload["hookSpecificOutput"]["additionalContext"]


def test_run_emits_absolutely_nothing_when_quiet(project):
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = hook.run([], stdin=json.dumps(_payload(project)))
    assert code == 0
    assert buf.getvalue() == ""


@pytest.mark.parametrize("bad", ["", "not json", "[]", "null", '{"cwd": 42}'])
def test_fails_open_on_any_malformed_payload(bad, project):
    assert hook.run([], stdin=bad) == 0


def test_hook_is_read_only(project):
    """The digest must never write to the store."""
    _seed_reverted(project)
    db = project / ".selvedge" / "selvedge.db"
    before = (_storage(project).count(), db.stat().st_mtime_ns)
    hook.evaluate(_payload(project))
    after = (_storage(project).count(), db.stat().st_mtime_ns)
    assert before == after


def test_end_to_end_through_the_hooks_cli(project):
    proc = subprocess.run(
        [sys.executable, "-m", "selvedge.hooks.cli", "sessionstart"],
        input=json.dumps(_payload(project)), capture_output=True, text=True,
        env={**os.environ, "SELVEDGE_QUIET": "1", "SELVEDGE_DB": ""},
        cwd=project,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""  # empty store, nothing to say


# ---------------------------------------------------------------------------
# Latency — the new hooks must not reintroduce the tax #20 removed
# ---------------------------------------------------------------------------


def test_quiet_path_does_not_import_storage(project):
    """The nothing-to-say path must stay off the heavy imports.

    Same discipline the gate adopted in #20: this runs in a fresh process on
    every session start, and two of the three exits need no database at all.
    """
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys, json\n"
         "from selvedge.hooks.sessionstart import evaluate\n"
         "evaluate({'cwd': '/nonexistent-project-xyz', 'session_id': 's'})\n"
         "print(json.dumps(sorted(m for m in sys.modules if m.startswith('selvedge'))))"],
        capture_output=True, text=True, check=True,
        env={**os.environ, "SELVEDGE_QUIET": "1"},
    )
    loaded = set(json.loads(result.stdout.strip().splitlines()[-1]))
    assert "selvedge.storage" not in loaded, (
        f"the quiet path imported the storage layer: {sorted(loaded)}"
    )
