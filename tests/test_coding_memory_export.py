"""Publication preserves every outcome while excluding private execution material."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bench.coding_memory.export import export_run, numeric_summary


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def _plan(root: Path, phase: str = "method-pilot") -> dict:
    arms = ["no-memory", "maintained-markdown", "selvedge-injected"]
    schedule = [{"slot": n + 1, "case": "synthetic-case", "arm": arm, "draw": draw}
                for n, (arm, draw) in enumerate((arm, draw) for arm in arms for draw in (1, 2))]
    history = [{"id": "decision-old", "reasoning": "Synthetic recorded decision rationale",
                "session_id": "history-session-private", "account": "history-account-private",
                "entity_path": "solution.py", "superseded_by": "decision-new"}]
    manifest = {"phase": phase, "protocol": "coding-memory-pilot/1", "cases": [
        {"id": "synthetic-case", "family": "test", "history": history}],
        "arms": arms, "schedule": schedule, "draws_per_task_arm": 2,
        "model_configured": "example-model", "effort": "medium",
        "source_sha256": {"bench/coding_memory/run.py": "a" * 64},
        "session_id": "manifest-session-private"}
    _json(root / "manifest.json", manifest)
    _json(root / "captures/synthetic-case.json", history)
    (root / "captures/synthetic-case.md").write_text("# Recorded decisions\n\n```json\n" +
                                                    json.dumps(history) + "\n```\n")
    (root / "captures/synthetic-case.db").write_bytes(b"database-private")
    return manifest


def _record(slot: dict, *, completed: bool = True, correct: bool = True) -> dict:
    grade = {"functional_correct": correct, "behavior_assessed": True,
             "repeated_rejected_path": not correct, "harmful_avoidance": False,
             "irrelevant_interference": False}
    return {**slot, "completed": completed, "status": "completed" if completed else "timeout",
            "functional_correct": completed and correct, "score": float(completed and correct),
            "grade": grade, "first_write_grade": {**grade, "repeated_rejected_path": True},
            "history_injected": slot["arm"] == "selvedge-injected",
            "file_retrieved_before_first_write": slot["arm"] == "maintained-markdown",
            "client": {"seconds": 2, "usage": {"input_tokens": 100, "output_tokens": None,
                                                "cached_input_tokens": 0},
                       "session_id": "client-session-private", "reasoning": "reasoning-private",
                       "raw_stream": "stream-private", "trace": [
                           {"type": "reasoning", "text": "reasoning-item-private"},
                           {"type": "thread.started", "id": "thread-private"},
                           {"type": "fixture_tool", "tool": "read_file", "arguments": {
                               "path": "solution.py", "session_id": "args-session-private"},
                            "result": {"content": "/Users/private-person/local/run/solution.py",
                                       "reasoning": "tool-reasoning-private"}},
                           {"type": "final_text", "text": "Done. session_id=private-session "
                            "owner@example.test C:\\Users\\private-person\\run.py "
                            "<analysis>tagged-private</analysis>"}]}}


def _trial(root: Path, record: dict) -> Path:
    return root / "trials" / (f"{record['slot']:02d}-{record['case']}-"
                              f"{record['arm']}-{record['draw']}")


def test_export_retains_failures_unknown_slots_and_observable_evidence(tmp_path: Path) -> None:
    """No best-of selection, denominator loss, unknown-to-zero or raw log publication."""
    source, output = tmp_path / "private", tmp_path / "public"
    manifest = _plan(source)
    records = [_record(manifest["schedule"][0]),
               _record(manifest["schedule"][1], completed=False),
               _record(manifest["schedule"][2], correct=False),
               {**manifest["schedule"][3], "completed": False, "status": "unattempted",
                "reason": "Stopped after incomplete trial"}]
    (source / "results.jsonl").write_text("\n".join(json.dumps(row) for row in records) + "\n")
    first = _trial(source, records[0])
    _json(first / "result.json", records[0])
    (first / "solution.py").write_text("def solution():\n    return 1\n")
    (first / "candidate-1.py").write_text("def solution():\n    return 0\n")
    (first / "prompt.txt").write_text("Task at /private/var/run/private-owner/job; write solution.py")
    (first / "fixture-trace.jsonl").write_text(json.dumps({
        "tool": "write_file", "path": "solution.py", "write": 1,
        "session_id": "fixture-session-private"}) + "\n")
    (first / "private").mkdir()
    (first / "private/raw-stream.jsonl").write_text("raw-exclusive-private")
    (first / "memory.db").write_bytes(b"raw-database-private")
    _json(source / "summary.json", {"planned": 1, "accounted": 1, "claim": "benefit-private"})
    receipt = export_run(source, output)
    assert receipt["accounted"] == receipt["planned"] == 6 and receipt["model_calls"] == 0
    published = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert [row["status"] for row in published] == [
        "completed", "timeout", "completed", "unattempted", "unverified", "unverified"]
    assert published[0]["first_write_grade"]["repeated_rejected_path"]
    assert published[2]["file_retrieved_before_first_write"]
    summary = json.loads((output / "summary.json").read_text())
    assert summary["task_rows"][0]["completed"] == 1
    assert summary["task_rows"][0]["grade"]["assessed"] == 1
    assert summary["task_rows"][0]["grade"]["unassessed"] == 1
    assert {row["outcome"] for row in summary["paired_tasks"]} == {"left_higher", "tie"}
    costs = {row["field"]: row for row in summary["operational_observations"]
             if row["arm"] == "no-memory"}
    assert costs["cached_input_tokens"]["mean"] == 0
    assert costs["output_tokens"]["mean"] is None and costs["output_tokens"]["unknown"] == 2
    all_text = "\n".join(path.read_text() for path in output.rglob("*") if path.is_file())
    for forbidden in ("session-private", "account-private", "reasoning-private", "stream-private",
                      "reasoning-item-private", "thread-private", "args-session-private",
                      "private-person", "owner@example.test", "private-session", "tagged-private",
                      "fixture-session-private", "raw-exclusive-private", "database-private",
                      "benefit-private", "/Users/", "/private/var/"):
        assert forbidden not in all_text, forbidden
    assert "Synthetic recorded decision rationale" in all_text
    assert "decision-old" in all_text and "decision-new" in all_text
    assert not list(output.rglob("*.db")) and not list(output.rglob("raw-stream.jsonl"))
    assert (output / first.relative_to(source) / "candidate-1.py").read_text().endswith("return 0\n")
    index = json.loads((output / "export-index.json").read_text())
    assert index["original_input_sha256"]["manifest.json"] == hashlib.sha256(
        (source / "manifest.json").read_bytes()).hexdigest()
    for name, digest in index["exported_sha256"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
    assert index["transformed"]["manifest.json"] is True
    assert index["transformed"][str(first.relative_to(source) / "solution.py")] is False


def test_input_history_and_code_survive_without_client_private_reasoning(tmp_path: Path) -> None:
    """Synthetic rationale is valid input evidence, and division/routes are not host paths."""
    source, output = tmp_path / "private", tmp_path / "public"
    manifest = _plan(source)
    history = "# Decisions\n\n```json\n" + json.dumps({"records_newest_first": [
        {"id": "old", "reasoning": "Exact synthetic rationale", "supersedes": "prior"}]}) + "\n```\n"
    record = _record(manifest["schedule"][0])
    record["client"]["trace"].append({"type": "fixture_tool", "tool": "read_file",
        "arguments": {"path": "DECISIONS.md"}, "result": {"path": "DECISIONS.md", "content": history}})
    (source / "results.jsonl").write_text(json.dumps(record) + "\n")
    trial = _trial(source, record)
    trial.mkdir(parents=True)
    _json(trial / "visible-case.json", {"project_files": {"DECISIONS.md": history}})
    (trial / "prompt.txt").write_text("Current task\n" + history)
    code = 'def solution(value):\n    return [value /2, "/api/feed"]\n'
    (trial / "solution.py").write_text(code)
    export_run(source, output)
    public_trial = output / trial.relative_to(source)
    assert (public_trial / "solution.py").read_text() == code
    visible = json.loads((public_trial / "visible-case.json").read_text())
    assert '"reasoning": "Exact synthetic rationale"' in visible["project_files"]["DECISIONS.md"]
    assert '"reasoning": "Exact synthetic rationale"' in (public_trial / "prompt.txt").read_text()
    trace = json.loads((public_trial / "client-trace.json").read_text())
    assert "Exact synthetic rationale" in trace[-1]["result"]["content"]
    assert '"reasoning"' in trace[-1]["result"]["content"]
    assert "reasoning-item-private" not in json.dumps(trace)


def test_manual_export_is_explicit_and_not_pooled(tmp_path: Path) -> None:
    """Decision rationale remains while the manual DB and runtime metadata do not."""
    source, manual, output = tmp_path / "private", tmp_path / "manual", tmp_path / "public"
    _plan(source)
    result = {"evidence_class": "synthetic manual feasibility demonstration; no model intervention",
              "old_contract_old_code": {"functional_correct": True},
              "new_contract_old_code": {"functional_correct": False},
              "new_contract_new_code": {"functional_correct": True},
              "old_reasoning_preserved": True, "session_id": "manual-session-private",
              "history_newest_first": [{"id": "new", "supersedes": "old", "reasoning": "Explicit change"}]}
    _json(manual / "result.json", result)
    (manual / "before.py").write_text("old = True\n")
    (manual / "after.py").write_text("old = False\n")
    (manual / "memory.db").write_bytes(b"private")
    export_run(source, output, manual)
    assert len((output / "results.jsonl").read_text().splitlines()) == 6
    saved = json.loads((output / "manual/result.json").read_text())
    assert saved["old_reasoning_preserved"] and "session_id" not in saved
    assert saved["history_newest_first"][0]["reasoning"] == "Explicit change"
    assert not (output / "manual/memory.db").exists()
    assert "not pooled with the pilot" in (output / "REPORT.md").read_text()


@pytest.mark.parametrize("phase", ["technical-smoke", "method-pilot"])
def test_cli_exports_each_evidence_set_separately(tmp_path: Path, phase: str) -> None:
    """The public CLI has no execution path and retains the supplied phase."""
    source, output = tmp_path / "private", tmp_path / "public"
    _plan(source, phase)
    run = subprocess.run([sys.executable, "-m", "bench.coding_memory.export", "--input", str(source),
                          "--output", str(output)], capture_output=True, text=True, check=True)
    assert json.loads(run.stdout)["phase"] == phase
    assert json.loads(run.stdout)["model_calls"] == 0
    assert phase in (output / "REPORT.md").read_text()


def test_conflicting_or_duplicate_receipts_are_not_silently_selected(tmp_path: Path) -> None:
    """The exporter must not choose the more favorable of inconsistent receipts."""
    source, output = tmp_path / "private", tmp_path / "public"
    manifest = _plan(source)
    record = _record(manifest["schedule"][0])
    (source / "results.jsonl").write_text(json.dumps(record) + "\n" + json.dumps(record) + "\n")
    with pytest.raises(ValueError, match="Duplicate"):
        export_run(source, output)
    assert not output.exists()
    (source / "results.jsonl").write_text(json.dumps(record) + "\n")
    _json(_trial(source, record) / "result.json", {**record, "score": 0})
    with pytest.raises(ValueError, match="conflict"):
        export_run(source, output)
    assert not output.exists()


def test_symlinked_artifacts_and_overwrites_are_refused(tmp_path: Path) -> None:
    """A permitted filename cannot be used to copy a different private artifact."""
    source, output = tmp_path / "private", tmp_path / "public"
    manifest = _plan(source)
    outside = tmp_path / "private-session.txt"
    outside.write_text("not public")
    trial = _trial(source, manifest["schedule"][0])
    trial.mkdir(parents=True)
    (trial / "solution.py").symlink_to(outside)
    with pytest.raises(ValueError, match="regular file"):
        export_run(source, output)
    assert not output.exists()
    output.mkdir()
    with pytest.raises(ValueError, match="new output"):
        export_run(source, output)


def test_nonfinite_and_missing_observations_remain_unknown() -> None:
    """Zero is measured; missing, booleans and nonfinite values are not measurements."""
    assert numeric_summary([0, 2, None, True, float("nan")]) == {
        "known": 2, "unknown": 3, "mean": 1, "median": 1}
