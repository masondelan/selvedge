"""Manually link a synthetic preference decision to ordinary before/after tests.

No model call, test generation service, policy enforcement or new product API.
The explicit user change authorizes supersession; tests only check behavior.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import time
from pathlib import Path

from bench.coding_memory.grade import grade_candidate
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage

HERE = Path(__file__).resolve().parent


def demonstrate(output: Path) -> dict:
    """Reproduce old-policy pass, new-policy failure and corrected-policy pass."""
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    case = next(case for case in json.loads((HERE / "fixtures.json").read_text())
                if case["id"] == "message-visibility")
    before = copy.deepcopy(case)
    before["task"] = (
        "Original synthetic user preference: all messages are participant-only; "
        "do not offer a public feed. Preserve order and input values."
    )
    for check in before["public_checks"] + before["private_checks"]:
        messages, viewer = check["args"]
        check["label"] = f"Original participant-only filtering ({check['id']})"
        check["expected"] = [message["id"] for message in messages
                             if viewer in message["participants"]]
    store = SelvedgeStorage(output / "memory.db")
    old_record = dict(case["history"][0])
    # The explicit decision-to-test link is existing metadata, not a schema addition.
    metadata = json.loads(old_record.get("metadata", "{}"))
    metadata.update(test_link="fixtures.json#message-visibility",
                    test_command="python -m bench.coding_memory.manual_decision_test --output NEW_DIR")
    old_record["metadata"] = json.dumps(metadata, sort_keys=True)
    old = store.log_event(ChangeEvent(**old_record))
    before_result = grade_candidate(before, case["retained_solution"])
    # Historical-contract checks have different expectations; new-contract
    # behavior predicates must not be interpreted against that historical run.
    for key in ("repeated_rejected_path", "harmful_avoidance", "irrelevant_interference",
                "behavior_assessed", "behavior_metric", "behavior_evidence_ids"):
        before_result.pop(key, None)
    changed_result = grade_candidate(case, case["retained_solution"])
    fixed_result = grade_candidate(case, case["superseded_solution"])
    if not before_result["functional_correct"] or changed_result["functional_correct"] \
            or not fixed_result["functional_correct"]:
        raise AssertionError("Manual before/after demonstration did not reproduce its contract")
    # This is an explicit author action following the stated synthetic user change.
    # No test output automatically performs this write or decides applicability.
    reopened = store.log_supersede(
        entity_path=case["entity"], supersedes=old.id, agent="synthetic-author",
        reasoning="The synthetic user explicitly requested a public feed while retaining "
                  "participant-only chats. The old chat-only preference was valid then. "
                  "The changed tests verify the new requested behavior; their pass alone "
                  "is not authorization to change a preference.",
    )
    history = store.get_entity_history(case["entity"])
    preserved = next(row for row in history if row["id"] == old.id)
    assert preserved["reasoning"] == old.reasoning
    assert preserved["superseded_by"] == reopened.id
    assert history[0]["id"] == reopened.id
    result = {
        "evidence_class": "synthetic manual feasibility demonstration; no model intervention",
        "case": case["id"], "decision_id": old.id, "superseding_id": reopened.id,
        "test_link": metadata["test_link"],
        "source_sha256": {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
                           for name in ("fixtures.json", "grade.py", "manual_decision_test.py")},
        "original_contract": before["task"], "current_contract": case["task"],
        "original_contract_checks": before["public_checks"] + before["private_checks"],
        "old_contract_old_code": before_result,
        "new_contract_old_code": changed_result,
        "new_contract_new_code": fixed_result,
        "history_newest_first": history, "old_reasoning_preserved": True,
        "review_burden": {
            "observable_material": "one user-preference change, two event rows, "
                                   "two implementations and three full checklist runs",
            "check_count_per_run": len(case["public_checks"]) + len(case["private_checks"]),
            "human_review_seconds": None, "human_review_measurement": "not measured",
        },
        "execution_seconds": round(time.monotonic() - started, 3),
        "limits": "Preference validity comes from the explicit user request. These tiny tests "
                  "do not prove security for a real feed or automatic decision applicability.",
    }
    (output / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (output / "before.py").write_text(case["retained_solution"])
    (output / "after.py").write_text(case["superseded_solution"])
    return result


def main() -> None:
    """Run the deterministic behavioral demonstration in a new output directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    result = demonstrate(parser.parse_args().output)
    print(json.dumps({"before": result["old_contract_old_code"]["functional_correct"],
                      "after_old_code": result["new_contract_old_code"]["functional_correct"],
                      "after_new_code": result["new_contract_new_code"]["functional_correct"],
                      "old_reasoning_preserved": result["old_reasoning_preserved"]}))


if __name__ == "__main__":
    main()
