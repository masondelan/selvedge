"""Exercise attribution and trust boundaries against real temporary stores."""

import json
import runpy
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

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
    ],
)
def test_action_main_trust_checks(tmp_path, monkeypatch, failure):
    """Run the actual comment orchestration, substituting only GitHub and git IO."""
    db = tmp_path / "store.db"
    store = SelvedgeStorage(db)
    store.log_event(
        ChangeEvent(entity_path="src/a.py", change_type="reject", reasoning="A recorded rejection")
    )
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
        "SELVEDGE_REVIEW_DRY_RUN": "true",
    }.items():
        monkeypatch.setenv(key, value)

    class API(FakeAPI):
        reads = 0

        def request(self, path, data=None, method="GET"):
            self.reads += 1
            if data is not None:
                raise AssertionError("dry run must not write")
            return {
                "state": "open",
                "base": {"sha": base},
                "head": {"sha": "c" * 40 if failure == "head-moved" and self.reads > 1 else head},
                "changed_files": 2
                if failure == "partial-files"
                else (0 if failure == "empty-files" else 1),
            }

        def pages(self, path, max_pages):
            return [] if failure == "empty-files" else [{"filename": "src/a.py"}]

    if failure in ("wal", "shm"):
        Path(str(db) + "-" + failure).write_text("sidecar")
    if failure == "bad-limit":
        monkeypatch.setenv("SELVEDGE_REVIEW_LIMIT", "bad")
    api = API([])
    namespace = ACTION["main"].__globals__
    monkeypatch.setitem(namespace, "GitHub", lambda repo, token: api)

    def git(args, **kwargs):
        if "rev-parse" in args:
            return ("c" * 40 if failure == "wrong-base" else base) + "\n"
        return b"different" if failure == "modified-db" else db.read_bytes()

    monkeypatch.setattr(namespace["subprocess"], "check_output", git)
    if failure:
        with pytest.raises(ValueError):
            ACTION["main"]()
    else:
        ACTION["main"]()
        assert "A recorded rejection" in summary.read_text()
        assert base in summary.read_text() and head in summary.read_text()
