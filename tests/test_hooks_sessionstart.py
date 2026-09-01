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
    # With SELVEDGE_DB unset, the hook's `payload.get("cwd") or os.getcwd()`
    # fallback walks UP from the process cwd — run from the repo root, a
    # payload with no cwd (the malformed-payload tests) would resolve the
    # maintainer's real dogfood store. chdir to tmp so the walk finds nothing.
    monkeypatch.chdir(tmp_path)
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


def test_wire_stdout_is_strict_json_even_with_hostile_reasoning(project):
    """Wire-path stdout is empty or strictly parseable JSON — nothing in between.

    Claude Code v2.1.248 turned a stdout ``{…}`` object that fails to parse
    from a silent plain-text fallback into a hook error carrying the parse
    message. The digest embeds stored reasoning verbatim, and reasoning is
    agent-authored free text, so a reason string full of braces, quotes,
    newlines, control characters and even a literal embedded JSON object must
    still leave the envelope strictly valid — the ``json.dumps`` wrapper is the
    thing that guarantees it. This is the exact regression that would be
    invisible on any Claude Code older than 2.1.248 (a stray ``print`` or an
    f-string swapped in for ``json.dumps``) yet break every user on a newer
    one.
    """
    hostile = (
        'REVERTED } { "quoted" \n newline \t tab \\ backslash '
        '— em-dash \x01 ctrl {"decision":"block","nested":[1,2]} \U0001f9f5'
    )
    st = _storage(project)
    st.log_event(ChangeEvent(entity_path="users.sso_token", change_type="add",
                             timestamp="2026-01-01T00:00:00Z",
                             reasoning="Tried a dedicated SSO token column."))
    st.log_event(ChangeEvent(entity_path="users.sso_token", change_type="remove",
                             timestamp="2026-01-02T00:00:00Z",
                             reasoning=hostile))

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = hook.run([], stdin=json.dumps(_payload(project)))
    out = buf.getvalue()

    assert code == 0
    assert out.strip(), "expected a digest for the seeded revert"
    assert out.lstrip().startswith("{")
    # Must not raise — this is the v2.1.248 contract the hook has to satisfy.
    payload = json.loads(out)
    assert hostile in payload["hookSpecificOutput"]["additionalContext"]


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


# ---------------------------------------------------------------------------
# Phase 2.17 regression fixture — seeded store, capped render, supersede race
# ---------------------------------------------------------------------------


def test_seeded_rejection_names_the_dead_path(project):
    """First arm of the fixture: the dead path is named in the digest.

    Seeded with the v0.3.11 `reject` type — a rejection is a standing
    negative verdict and must surface exactly like a revert does.
    """
    _storage(project).log_event(ChangeEvent(
        entity_path="payments.retry_queue", change_type="reject",
        timestamp="2026-01-05T00:00:00Z",
        reasoning="Rejected: at-least-once delivery already covers retries."))
    digest = hook.evaluate(_payload(project))
    assert "payments.retry_queue" in digest
    assert "REVERTED" in digest
    assert "at-least-once delivery" in digest


def test_byte_cap_cuts_the_rendered_digest_on_a_line_boundary(project, monkeypatch):
    """Second arm: digest_max_bytes holds against the REAL digest.

    `test_cap_cuts_on_a_line_boundary` proves it for synthetic text; this
    pins it end-to-end — the capped render is a whole-line prefix of the
    uncapped one, never a mid-line clip.
    """
    st = _storage(project)
    for i in range(8):
        st.log_event(ChangeEvent(
            entity_path=f"users.col{i}", change_type="add",
            timestamp=f"2026-01-0{i + 1}T00:00:00Z",
            reasoning="tried " + "x" * 80))
        st.log_event(ChangeEvent(
            entity_path=f"users.col{i}", change_type="revert",
            timestamp=f"2026-02-0{i + 1}T00:00:00Z",
            reasoning="reverted " + "y" * 80))

    full = hook.evaluate(_payload(project))
    assert full
    cap = len(full.encode("utf-8")) // 2
    monkeypatch.setenv("SELVEDGE_DIGEST_MAX_BYTES", str(cap))
    capped = hook.evaluate(_payload(project))

    assert 0 < len(capped.encode("utf-8")) <= cap
    assert len(capped) < len(full)
    assert full.startswith(capped), "the cap must keep a prefix, unreordered"
    assert full[len(capped)] == "\n", "the cut must land on a line boundary"


def test_supersede_between_seed_and_render_leaves_one_effective_verdict(project):
    """Third arm: the digest-boundary race, made deterministic.

    Two dead paths are seeded; a supersede for one lands after the seed but
    before the render. The digest must name exactly one effective verdict —
    the still-standing one — and drop the re-opened path entirely.
    """
    _seed_reverted(project, path="users.sso_token")
    st = _storage(project)
    st.log_event(ChangeEvent(
        entity_path="billing.tax_rate", change_type="add",
        timestamp="2026-01-03T00:00:00Z",
        reasoning="Tried a column-level tax rate."))
    st.log_event(ChangeEvent(
        entity_path="billing.tax_rate", change_type="revert",
        timestamp="2026-01-04T00:00:00Z",
        reasoning="Reverted: rates live in the tax service."))
    # The race, replayed as a fixture: the supersede lands between seed and
    # render.
    st.log_event(ChangeEvent(
        entity_path="users.sso_token", change_type="supersede",
        timestamp="2026-03-01T00:00:00Z",
        reasoning="Constraint lifted; SSO tokens re-opened."))

    digest = hook.evaluate(_payload(project))
    assert "billing.tax_rate" in digest, "the standing verdict must surface"
    assert digest.count("users.sso_token") == 0, (
        "a superseded verdict must not appear even once — one effective "
        "verdict, not a reverted row plus a supersede footnote"
    )


# ---------------------------------------------------------------------------
# stale_when presentation — matched condition reads as *re-examine*
# ---------------------------------------------------------------------------


def _seed_matched_stale_when(project):
    """A rejection with a stale_when condition, then a later change that
    matches it via the v0.3.8 keyword-overlap surfacing."""
    st = _storage(project)
    st.log_event(ChangeEvent(
        entity_path="billing.provider_fee", change_type="reject",
        timestamp="2026-01-01T00:00:00Z",
        reasoning="Rejected a separate fee column while we stay on Stripe.",
        stale_when="stripe payment processor replaced"))
    st.log_event(ChangeEvent(
        entity_path="billing.gateway", change_type="modify",
        timestamp="2026-02-01T00:00:00Z",
        reasoning="Migrated the payment processor from stripe to adyen."))


def test_matched_stale_when_presents_as_re_examine(project):
    """A dead path whose invalidating condition has since matched reads as
    *re-examine*, not a bare warning."""
    _seed_matched_stale_when(project)
    digest = hook.evaluate(_payload(project))
    assert "billing.provider_fee" in digest
    assert "re-examine" in digest
    assert "stale_when" in digest, "the row must say WHY it is re-examine"


def test_unmatched_stale_when_stays_a_bare_reverted_row(project):
    """No later match, no re-examine marker — the condition alone changes
    nothing."""
    _storage(project).log_event(ChangeEvent(
        entity_path="billing.provider_fee", change_type="reject",
        timestamp="2026-01-01T00:00:00Z",
        reasoning="Rejected a separate fee column.",
        stale_when="stripe payment processor replaced"))
    digest = hook.evaluate(_payload(project))
    assert "billing.provider_fee" in digest
    assert "re-examine" not in digest


def test_re_examine_is_presentation_only(project):
    """The stored verdict never mutates — append-only stays append-only."""
    _seed_matched_stale_when(project)
    st = _storage(project)
    before = st.count()

    digest = hook.evaluate(_payload(project))
    assert "re-examine" in digest

    assert st.count() == before, "rendering the digest must write nothing"
    assert any(
        r["entity_path"] == "billing.provider_fee" and r["change_type"] == "reject"
        for r in st.get_reverted_entities()
    ), "the standing verdict is still the rejection; only the wording changed"


# ---------------------------------------------------------------------------
# Selection order — documented contract, pinned (no ranking system here)
# ---------------------------------------------------------------------------


def test_selection_order_due_section_most_overdue_first_capped_at_five(project):
    """Seven due decisions; the five most overdue surface, most overdue
    leading. Store growth changes WHICH five, never HOW MANY."""
    st = _storage(project)
    for i in range(7):
        path = f"svc.decision{i}"
        st.log_event(ChangeEvent(
            entity_path=path, change_type="add",
            timestamp="2020-01-01T00:00:00Z",
            revisit_after=f"2020-0{i + 2}-01",  # i=0 is the most overdue
            reasoning=f"decision number {i}"))
        st.record_tool_call("prior_attempts", entity_path=path)

    digest = hook.evaluate(_payload(project))
    surfaced = [f"svc.decision{i}" for i in range(5)]
    positions = [digest.index(p) for p in surfaced]
    assert positions == sorted(positions), "most overdue must lead"
    for dropped in ("svc.decision5", "svc.decision6"):
        assert dropped not in digest, "the cap keeps the five MOST overdue"


def test_selection_order_reverted_section_most_recent_first_capped_at_five(project):
    """Six standing reverts; the five most recent surface, newest leading."""
    st = _storage(project)
    for i in range(6):
        st.log_event(ChangeEvent(
            entity_path=f"svc.dead{i}", change_type="revert",
            timestamp=f"2026-01-0{i + 1}T00:00:00Z",
            reasoning=f"reverted path number {i}"))

    digest = hook.evaluate(_payload(project))
    assert "svc.dead0" not in digest, "the oldest revert falls off the cap"
    surfaced = [f"svc.dead{i}" for i in (5, 4, 3, 2, 1)]
    positions = [digest.index(p) for p in surfaced]
    assert positions == sorted(positions), "most recent revert must lead"
