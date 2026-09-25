"""Keep a model's written claim separate from an observed successful action."""

import json
import runpy
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1] / "bench" / "decision_memory"
RUNNER = runpy.run_path(str(BENCH / "run.py"))
CASE = json.loads((BENCH / "cases.json").read_text())[0]


def events_for(arm: str, *, late: bool = False, wrong_id: bool = False) -> list[dict]:
    """Create observable tool calls and results, with a deliberately bad variant."""
    tools = [f"mcp__fixture__{n}" for n in ("read_project", "read_decisions", "apply_choice")]
    if arm == "selvedge-pull":
        tools.append("mcp__selvedge__prior_attempts")
    query = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "query",
                    "name": "mcp__selvedge__prior_attempts",
                    "input": {},
                }
            ]
        },
    }
    retrieved = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "query",
                    "content": "wrong" if wrong_id else "record-1",
                }
            ]
        },
    }
    edit = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "edit",
                    "name": "mcp__fixture__apply_choice",
                    "input": {},
                }
            ]
        },
    }
    sequence = [edit, query, retrieved] if late else [query, retrieved, edit]
    return [
        {"type": "system", "subtype": "init", "tools": tools, "model": "claude-sonnet-4-6"},
        *sequence,
        {"type": "result", "is_error": False},
    ]


def test_application_and_retrieval_are_separate() -> None:
    """A retrieved record can still be followed by an incorrect change."""
    result = RUNNER["score"](
        CASE, "selvedge-pull", events_for("selvedge-pull"), {"value": "8"}, ["record-1"], 0
    )
    assert result["completed"] and result["retrieved_before_first_edit"]
    assert not result["correct_application"]


@pytest.mark.parametrize("kwargs", [{"late": True}, {"wrong_id": True}])
def test_late_or_wrong_record_does_not_count(kwargs: dict) -> None:
    """Only the actual seeded record, returned before editing, counts."""
    result = RUNNER["score"](
        CASE,
        "selvedge-pull",
        events_for("selvedge-pull", **kwargs),
        {"value": CASE["expected"]},
        ["record-1"],
        0,
    )
    assert result["correct_application"]
    assert not result["retrieved_before_first_edit"]


@pytest.mark.parametrize("configuration,exit_code", [({}, 0), ({"value": "2"}, 1)])
def test_failed_process_or_missing_edit_is_not_a_success(
    configuration: dict, exit_code: int
) -> None:
    """Fluent text and a failed subprocess cannot produce a passing trial."""
    result = RUNNER["score"](
        CASE, "selvedge-pull", events_for("selvedge-pull"), configuration, ["record-1"], exit_code
    )
    assert not result["completed"] and not result["correct_application"]


def test_tool_configuration_must_be_isolated_and_ready() -> None:
    """Missing MCP tools and unexpected filesystem access invalidate the trial."""
    events = events_for("selvedge-pull")
    for tools in ([], [*events[0]["tools"], "Bash"]):
        events[0]["tools"] = tools
        result = RUNNER["score"](
            CASE, "selvedge-pull", events, {"value": CASE["expected"]}, ["record-1"], 0
        )
        assert not result["completed"] and not result["tool_configuration_valid"]


def test_public_trace_excludes_private_thinking_blocks() -> None:
    """Share observable explanations and tool evidence only."""
    event = {
        "message": {
            "content": [
                {"type": "thinking", "thinking": "private"},
                {"type": "text", "text": "explanation"},
            ]
        }
    }
    assert RUNNER["content_blocks"](event) == [{"type": "text", "text": "explanation"}]


def test_subscription_environment_ignores_api_billing_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runner must not silently use an API key or alternate endpoint."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    env = RUNNER["clean_env"]()
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_BASE_URL" not in env
    assert env["ENABLE_TOOL_SEARCH"] == "false"
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
