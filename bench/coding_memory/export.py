"""Export a reviewed publication projection of one private coding-memory run.

This command is offline: it never executes candidates, invokes a client, or
copies a directory. Original input hashes and exported hashes are distinct.
Synthetic decision rationale is evidence; private client reasoning is not.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import re
import statistics
from pathlib import Path
from typing import Any

TOOLS = {"read_project", "read_file", "write_file", "run_checks"}
METRICS = ("repeated_rejected_path", "harmful_avoidance", "irrelevant_interference")
MANIFEST_FIELDS = """protocol phase created_at revision source_sha256 seed arms
draws_per_task_arm schedule model_configured model_build effort timeout_seconds
max_tool_calls max_writes max_public_checks workers system_prompt capture_evidence
native_memory autonomous_capture environment executed""".split()
CASE_FIELDS = """id family entity entrypoint task project_files public_checks private_checks
reference_solution bad_solution retained_solution superseded_solution behavior_probe""".split()
HISTORY_FIELDS = """id timestamp entity_path entity_type change_type diff reasoning agent
git_commit project changeset_id metadata revisit_after expires_when supersedes superseded_by
constraint stale_when""".split()
RECORD_FIELDS = """slot case arm draw family completed artifact_written status reason error_type
error grade first_write_grade functional_correct score history_injected
file_retrieved_before_first_write capture_verified history_sha256 writes public_checks""".split()
CLIENT_FIELDS = """completed invalid_reasons model_configured model_resolved
model_resolution_source fixture_tool_calls turn_usage usage usage_complete exit_code seconds
effort timeout_seconds max_observed_tool_calls native_turn_cap isolation""".split()
MANUAL_FIELDS = """evidence_class case decision_id superseding_id test_link source_sha256
original_contract current_contract original_contract_checks old_contract_old_code
new_contract_old_code new_contract_new_code old_reasoning_preserved review_burden
execution_seconds limits""".split()
PRIVATE_KEYS = re.compile(
    r"account|session|thread_id|raw.stream|stderr|stdout|api.key|access.token|refresh.token|"
    r"auth.token|password|secret|email", re.I,
)
PRIVATE_ITEM_TYPES = {"reasoning", "analysis", "thinking", "thread.started", "session.started"}
NUMERIC_FIELDS = ("seconds", "input_tokens", "cached_input_tokens", "output_tokens")


def _text(value: str, *, synthetic: bool = False) -> str:
    # JSON embedded in the authored history bundle needs the same key filtering
    # as ordinary JSON. Keep decision IDs so supersession remains auditable.
    def fenced(match: re.Match[str]) -> str:
        try:
            data = json.loads(match.group(1))
        except ValueError:
            return match.group(0)
        return "```json\n" + json.dumps(_clean(data, synthetic=synthetic), indent=2,
                                        sort_keys=True) + "\n```"
    value = re.sub(r"```json\s*\n(.*?)\n```", fenced, value, flags=re.S)
    if value.lstrip().startswith(("{", "[")):
        try:
            return json.dumps(_clean(json.loads(value), synthetic=synthetic), sort_keys=True)
        except ValueError:
            pass
    value = re.sub(r"<(?:analysis|thinking|think)>.*?</(?:analysis|thinking|think)>",
                   "<private-content-removed>", value, flags=re.S | re.I)
    # Match host locations, not ordinary division or application route literals.
    value = re.sub(r"(?<![\w:/])/(?:Users|home|private|tmp|var|opt|etc|root|mnt|srv|"
                   r"Volumes|Applications|workspace|workspaces)(?:/[^\s\"'<>`]*)?",
                   "<host-path>", value)
    value = re.sub(r"\b[A-Za-z]:[\\/][^\s\"'<>`]+", "<host-path>", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<email>", value)
    value = re.sub(r"(?i)\b(?:session[_ -]?id|thread[_ -]?id|account[_ -]?id)"
                   r"\s*[:=]\s*[\w.-]+", "<private-id>", value)
    return value


def _clean(value: Any, *, synthetic: bool = False) -> Any:
    if isinstance(value, str):
        return _text(value, synthetic=synthetic)
    if isinstance(value, list):
        return [_clean(item, synthetic=synthetic) for item in value
                if not isinstance(item, dict) or item.get("type") not in PRIVATE_ITEM_TYPES]
    if isinstance(value, dict):
        return {
            _text(str(key)): _clean(item, synthetic=synthetic)
            for key, item in value.items()
            if not PRIVATE_KEYS.search(str(key))
            and (synthetic or str(key).lower() not in {"reasoning", "analysis", "thinking"})
        }
    return value


def _pick(value: dict, fields: list[str]) -> dict:
    return _clean({key: value[key] for key in fields if key in value})


def _history(rows: list[dict]) -> list[dict]:
    return [_clean({key: row[key] for key in HISTORY_FIELDS if key in row}, synthetic=True)
            for row in rows]


def _client(value: dict) -> dict:
    result = _pick(value, CLIENT_FIELDS)
    if isinstance(value.get("preflight"), dict):
        result["preflight"] = _pick(value["preflight"], [
            "ready", "cli_version", "auth_method", "included_allowance_verified", "error",
        ])
    result["trace"] = []
    for event in value.get("trace", []):
        if not isinstance(event, dict):
            continue
        if event.get("type") == "fixture_tool" and event.get("tool") in TOOLS:
            payload = {key: event[key] for key in ("type", "tool", "arguments", "result", "status")
                       if key in event}
            history_read = event.get("tool") == "read_file" and isinstance(event.get("arguments"), dict) \
                and event["arguments"].get("path") == "DECISIONS.md"
            result["trace"].append(_clean(payload, synthetic=history_read))
        elif event.get("type") == "final_text":
            result["trace"].append(_pick(event, ["type", "text"]))
    return result


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def numeric_summary(values: list[Any]) -> dict:
    """Report measured mean/median and explicit missing observations."""
    known = [value for value in values if _number(value)]
    return {"known": len(known), "unknown": len(values) - len(known),
            "mean": statistics.mean(known) if known else None,
            "median": statistics.median(known) if known else None}


def summarize(manifest: dict, records: list[dict]) -> dict:
    """Describe all frozen slots, task pairs, and observed operational costs."""
    tasks = []
    for case in manifest["cases"]:
        for arm in manifest["arms"]:
            rows = [row for row in records if row["case"] == case["id"] and row["arm"] == arm]
            complete = [row for row in rows if row.get("completed")]
            item = {"case": case["id"], "arm": arm, "planned": len(rows),
                    "completed": len(complete), "correct": sum(
                        row.get("functional_correct") is True for row in complete),
                    "scores_all_draws": [row.get("score", 0) if row.get("completed") else 0
                                         for row in rows],
                    "history_injected": sum(row.get("history_injected") is True for row in rows),
                    "file_retrieved_before_first_write": sum(
                        row.get("file_retrieved_before_first_write") is True for row in rows)}
            for name in ("grade", "first_write_grade"):
                assessed = [row[name] for row in complete if row.get(name, {}).get("behavior_assessed")]
                item[name] = {"assessed": len(assessed), "unassessed": len(rows) - len(assessed),
                              **{metric: sum(grade.get(metric) is True for grade in assessed)
                                 for metric in METRICS}}
            tasks.append(item)
    pairs = []
    for case in manifest["cases"]:
        selected = {row["arm"]: row for row in tasks if row["case"] == case["id"]}
        for left, right in itertools.combinations(manifest["arms"], 2):
            a, b = selected[left], selected[right]
            delta = a["correct"] / a["planned"] - b["correct"] / b["planned"] \
                if a["planned"] and b["planned"] else None
            pairs.append({"case": case["id"], "left": left, "right": right,
                          "left_correct": a["correct"], "left_planned": a["planned"],
                          "right_correct": b["correct"], "right_planned": b["planned"],
                          "correctness_difference": delta,
                          "outcome": "unknown" if delta is None else
                          "tie" if delta == 0 else "left_higher" if delta > 0 else "right_higher"})
    costs = []
    for arm in manifest["arms"]:
        rows = [row for row in records if row["arm"] == arm]
        for field in NUMERIC_FIELDS:
            values = [row.get("client", {}).get("seconds") if field == "seconds"
                      else row.get("client", {}).get("usage", {}).get(field) for row in rows]
            costs.append({"arm": arm, "field": field, **numeric_summary(values)})
    return {"phase": manifest["phase"], "planned": len(manifest["schedule"]),
            "accounted": len(records), "independent_tasks": len(manifest["cases"]),
            "task_rows": tasks, "paired_tasks": pairs, "operational_observations": costs}


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _behavior(row: dict, field: str) -> str:
    grade = row.get(field, {})
    if not row.get("completed") or not grade.get("behavior_assessed"):
        return "unassessed"
    return ", ".join(metric for metric in METRICS if grade.get(metric)) or "none observed"


def report(manifest: dict, records: list[dict], summary: dict, manual: dict | None) -> str:
    """Render a descriptive report without converting a tiny pilot into a benefit claim."""
    lines = ["# Coding memory evidence", "", f"Phase: **{_cell(manifest['phase'])}**. "
             f"{len(records)} / {len(manifest['schedule'])} planned slots accounted for; "
             f"{len(manifest['cases'])} author-written synthetic tasks.", "",
             f"Configured model: `{_cell(manifest.get('model_configured', 'unknown'))}`; "
             f"effort: `{_cell(manifest.get('effort', 'unknown'))}`. Provider identity/build "
             "is only verified where the retained client metadata exposes it.", "",
             "## Every draw", "", "| Slot | Task | Arm | Draw | Status | Correct | Score | "
             "Final specific behavior | First-write specific behavior | History evidence |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in records:
        history = "injected" if row.get("history_injected") else \
            "file read before write" if row.get("file_retrieved_before_first_write") else \
            "file read not observed" if row["arm"] == "maintained-markdown" else "not observed / N/A"
        cells = [row["slot"], row["case"], row["arm"], row["draw"], row["status"],
                 row.get("functional_correct", "unknown"), row.get("score", "unknown"),
                 _behavior(row, "grade"), _behavior(row, "first_write_grade"), history]
        lines.append("| " + " | ".join(map(_cell, cells)) + " |")
    lines += ["", "Specific behavior uses predeclared behavioral predicates. Unassessed code, "
              "incomplete runs and arbitrary errors are not classified as a specific failure. "
              "Injection records delivery; it does not prove attention or correct application.", "",
              "| Task | Arm | Stage | Assessed | Unassessed | Repeat | Harmful avoidance | "
              "Irrelevant signature |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in summary["task_rows"]:
        for name in ("grade", "first_write_grade"):
            counts = row[name]
            cells = [row["case"], row["arm"], name, counts["assessed"], counts["unassessed"],
                     *[counts[metric] for metric in METRICS]]
            lines.append("| " + " | ".join(map(_cell, cells)) + " |")
    lines += ["",
              "## Task-paired correctness", "", "| Task | Left arm | Right arm | Left | Right | "
              "Difference (left − right) | Outcome |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in summary["paired_tasks"]:
        delta = row["correctness_difference"]
        cells = [row["case"], row["left"], row["right"],
                 f"{row['left_correct']}/{row['left_planned']}",
                 f"{row['right_correct']}/{row['right_planned']}",
                 "unknown" if delta is None else f"{delta:+.3f}", row["outcome"]]
        lines.append("| " + " | ".join(map(_cell, cells)) + " |")
    lines += ["", "All planned draws are in the denominator. Incomplete draws contribute zero "
              "to aggregate functional scoring and retain their actual status above. Ties and "
              "losses are retained; no best-of selection is used.", "", "## Operational observations", "",
              "| Arm | Component | Known | Unknown | Mean | Median |",
              "| --- | --- | --- | --- | --- | --- |"]
    for row in summary["operational_observations"]:
        cells = [row["arm"], row["field"], row["known"], row["unknown"],
                 *["unknown" if row[key] is None else f"{row[key]:.3f}" for key in ("mean", "median")]]
        lines.append("| " + " | ".join(map(_cell, cells)) + " |")
    lines += ["", "Unknown usage is not zero. Cached input is a subset of input and is not added "
              "again. Latency is elapsed client time, not a controlled speed comparison or a "
              "payment receipt.", "", "## Evidence and limits", "",
              "The manifest retains frozen settings, fixtures and source hashes. export-index.json "
              "separately hashes original permitted inputs and sanitized publication files. "
              "Sanitized history or prompts may have different bytes from their original input "
              "hashes. Raw execution logs, databases, account/session metadata and private client "
              "reasoning are excluded. Recorded synthetic decision rationale is retained.", "",
              "This is a descriptive method pilot. Repeated draws are not independent projects. "
              "Three small author-written tasks cannot establish a general confidence interval, "
              "significance, superiority, causal memory effect, or real-world product benefit. "
              "Native memory, autonomous capture and human review effort are not measured. "
              "Technical smoke and the method matrix are separate evidence sets and are not pooled."]
    if manual is not None:
        lines += ["", "## Separate manual decision-to-test demonstration", "",
                  "This deterministic synthetic demonstration is not a model draw and is not "
                  "pooled with the pilot. An explicit changed user preference authorizes "
                  "supersession; passing tests alone does not authorize a preference change.", "",
                  "| Check | Correct |", "| --- | --- |"]
        for key in ("old_contract_old_code", "new_contract_old_code", "new_contract_new_code"):
            lines.append(f"| {key} | {_cell(manual.get(key, {}).get('functional_correct', 'unknown'))} |")
        lines += ["", f"Old rationale preserved: {_cell(manual.get('old_reasoning_preserved', 'unknown'))}. "
                  "Human review time was not measured; see manual/result.json for the observed material."]
    return "\n".join(lines) + "\n"


def export_run(source: Path, output: Path, manual_input: Path | None = None) -> dict:
    """Export only named evidence artifacts into a new directory; make no live calls."""
    source, output = source.resolve(), output.resolve()
    if output.exists() or source == output or source in output.parents:
        raise ValueError("Choose a new output directory outside the private input")
    originals: dict[str, str] = {}
    files: dict[str, str] = {}

    def read(root: Path, relative: str, *, required: bool = False, prefix: str = "") -> str | None:
        path = root / relative
        if not path.exists():
            if required:
                raise ValueError(f"Missing required evidence: {relative}")
            return None
        if path.resolve() != path or root not in path.resolve().parents or not path.is_file():
            raise ValueError(f"Evidence must be a regular file inside its input: {relative}")
        data = path.read_bytes()
        originals[prefix + relative] = hashlib.sha256(data).hexdigest()
        return data.decode("utf-8")

    def emit(name: str, value: Any) -> None:
        files[name] = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"

    raw_manifest = json.loads(read(source, "manifest.json", required=True))
    manifest = _pick(raw_manifest, MANIFEST_FIELDS)
    manifest["cases"] = []
    for case in raw_manifest["cases"]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", str(case["id"])):
            raise ValueError("Unsafe case identity")
        public_case = _pick(case, CASE_FIELDS)
        public_case["history"] = _history(case.get("history", []))
        manifest["cases"].append(public_case)
    emit("manifest.json", manifest)
    schedule = manifest["schedule"]
    identities = {}
    for slot in schedule:
        if type(slot.get("slot")) is not int or slot["slot"] < 1 or slot["slot"] in identities:
            raise ValueError("Frozen schedule must have unique integer slots")
        if not all(re.fullmatch(r"[A-Za-z0-9_-]+", str(slot[key])) for key in ("case", "arm", "draw")):
            raise ValueError("Unsafe scheduled artifact identity")
        identities[slot["slot"]] = slot
    records_by_slot = {}
    results = read(source, "results.jsonl") or ""
    for line in results.splitlines():
        record = json.loads(line)
        slot = identities.get(record.get("slot"))
        if slot is None or any(record.get(key) != slot[key] for key in ("case", "arm", "draw")):
            raise ValueError("Result is outside the frozen schedule")
        if record["slot"] in records_by_slot:
            raise ValueError("Duplicate result; refusing selective publication")
        records_by_slot[record["slot"]] = record
    records = []
    for slot in schedule:
        relative = f"trials/{slot['slot']:02d}-{slot['case']}-{slot['arm']}-{slot['draw']}"
        per_trial = read(source, relative + "/result.json")
        record = records_by_slot.get(slot["slot"])
        if record is not None and per_trial is not None and json.loads(per_trial) != record:
            raise ValueError("Result receipts conflict; reconcile before publication")
        if record is None and per_trial is not None:
            record = json.loads(per_trial)
            if any(record.get(key) != slot[key] for key in ("slot", "case", "arm", "draw")):
                raise ValueError("Per-trial result contradicts the frozen slot")
        if record is None:
            record = {**slot, "completed": False, "status": "unverified",
                      "reason": "No result receipt; execution is unverified, not a model failure"}
        public = _pick(record, RECORD_FIELDS)
        if isinstance(record.get("client"), dict):
            public["client"] = _client(record["client"])
            emit(relative + "/client-trace.json", public["client"]["trace"])
        records.append(public)
        emit(relative + "/result.json", public)
        for name in ("prompt.txt", "solution.py", "visible-case.json", "candidate-1.py", "candidate-2.py"):
            data = read(source, relative + "/" + name)
            if data is not None:
                if name.endswith(".json"):
                    emit(relative + "/" + name, _clean(json.loads(data), synthetic=True))
                else:
                    files[relative + "/" + name] = _text(data, synthetic=name == "prompt.txt")
        fixture = read(source, relative + "/fixture-trace.jsonl")
        if fixture is not None:
            actions = [json.loads(line) for line in fixture.splitlines()]
            public_actions = [_pick(action, ["at", "tool", "path", "characters", "write", "result"])
                              for action in actions if action.get("tool") in TOOLS]
            files[relative + "/fixture-trace.jsonl"] = "".join(
                json.dumps(action, sort_keys=True) + "\n" for action in public_actions)
    for case in manifest["cases"]:
        for suffix in ("json", "md"):
            name = f"captures/{case['id']}.{suffix}"
            data = read(source, name)
            if data is not None:
                if suffix == "json":
                    emit(name, _history(json.loads(data)))
                else:
                    files[name] = _text(data, synthetic=True)
    execution = read(source, "execution.json")
    if execution is not None:
        receipt = json.loads(execution)
        public_receipt = _pick(receipt, ["started_at", "finished_at", "manifest_sha256", "status"])
        public_receipt["client"] = _pick(receipt.get("client", {}), [
            "ready", "cli_version", "auth_method", "included_allowance_verified", "error",
        ])
        emit("execution.json", public_receipt)
    # Recompute against the accounted schedule, so a truncated original summary
    # cannot silently make failed or unattempted draws disappear.
    read(source, "summary.json")
    files["results.jsonl"] = "".join(json.dumps(row, sort_keys=True) + "\n" for row in records)
    summary = summarize(manifest, records)
    emit("summary.json", summary)
    manual = None
    if manual_input is not None:
        manual_root = manual_input.resolve()
        value = json.loads(read(manual_root, "result.json", required=True, prefix="manual/"))
        manual = _pick(value, MANUAL_FIELDS)
        manual["history_newest_first"] = _history(value.get("history_newest_first", []))
        emit("manual/result.json", manual)
        for name in ("before.py", "after.py"):
            data = read(manual_root, name, prefix="manual/")
            if data is not None:
                files["manual/" + name] = _text(data)
    files["REPORT.md"] = report(manifest, records, summary, manual)
    exported_hashes = {name: hashlib.sha256(value.encode()).hexdigest()
                       for name, value in sorted(files.items())}
    index = {"format": "coding-memory-publication/1", "phase": manifest["phase"],
             "original_input_sha256": originals,
             "exported_sha256": exported_hashes,
             "transformed": {name: originals[name] != digest if name in originals else None
                             for name, digest in exported_hashes.items()},
             "policy": "Explicit artifact projection; no databases or raw client streams. "
                       "Synthetic decision rationale retained; client reasoning excluded. "
                       "Independent inspection is required before publication."}
    emit("export-index.json", index)
    output.mkdir(parents=True, exist_ok=False)
    for name, value in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    return {"phase": manifest["phase"], "planned": len(schedule), "accounted": len(records),
            "exported_files": len(files), "model_calls": 0}


def main() -> None:
    """Export a smoke or matrix run separately, optionally with a manual experiment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manual-input", type=Path)
    args = parser.parse_args()
    print(json.dumps(export_run(args.input, args.output, args.manual_input), sort_keys=True))


if __name__ == "__main__":
    main()
