"""Offline evidence, accounting and MCP boundary checks for the coding pilot."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from collections import Counter
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from bench.coding_memory import manual_decision_test, memory
from bench.coding_memory import run as runner
from selvedge.storage import SelvedgeStorage

CASES = json.loads((runner.HERE / "fixtures.json").read_text())
BEHAVIOR_KEYS = {"repeated_rejected_path", "harmful_avoidance", "irrelevant_interference"}


@pytest.fixture(autouse=True)
def prevent_real_client_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test in this file may accidentally start a model or account preflight."""
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Unexpected external client call in an offline test")

    monkeypatch.setattr(runner.codex_adapter, "run_client", forbidden)
    monkeypatch.setattr(runner.codex_adapter, "preflight", forbidden)


def prepare_plan(output: Path, *, case: str | None = None, trials: int = 2) -> dict:
    """Freeze a genuine plan and seeded stores in a disposable output directory."""
    runner.prepare(argparse.Namespace(
        output=output, phase="technical-smoke", seed=42, model="test-model",
        arms=list(runner.ARMS), trials=trials, case=case,
    ))
    return json.loads((output / "manifest.json").read_text())


def test_schedule_is_deterministic_complete_and_blocked() -> None:
    """Each of three tasks gets two draws of every arm, with no best-of selection."""
    actual = runner.schedule(CASES, list(runner.ARMS), trials=2, seed=42)
    assert actual == runner.schedule(CASES, list(runner.ARMS), trials=2, seed=42)
    assert actual != runner.schedule(CASES, list(runner.ARMS), trials=2, seed=43)
    assert [row["slot"] for row in actual] == list(range(1, 19))
    expected = Counter((case["id"], arm, draw) for case in CASES
                       for arm in runner.ARMS for draw in (1, 2))
    assert Counter((row["case"], row["arm"], row["draw"]) for row in actual) == expected
    for start in range(0, len(actual), 3):
        block = actual[start:start + 3]
        assert len({(row["case"], row["draw"]) for row in block}) == 1
        assert {row["arm"] for row in block} == set(runner.ARMS)


@pytest.mark.parametrize("arm", runner.ARMS)
def test_visible_case_excludes_grading_answers_and_does_not_mutate_fixture(arm: str) -> None:
    """Private solutions, hidden checks and behavioral labels never become tool files."""
    case = copy.deepcopy(CASES[1])
    original = copy.deepcopy(case)
    visible = runner.visible_case(case, arm, "# Exact synthetic history\n")
    assert set(visible) == {"id", "entrypoint", "task", "project_files", "public_checks"}
    assert visible["public_checks"] == case["public_checks"]
    assert visible["task"] == case["task"]
    if arm == "maintained-markdown":
        assert visible["project_files"]["DECISIONS.md"] == "# Exact synthetic history\n"
    else:
        assert "DECISIONS.md" not in visible["project_files"]
    visible["project_files"]["solution.py"] = "changed only in this view"
    assert case == original


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_real_store_reconstructs_identical_history_without_overwriting(
    case: dict, tmp_path: Path,
) -> None:
    """Fresh stores preserve every source row, including supersession and irrelevant rows."""
    database = tmp_path / "first.db"
    markdown, rows = memory.seed_history(case, database)
    other_markdown, other_rows = memory.seed_history(case, tmp_path / "second.db")
    assert markdown == other_markdown and rows == other_rows
    assert {row["id"] for row in rows} == {row["id"] for row in case["history"]}
    assert rows == sorted(rows, key=lambda row: (row["timestamp"], row["id"]), reverse=True)
    store = SelvedgeStorage(database)
    for record in case["history"]:
        stored = next(row for row in store.get_entity_history(record["entity_path"])
                      if row["id"] == record["id"])
        assert stored["reasoning"] == record["reasoning"]
        assert stored["supersedes"] == record["supersedes"]
    if case["id"] == "message-visibility":
        old, new = case["history"]
        assert rows[0]["id"] == new["id"]
        assert rows[0]["supersedes"] == old["id"]
        assert next(row for row in rows if row["id"] == old["id"])["superseded_by"] == new["id"]
    if case["id"] == "chunking":
        assert all(row["entity_path"] != case["entity"] for row in rows)
    before = database.read_bytes()
    with pytest.raises(FileExistsError):
        memory.seed_history(case, database)
    assert database.read_bytes() == before


def test_prepare_freezes_complete_plan_without_model_calls(tmp_path: Path) -> None:
    """Offline preparation records immutable sources, real capture IDs and all slots."""
    output = tmp_path / "plan"
    manifest = prepare_plan(output)
    assert len(manifest["schedule"]) == 18
    assert manifest["executed"] is False
    assert not (output / "execution.json").exists()
    for case in CASES:
        receipt = manifest["capture_evidence"][case["id"]]
        assert set(receipt["seeded_ids"]) == set(receipt["retrieved_ids"])
        evidence = output / "captures" / (case["id"] + ".md")
        assert receipt["bundle_sha256"] == runner.digest(evidence.read_bytes())
    with pytest.raises(FileExistsError):
        prepare_plan(output)


@pytest.mark.parametrize("reuse", [False, True], ids=["changed-source", "reused-execution"])
def test_execute_refuses_changed_sources_and_reuse_before_client_access(
    reuse: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source mismatch or prior execution cannot silently become another trial."""
    output = tmp_path / "plan"
    prepare_plan(output)
    if reuse:
        receipt = {"status": "running", "original": True}
        runner.write_json(output / "execution.json", receipt)
        expected = FileExistsError
    else:
        monkeypatch.setattr(runner, "source_hashes", lambda: {"changed": "not-the-frozen-source"})
        expected = ValueError
    with pytest.raises(expected):
        runner.execute(output)
    assert not (output / "results.jsonl").exists()
    if reuse:
        assert json.loads((output / "execution.json").read_text()) == receipt


def test_execute_accounts_for_every_unattempted_slot_after_incomplete_trial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An early failure leaves 17 explicit unattempted records, never replacement draws."""
    output = tmp_path / "plan"
    manifest = prepare_plan(output)
    monkeypatch.setattr(runner, "source_hashes", lambda: manifest["source_sha256"])
    monkeypatch.setattr(runner.codex_adapter, "preflight", lambda: {"ready": True})
    attempts = []

    def incomplete(slot: dict, case: dict, frozen: dict, destination: Path) -> dict:
        attempts.append(slot["slot"])
        return {**slot, "completed": False, "status": "mock-client-incomplete"}

    monkeypatch.setattr(runner, "run_one", incomplete)
    runner.execute(output)
    records = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert attempts == [1]
    assert len(records) == 18 and [row["slot"] for row in records] == list(range(1, 19))
    assert records[0]["status"] == "mock-client-incomplete"
    assert all(row["status"] == "unattempted" for row in records[1:])
    assert json.loads((output / "execution.json").read_text())["status"] == "stopped_incomplete"
    summary = json.loads((output / "summary.json").read_text())
    assert summary["planned"] == summary["accounted"] == 18
    assert all(row["accounted"] == 2 and row["completed"] == 0 for row in summary["task_rows"])


def test_execute_persists_all_slots_before_reraising_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interrupted second trial retains the first result and every remaining slot."""
    output = tmp_path / "plan"
    manifest = prepare_plan(output)
    monkeypatch.setattr(runner, "source_hashes", lambda: manifest["source_sha256"])
    monkeypatch.setattr(runner.codex_adapter, "preflight", lambda: {"cli_version": "build-one"})
    attempts = []

    def interrupt_second(slot: dict, case: dict, frozen: dict, destination: Path) -> dict:
        attempts.append(slot["slot"])
        if slot["slot"] == 2:
            raise KeyboardInterrupt
        return {**slot, "completed": True, "status": "completed", "score": 1.0,
                "functional_correct": True, "grade": {}, "first_write_grade": {}}

    monkeypatch.setattr(runner, "run_one", interrupt_second)
    with pytest.raises(KeyboardInterrupt):
        runner.execute(output)
    records = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert attempts == [1, 2]
    assert len(records) == 18 and [row["slot"] for row in records] == list(range(1, 19))
    assert records[0]["completed"] and records[0]["functional_correct"]
    assert records[1]["status"] == "interrupted" and not records[1]["completed"]
    assert all(row["status"] == "unattempted" for row in records[2:])
    receipt = json.loads((output / "execution.json").read_text())
    assert receipt["status"] == "stopped_incomplete" and receipt["finished_at"]
    summary = json.loads((output / "summary.json").read_text())
    assert summary["planned"] == summary["accounted"] == 18
    assert sum(row["completed"] for row in summary["task_rows"]) == 1
    assert sum(row["correct"] for row in summary["task_rows"]) == 1


def test_execute_stops_before_trial_when_client_build_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build change between slots cannot mix clients in one frozen experiment."""
    output = tmp_path / "plan"
    manifest = prepare_plan(output)
    monkeypatch.setattr(runner, "source_hashes", lambda: manifest["source_sha256"])
    versions = iter(("build-one", "build-one", "build-two"))
    monkeypatch.setattr(runner.codex_adapter, "preflight", lambda: {"cli_version": next(versions)})
    attempts = []

    def complete(slot: dict, case: dict, frozen: dict, destination: Path) -> dict:
        attempts.append(slot["slot"])
        assert frozen["_cli_build_at_start"] == "build-one"
        return {**slot, "completed": True, "status": "completed", "score": 1.0,
                "functional_correct": True, "grade": {}, "first_write_grade": {}}

    monkeypatch.setattr(runner, "run_one", complete)
    runner.execute(output)
    records = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert attempts == [1]
    assert len(records) == 18 and records[0]["completed"]
    assert records[1]["status"] == "harness_error" and records[1]["error_type"] == "ValueError"
    assert "CLI build changed" in records[1]["error"]
    assert all(row["status"] == "unattempted" for row in records[2:])
    receipt = json.loads((output / "execution.json").read_text())
    assert receipt["client"]["cli_version"] == "build-one"
    assert receipt["status"] == "stopped_incomplete"
    summary = json.loads((output / "summary.json").read_text())
    assert summary["planned"] == summary["accounted"] == 18
    assert sum(row["completed"] for row in summary["task_rows"]) == 1


def test_client_completion_without_written_code_is_not_a_completed_trial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fluent final message cannot substitute for a host-observed code artifact."""
    output = tmp_path / "plan"
    manifest = prepare_plan(output, case=CASES[0]["id"], trials=1)
    monkeypatch.setattr(runner.codex_adapter, "run_client", lambda **kwargs: {"completed": True})
    record = runner.run_one(manifest["schedule"][0], CASES[0], manifest, output)
    assert not record["completed"] and not record["artifact_written"]
    assert not record["functional_correct"] and record["score"] == 0
    assert record["writes"] == record["public_checks"] == 0


def test_both_memory_arms_deliver_exactly_the_reconstructed_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two interventions expose identical bytes via file versus prompt injection."""
    case = CASES[1]
    output = tmp_path / "plan"
    manifest = prepare_plan(output, case=case["id"], trials=1)
    observed = []

    def complete_client(**kwargs: object) -> dict:
        run_dir = kwargs["run_dir"]
        assert isinstance(run_dir, Path)
        visible = json.loads((run_dir / "visible-case.json").read_text())
        observed.append((kwargs["prompt"], visible))
        source = case["reference_solution"]
        (run_dir / "solution.py").write_text(source)
        (run_dir / "candidate-1.py").write_text(source)
        actions = []
        if "DECISIONS.md" in visible["project_files"]:
            actions.append({"tool": "read_file", "path": "DECISIONS.md"})
        actions.append({"tool": "write_file", "path": "solution.py"})
        (run_dir / "fixture-trace.jsonl").write_text("".join(json.dumps(row) + "\n" for row in actions))
        return {"completed": True}

    monkeypatch.setattr(runner.codex_adapter, "run_client", complete_client)
    markdown = (output / "captures" / (case["id"] + ".md")).read_text()
    for slot in manifest["schedule"]:
        record = runner.run_one(slot, case, manifest, output)
        prompt, visible = observed[-1]
        assert record["completed"] and record["functional_correct"] and record["capture_verified"]
        assert record["history_sha256"] == runner.digest(markdown.encode())
        if slot["arm"] == "maintained-markdown":
            assert visible["project_files"]["DECISIONS.md"] == markdown
            assert markdown not in prompt
            assert record["file_retrieved_before_first_write"]
        elif slot["arm"] == "selvedge-injected":
            assert prompt.endswith(markdown) and "DECISIONS.md" not in visible["project_files"]
            assert record["history_injected"]
        else:
            assert markdown not in prompt and "DECISIONS.md" not in visible["project_files"]
            assert not record["history_injected"]


def test_manual_decision_example_preserves_history_and_changes_the_contract(tmp_path: Path) -> None:
    """An explicit preference change has a before-pass, changed-fail, corrected-pass trail."""
    output = tmp_path / "manual"
    result = manual_decision_test.demonstrate(output)
    assert result["old_contract_old_code"]["functional_correct"]
    assert all(check["label"] == f"Original participant-only filtering ({check['id']})"
               for check in result["original_contract_checks"])
    assert all(check["label"].startswith("Original participant-only filtering")
               for check in result["old_contract_old_code"]["checks"])
    assert any("Public visibility" in check["label"]
               for check in result["new_contract_new_code"]["checks"])
    assert BEHAVIOR_KEYS.isdisjoint(result["old_contract_old_code"])
    assert not result["new_contract_old_code"]["functional_correct"]
    assert result["new_contract_old_code"]["harmful_avoidance"]
    assert result["new_contract_new_code"]["functional_correct"]
    assert result["old_reasoning_preserved"]
    assert result["history_newest_first"][0]["id"] == result["superseding_id"]
    old = next(row for row in result["history_newest_first"] if row["id"] == result["decision_id"])
    assert old["reasoning"] == CASES[1]["history"][0]["reasoning"]
    assert old["superseded_by"] == result["superseding_id"]
    assert old["metadata"]["test_link"] == "fixtures.json#message-visibility"
    assert json.loads((output / "result.json").read_text()) == result
    with pytest.raises(FileExistsError):
        manual_decision_test.demonstrate(output)


def payload(result: object) -> dict:
    """Read either supported MCP serialization shape without flattening tool errors."""
    assert not result.isError
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_fixture_mcp_exposes_only_public_files_and_enforces_write_check_budgets(
    tmp_path: Path,
) -> None:
    """Exercise the real stdio server, path boundary, snapshots and two-call budgets."""
    case = CASES[0]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    runner.write_json(run_dir / "visible-case.json", runner.visible_case(case, "no-memory", ""))
    marker = tmp_path / "host-marker.txt"
    marker.write_text("not in the public project")
    params = StdioServerParameters(
        command=sys.executable, args=[str(runner.HERE / "fixture_server.py")],
        env={**os.environ, "SELVEDGE_CODING_RUN": str(run_dir), "SELVEDGE_TELEMETRY": "0"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            assert {tool.name for tool in (await session.list_tools()).tools} == {
                "read_project", "read_file", "write_file", "run_checks",
            }
            project = payload(await session.call_tool("read_project", arguments={}))
            assert project["files"] == ["README.md", "solution.py"]
            readme = payload(await session.call_tool("read_file", arguments={"path": "README.md"}))
            assert readme["content"] == case["project_files"]["README.md"]
            assert "error" in payload(await session.call_tool("read_file", arguments={"path": "../host-marker.txt"}))
            assert "error" in payload(await session.call_tool("write_file", arguments={
                "path": "../should-not-exist.py", "content": "not allowed",
            }))
            assert "error" in payload(await session.call_tool("run_checks", arguments={}))
            for source in (case["bad_solution"], case["reference_solution"]):
                written = payload(await session.call_tool("write_file", arguments={
                    "path": "solution.py", "content": source,
                }))
                assert written == {"written": "solution.py"}
                checked = payload(await session.call_tool("run_checks", arguments={}))
                assert checked["check_scope"] == "public"
                assert checked["functional_correct"] is (source == case["reference_solution"])
                assert BEHAVIOR_KEYS.isdisjoint(checked)
                assert {row["id"] for row in checked["checks"]} == {
                    check["id"] for check in case["public_checks"]
                }
            assert checked["functional_correct"]
            assert "error" in payload(await session.call_tool("write_file", arguments={
                "path": "solution.py", "content": case["bad_solution"],
            }))
            assert "error" in payload(await session.call_tool("run_checks", arguments={}))
    assert (run_dir / "solution.py").read_text() == case["reference_solution"]
    assert (run_dir / "candidate-1.py").read_text() == case["bad_solution"]
    assert (run_dir / "candidate-2.py").read_text() == case["reference_solution"]
    assert not (tmp_path / "should-not-exist.py").exists()
    actions = runner.fixture_trace(run_dir)
    assert sum(row["tool"] == "write_file" for row in actions) == 2
    assert sum(row["tool"] == "run_checks" for row in actions) == 2
