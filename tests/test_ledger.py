"""Exercise attribution and trust boundaries against real temporary stores."""

import json
import runpy
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from selvedge import ledger
from selvedge.cli import cli
from selvedge.ledger import read_ledger, render_ledger
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage

ACTION = runpy.run_path(str(Path(__file__).parents[1] / "scripts/review_context.py"))


def test_cross_agent_revision_resolves_beyond_limit(tmp_path):
    store = SelvedgeStorage(tmp_path / "store.db")
    old = store.log_event(
        ChangeEvent(
            entity_path="src/a.py",
            change_type="reject",
            agent="maintainer",
            session_id="one",
            reasoning="Rejected cache; stale credentials.",
            stale_when="Revocation tests pass.",
        )
    )
    new = store.log_supersede(
        "src/a.py",
        supersedes=old.id,
        agent="reviewer",
        session_id="two",
        reasoning="Revocation regression now passes.",
    )
    report = read_ledger(store.db_path, ["src/a.py"], limit=1)
    assert report["shown"] == 1 and report["omitted"] == 1
    assert report["agents"] == {"maintainer": 1, "reviewer": 1}
    event = report["events"][0]
    assert event["id"] == new.id and event["cross_agent_revision"]
    assert event["revises_agent"] == "maintainer" and event["session_id"] == "two"
    full = read_ledger(store.db_path, ["src/a.py"])
    assert full["events"][1]["superseded_by"] == [new.id]
    assert "Cross-agent revision" in render_ledger(full)


def test_dotted_entities_literal_wildcards_and_missing_history(tmp_path):
    store = SelvedgeStorage(tmp_path / "store.db")
    for path in ["src/a_b.py", "src/a_b.py.fn", "src/axb.py", "private/ops"]:
        store.log_event(
            ChangeEvent(entity_path=path, change_type="modify", reasoning="Recorded rationale")
        )
    report = read_ledger(store.db_path, ["./src/a_b.py"])
    assert report["total_matching"] == 2
    assert {e["entity_path"] for e in report["events"]} == {"src/a_b.py", "src/a_b.py.fn"}
    assert "Missing history is not approval" in render_ledger(
        read_ledger(store.db_path, ["missing"])
    )
    # More paths than SQLite's expression-depth limit still works.
    assert read_ledger(store.db_path, [f"src/{i}.py" for i in range(1100)])["total_matching"] == 0


def test_report_does_not_create_missing_db(tmp_path):
    db = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        read_ledger(db, [])
    assert not db.exists()


def test_tampering_and_coverage_are_distinct(tmp_path):
    store = SelvedgeStorage(tmp_path / "store.db")
    event = store.log_event(
        ChangeEvent(entity_path="a", change_type="reject", reasoning="Original reason")
    )
    before = store.db_path.read_bytes()
    assert read_ledger(store.db_path, ["a"])["chain"]["intact"]
    assert store.db_path.read_bytes() == before
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE events SET reasoning='edited' WHERE id=?", (event.id,))
    report = read_ledger(store.db_path, ["a"])
    assert not report["chain"]["intact"]
    assert "**FAIL**" in render_ledger(report)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DROP TABLE event_chain")
    report = read_ledger(store.db_path, ["a"])
    assert report["chain_coverage"]["unchained"] == 1
    assert "**UNAVAILABLE**" in render_ledger(report)


def test_untrusted_text_and_size_are_bounded(tmp_path):
    store = SelvedgeStorage(tmp_path / "store.db")
    for _i in range(30):
        store.log_event(
            ChangeEvent(
                entity_path="a",
                change_type="modify",
                agent="@everyone",
                reasoning="</details><script>x</script> [click](https://evil.invalid) " * 50,
            )
        )
    report = read_ledger(store.db_path, ["a"])
    rendered = render_ledger(report, max_chars=4000)
    assert "<script>" not in rendered and "@everyone" not in rendered
    assert "[click](" not in rendered and len(rendered) <= 4000
    assert "Report size limit omitted" in rendered
    assert rendered == render_ledger(report, max_chars=4000)


def test_cli_json_and_integrity_exit(tmp_path, monkeypatch):
    db = tmp_path / "store.db"
    monkeypatch.setenv("SELVEDGE_DB", str(db))
    store = SelvedgeStorage(db)
    store.log_event(
        ChangeEvent(entity_path="a", change_type="modify", reasoning="The actual reason")
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["ledger", "--entity", "a", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["events"][0]["reasoning"] == "The actual reason"
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE events SET reasoning='tampered'")
    result = runner.invoke(cli, ["ledger", "--json"])
    assert result.exit_code == 1 and not json.loads(result.output)["chain"]["intact"]


class FakeAPI:
    def __init__(self, comments):
        self.comments = comments
        self.writes = []

    def pages(self, path, max_pages):
        return self.comments

    def request(self, path, data, method):
        self.writes.append((path, data, method))


def test_comment_update_ignores_forged_human_marker():
    marker = ACTION["MARKER"] + "\n"
    human = {"id": 1, "body": marker + "human", "user": {"login": "human", "type": "User"}}
    api = FakeAPI([human])
    assert ACTION["upsert"](api, 7, marker + "new") == "created"
    assert api.writes[0][2] == "POST"
    bot = {"id": 2, "body": marker + "old", "user": {"login": "github-actions[bot]", "type": "Bot"}}
    api = FakeAPI([human, bot])
    assert ACTION["upsert"](api, 7, marker + "new") == "updated"
    assert api.writes[0][0] == "/issues/comments/2"
    api = FakeAPI([{**bot, "body": marker + "new"}])
    assert ACTION["upsert"](api, 7, marker + "new") == "unchanged"
    assert not api.writes


def test_rename_and_deleted_file_mapping():
    assert ACTION["touched_paths"](
        [{"filename": "new.py", "previous_filename": "old.py"}, {"filename": "deleted.py"}]
    ) == ["deleted.py", "new.py", "old.py"]


def test_action_refuses_untrusted_event(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    with pytest.raises(ValueError, match="base-only"):
        ACTION["main"]()


@pytest.mark.parametrize(
    "failure",
    [
        "",
        "modified-db",
        "wrong-base",
        "partial-files",
        "head-moved",
        "empty-files",
        "wal",
        "shm",
        "bad-limit",
        "bad-budget",
        "no-history",
        "no-history-existing-comment",
        "no-history-broken-chain",
    ],
)
def test_action_main_trust_checks(tmp_path, monkeypatch, failure):
    """Run the actual comment orchestration, substituting only GitHub and git IO."""
    db = tmp_path / "store.db"
    store = SelvedgeStorage(db)
    store.log_event(
        ChangeEvent(entity_path="src/a.py", change_type="reject", reasoning="A recorded rejection")
    )
    no_history = failure.startswith("no-history")
    if failure == "no-history-broken-chain":
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE events SET reasoning='tampered'")
        conn.close()
    base, head = "a" * 40, "b" * 40
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"number": 7}))
    summary = tmp_path / "summary.md"
    for key, value in {
        "GITHUB_EVENT_NAME": "pull_request_target",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_WORKSPACE": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(summary),
        "SELVEDGE_REVIEW_TOKEN": "test",
        "SELVEDGE_REVIEW_DB": "store.db",
        "SELVEDGE_REVIEW_DRY_RUN": "false" if no_history else "true",
    }.items():
        monkeypatch.setenv(key, value)

    class API(FakeAPI):
        reads = 0

        def request(self, path, data=None, method="GET"):
            self.reads += 1
            if data is not None:
                assert no_history, "dry run must not write"
                self.writes.append((path, data, method))
                return {}
            return {
                "state": "open",
                "base": {"sha": base},
                "head": {"sha": "c" * 40 if failure == "head-moved" and self.reads > 1 else head},
                "changed_files": 2
                if failure == "partial-files"
                else (0 if failure == "empty-files" else 1),
            }

        def pages(self, path, max_pages):
            if path.endswith("/comments"):
                return self.comments
            return [] if failure == "empty-files" else [
                {"filename": "src/unrecorded.py" if no_history else "src/a.py"}
            ]

    if failure in ("wal", "shm"):
        Path(str(db) + "-" + failure).write_text("sidecar")
    if failure == "bad-limit":
        monkeypatch.setenv("SELVEDGE_REVIEW_LIMIT", "bad")
    if failure == "bad-budget":
        monkeypatch.setenv("SELVEDGE_REVIEW_BUDGET", "70000")
    comments = []
    if failure == "no-history-existing-comment":
        comments.append({
            "id": 2,
            "body": ACTION["MARKER"] + "\nold revision context",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        })
    api = API(comments)
    namespace = ACTION["main"].__globals__
    monkeypatch.setitem(namespace, "GitHub", lambda repo, token: api)

    def git(args, **kwargs):
        if "rev-parse" in args:
            return ("c" * 40 if failure == "wrong-base" else base) + "\n"
        return b"different" if failure == "modified-db" else db.read_bytes()

    monkeypatch.setattr(namespace["subprocess"], "check_output", git)
    if failure == "no-history-broken-chain":
        with pytest.raises(SystemExit, match="verification failed"):
            ACTION["main"]()
        assert len(api.writes) == 1 and api.writes[0][2] == "POST"
        assert "**FAIL**" in api.writes[0][1]["body"]
    elif no_history:
        ACTION["main"]()
        assert "Missing history is not approval" in summary.read_text()
        assert base in summary.read_text() and head in summary.read_text()
        if comments:
            assert len(api.writes) == 1 and api.writes[0][2] == "PATCH"
            assert "Missing history is not approval" in api.writes[0][1]["body"]
            assert "old revision context" not in api.writes[0][1]["body"]
        else:
            assert not api.writes
    elif failure:
        with pytest.raises(ValueError):
            ACTION["main"]()
    else:
        ACTION["main"]()
        assert "A recorded rejection" in summary.read_text()
        assert base in summary.read_text() and head in summary.read_text()


# --- Phase 2.26 compact review context -------------------------------------

BASE, HEAD = "a" * 40, "b" * 40


def _compact(store, paths, **kwargs):
    report = read_ledger(store.db_path, paths, kwargs.pop("limit", 100))
    return report, ACTION["compose"](report, BASE, HEAD, **kwargs)


def test_compact_context_puts_decision_evidence_before_metadata(tmp_path):
    """Relevant + superseded: reasons, constraints and revisions lead."""
    store = SelvedgeStorage(tmp_path / "store.db")
    rejected = store.log_event(
        ChangeEvent(
            entity_path="src/a.py",
            change_type="reject",
            agent="maintainer",
            reasoning="Rejected token cache.",
            constraint="No plaintext credentials at rest.",
            expires_when="date:2027-01-01",
        )
    )
    store.log_supersede(
        "src/a.py", supersedes=rejected.id, agent="reviewer", reasoning="Vault rollout finished."
    )
    store.log_event(
        ChangeEvent(entity_path="src/a.py", change_type="modify", agent="maintainer", reasoning="Tidy")
    )
    store.log_event(
        ChangeEvent(entity_path="src/unrelated.py", change_type="reject", reasoning="Irrelevant")
    )
    report, body = _compact(store, ["src/a.py"], repo="o/r", db_path="store.db")
    assert body.startswith(ACTION["MARKER"] + "\n") and BASE in body and HEAD in body
    assert "Irrelevant" not in body and report["total_matching"] == 3
    evidence, routine = body.index("### Decision evidence"), body.index("### Other recorded changes")
    assert evidence < body.index("Vault rollout finished") < body.index("Rejected token cache")
    assert body.index("Rejected token cache") < routine < body.index("Tidy")
    block = body[body.index("**Rejected approach**") : routine]
    assert block.index("Rejected token cache") < block.index("Constraint: No plaintext")
    assert block.index("Constraint:") < block.index("Status: superseded") < block.index("Recorded ")
    assert "Expiry condition (not evaluated here)" in block
    assert "Cross-agent revision: maintainer → reviewer." in body
    assert "Not evaluated: correctness, semantic conflicts" in body
    assert "omitted" not in body.split("Showing")[1]
    assert body == _compact(store, ["src/a.py"], repo="o/r", db_path="store.db")[1]


def test_compact_context_empty_history_is_explicitly_unknown(tmp_path):
    store = SelvedgeStorage(tmp_path / "store.db")
    store.log_event(ChangeEvent(entity_path="src/other.py", change_type="reject", reasoning="x"))
    report, body = _compact(store, ["src/a.py"])
    assert report["total_matching"] == 0
    assert "No matching decisions recorded." in body and "Missing history is not approval" in body
    assert "### " not in body.split("## Selvedge decision context")[1]
    assert "Showing 0 of 0 matching events within a 6000-character budget." in body


def test_compact_context_oversized_stays_within_budget(tmp_path):
    store = SelvedgeStorage(tmp_path / "store.db")
    store.log_event(
        ChangeEvent(entity_path="src/a.py", change_type="revert", reasoning="Old rollback reason")
    )
    for i in range(150):
        store.log_event(
            ChangeEvent(entity_path="src/a.py", change_type="modify", reasoning=f"routine {i} " * 40)
        )
    report, body = _compact(store, ["src/a.py"], limit=150, budget=2000)
    assert len(body) <= 2000 and report["omitted"] == 1
    # The rollback is the oldest row, beyond the event limit: counted, not shown.
    # Once the limit selects it, evidence is listed before newer routine rows.
    assert "Old rollback reason" not in body
    shown = body.count("\n- **")
    assert f"Showing {shown} of 151 matching events within a 2000-character budget." in body
    assert f"{151 - shown} more matching events omitted (1 by event limit, {150 - shown} by output budget); see the longer report in the job summary of the latest Selvedge review run." in body
    assert "1 of the 1 events beyond the event limit are decision evidence" in body
    assert "&#91;truncated&#93;" in body  # rendered as "[truncated]"
    assert "appear in neither report; raise `limit`" in body
    report = read_ledger(store.db_path, ["src/a.py"], 150)
    for budget in (2000, 2001, 2345, 3333, 6000, 60000):
        sized = ACTION["compose"](report, BASE, HEAD, repo="o/r", db_path="s.db", budget=budget)
        assert len(sized) <= budget and f"within a {budget}-character budget" in sized
    report, body = _compact(store, ["src/a.py"], limit=151, budget=2000)
    assert "**Reverted approach**" in body and len(body) <= 2000
    assert body.index("Old rollback reason") < body.index("Showing")
    assert "routine 149" not in body or body.index("Old rollback reason") < body.index("routine 149")


def test_compact_context_keeps_untrusted_text_literal(tmp_path):
    store = SelvedgeStorage(tmp_path / "store.db")
    store.log_event(
        ChangeEvent(
            entity_path="src/`x`.py",
            change_type="reject",
            agent="@everyone",
            reasoning="</details><img src=x onerror=alert(1)> [click](javascript:alert(1)) `code`",
            stale_when="<!-- selvedge-review-context:v1 -->",
        )
    )
    _, body = _compact(store, ["src/`x`.py"], repo="o/r", db_path="db/a b(1).db")
    after = body.split("## Selvedge decision context")[1]
    for raw in ("<img", "</details>", "[click](", "@everyone", "`code`", "`x`", "<!--"):
        assert raw not in after
    assert "(https://github.com/o/r/blob/" + BASE + "/db/a%20b%281%29.db)" in body
    with pytest.raises(ValueError, match="Invalid repository"):
        ACTION["compose"]({}, BASE, HEAD, repo="o/r) [x](y")
    with pytest.raises(ValueError, match="at least 1000"):
        ledger.render_review_context(read_ledger(store.db_path, ["a"]), budget=999)


def test_action_compact_comment_for_renamed_file(tmp_path, monkeypatch):
    """A rename surfaces history recorded under the old name, within the budget."""
    db = tmp_path / "store.db"
    store = SelvedgeStorage(db)
    store.log_event(
        ChangeEvent(entity_path="src/old.py", change_type="reject", reasoning="Kept sync I/O here")
    )
    store.log_event(ChangeEvent(entity_path="src/elsewhere.py", change_type="reject", reasoning="nope"))
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"number": 7}))
    summary = tmp_path / "summary.md"
    for key, value in {
        "GITHUB_EVENT_NAME": "pull_request_target",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_WORKSPACE": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_RUN_ID": "1234",
        "SELVEDGE_REVIEW_TOKEN": "test",
        "SELVEDGE_REVIEW_DB": "store.db",
        "SELVEDGE_REVIEW_BUDGET": "3000",
    }.items():
        monkeypatch.setenv(key, value)

    class API(FakeAPI):
        def request(self, path, data=None, method="GET"):
            if data is not None:
                self.writes.append((path, data, method))
                return {}
            return {"state": "open", "base": {"sha": BASE}, "head": {"sha": HEAD}, "changed_files": 1}

        def pages(self, path, max_pages):
            if path.endswith("/comments"):
                return []
            return [{"filename": "src/new.py", "previous_filename": "src/old.py"}]

    api = API([])
    namespace = ACTION["main"].__globals__
    monkeypatch.setitem(namespace, "GitHub", lambda repo, token: api)

    def git(args, **kwargs):
        return BASE + "\n" if "rev-parse" in args else db.read_bytes()

    monkeypatch.setattr(namespace["subprocess"], "check_output", git)
    ACTION["main"]()
    assert len(api.writes) == 1 and api.writes[0][2] == "POST"
    body = api.writes[0][1]["body"]
    assert len(body) <= 3000 and "Kept sync I&#47;O here" in body and "nope" not in body
    assert "src&#47;old&#46;py" in body and "matched against 2 touched path(s)" in body
    assert f"(https://github.com/owner/repo/blob/{BASE}/store.db)" in body
    assert "within a 3000-character budget" in body
    text = summary.read_text()
    assert text.startswith(body) and "Event: " in text.split("\n---\n", 1)[1]


# --- Phase 2.26 cross-model review remediation ------------------------------


def test_upsert_is_noop_for_same_report_with_new_run_id(tmp_path, monkeypatch):
    """Finding 1: a re-run with a different GITHUB_RUN_ID writes nothing."""
    store = SelvedgeStorage(tmp_path / "store.db")
    for i in range(30):
        store.log_event(
            ChangeEvent(entity_path="src/a.py", change_type="modify", reasoning=f"r{i} " * 60)
        )
    report = read_ledger(store.db_path, ["src/a.py"], 20)
    assert report["omitted"] == 10  # omission line (formerly carrying the run link) is present
    bodies = []
    for run_id in ("1", "2"):
        monkeypatch.setenv("GITHUB_RUN_ID", run_id)
        bodies.append(ACTION["compose"](report, BASE, HEAD, repo="o/r", db_path="s.db", budget=2000))
    assert bodies[0] == bodies[1] and "actions/runs" not in bodies[0]
    assert "more matching events omitted" in bodies[0]
    existing = {
        "id": 5,
        "body": bodies[0],
        "user": {"login": "github-actions[bot]", "type": "Bot"},
    }
    api = FakeAPI([existing])
    assert ACTION["upsert"](api, 7, bodies[1]) == "unchanged"
    assert not api.writes


def test_evidence_beyond_event_limit_is_counted(tmp_path):
    """Finding 2: hidden rejects/reverts/conditions/superseded rows are counted."""
    store = SelvedgeStorage(tmp_path / "store.db")
    old = store.log_event(ChangeEvent(entity_path="src/a.py", change_type="add", reasoning="v1"))
    store.log_event(ChangeEvent(entity_path="src/a.py", change_type="reject", reasoning="no"))
    store.log_event(
        ChangeEvent(entity_path="src/a.py", change_type="modify", revisit_after="2027-01-01")
    )
    store.log_event(ChangeEvent(entity_path="src/a.py", change_type="modify", reasoning="plain"))
    store.log_supersede("src/a.py", supersedes=old.id, reasoning="moved")
    for i in range(5):
        store.log_event(ChangeEvent(entity_path="src/a.py", change_type="modify", reasoning=f"n{i}"))
    report = read_ledger(store.db_path, ["src/a.py"], 5)
    assert report["omitted"] == 5 and report["evidence_omitted"] == 4
    body = ledger.render_review_context(report)
    assert "4 of the 5 events beyond the event limit are decision evidence" in body
    assert read_ledger(store.db_path, ["src/a.py"], 100)["evidence_omitted"] == 0


def test_revisit_after_only_event_is_decision_evidence(tmp_path):
    """Finding 3: a bare revisit_after groups under Decision evidence."""
    store = SelvedgeStorage(tmp_path / "store.db")
    store.log_event(
        ChangeEvent(
            entity_path="src/a.py",
            change_type="modify",
            reasoning="Pinned retry count",
            revisit_after="2027-03-01",
        )
    )
    store.log_event(ChangeEvent(entity_path="src/a.py", change_type="modify", reasoning="Tidy"))
    _, body = _compact(store, ["src/a.py"])
    evidence, routine = body.index("### Decision evidence"), body.index("### Other recorded changes")
    assert evidence < body.index("Pinned retry count") < routine < body.index("Tidy")
    assert "Revisit after (not evaluated here)" in body[evidence:routine]


def test_field_cap_and_head_overflow_stay_within_budget(tmp_path):
    """Finding 4: every field honors the 300 cap; a long db-path cannot overflow."""
    store = SelvedgeStorage(tmp_path / "store.db")
    store.log_event(
        ChangeEvent(entity_path="src/a.py", change_type="reject", agent="x" * 800, reasoning="Why")
    )
    report = read_ledger(store.db_path, ["src/a.py"])
    block = "\n".join(ledger._compact_block(report["events"][0], 300))
    assert "x" * 301 not in block and "x" * 300 in block
    long_path = "d/" * 2000 + "store.db"
    body = ACTION["compose"](report, BASE, HEAD, repo="o/r", db_path=long_path, budget=2000)
    assert len(body) <= 2000 and "link omitted (too long for the output budget)" in body
    assert "d/d/d/d/" not in body and "**Rejected approach**" in body
    with pytest.raises(ValueError, match="too small"):
        ledger.render_review_context(report, budget=2000, prefix_chars=990, details="y" * 200)
