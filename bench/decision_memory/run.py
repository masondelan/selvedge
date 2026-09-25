"""Run a controlled, synthetic decision-memory pilot through Claude Code.

No model or network calls occur unless --execute is supplied. This harness is
outside Selvedge core and requires an existing Claude subscription login.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from selvedge import __version__
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage

HERE = Path(__file__).resolve().parent
ARMS = ("no-memory", "decision-file", "inline-context", "selvedge-pull")
SYSTEM = "You are working on a small configuration task. Use only the provided tools and evidence. Consult available project records when relevant. Apply a justified choice with apply_choice and briefly explain it. Do not invent tests or missing requirements."


def clean_env() -> dict[str, str]:
    """Use subscription auth and suppress unrelated optional client features."""
    env = dict(os.environ)
    for key in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_ANTHROPIC_AWS",
        "CLAUDE_CODE_USE_MANTLE",
    ):
        env.pop(key, None)
    env.update(
        CLAUDE_CODE_DISABLE_AUTO_MEMORY="1",
        DISABLE_AUTOUPDATER="1",
        SELVEDGE_TELEMETRY="0",
        SELVEDGE_QUIET="1",
        ENABLE_TOOL_SEARCH="false",
        ENABLE_CLAUDEAI_MCP_SERVERS="false",
        CLAUDE_CODE_AUTO_CONNECT_IDE="false",
    )
    return env


def content_blocks(event: dict) -> list[dict]:
    """Read public tool/text blocks from a Claude stream event."""
    content = event.get("message", {}).get("content", [])
    return (
        [
            b
            for b in content
            if isinstance(b, dict) and b.get("type") in ("text", "tool_use", "tool_result")
        ]
        if isinstance(content, list)
        else []
    )


def score(
    case: dict,
    arm: str,
    events: list[dict],
    configuration: dict,
    record_ids: list[str],
    exit_code: int,
) -> dict:
    """Keep correct application, observed retrieval and failed runs separate."""
    calls: dict[str, str] = {}
    first_edit = None
    retrievals: list[int] = []
    final = next((e for e in reversed(events) if e.get("type") == "result"), {})
    init = next((e for e in events if e.get("subtype") == "init"), {})
    tool_names = init.get("tools", [])
    expected_tools = {
        "mcp__fixture__read_project",
        "mcp__fixture__read_decisions",
        "mcp__fixture__apply_choice",
    }
    valid_tools = expected_tools.issubset(tool_names) and all(
        name.startswith("mcp__fixture__")
        or (arm == "selvedge-pull" and name.startswith("mcp__selvedge__"))
        for name in tool_names
    )
    if arm == "selvedge-pull":
        valid_tools = valid_tools and "mcp__selvedge__prior_attempts" in tool_names
    for index, event in enumerate(events):
        for block in content_blocks(event):
            if block.get("type") == "tool_use":
                calls[block["id"]] = block["name"]
                if block["name"].endswith("__apply_choice") and first_edit is None:
                    first_edit = index
            elif block.get("type") == "tool_result" and not block.get("is_error", False):
                name = calls.get(block.get("tool_use_id", ""), "")
                text = json.dumps(block.get("content", ""))
                if (
                    arm == "selvedge-pull"
                    and name.startswith("mcp__selvedge__")
                    and any(rid in text for rid in record_ids)
                ):
                    retrievals.append(index)
                elif (
                    arm == "decision-file"
                    and name.endswith("__read_decisions")
                    and case["decisions"][0]["reasoning"] in text
                ):
                    retrievals.append(index)
    completed = (
        valid_tools
        and exit_code == 0
        and bool(final)
        and not final.get("is_error", False)
        and bool(configuration)
    )
    return {
        "completed": completed,
        "correct_application": completed and configuration.get("value") == case["expected"],
        "selected": configuration.get("value", ""),
        "expected": case["expected"],
        "retrieved_before_first_edit": first_edit is not None
        and any(i < first_edit for i in retrievals),
        "inline_context_supplied": arm == "inline-context",
        "tool_configuration_valid": valid_tools,
        "available_tools": tool_names,
        "model_resolved": init.get("model", ""),
        "usage": final.get("usage", {}),
        "model_usage": final.get("modelUsage", {}),
        "error": final.get("result", "")
        if final.get("is_error")
        else ("process failed or no completed edit" if not completed else ""),
    }


def run_one(case: dict, arm: str, trial: int, root: Path, model: str, timeout: int) -> dict:
    """Run one fresh isolated client with only the fixture and chosen memory."""
    run_dir = root / f"{case['id']}--{arm}--{trial}"
    run_dir.mkdir()
    records = [{"entity_path": case["entity"], **d} for d in case["decisions"]]
    fixture = {"entity": case["entity"], "project": case["project"], "decisions": records}
    (run_dir / "case.json").write_text(json.dumps(fixture, indent=2) + "\n")
    env = clean_env()
    servers = {
        "fixture": {
            "command": sys.executable,
            "args": [str(HERE / "fixture_server.py")],
            "env": {"SELVEDGE_BENCH_RUN": str(run_dir), "SELVEDGE_BENCH_ARM": arm},
        }
    }
    record_ids = []
    if arm == "selvedge-pull":
        db = run_dir / "memory.db"
        storage = SelvedgeStorage(db)
        for decision in records:
            saved = storage.log_event(ChangeEvent(**decision, agent="fixture-author"))
            record_ids.append(saved.id)
        servers["selvedge"] = {
            "command": sys.executable,
            "args": ["-m", "selvedge.server"],
            "env": {
                "SELVEDGE_DB": str(db),
                "SELVEDGE_TELEMETRY": "0",
                "SELVEDGE_QUIET": "1",
                "SELVEDGE_LOG_LEVEL": "ERROR",
            },
        }
    config = run_dir / "mcp.json"
    config.write_text(json.dumps({"mcpServers": servers}, indent=2) + "\n")
    prompt = case["task"]
    if arm == "inline-context":
        prompt += "\n\nPrior project decisions (assess against current evidence):\n" + json.dumps(
            records
        )
    (run_dir / "prompt.txt").write_text(prompt + "\n")
    command = [
        "claude",
        "-p",
        prompt,
        "--model",
        model,
        "--system-prompt",
        SYSTEM,
        "--tools",
        "",
        "--allowedTools",
        "mcp__fixture",
        "mcp__selvedge",
        "--permission-mode",
        "dontAsk",
        "--strict-mcp-config",
        "--mcp-config",
        str(config),
        "--setting-sources",
        "project",
        "--settings",
        '{"disableAllHooks":true,"autoMemoryEnabled":false}',
        "--disable-slash-commands",
        "--no-chrome",
        "--no-session-persistence",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    started = datetime.now(timezone.utc).isoformat()
    before = time.monotonic()
    proc = subprocess.Popen(
        command,
        cwd=run_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
        stderr += "\nHarness timeout."
    elapsed = time.monotonic() - before
    (run_dir / "raw-stream.jsonl").write_text(stdout)
    (run_dir / "stderr.txt").write_text(stderr)
    events = []
    parse_errors = 0
    for line in stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            parse_errors += 1
    config_path = run_dir / "configuration.json"
    configuration = json.loads(config_path.read_text()) if config_path.exists() else {}
    result = {
        "case": case["id"],
        "kind": case["kind"],
        "arm": arm,
        "trial": trial,
        "started_at": started,
        "seconds": round(elapsed, 3),
        "exit_code": proc.returncode,
        **score(case, arm, events, configuration, record_ids, proc.returncode),
    }
    result["stream_parse_errors"] = parse_errors
    if parse_errors or result["model_resolved"] != model:
        result.update(
            completed=False, correct_application=False, error="Invalid stream or unexpected model"
        )
    (run_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    # Public evidence intentionally excludes session IDs, account data and paths.
    public_trace = [
        {"type": e["type"], "content": content_blocks(e)}
        for e in events
        if e.get("type") in ("assistant", "user")
    ]
    (run_dir / "tool-trace.json").write_text(
        json.dumps(public_trace, indent=2)
        .replace(str(run_dir), "<trial>")
        .replace(str(Path.home()), "<home>")
        + "\n"
    )
    return result


def main() -> None:
    """Freeze the protocol before execution and retain failures in the results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", required=True, help="Exact model ID; no moving alias.")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--workers",
        type=int,
        choices=(1, 2, 3),
        default=1,
        help="Bounded concurrent fresh clients; timing is not a latency benchmark.",
    )
    parser.add_argument(
        "--case", choices=[c["id"] for c in json.loads((HERE / "cases.json").read_text())]
    )
    args = parser.parse_args()
    if not args.model.startswith("claude-"):
        parser.error("Use a full model ID rather than a moving alias")
    if args.trials < 1 or args.timeout < 1:
        parser.error("trials and timeout must be positive")
    cases = json.loads((HERE / "cases.json").read_text())
    if args.case:
        cases = [case for case in cases if case["id"] == args.case]
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    schedule = [(c["id"], arm, t) for c in cases for arm in ARMS for t in range(1, args.trials + 1)]
    random.Random(20260925).shuffle(schedule)
    manifest = {
        "protocol": "decision-memory-pilot/1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "selvedge_version": __version__,
        "model_requested": args.model,
        "trials": args.trials,
        "timeout_seconds": args.timeout,
        "workers": args.workers,
        "arms": ARMS,
        "system_prompt": SYSTEM,
        "cases": cases,
        "schedule": schedule,
        "source_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (HERE / "cases.json", HERE / "fixture_server.py", HERE / "run.py")
        },
        "executed": args.execute,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if not args.execute:
        print(f"Prepared {len(schedule)} trials. No model calls made. {root / 'manifest.json'}")
        return
    auth = subprocess.run(
        ["claude", "auth", "status"], capture_output=True, text=True, env=clean_env(), check=True
    )
    auth_data = json.loads(auth.stdout)
    if auth_data.get("authMethod") != "claude.ai" or not auth_data.get("loggedIn"):
        raise SystemExit(
            "This runner requires an existing Claude subscription login; API billing is not enabled by the harness."
        )
    manifest["claude_version"] = subprocess.check_output(["claude", "--version"], text=True).strip()
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    by_id = {case["id"]: case for case in cases}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for offset in range(0, len(schedule), args.workers):
            batch = schedule[offset : offset + args.workers]
            futures = [
                pool.submit(run_one, by_id[case_id], arm, trial, root, args.model, args.timeout)
                for case_id, arm, trial in batch
            ]
            results = [future.result() for future in futures]
            for result in results:
                with (root / "results.jsonl").open("a") as handle:
                    handle.write(json.dumps(result) + "\n")
                print(
                    json.dumps(
                        {
                            k: result[k]
                            for k in (
                                "case",
                                "arm",
                                "trial",
                                "completed",
                                "correct_application",
                                "retrieved_before_first_edit",
                                "seconds",
                                "error",
                            )
                        }
                    ),
                    flush=True,
                )
            if any(not result["completed"] for result in results):
                raise SystemExit(
                    "Stopped after an incomplete batch. Preserve failures; diagnose before a separately labelled rerun."
                )


if __name__ == "__main__":
    main()
