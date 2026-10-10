"""Actual function execution, behavioral controls and the grader boundary."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1] / "bench" / "coding_memory"
GRADER = runpy.run_path(str(BENCH / "grade.py"))
grade_candidate = GRADER["grade_candidate"]
CASES = json.loads((BENCH / "fixtures.json").read_text())
METRICS = ("repeated_rejected_path", "harmful_avoidance", "irrelevant_interference")


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_reference_code_passes_real_checks(case: dict) -> None:
    """Reference implementations satisfy public/hidden cases without adverse flags."""
    result = grade_candidate(case, case["reference_solution"])
    assert result["execution_status"] == "completed", result
    assert result["functional_correct"] and result["score"] == 1
    assert result["behavior_assessed"]
    assert all(row["passed"] for row in result["checks"])
    assert not any(result[metric] for metric in METRICS)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_declared_bad_controls_trigger_only_their_actual_behavior(case: dict) -> None:
    """Predeclared wrong outputs distinguish the three failure mechanisms."""
    result = grade_candidate(case, case["bad_solution"])
    assert result["execution_status"] == "completed", result
    assert not result["functional_correct"]
    assert 0 < result["score"] < 1
    assert result["behavior_assessed"]
    assert [metric for metric in METRICS if result[metric]] == [case["behavior_probe"]["metric"]]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_arbitrary_errors_are_not_counted_as_a_behavioral_failure(case: dict) -> None:
    """A failed task is not evidence for one of the specific rejected approaches."""
    source = case["project_files"]["solution.py"]
    result = grade_candidate(case, source)
    assert result["execution_status"] == "completed"
    assert not result["functional_correct"] and result["score"] == 0
    assert not any(result[metric] for metric in METRICS)
    empty = source.replace("return None", "return []")
    result = grade_candidate(case, empty)
    assert not result["functional_correct"]
    assert not any(result[metric] for metric in METRICS)


def test_changed_preference_preserves_old_reason_without_treating_it_as_a_failure() -> None:
    """Retaining the superseded preference harms this task; implementing the change passes."""
    case = CASES[1]
    old, new = case["history"]
    assert old["change_type"] == "reject" and new["change_type"] == "supersede"
    assert new["supersedes"] == old["id"] and new["timestamp"] > old["timestamp"]
    assert old["reasoning"] and new["reasoning"]
    assert json.loads(old["metadata"])["technical_failure"] is False
    retained = grade_candidate(case, case["retained_solution"])
    updated = grade_candidate(case, case["superseded_solution"])
    assert retained["harmful_avoidance"] and not retained["repeated_rejected_path"]
    assert updated["functional_correct"] and not updated["harmful_avoidance"]


def test_unrelated_history_is_separate_from_the_task_entity() -> None:
    """The irrelevant-control arm cannot silently become relevant task evidence."""
    case = CASES[2]
    assert all(event["entity_path"] != case["entity"] for event in case["history"])
    assert "sizes above two are explicitly supported" in case["task"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_public_feedback_never_leaks_private_checks_or_behavior_labels(case: dict) -> None:
    """Agent-visible feedback consists only of the same public checklist in every arm."""
    result = grade_candidate(case, case["bad_solution"], public_only=True)
    assert result["check_scope"] == "public"
    assert {row["id"] for row in result["checks"]} == {row["id"] for row in case["public_checks"]}
    assert not any(key in result for key in (*METRICS, "behavior_metric", "behavior_evidence_ids"))
    encoded = json.dumps(result)
    assert all(check["id"] not in encoded for check in case["private_checks"])


def test_public_success_does_not_imply_hidden_success() -> None:
    """A schedule truncated after the visible examples earns only partial credit."""
    case = CASES[0]
    source = case["reference_solution"].replace("range(count)", "range(min(count, 5))")
    visible = grade_candidate(case, source, public_only=True)
    full = grade_candidate(case, source)
    assert visible["functional_correct"] and visible["check_scope"] == "public"
    assert not full["functional_correct"] and 0 < full["score"] < 1
    assert not full["repeated_rejected_path"]


def test_input_mutation_fails_even_when_return_value_is_correct() -> None:
    """Passing output examples cannot hide modification of the caller's list."""
    case = CASES[2]
    source = '''def chunk_items(items, size):
    result = [items[start:start + size] for start in range(0, len(items), size)]
    items.append("unexpected")
    return result
'''
    result = grade_candidate(case, source)
    assert result["execution_status"] == "completed"
    assert not result["functional_correct"] and result["score"] == 0
    assert all(not row["input_unchanged"] for row in result["checks"])


@pytest.mark.parametrize("body", [
    "import os\n    return os.environ",
    'return open("/etc/passwd").read()',
    'return retry_delays.__globals__',
    'return getattr(retry_delays, "__globals__")',
    'return __builtins__',
    'return (lambda: 1)()',
    'return [value for value in ().__class__.__base__.__subclasses__()]',
    'import socket\n    return socket.socket()',
])
def test_host_access_and_introspection_are_rejected_before_execution(body: str) -> None:
    """Only the exercise's pure-function language reaches a subprocess."""
    result = grade_candidate(CASES[0], "def retry_delays(count, base, cap):\n    " + body + "\n")
    assert result["execution_status"] == "invalid_source"
    assert result["score"] == 0 and not result["behavior_assessed"]
    assert not any(result[metric] for metric in METRICS)


def test_candidate_cannot_create_host_files(tmp_path: Path) -> None:
    """Reject a concrete filesystem write, leaving the host marker absent."""
    marker = tmp_path / "must-not-exist.txt"
    source = f'def retry_delays(count, base, cap):\n    return open({str(marker)!r}, "w")\n'
    result = grade_candidate(CASES[0], source)
    assert result["execution_status"] == "invalid_source"
    assert not marker.exists()


def test_nonterminating_candidate_is_bounded() -> None:
    """An accepted loop cannot hang the parent grader or invent a repeat event."""
    source = "def retry_delays(count, base, cap):\n    while True:\n        pass\n"
    result = grade_candidate(CASES[0], source)
    assert result["execution_status"] == "completed"
    assert all(row["error"] == "operation_limit" for row in result["checks"])
    assert result["score"] == 0 and not result["behavior_assessed"]
    assert not any(result[metric] for metric in METRICS)


def test_large_return_is_bounded_without_misclassifying_behavior() -> None:
    """Huge output cannot flood the caller or be treated as one of the target failures."""
    result = grade_candidate(CASES[0], "def retry_delays(count, base, cap):\n    return [1] * 5000\n")
    assert result["execution_status"] == "completed"
    assert result["score"] == 0 and not result["behavior_assessed"]
    assert all(row["error"] == "operation_limit" for row in result["checks"])


def test_weighted_score_comes_from_the_predeclared_checklist() -> None:
    """Partial credit is reproducible arithmetic over passed checks, not a text judge."""
    case = json.loads(json.dumps(CASES[0]))
    case["public_checks"][0]["weight"] = 2
    result = grade_candidate(case, case["bad_solution"])
    passed = sum(row["weight"] for row in result["checks"] if row["passed"])
    total = sum(check["weight"] for check in [*case["public_checks"], *case["private_checks"]])
    assert result["score"] == passed / total


def test_nonfinite_and_non_json_returns_are_not_successes() -> None:
    """A set or nonfinite float is not an accepted JSON-shaped function result."""
    for value in ("{1, 2}", 'float("nan")'):
        result = grade_candidate(CASES[0], f"def retry_delays(count, base, cap):\n    return {value}\n")
        assert result["execution_status"] == "completed" and result["score"] == 0
        assert not result["behavior_assessed"]


@pytest.mark.parametrize("expression", [
    "list(range(5000))",
    "[i for i in range(1000) for j in range(1000)]",
    '"%10000s" % "x"',
    "str([[1] * 1000] * 1000)",
])
def test_allocation_and_nested_iteration_controls_are_bounded(expression: str) -> None:
    """Use modest counterexamples; never run an unguarded host-OOM experiment."""
    source = f"def retry_delays(count, base, cap):\n    return {expression}\n"
    result = grade_candidate(CASES[0], source)
    assert result["execution_status"] == "completed"
    assert all(row["error"] == "operation_limit" for row in result["checks"])


def test_giant_single_operations_are_rejected_before_the_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub dangerous operators so a regression fails safely rather than allocating."""
    def must_not_execute(*args: object) -> None:
        raise AssertionError("Dangerous operator was reached")

    operators = GRADER["operator"]
    monkeypatch.setattr(operators, "pow", must_not_execute)
    monkeypatch.setattr(operators, "mul", must_not_execute)
    budget = GRADER["_Budget"]()
    for operation, left, right in (("Pow", 2, 1_000_000_000), ("Mult", [1], 1_000_000_000)):
        with pytest.raises(GRADER["_ExecutionLimit"]):
            budget.binary(operation, left, right)


def test_augmented_list_assignment_retains_real_mutation_semantics() -> None:
    """Instrumentation must not turn a mutating Python solution into a pure one."""
    source = '''def chunk_items(items, size):
    result = [items[start:start + size] for start in range(0, len(items), size)]
    items += [99]
    return result
'''
    result = grade_candidate(CASES[2], source)
    assert result["execution_status"] == "completed" and result["score"] == 0
    assert all(not row["input_unchanged"] for row in result["checks"])


def test_wall_timeout_has_its_own_failure_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real child killed during startup is not a completed or behavioral trial."""
    monkeypatch.setitem(grade_candidate.__globals__, "WALL_TIMEOUT_SECONDS", 0.000001)
    result = grade_candidate(CASES[0], CASES[0]["reference_solution"])
    assert result["execution_status"] == "timeout"
    assert result["score"] == 0 and not result["behavior_assessed"]


@pytest.mark.parametrize("case_index,source", [
    (0, '''def retry_delays(count: int, base: int, cap: int) -> list[int]:
    result = []
    delay = base
    for index in range(count):
        result.append(min(delay, cap))
        delay *= 2
    return result
'''),
    (1, '''def visible_messages(messages, viewer_id):
    result = []
    for message in messages:
        if message.get("visibility") == "public" or viewer_id in message.get("participants"):
            result.extend([message["id"]])
    return result
'''),
    (2, '''def chunk_items(items, size):
    result = []
    index = 0
    while index < len(items):
        result.append(items[index:index + size])
        index += size
    return result
'''),
])
def test_ordinary_imperative_solutions_remain_valid(case_index: int, source: str) -> None:
    """Allocation guards preserve useful loops, annotations and supported methods."""
    result = grade_candidate(CASES[case_index], source)
    assert result["functional_correct"] and result["score"] == 1, result
