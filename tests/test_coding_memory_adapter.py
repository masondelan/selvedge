"""Observable client evidence must remain bounded, private and honest."""

import json
from pathlib import Path

import pytest

from bench.coding_memory import codex_adapter as adapter
from bench.coding_memory.run import numeric_summary


def events(usage=None):
    return [
        {"type": "thread.started", "thread_id": "private-id"},
        {"type": "item.started", "item": {
            "id": "a", "type": "mcp_tool_call", "server": "fixture", "tool": "read_project"}},
        {"type": "item.completed", "item": {
            "id": "a", "type": "mcp_tool_call", "server": "fixture", "tool": "read_project",
            "result": {"task": "visible task"}}},
        {"type": "item.completed", "item": {
            "id": "r", "type": "reasoning", "text": "PRIVATE ANALYSIS"}},
        {"type": "item.completed", "item": {
            "id": "f", "type": "agent_message", "text": "Implemented."}},
        {"type": "turn.completed", "usage": usage or {
            "input_tokens": 12, "cached_input_tokens": 3, "output_tokens": 7}},
    ]


def test_only_visible_evidence_and_no_double_count(tmp_path):
    result = adapter.parse_events(events(), adapter.MODEL, tmp_path)
    assert result["completed"]
    assert result["fixture_tool_calls"] == 1
    assert result["usage"]["input_tokens"] == 12
    assert result["usage"]["total_tokens"] is None
    assert result["model_resolved"] == "unknown"
    assert "PRIVATE ANALYSIS" not in json.dumps(result)
    assert "private-id" not in json.dumps(result)
    assert result["trace"][-1] == {"type": "final_text", "text": "Implemented."}


def test_partial_usage_is_unknown_not_zero_or_invalid(tmp_path):
    stream = events({"input_tokens": 12})
    stream.append({"type": "turn.completed", "usage": {"output_tokens": 2}})
    result = adapter.parse_events(stream, adapter.MODEL, tmp_path)
    assert result["completed"]
    assert not result["usage_complete"]
    assert result["usage"]["input_tokens"] is None
    assert result["usage"]["output_tokens"] is None
    assert numeric_summary([None, 12, 24]) == {
        "observed": 2, "unknown": 1, "mean": 18, "median": 18}


@pytest.mark.parametrize("item", [
    {"type": "command_execution", "command": "read private files"},
    {"type": "file_change"},
    {"type": "unknown_new_action"},
    {"id": "bad", "type": "mcp_tool_call", "server": "other", "tool": "read_project"},
    {"id": "bad", "type": "mcp_tool_call", "server": "fixture", "tool": "secret"},
])
def test_unexpected_tool_is_invalid(tmp_path, item):
    result = adapter.parse_events(events() + [{"type": "item.completed", "item": item}],
                                  adapter.MODEL, tmp_path)
    assert not result["completed"]
    assert "nonfixture_tool_activity" in result["invalid_reasons"]


def test_model_mismatch_and_observation_boundary(tmp_path):
    result = adapter.parse_events(
        events() + [{"type": "session.started", "model": "different-model"}],
        adapter.MODEL, tmp_path, max_tool_calls=1)
    assert not result["completed"]
    assert "unexpected_resolved_model" in result["invalid_reasons"]
    assert "observed_tool_budget_reached" in result["invalid_reasons"]


def test_command_and_env_do_not_inherit_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "another-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "credential")
    env = adapter.clean_env()
    assert not any(key in env for key in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY"))
    command = adapter.command("test prompt", tmp_path, "/python")
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--ephemeral" in command
    assert "read-only" in command
    assert 'forced_login_method="chatgpt"' in command
    assert 'web_search="disabled"' in command
    assert 'mcp_servers.fixture.default_tools_approval_mode="prompt"' in command
    for tool in adapter.FIXTURE_TOOLS:
        assert f'mcp_servers.fixture.tools.{tool}.approval_mode="approve"' in command
    assert 'approval_policy="never"' in command
    assert command[-1] == "-"
    assert "test prompt" not in command


def test_redacts_machine_and_account_metadata(tmp_path):
    stream = events()
    stream[2]["item"]["result"] = {
        "text": f"{tmp_path}/solution.py at {Path.home()} owner@example.com",
        "account_id": "secret", "session_id": "secret", "thinking": "secret",
    }
    result = adapter.parse_events(stream, adapter.MODEL, tmp_path)
    encoded = json.dumps(result)
    assert str(tmp_path) not in encoded
    assert "owner@example.com" not in encoded
    assert "secret" not in encoded


def test_fixture_permission_or_transport_failure_is_invalid(tmp_path):
    stream = events()
    stream[2]["item"].update(status="failed", error={
        "message": "MCP tool call requires approval, but approval policy is never"})
    result = adapter.parse_events(stream, adapter.MODEL, tmp_path)
    assert not result["completed"]
    assert "fixture_tool_failure" in result["invalid_reasons"]
    assert "requires approval" in result["trace"][0]["error"]["message"]
