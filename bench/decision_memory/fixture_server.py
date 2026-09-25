"""Bounded configuration fixture: the model cannot read the scorer or host files."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

ROOT = Path(os.environ["SELVEDGE_BENCH_RUN"])
CASE = json.loads((ROOT / "case.json").read_text())
ARM = os.environ["SELVEDGE_BENCH_ARM"]
mcp = FastMCP(
    "fixture",
    instructions="Inspect the project, consult available decision records when relevant, and apply a configuration choice supported by current evidence.",
)


def record(tool: str, payload: dict) -> None:
    """Append an observable fixture action with a UTC timestamp."""
    with (ROOT / "fixture-trace.jsonl").open("a") as handle:
        handle.write(
            json.dumps(
                {"timestamp": datetime.now(timezone.utc).isoformat(), "tool": tool, **payload}
            )
            + "\n"
        )


@mcp.tool()
def read_project() -> dict:
    """Read this project's configuration choices and current test evidence."""
    record("read_project", {})
    return {"entity_path": CASE["entity"], **CASE["project"]}


@mcp.tool()
def read_decisions() -> dict:
    """Read the project's maintained decision file, if one exists."""
    decisions = CASE["decisions"] if ARM == "decision-file" else []
    record("read_decisions", {"count": len(decisions)})
    return {
        "decisions": decisions,
        "note": "This file is independent of any connected memory server.",
    }


@mcp.tool()
def apply_choice(value: str, rationale: str) -> dict:
    """Write one allowed value to this fixture's configuration and explain why."""
    if value not in CASE["project"]["choices"]:
        record("invalid_choice", {"value": value})
        return {"error": "Choose one of the project's listed values."}
    result = {"key": CASE["project"]["key"], "value": value, "rationale": rationale}
    (ROOT / "configuration.json").write_text(json.dumps(result, indent=2) + "\n")
    record("apply_choice", result)
    return {"status": "applied", **result}


if __name__ == "__main__":
    mcp.run()
