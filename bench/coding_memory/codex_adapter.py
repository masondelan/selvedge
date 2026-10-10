"""Subscription-authenticated Codex adapter for the bounded coding fixture.

Importing, constructing a command, and preflight do not call a model. Only
``run_client`` executes inference. CLI sandboxing does not sandbox an MCP server:
the trusted fixture enforces its own filename, write and test restrictions.
Feature exclusions reduce ambient capabilities; they are not a claim that every
built-in tool disappears. Any observed non-fixture action invalidates the run.
"""

from __future__ import annotations

import json
import os
import re
import selectors
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

FIXTURE_TOOLS = frozenset({"read_project", "read_file", "write_file", "run_checks"})
MODEL = "gpt-6-sol"
EFFORT = "medium"
DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "shell_snapshot", "apps", "plugins",
    "browser_use", "browser_use_external", "computer_use", "in_app_browser",
    "image_generation", "view_image", "multi_agent", "multi_agent_v2", "hooks",
    "memories", "skill_search", "skill_mcp_dependency_install", "tool_suggest",
    "goals", "sleep_tool", "workspace_dependencies", "unbounded_connection_retries",
)
MAX_STREAM_BYTES = 8 * 1024 * 1024


def clean_env() -> dict[str, str]:
    """Retain saved-login locations and basic OS settings, never API credentials."""
    keep = {
        "PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "TMP", "TEMP", "LANG",
        "LC_ALL", "LC_CTYPE", "TERM", "CODEX_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR",
    }
    return {key: value for key, value in os.environ.items() if key in keep}


def preflight() -> dict[str, Any]:
    """Check CLI version and saved ChatGPT login without exposing account data."""
    result: dict[str, Any] = {"ready": False, "cli_version": "", "auth_method": "unknown"}
    try:
        version = subprocess.run(
            ["codex", "--version"], env=clean_env(), capture_output=True,
            text=True, timeout=10, check=False,
        )
        auth = subprocess.run(
            ["codex", "login", "status"], env=clean_env(), capture_output=True,
            text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        result["error"] = "preflight_unavailable"
        return result
    match = re.search(r"codex-cli\s+([A-Za-z0-9.+-]+)", version.stdout)
    result["cli_version"] = match.group(1) if match else "unknown"
    if auth.returncode == 0 and "Logged in using ChatGPT" in auth.stdout + auth.stderr:
        result["auth_method"] = "chatgpt"
    result["ready"] = version.returncode == 0 and bool(match) and result["auth_method"] == "chatgpt"
    # Login proves the authentication route, not remaining allowance or credits.
    result["included_allowance_verified"] = False
    return result


def command(
    prompt: str, run_dir: Path, python_executable: str,
    model: str = MODEL, effort: str = EFFORT,
) -> list[str]:
    """Build one fresh CLI invocation; pass ``prompt`` through stdin when running."""
    if not prompt.strip() or not model or effort not in {"low", "medium", "high", "xhigh", "max"}:
        raise ValueError("A prompt, exact model identifier and supported effort are required")
    root = run_dir.resolve()
    fixture = Path(__file__).with_name("fixture_server.py").resolve()
    args = [
        "codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
        "--json", "--color", "never", "--sandbox", "read-only", "--skip-git-repo-check",
        "--cd", str(root), "--model", model,
    ]
    config: dict[str, Any] = {
        "forced_login_method": "chatgpt", "model_provider": "openai",
        "approval_policy": "never", "model_reasoning_effort": effort,
        "web_search": "disabled", "hide_agent_reasoning": True,
        "project_doc_max_bytes": 0, "history.persistence": "none",
        "memories.generate_memories": False, "memories.use_memories": False,
        "skills.include_instructions": False, "agents.enabled": False,
        "apps._default.enabled": False, "shell_environment_policy.inherit": "none",
        "mcp_servers.fixture.command": python_executable,
        "mcp_servers.fixture.args": [str(fixture)],
        "mcp_servers.fixture.cwd": str(root),
        "mcp_servers.fixture.enabled": True, "mcp_servers.fixture.required": True,
        "mcp_servers.fixture.enabled_tools": sorted(FIXTURE_TOOLS),
        "mcp_servers.fixture.default_tools_approval_mode": "prompt",
        "mcp_servers.fixture.startup_timeout_sec": 15,
        "mcp_servers.fixture.tool_timeout_sec": 20,
        "mcp_servers.fixture.env.SELVEDGE_CODING_RUN": str(root),
        "mcp_servers.fixture.env.SELVEDGE_TELEMETRY": "0",
        "mcp_servers.fixture.env.SELVEDGE_QUIET": "1",
        "mcp_servers.fixture.env.PYTHONDONTWRITEBYTECODE": "1",
    }
    # Explicit grants cover only these trusted bounded fixture operations.
    # "auto" still prompts for unclassified MCP tools under policy="never".
    for tool in FIXTURE_TOOLS:
        config[f"mcp_servers.fixture.tools.{tool}.approval_mode"] = "approve"
    for key, value in config.items():
        # JSON scalars/arrays are valid TOML values here; no shell interpolation.
        args.extend(["-c", f"{key}={json.dumps(value)}"])
    for feature in DISABLED_FEATURES:
        args.extend(["--disable", feature])
    args.append("-")
    return args


def _sanitize(value: Any, run_dir: Path) -> Any:
    if isinstance(value, str):
        value = value.replace(str(run_dir.resolve()), "<trial>")
        value = value.replace(str(Path.home()), "<home>")
        value = re.sub(r"(?:/Users/|/home/|/private/|/tmp/|/var/folders/)[^\s\"'<>]*", "<path>", value)
        value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<email>", value)
        value = re.sub(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", "<id>", value)
        return value
    if isinstance(value, list):
        return [_sanitize(item, run_dir) for item in value]
    if isinstance(value, dict):
        return {
            key: _sanitize(item, run_dir) for key, item in value.items()
            if not any(part in key.lower() for part in (
                "reasoning", "thinking", "session", "thread_id", "account", "token_secret",
            ))
        }
    return value


def _fixture_call(item: dict[str, Any]) -> bool:
    return item.get("server") == "fixture" and item.get("tool") in FIXTURE_TOOLS


def _is_action(item: dict[str, Any]) -> bool:
    kind = str(item.get("type", ""))
    # Fail closed on new action item types instead of silently accepting a new
    # tool surface. Passive analysis is ignored and never enters public traces.
    return bool(kind) and kind not in {"reasoning", "agent_message"}


def parse_events(
    events: list[dict[str, Any]], model: str, run_dir: Path, max_tool_calls: int = 12,
) -> dict[str, Any]:
    """Score observed CLI events and export only visible fixture actions/final text."""
    trace: list[dict[str, Any]] = []
    calls: set[str] = set()
    reasons: set[str] = set()
    resolved: set[str] = set()
    usage_keys = ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")
    usage: list[dict[str, int | None]] = []
    final_text = ""
    completed = False
    for event in events:
        event_type = event.get("type", "")
        if event_type in {"thread.started", "session.started", "system.init"}:
            actual_model = event.get("model")
            if isinstance(actual_model, str) and actual_model:
                resolved.add(actual_model)
        if event_type in {"error", "turn.failed"}:
            reasons.add("client_error")
        if event_type == "turn.completed":
            completed = True
            values = event.get("usage", {})
            if not isinstance(values, dict):
                values = {}
            usage.append({
                key: value if isinstance(value := values.get(key), int)
                and not isinstance(value, bool) and value >= 0 else None
                for key in usage_keys
            })
        item = event.get("item", {})
        if not isinstance(item, dict) or not str(event_type).startswith("item."):
            continue
        kind = item.get("type", "")
        if kind == "mcp_tool_call":
            if not _fixture_call(item):
                reasons.add("nonfixture_tool_activity")
                continue
            call_id = item.get("id", item.get("call_id"))
            if not isinstance(call_id, str) or not call_id:
                reasons.add("missing_tool_call_id")
                continue
            calls.add(call_id)
            if event_type == "item.completed":
                if item.get("status") == "failed" or item.get("error"):
                    reasons.add("fixture_tool_failure")
                trace.append(_sanitize({
                    "type": "fixture_tool", "tool": item["tool"],
                    "arguments": item.get("arguments", {}),
                    "result": item.get("result"), "error": item.get("error"),
                    "status": item.get("status", "unknown"),
                }, run_dir))
        elif _is_action(item):
            reasons.add("nonfixture_tool_activity")
        elif kind == "agent_message" and event_type == "item.completed":
            # Analysis/reasoning items are deliberately excluded, not summarized.
            if item.get("phase") not in {"analysis", "commentary"}:
                final_text = str(item.get("text", ""))
    if len(calls) >= max_tool_calls:
        reasons.add("observed_tool_budget_reached")
    if resolved and resolved != {model}:
        reasons.add("unexpected_resolved_model")
    if not completed:
        reasons.add("no_completed_turn")
    if not calls:
        reasons.add("no_fixture_tool_activity")
    if final_text:
        trace.append({"type": "final_text", "text": _sanitize(final_text, run_dir)})
    return {
        "completed": completed and not reasons, "invalid_reasons": sorted(reasons),
        "model_configured": model, "model_resolved": next(iter(resolved)) if len(resolved) == 1 else "unknown",
        "model_resolution_source": "event_metadata" if resolved else "not_exposed",
        "fixture_tool_calls": len(calls), "turn_usage": usage,
        # An absent component is unknown, never zero. Cached input remains a
        # subset of input and is not added to it. The CLI may omit total_tokens.
        "usage": {
            key: sum(row[key] for row in usage) if usage
            and all(row[key] is not None for row in usage) else None
            for key in usage_keys
        },
        "usage_complete": bool(usage) and all(
            row[key] is not None for row in usage for key in usage_keys[:3]
        ),
        "trace": trace,
    }


def _signal_group(proc: subprocess.Popen[bytes], sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


def run_client(
    prompt: str, run_dir: Path, python_executable: str, raw_dir: Path,
    model: str = MODEL, effort: str = EFFORT, timeout: int = 180,
    max_tool_calls: int = 12,
) -> dict[str, Any]:
    """Run a bounded client after external allowance approval and protocol freeze.

    The observation budget stops at the twelfth distinct tool call; a run reaching
    that boundary is incomplete, not a model-quality failure. This is an external
    stop, not a native turn limit or prevention of already-dispatched tool calls.
    Raw output is private; ``trace`` is the publication-oriented projection.
    """
    if timeout <= 0 or max_tool_calls <= 0:
        raise ValueError("Positive wall-time and observed tool-call budgets required")
    repository = Path(__file__).resolve().parents[2]
    raw_dir = raw_dir.resolve()
    if raw_dir == repository or repository in raw_dir.parents:
        raise ValueError("Raw streams must be stored outside the repository")
    root = run_dir.resolve()
    if root == repository or repository in root.parents:
        raise ValueError("Trial state must be outside the repository and its hidden graders")
    if not (root / "visible-case.json").is_file():
        raise ValueError("Missing visible-case.json")
    ready = preflight()
    if not ready["ready"]:
        return {"completed": False, "invalid_reasons": ["subscription_auth_unavailable"], "preflight": ready}
    raw_dir.mkdir(parents=True, exist_ok=True)
    if any((raw_dir / name).exists() for name in ("raw-stream.jsonl", "stderr.txt")):
        raise ValueError("Refusing to overwrite raw evidence")
    started = time.monotonic()
    events: list[dict[str, Any]] = []
    buffer = b""
    stop_reason = ""
    stop_at: float | None = None
    observed: set[str] = set()
    bytes_seen = 0
    with tempfile.TemporaryFile() as stdin, (raw_dir / "raw-stream.jsonl").open("wb") as stdout, \
            (raw_dir / "stderr.txt").open("wb") as stderr:
        stdin.write(prompt.encode())
        stdin.seek(0)
        proc = subprocess.Popen(
            command(prompt, root, python_executable, model, effort),
            cwd=root, env=clean_env(), stdin=stdin, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True,
        )
        assert proc.stdout is not None and proc.stderr is not None
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ, stdout)
            selector.register(proc.stderr, selectors.EVENT_READ, stderr)
            try:
                while selector.get_map():
                    now = time.monotonic()
                    if now - started >= timeout and not stop_reason:
                        stop_reason = "wall_timeout"
                    if stop_reason and stop_at is None:
                        _signal_group(proc, signal.SIGTERM)
                        stop_at = now
                    if stop_at is not None and now - stop_at >= 5:
                        _signal_group(proc, signal.SIGKILL)
                        break
                    for key, _ in selector.select(0.1):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        bytes_seen += len(chunk)
                        if bytes_seen > MAX_STREAM_BYTES:
                            stop_reason = "stream_size_limit"
                            continue
                        key.data.write(chunk)
                        if key.fileobj is not proc.stdout:
                            continue
                        buffer += chunk
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            try:
                                event = json.loads(line)
                            except (ValueError, UnicodeDecodeError):
                                stop_reason = "invalid_json_stream"
                                continue
                            if not isinstance(event, dict):
                                stop_reason = "invalid_json_stream"
                                continue
                            events.append(event)
                            item = event.get("item", {})
                            if not isinstance(item, dict):
                                continue
                            if item.get("type") == "mcp_tool_call" and _fixture_call(item):
                                call_id = item.get("id", item.get("call_id"))
                                if isinstance(call_id, str):
                                    observed.add(call_id)
                                else:
                                    stop_reason = "missing_tool_call_id"
                                if len(observed) >= max_tool_calls:
                                    stop_reason = "observed_tool_budget_reached"
                            elif _is_action(item):
                                stop_reason = "nonfixture_tool_activity"
            finally:
                if proc.poll() is None:
                    _signal_group(proc, signal.SIGKILL)
                proc.wait(timeout=5)
                proc.stdout.close()
                proc.stderr.close()
    if buffer.strip():
        stop_reason = stop_reason or "unterminated_json_stream"
    result = parse_events(events, model, root, max_tool_calls)
    if stop_reason:
        result["invalid_reasons"] = sorted(set(result["invalid_reasons"]) | {stop_reason})
    if proc.returncode != 0:
        result["invalid_reasons"] = sorted(set(result["invalid_reasons"]) | {"nonzero_exit"})
    result["completed"] = not result["invalid_reasons"]
    result.update({
        "exit_code": proc.returncode, "seconds": round(time.monotonic() - started, 3),
        "effort": effort, "timeout_seconds": timeout, "max_observed_tool_calls": max_tool_calls,
        "native_turn_cap": None, "preflight": ready,
        "isolation": "read-only client; bounded trusted MCP fixture; nonfixture actions invalidate",
    })
    return result
