"""Tests for Selvedge ↔ Agent Trace v0.1.0 interop.

Covers the pure converter (selvedge.exporters.agent_trace) and the CLI
surface (`selvedge export --format agent-trace`, `selvedge import
--format agent-trace`). Never touches the real DB — CLI tests use the
SELVEDGE_DB env override into a tmp_path.
"""

import json
import uuid
from pathlib import Path

import pytest
from click.testing import CliRunner

from selvedge.cli import cli
from selvedge.config import get_db_path
from selvedge.exporters.agent_trace import (
    AGENT_TRACE_VERSION,
    SELVEDGE_NS,
    event_to_trace_record,
    events_to_trace_records,
    export_preamble,
    extract_line_ranges,
    load_trace_records,
    trace_record_to_event,
    trace_record_to_events,
    validate_trace_record,
)
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage


def make_event(**overrides) -> dict:
    """A ChangeEvent-shaped dict with sensible defaults, overridable per test."""
    base = {
        "id": str(uuid.uuid4()),
        "timestamp": "2026-06-22T10:00:00Z",
        "entity_type": "function",
        "entity_path": "src/auth.py::login",
        "change_type": "modify",
        "diff": "@@ -40,3 +42,5 @@\n+a\n+b",
        "reasoning": "Add a 2FA gate to the login path.",
        "agent": "claude-code",
        "session_id": "session-1",
        "git_commit": "abc1234",
        "project": "api",
        "changeset_id": "add-2fa",
        "metadata": '{"pr": 7}',
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# line-range extraction
# ---------------------------------------------------------------------------


def test_extract_single_hunk():
    assert extract_line_ranges("@@ -40,3 +42,5 @@\n+x") == [{"start_line": 42, "end_line": 46}]


def test_extract_multiple_hunks():
    diff = "@@ -1,2 +1,3 @@\n+a\n@@ -10,1 +12,2 @@\n+b"
    assert extract_line_ranges(diff) == [
        {"start_line": 1, "end_line": 3},
        {"start_line": 12, "end_line": 13},
    ]


def test_extract_single_line_hunk_no_count():
    # "+5" with no count means a 1-line range.
    assert extract_line_ranges("@@ -5 +5 @@\n+x") == [{"start_line": 5, "end_line": 5}]


def test_extract_no_hunks():
    assert extract_line_ranges("CREATE TABLE users (id INT);") == []
    assert extract_line_ranges("") == []


# ---------------------------------------------------------------------------
# ChangeEvent -> Agent Trace
# ---------------------------------------------------------------------------


def test_file_event_maps_to_files_with_ranges():
    rec = event_to_trace_record(make_event(), tool_version="0.3.9")
    assert rec["version"] == AGENT_TRACE_VERSION
    assert rec["tool"] == {"name": "selvedge", "version": "0.3.9"}
    assert rec["vcs"] == {"type": "git", "revision": "abc1234"}
    assert len(rec["files"]) == 1
    f = rec["files"][0]
    assert f["path"] == "src/auth.py"  # ::login stripped
    conv = f["conversations"][0]
    assert conv["contributor"] == {"type": "ai"}
    assert conv["ranges"] == [{"start_line": 42, "end_line": 46}]
    assert conv["related"] == [{"type": "changeset", "url": "urn:selvedge:changeset:add-2fa"}]
    sv = rec["metadata"][SELVEDGE_NS]
    assert sv["entity"] == "src/auth.py::login"
    assert sv["reasoning"] == "Add a 2FA gate to the login path."
    assert sv["range_unknown"] is False
    assert sv["metadata"] == {"pr": 7}


def test_non_file_event_has_empty_files_and_metadata():
    ev = make_event(entity_type="column", entity_path="users.email", diff="", changeset_id="")
    rec = event_to_trace_record(ev)
    assert rec["files"] == []
    sv = rec["metadata"][SELVEDGE_NS]
    assert sv["entity"] == "users.email"
    assert sv["entity_type"] == "column"
    assert sv["range_unknown"] is True


def test_contributor_unknown_when_no_agent():
    rec = event_to_trace_record(make_event(agent=""))
    assert rec["files"][0]["conversations"][0]["contributor"] == {"type": "unknown"}


def test_no_vcs_without_commit():
    rec = event_to_trace_record(make_event(git_commit=""))
    assert "vcs" not in rec


def test_non_uuid_id_becomes_deterministic_uuid():
    rec1 = event_to_trace_record(make_event(id="not-a-uuid"))
    rec2 = event_to_trace_record(make_event(id="not-a-uuid"))
    uuid.UUID(rec1["id"])  # parses
    assert rec1["id"] == rec2["id"]  # deterministic


# ---------------------------------------------------------------------------
# validation + vendored schema
# ---------------------------------------------------------------------------


def test_emitted_records_pass_validation():
    for ev in (make_event(), make_event(entity_type="column", entity_path="users.email", diff="")):
        assert validate_trace_record(event_to_trace_record(ev)) == []


def test_validation_catches_bad_records():
    assert "version must be '0.1.0'" in " ".join(validate_trace_record({"version": "9.9.9"}))
    bad = event_to_trace_record(make_event())
    bad["files"][0]["conversations"][0]["ranges"][0]["start_line"] = 0
    problems = validate_trace_record(bad)
    assert any("start_line" in p for p in problems)
    assert validate_trace_record("not a dict") == ["record is not an object"]


def test_vendored_schema_matches_validator_required_fields():
    schema_path = Path(__file__).resolve().parent.parent / "selvedge" / "exporters" / "agent_trace_schema.json"
    schema = json.loads(schema_path.read_text())
    assert schema["required"] == ["version", "id", "timestamp", "files"]
    # A record missing a top-level required field fails our validator too.
    rec = event_to_trace_record(make_event())
    del rec["timestamp"]
    assert any("timestamp" in p for p in validate_trace_record(rec))


# ---------------------------------------------------------------------------
# round-trip (Agent Trace -> ChangeEvent)
# ---------------------------------------------------------------------------


def test_roundtrip_file_event():
    ev = make_event()
    back = trace_record_to_event(event_to_trace_record(ev))
    for key in ("entity_path", "change_type", "reasoning", "agent", "session_id",
                "project", "changeset_id", "entity_type"):
        assert back[key] == ev[key], key
    assert back["git_commit"] == ev["git_commit"]


def test_roundtrip_non_file_entity_preserved():
    ev = make_event(entity_type="column", entity_path="users.email", diff="")
    back = trace_record_to_event(event_to_trace_record(ev))
    assert back["entity_path"] == "users.email"
    assert back["entity_type"] == "column"
    assert back["change_type"] == ev["change_type"]
    assert back["reasoning"] == ev["reasoning"]
    # Reconstructable as a real ChangeEvent.
    rebuilt = ChangeEvent(**back)
    assert rebuilt.entity_path == "users.email"


def test_foreign_record_imports_best_effort():
    foreign = {
        "version": "0.1.0",
        "id": str(uuid.uuid4()),
        "timestamp": "2026-06-22T10:00:00Z",
        "files": [{"path": "src/x.ts", "conversations": [
            {"contributor": {"type": "ai", "model_id": "openai/gpt-4o"}, "ranges": []}
        ]}],
    }
    ev = trace_record_to_event(foreign)
    assert ev is not None
    assert ev["entity_path"] == "src/x.ts"
    assert ev["change_type"] == "modify"
    assert ev["reasoning"] == ""
    assert ev["agent"] == "ai"
    ChangeEvent(**ev)  # constructs without error


def test_record_with_no_usable_entity_is_skipped():
    assert trace_record_to_event({"version": "0.1.0", "id": str(uuid.uuid4()),
                                  "timestamp": "2026-06-22T10:00:00Z", "files": []}) is None


def test_reasoning_quality_passthrough():
    # Weak/empty reasoning is preserved verbatim, not "fixed".
    for reasoning in ("", "fix", "done"):
        ev = make_event(reasoning=reasoning)
        rec = event_to_trace_record(ev)
        assert rec["metadata"][SELVEDGE_NS]["reasoning"] == reasoning
        assert trace_record_to_event(rec)["reasoning"] == reasoning


# ---------------------------------------------------------------------------
# collapse-by-session
# ---------------------------------------------------------------------------


def test_collapse_by_session():
    rows = [make_event(id=str(uuid.uuid4()), entity_path=f"src/m{i}.py", entity_type="file",
                       session_id="S") for i in range(5)]
    collapsed = events_to_trace_records(rows, collapse_by_session=True)
    assert len(collapsed) == 1
    sv = collapsed[0]["metadata"][SELVEDGE_NS]
    assert sv["collapsed"] is True
    assert sv["event_count"] == 5
    assert len(sv["events"]) == 5
    assert validate_trace_record(collapsed[0]) == []

    separate = events_to_trace_records(rows, collapse_by_session=False)
    assert len(separate) == 5


def test_collapse_keeps_sessionless_events_separate():
    rows = [
        make_event(id=str(uuid.uuid4()), session_id="S", entity_path="src/a.py", entity_type="file"),
        make_event(id=str(uuid.uuid4()), session_id="S", entity_path="src/b.py", entity_type="file"),
        make_event(id=str(uuid.uuid4()), session_id="", entity_path="src/c.py", entity_type="file"),
    ]
    records = events_to_trace_records(rows, collapse_by_session=True)
    assert len(records) == 2  # one merged session + one standalone


# ---------------------------------------------------------------------------
# bundle / NDJSON loading
# ---------------------------------------------------------------------------


def test_load_records_from_bundle_array_single_and_ndjson():
    rec = event_to_trace_record(make_event())
    header = export_preamble()[SELVEDGE_NS]
    assert len(load_trace_records(json.dumps({**header, "records": [rec, rec]}))) == 2
    assert len(load_trace_records(json.dumps([rec, rec]))) == 2
    assert len(load_trace_records(json.dumps(rec))) == 1
    assert len(load_trace_records(json.dumps(rec) + "\n" + json.dumps(rec))) == 2
    assert load_trace_records("") == []


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SELVEDGE_DB", str(tmp_path / "selvedge.db"))
    import selvedge.server as srv
    srv._storage = None
    yield
    srv._storage = None


def _seed(entity="users.email", change_type="add", **kw):
    storage = SelvedgeStorage(get_db_path())
    storage.log_event(ChangeEvent(entity_path=entity, change_type=change_type, **kw))


def test_cli_export_agent_trace_bundle(runner, tmp_path):
    _seed(entity="users.email", change_type="add", reasoning="add email column", git_commit="c0ffee")
    _seed(entity="src/auth.py::login", change_type="modify", entity_type="function",
          diff="@@ -1,2 +1,3 @@\n+x", reasoning="harden login")
    out = tmp_path / "trace.json"
    result = runner.invoke(cli, ["export", "--format", "agent-trace", "-o", str(out)])
    assert result.exit_code == 0, result.output
    bundle = json.loads(out.read_text())
    assert bundle["agent_trace_version"] == AGENT_TRACE_VERSION
    assert "selvedge" in bundle["producer"]
    assert len(bundle["records"]) == 2
    for rec in bundle["records"]:
        assert validate_trace_record(rec) == []


def test_cli_export_agent_trace_ndjson(runner):
    _seed()
    _seed(entity="src/x.py", entity_type="file", change_type="modify")
    result = runner.invoke(cli, ["export", "--format", "agent-trace", "--ndjson"])
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.strip().splitlines() if ln.strip()]
    assert len(lines) == 2
    for line in lines:
        assert validate_trace_record(json.loads(line)) == []


def test_cli_export_collapse_by_session(runner):
    for i in range(3):
        _seed(entity=f"src/m{i}.py", entity_type="file", change_type="modify", session_id="sess")
    result = runner.invoke(cli, ["export", "--format", "agent-trace", "--collapse-by-session"])
    assert result.exit_code == 0, result.output
    bundle = json.loads(result.output)
    assert len(bundle["records"]) == 1
    assert bundle["records"][0]["metadata"][SELVEDGE_NS]["event_count"] == 3


def test_cli_import_agent_trace_roundtrip(runner, tmp_path, monkeypatch):
    _seed(entity="users.email", change_type="add", reasoning="add email column")
    _seed(entity="deps/stripe", change_type="add", entity_type="dependency", reasoning="pin stripe")
    out = tmp_path / "trace.json"
    assert runner.invoke(cli, ["export", "--format", "agent-trace", "-o", str(out)]).exit_code == 0

    # Fresh DB, then import the trace back.
    monkeypatch.setenv("SELVEDGE_DB", str(tmp_path / "fresh.db"))
    import selvedge.server as srv
    srv._storage = None
    result = runner.invoke(cli, ["import", str(out), "--format", "agent-trace"])
    assert result.exit_code == 0, result.output

    history = SelvedgeStorage(get_db_path()).get_history(limit=100)
    entities = {e["entity_path"] for e in history}
    assert {"users.email", "deps/stripe"} <= entities


def test_cli_import_agent_trace_dry_run_json(runner, tmp_path):
    _seed(entity="users.email", change_type="add")
    out = tmp_path / "trace.json"
    runner.invoke(cli, ["export", "--format", "agent-trace", "-o", str(out)])
    result = runner.invoke(cli, ["import", str(out), "--format", "agent-trace", "--json"])
    assert result.exit_code == 0, result.output
    events = json.loads(result.output)
    assert any(e["entity_path"] == "users.email" for e in events)


# ---------------------------------------------------------------------------
# regression tests for the adversarial-review findings
# ---------------------------------------------------------------------------


def test_validator_contributor_optional_ranges_required():
    # contributor is optional at the conversation level (can be per-range)
    rec = event_to_trace_record(make_event())
    conv = rec["files"][0]["conversations"][0]
    del conv["contributor"]
    conv["ranges"] = [{"start_line": 1, "end_line": 2, "contributor": {"type": "ai"}}]
    assert validate_trace_record(rec) == []
    # ranges is required
    rec2 = event_to_trace_record(make_event())
    del rec2["files"][0]["conversations"][0]["ranges"]
    assert any("ranges is required" in p for p in validate_trace_record(rec2))
    # a bad per-range contributor type is caught
    rec3 = event_to_trace_record(make_event())
    rec3["files"][0]["conversations"][0]["ranges"][0]["contributor"] = {"type": "robot"}
    assert any("contributor.type" in p for p in validate_trace_record(rec3))


def test_validator_requires_vcs_revision():
    rec = event_to_trace_record(make_event())  # has vcs with a revision
    del rec["vcs"]["revision"]
    assert any("revision" in p for p in validate_trace_record(rec))


def test_vendored_schema_nested_required_lists():
    schema_path = Path(__file__).resolve().parent.parent / "selvedge" / "exporters" / "agent_trace_schema.json"
    schema = json.loads(schema_path.read_text())
    file_items = schema["properties"]["files"]["items"]
    assert set(file_items["required"]) == {"path", "conversations"}
    conv_items = file_items["properties"]["conversations"]["items"]
    assert conv_items["required"] == ["ranges"]


def test_invalid_change_type_coerced_to_modify():
    rec = event_to_trace_record(make_event())
    rec["metadata"][SELVEDGE_NS]["change_type"] = "edit"  # not a valid ChangeType
    ev = trace_record_to_event(rec)
    assert ev["change_type"] == "modify"
    ChangeEvent(**ev)  # would raise if change_type were left as "edit"


def test_non_dict_metadata_roundtrips():
    for raw in ("[1, 2, 3]", '"scalar"', "0"):
        ev = make_event(metadata=raw)
        back = trace_record_to_event(event_to_trace_record(ev))
        assert json.loads(back["metadata"]) == json.loads(raw)


def test_foreign_multi_file_record_fans_out():
    rec = {
        "version": "0.1.0", "id": str(uuid.uuid4()), "timestamp": "2026-06-22T10:00:00Z",
        "files": [
            {"path": "a.py", "conversations": [{"contributor": {"type": "ai"}, "ranges": []}]},
            {"path": "b.py", "conversations": [{"contributor": {"type": "ai"}, "ranges": []}]},
        ],
    }
    events = trace_record_to_events(rec)
    assert {e["entity_path"] for e in events} == {"a.py", "b.py"}
    assert len({e["id"] for e in events}) == 2  # distinct ids, no PK collision


def test_foreign_idless_records_get_distinct_ids():
    base = {"version": "0.1.0", "timestamp": "2026-06-22T10:00:00Z"}
    r1 = dict(base, files=[{"path": "a.py", "conversations": [{"contributor": {"type": "ai"}, "ranges": []}]}])
    r2 = dict(base, files=[{"path": "b.py", "conversations": [{"contributor": {"type": "ai"}, "ranges": []}]}])
    assert trace_record_to_events(r1)[0]["id"] != trace_record_to_events(r2)[0]["id"]


def test_collapsed_record_roundtrips():
    rows = [
        make_event(id=str(uuid.uuid4()), session_id="S", entity_type="file",
                   entity_path="src/a.py", reasoning="reason A", change_type="add"),
        make_event(id=str(uuid.uuid4()), session_id="S", entity_type="function",
                   entity_path="src/b.py::foo", reasoning="reason B", change_type="modify"),
        make_event(id=str(uuid.uuid4()), session_id="S", entity_type="env_var",
                   entity_path="env/X", reasoning="reason C", change_type="add", diff=""),
    ]
    collapsed = events_to_trace_records(rows, collapse_by_session=True)
    assert len(collapsed) == 1
    back = trace_record_to_events(collapsed[0])
    assert len(back) == 3
    by_entity = {e["entity_path"]: e for e in back}
    assert set(by_entity) == {"src/a.py", "src/b.py::foo", "env/X"}
    assert by_entity["src/b.py::foo"]["reasoning"] == "reason B"
    assert by_entity["env/X"]["change_type"] == "add"
    assert all(e["session_id"] == "S" for e in back)
    assert len({e["id"] for e in back}) == 3  # unique ids
    for e in back:
        ChangeEvent(**e)  # all reconstruct cleanly


def test_collapse_merge_semantics():
    rows = [
        make_event(id=str(uuid.uuid4()), session_id="S", entity_path="src/a.py",
                   entity_type="file", timestamp="2026-06-22T12:00:00Z", git_commit=""),
        make_event(id=str(uuid.uuid4()), session_id="S", entity_path="src/a.py",
                   entity_type="file", timestamp="2026-06-22T09:00:00Z", git_commit="deadbee"),
        make_event(id=str(uuid.uuid4()), session_id="S", entity_path="src/b.py",
                   entity_type="file", timestamp="2026-06-22T15:00:00Z", git_commit="f00d"),
    ]
    rec = events_to_trace_records(rows, collapse_by_session=True)[0]
    assert rec["timestamp"] == "2026-06-22T09:00:00Z"          # min, even out of order
    assert rec["vcs"] == {"type": "git", "revision": "deadbee"}  # first event WITH a commit
    files = {f["path"]: f for f in rec["files"]}
    assert set(files) == {"src/a.py", "src/b.py"}
    assert len(files["src/a.py"]["conversations"]) == 2          # same path merged
    assert len(files["src/b.py"]["conversations"]) == 1


def test_cli_import_collapsed_roundtrip(runner, tmp_path, monkeypatch):
    for i in range(3):
        _seed(entity=f"src/m{i}.py", entity_type="file", change_type="modify",
              session_id="sess", reasoning=f"reason {i}")
    out = tmp_path / "trace.json"
    assert runner.invoke(
        cli, ["export", "--format", "agent-trace", "--collapse-by-session", "-o", str(out)]
    ).exit_code == 0
    monkeypatch.setenv("SELVEDGE_DB", str(tmp_path / "fresh.db"))
    import selvedge.server as srv
    srv._storage = None
    assert runner.invoke(cli, ["import", str(out), "--format", "agent-trace"]).exit_code == 0
    entities = {e["entity_path"] for e in SelvedgeStorage(get_db_path()).get_history(limit=100)}
    assert {"src/m0.py", "src/m1.py", "src/m2.py"} <= entities  # all 3 survived collapse
