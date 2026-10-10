"""Optional MCP adapter for bounded code edits and public checks.

The client receives only the visible fixture. Private grader cases and reference
solutions are never loaded by this server. No arbitrary host path is accepted.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from grade import grade_candidate
from mcp.server.fastmcp import FastMCP

ROOT = Path(os.environ["SELVEDGE_CODING_RUN"])
CASE = json.loads((ROOT / "visible-case.json").read_text())
mcp = FastMCP("coding_fixture")
LOCK = threading.RLock()


def record(tool: str, payload: dict) -> None:
    """Append an observable action without account or private reasoning data."""
    with LOCK, (ROOT / "fixture-trace.jsonl").open("a") as handle:
        handle.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(),
                                 "tool": tool, **payload}) + "\n")


def used(tool: str) -> int:
    """Count accepted writes/checks against the same per-run budget."""
    path = ROOT / "fixture-trace.jsonl"
    return sum(json.loads(line)["tool"] == tool for line in path.read_text().splitlines()) \
        if path.exists() else 0


@mcp.tool()
def read_project() -> dict:
    """Read the current task, permitted Python subset and available project files."""
    result = {"task": CASE["task"], "files": sorted(CASE["project_files"]),
              "entrypoint": CASE["entrypoint"], "write_budget": 2, "check_budget": 2}
    record("read_project", {})
    return result


@mcp.tool()
def read_file(path: str) -> dict:
    """Read an exact project filename; no filesystem traversal or host access."""
    if path == "solution.py" and (ROOT / "solution.py").exists():
        content = (ROOT / "solution.py").read_text()
    elif path in CASE["project_files"]:
        content = CASE["project_files"][path]
    else:
        return {"error": "No such project file."}
    record("read_file", {"path": path})
    return {"path": path, "content": content}


@mcp.tool()
def write_file(path: str, content: str) -> dict:
    """Write solution.py only (at most two writes, at most 16,000 characters)."""
    with LOCK:
        count = used("write_file")
        if path != "solution.py" or len(content) > 16000 or count >= 2:
            return {"error": "Only solution.py, <=16000 characters, two writes maximum."}
        (ROOT / "solution.py").write_text(content)
        (ROOT / f"candidate-{count + 1}.py").write_text(content)
        record("write_file", {"path": path, "characters": len(content), "write": count + 1})
    return {"written": path}


@mcp.tool()
def run_checks() -> dict:
    """Execute the public checks against the written solution, at most twice."""
    with LOCK:
        if used("run_checks") >= 2 or not (ROOT / "solution.py").exists():
            return {"error": "Write a solution first; at most two public-check calls."}
        result = grade_candidate(CASE, (ROOT / "solution.py").read_text(), public_only=True)
        record("run_checks", {"result": result})
    return result


if __name__ == "__main__":
    mcp.run()
