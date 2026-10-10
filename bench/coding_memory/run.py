"""Prepare, execute and summarize a frozen, bounded synthetic coding pilot.

Planning is offline. Execution uses the optional installed-client adapter and
existing subscription only. Runtime Selvedge behavior is never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from bench.coding_memory import codex_adapter
from bench.coding_memory.grade import grade_candidate
from bench.coding_memory.memory import seed_history

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ARMS = ("no-memory", "maintained-markdown", "selvedge-injected")
SYSTEM = (
    "Complete this small Python coding task using ONLY the coding_fixture MCP tools. "
    "Read the current project and its available files. Consult available project "
    "history when relevant, assessing scope and newer decisions. Implement solution.py "
    "with write_file; you may run the public checks. Use no shell, filesystem, network, "
    "browser, planning or other tools. There are at most two writes, two public-check "
    "calls and twelve total tool calls. Finish with a brief visible explanation; "
    "do not merely claim to have written code."
)


def write_json(path: Path, value: object) -> None:
    """Write deterministic readable JSON evidence."""
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def digest(data: bytes) -> str:
    """Return a SHA-256 digest for an immutable artifact."""
    return hashlib.sha256(data).hexdigest()


def source_hashes() -> dict[str, str]:
    """Freeze the fixture, grader, protocol and all execution/delivery sources."""
    paths = sorted(HERE.glob("*.py")) + [HERE / "fixtures.json", HERE / "PROTOCOL.md"]
    return {str(path.relative_to(REPO)): digest(path.read_bytes()) for path in paths}


def schedule(cases: list[dict], arms: list[str], trials: int, seed: int) -> list[dict]:
    """Shuffle arms within each task/draw block, and then shuffle the blocks."""
    rng = random.Random(seed)
    blocks = [(case["id"], draw) for case in cases for draw in range(1, trials + 1)]
    rng.shuffle(blocks)
    slots = []
    for case, draw in blocks:
        shuffled = list(arms)
        rng.shuffle(shuffled)
        slots.extend({"case": case, "arm": arm, "draw": draw} for arm in shuffled)
    return [{"slot": n, **slot} for n, slot in enumerate(slots, 1)]


def prepare(args: argparse.Namespace) -> None:
    """Freeze the complete planned matrix without invoking any model."""
    cases = json.loads((HERE / "fixtures.json").read_text())
    if args.case:
        cases = [case for case in cases if case["id"] == args.case]
    if not cases or args.trials < 1:
        raise ValueError("Select at least one existing case and a positive draw count")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    captures = output / "captures"
    captures.mkdir()
    evidence = {}
    for case in cases:
        markdown, rows = seed_history(case, captures / f"{case['id']}.db")
        (captures / f"{case['id']}.md").write_text(markdown)
        write_json(captures / f"{case['id']}.json", rows)
        evidence[case["id"]] = {"bundle_sha256": digest(markdown.encode()),
                                 "seeded_ids": [record["id"] for record in case["history"]],
                                 "retrieved_ids": [record["id"] for record in rows],
                                 "capture": "author-seeded; not autonomous capture"}
        if set(evidence[case["id"]]["seeded_ids"]) != set(evidence[case["id"]]["retrieved_ids"]):
            raise ValueError("Capture/reconstruction mismatch")
    manifest = {
        "protocol": "coding-memory-pilot/1", "phase": args.phase,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO,
                                             text=True).strip(),
        "source_sha256": source_hashes(), "seed": args.seed, "cases": cases,
        "arms": list(dict.fromkeys(args.arms)), "draws_per_task_arm": args.trials,
        "schedule": schedule(cases, list(dict.fromkeys(args.arms)), args.trials, args.seed),
        "model_configured": args.model, "model_build": "provider build not exposed",
        "effort": "medium", "timeout_seconds": 180, "max_tool_calls": 12,
        "max_writes": 2, "max_public_checks": 2, "workers": 1,
        "system_prompt": SYSTEM, "capture_evidence": evidence,
        "native_memory": "not evaluated", "autonomous_capture": "not evaluated",
        "environment": {"python": sys.version.split()[0]}, "executed": False,
    }
    write_json(output / "manifest.json", manifest)
    print(json.dumps({"prepared": len(manifest["schedule"]), "model_calls": 0,
                      "manifest_sha256": digest((output / "manifest.json").read_bytes())}))


def visible_case(case: dict, arm: str, markdown: str) -> dict:
    """Exclude private tests, behavior labels, solutions and history from tool files."""
    visible = {key: case[key] for key in ("id", "entrypoint", "task", "project_files",
                                         "public_checks")}
    visible = json.loads(json.dumps(visible))
    if arm == "maintained-markdown":
        visible["project_files"]["DECISIONS.md"] = markdown
    return visible


def fixture_trace(run_dir: Path) -> list[dict]:
    """Read host-observed actions, separately from a model's written claims."""
    path = run_dir / "fixture-trace.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def run_one(slot: dict, case: dict, manifest: dict, output: Path) -> dict:
    """Execute one fresh session/store, then grade final and first written code."""
    run_dir = output / "trials" / f"{slot['slot']:02d}-{case['id']}-{slot['arm']}-{slot['draw']}"
    run_dir.mkdir(parents=True)
    markdown, rows = seed_history(case, run_dir / "memory.db")
    if digest(markdown.encode()) != manifest["capture_evidence"][case["id"]]["bundle_sha256"]:
        raise ValueError("Reconstructed history changed after freeze")
    write_json(run_dir / "visible-case.json", visible_case(case, slot["arm"], markdown))
    prompt = manifest["system_prompt"] + "\n\nCurrent task:\n" + case["task"]
    if slot["arm"] == "selvedge-injected":
        prompt += "\n\n" + markdown
    (run_dir / "prompt.txt").write_text(prompt)
    result = codex_adapter.run_client(
        prompt=prompt, run_dir=run_dir, python_executable=sys.executable,
        raw_dir=run_dir / "private", model=manifest["model_configured"],
        effort=manifest["effort"], timeout=manifest["timeout_seconds"],
        max_tool_calls=manifest["max_tool_calls"],
    )
    expected_cli = manifest.get("_cli_build_at_start")
    actual_cli = result.get("preflight", {}).get("cli_version")
    if expected_cli and actual_cli != expected_cli:
        result["completed"] = False
        result["invalid_reasons"] = [*result.get("invalid_reasons", []), "cli_build_changed"]
    actions = fixture_trace(run_dir)
    code = run_dir / "solution.py"
    final_grade = grade_candidate(case, code.read_text() if code.exists() else "")
    first = run_dir / "candidate-1.py"
    first_grade = grade_candidate(case, first.read_text() if first.exists() else "")
    first_write = next((i for i, action in enumerate(actions)
                        if action["tool"] == "write_file"), None)
    retrieved = any(action["tool"] == "read_file" and action.get("path") == "DECISIONS.md"
                    for action in actions[:first_write] if first_write is not None)
    # The adapter status is process/setup evidence; syntax/test failures remain outcomes.
    completed = bool(result.get("completed")) and code.exists()
    record = {
        **slot, "family": case["family"], "client": result,
        "completed": completed, "artifact_written": code.exists(),
        "status": "completed" if completed else (
            "timeout" if "wall_timeout" in result.get("invalid_reasons", [])
            else "invalid_client" if not result.get("completed") else "missing_implementation"
        ),
        "grade": final_grade, "first_write_grade": first_grade,
        "functional_correct": completed and bool(final_grade["functional_correct"]),
        "score": final_grade["score"] if completed else 0.0,
        "history_injected": slot["arm"] == "selvedge-injected",
        "file_retrieved_before_first_write": retrieved,
        "capture_verified": len(rows) == len(case["history"]),
        "history_sha256": digest(markdown.encode()),
        "writes": sum(action["tool"] == "write_file" for action in actions),
        "public_checks": sum(action["tool"] == "run_checks" for action in actions),
    }
    write_json(run_dir / "result.json", record)
    return record


def execute(output: Path) -> None:
    """Run a frozen plan once; retain incomplete/unattempted slots without retries."""
    output = output.resolve()
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if (output / "execution.json").exists():
        raise FileExistsError("This plan has an execution receipt; create a separately labeled plan")
    if source_hashes() != manifest["source_sha256"]:
        raise ValueError("Sources differ from the frozen plan; prepare a new version")
    version = codex_adapter.preflight()
    execution = {"started_at": datetime.now(timezone.utc).isoformat(), "client": version,
                 "manifest_sha256": digest(manifest_path.read_bytes()), "status": "running"}
    write_json(output / "execution.json", execution)
    manifest["_cli_build_at_start"] = version.get("cli_version")
    cases = {case["id"]: case for case in manifest["cases"]}
    records = []
    stop_reason = ""
    interrupted = False
    for slot in manifest["schedule"]:
        if stop_reason:
            record = {**slot, "completed": False, "status": "unattempted", "reason": stop_reason}
        else:
            try:
                if codex_adapter.preflight().get("cli_version") != version.get("cli_version"):
                    raise ValueError("CLI build changed after execution started")
                record = run_one(slot, cases[slot["case"]], manifest, output)
            except KeyboardInterrupt:
                interrupted = True
                record = {**slot, "completed": False, "status": "interrupted",
                          "reason": "Execution interrupted; artifacts retained and no retry"}
            except Exception as exc:
                record = {**slot, "completed": False, "status": "harness_error",
                          "error_type": type(exc).__name__, "error": str(exc)}
            if not record["completed"]:
                stop_reason = "Stopped after incomplete trial; no automatic retry or substitution"
        records.append(record)
        with (output / "results.jsonl").open("a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        print(json.dumps({key: record.get(key) for key in (
            "slot", "case", "arm", "draw", "completed", "functional_correct", "score", "status"
        )}), flush=True)
    execution.update(finished_at=datetime.now(timezone.utc).isoformat(),
                     status="stopped_incomplete" if stop_reason else "completed")
    write_json(output / "execution.json", execution)
    write_json(output / "summary.json", summarize(manifest, records))
    if interrupted:
        raise KeyboardInterrupt


def summarize(manifest: dict, records: list[dict]) -> dict:
    """Report all scheduled slots and paired task counts, never best-of selection."""
    rows = []
    for case in manifest["cases"]:
        for arm in manifest["arms"]:
            selected = [row for row in records if row["case"] == case["id"] and row["arm"] == arm]
            complete = [row for row in selected if row.get("completed")]
            rows.append({
                "case": case["id"], "family": case["family"], "arm": arm,
                "planned": manifest["draws_per_task_arm"], "accounted": len(selected),
                "completed": len(complete),
                "correct": sum(row.get("functional_correct", False) for row in selected),
                "scores_all_draws": [row.get("score", 0.0) for row in selected],
                "behavior_assessed": sum(row["grade"].get("behavior_assessed", False)
                                         for row in complete),
                "behavior_unassessed": sum(not row["grade"].get("behavior_assessed", False)
                                           for row in complete),
                "specific_repeat": sum(row["grade"].get("repeated_rejected_path", False)
                                       for row in complete),
                "harmful_avoidance": sum(row["grade"].get("harmful_avoidance", False)
                                         for row in complete),
                "irrelevant_signature": sum(row["grade"].get("irrelevant_interference", False)
                                            for row in complete),
                "first_write_specific_repeat": sum(
                    row["first_write_grade"].get("repeated_rejected_path", False)
                    for row in complete),
                "first_write_harmful_avoidance": sum(
                    row["first_write_grade"].get("harmful_avoidance", False)
                    for row in complete),
                "first_write_irrelevant_signature": sum(
                    row["first_write_grade"].get("irrelevant_interference", False)
                    for row in complete),
                "file_retrieved_before_first_write": sum(
                    row.get("file_retrieved_before_first_write", False) for row in complete),
                "operational_observations": {
                    field: numeric_summary([
                        row.get("client", {}).get("seconds") if field == "seconds"
                        else row.get("client", {}).get("usage", {}).get(field)
                        for row in selected
                    ]) for field in ("seconds", "input_tokens", "cached_input_tokens", "output_tokens")
                },
            })
    return {"phase": manifest["phase"], "independent_tasks": len(manifest["cases"]),
            "planned": len(manifest["schedule"]), "accounted": len(records), "task_rows": rows,
            "interpretation": "Descriptive method pilot; repeated draws are not independent projects. "
                              "No population confidence interval, superiority or causal benefit claim."}


def numeric_summary(values: list) -> dict:
    """Describe available operational observations; never impute unknowns as zero."""
    known = [value for value in values if isinstance(value, (int, float))
             and not isinstance(value, bool)]
    return {"observed": len(known), "unknown": len(values) - len(known),
            "mean": statistics.mean(known) if known else None,
            "median": statistics.median(known) if known else None}


def main() -> None:
    """Dispatch offline preparation or explicitly requested execution."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("prepare")
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--model", default="gpt-6-sol")
    plan.add_argument("--trials", type=int, default=2)
    plan.add_argument("--arms", nargs="+", choices=ARMS, default=list(ARMS))
    plan.add_argument("--case")
    plan.add_argument("--phase", choices=("technical-smoke", "method-pilot"), required=True)
    plan.add_argument("--seed", type=int, default=20261009)
    run = commands.add_parser("execute")
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args)
    else:
        execute(args.output)


if __name__ == "__main__":
    main()
