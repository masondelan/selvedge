#!/usr/bin/env python3
"""Measure Selvedge's MCP "schema tax" — the tokens its tool definitions add
to an agent's context window at init.

Two backends:

* ``proxy`` (default) — counts with ``tiktoken`` (o200k_base) if installed,
  else a chars/4 estimate. Offline, no API key, good for a quick read and for
  CI drift checks. Approximate (~5-15% off Claude's real tokenizer for JSON).

* ``api`` — the authoritative count via Anthropic's ``count_tokens`` endpoint.
  Isolates the tool-definition cost by diffing ``input_tokens`` with vs.
  without the ``tools`` array. Requires ``anthropic`` installed and
  ``ANTHROPIC_API_KEY`` set. Use this for any number you publish.

Usage:
    python scripts/schema_tax.py                 # proxy table
    python scripts/schema_tax.py --json          # machine-readable
    python scripts/schema_tax.py --backend api   # authoritative (needs key)
    python scripts/schema_tax.py --backend api --max 3500  # CI gate
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Run-as-script: make the repo root importable so ``import selvedge`` works.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

WINDOW = 200_000  # reference context window for the "% of window" figure


def live_tool_defs() -> list[dict]:
    """Return the tool definitions the FastMCP server advertises on tools/list.

    Imports the live server so the @mcp.tool decorators and the
    _tighten_descriptions() pass have run — this is the on-the-wire shape,
    not the raw docstrings or the (possibly stale) manifest.json.
    """
    import selvedge.server as server

    tools = asyncio.run(server.mcp.list_tools())
    defs: list[dict] = []
    for t in tools:
        try:
            d = t.model_dump(by_alias=True, exclude_none=True)
        except AttributeError:  # pragma: no cover - older pydantic
            d = json.loads(t.model_dump_json(by_alias=True, exclude_none=True))
        defs.append(d)
    return defs


def _proxy_counter():
    """Return (name, fn) where fn(str)->int counts tokens. Prefers tiktoken."""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("o200k_base")
        return "tiktoken/o200k_base", lambda s: len(enc.encode(s))
    except Exception:
        return "chars/4 estimate", lambda s: round(len(s) / 4)


def measure_proxy(defs: list[dict]) -> dict:
    """Token cost per tool and in total, using the offline proxy counter."""
    label, count = _proxy_counter()
    rows = []
    for d in defs:
        core = {k: d[k] for k in ("name", "description", "inputSchema") if k in d}
        core_json = json.dumps(core, separators=(",", ":"))
        full_json = json.dumps(d, separators=(",", ":"))
        rows.append(
            {
                "name": d.get("name", "?"),
                "core_tokens": count(core_json),  # name + description + inputSchema
                "full_tokens": count(full_json),  # + outputSchema/annotations/title
            }
        )
    rows.sort(key=lambda r: -r["core_tokens"])
    core_total = sum(r["core_tokens"] for r in rows)
    full_total = sum(r["full_tokens"] for r in rows)
    return {
        "backend": "proxy",
        "counter": label,
        "tools": len(defs),
        "rows": rows,
        "core_total": core_total,
        "full_total": full_total,
        "core_pct_window": round(core_total / WINDOW * 100, 2),
        "full_pct_window": round(full_total / WINDOW * 100, 2),
    }


def measure_api(defs: list[dict], model: str) -> dict:
    """Authoritative per-tool cost via Anthropic count_tokens (diff technique).

    For each tool we count input_tokens for a trivial message with just that
    tool minus the same message with no tools, isolating its definition cost.
    The reported total is a single count of all tools together (the real
    at-init cost), which is <= the sum of per-tool diffs because the tools
    block has shared framing.
    """
    try:
        from anthropic import Anthropic
    except ModuleNotFoundError:
        sys.exit(
            "schema_tax: --backend api needs the 'anthropic' package "
            "(pip install anthropic) and ANTHROPIC_API_KEY set."
        )
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("schema_tax: --backend api needs ANTHROPIC_API_KEY in the environment.")

    client = Anthropic()
    msg = [{"role": "user", "content": "."}]

    def to_anthropic(d: dict) -> dict:
        return {
            "name": d["name"],
            "description": d.get("description", ""),
            "input_schema": d.get("inputSchema", {"type": "object"}),
        }

    base = client.messages.count_tokens(model=model, messages=msg).input_tokens
    rows = []
    for d in defs:
        with_one = client.messages.count_tokens(
            model=model, messages=msg, tools=[to_anthropic(d)]
        ).input_tokens
        rows.append({"name": d["name"], "core_tokens": with_one - base, "full_tokens": with_one - base})
    rows.sort(key=lambda r: -r["core_tokens"])

    all_tools = [to_anthropic(d) for d in defs]
    with_all = client.messages.count_tokens(model=model, messages=msg, tools=all_tools).input_tokens
    core_total = with_all - base
    return {
        "backend": "api",
        "counter": f"anthropic/count_tokens ({model})",
        "tools": len(defs),
        "rows": rows,
        "core_total": core_total,
        "full_total": core_total,
        "core_pct_window": round(core_total / WINDOW * 100, 2),
        "full_pct_window": round(core_total / WINDOW * 100, 2),
    }


def render(result: dict) -> str:
    """Plain-text table for humans."""
    lines = [
        f"Selvedge schema tax  —  {result['tools']} tools  —  counter: {result['counter']}",
        "",
        f"{'tool':<18}{'core_tokens':>12}{'full_tokens':>12}",
        "-" * 42,
    ]
    for r in result["rows"]:
        lines.append(f"{r['name']:<18}{r['core_tokens']:>12}{r['full_tokens']:>12}")
    lines += [
        "-" * 42,
        f"{'TOTAL':<18}{result['core_total']:>12}{result['full_total']:>12}",
        "",
        f"core  = name + description + inputSchema (universally loaded)",
        f"full  = core + outputSchema + annotations + title (some clients)",
        f"core is {result['core_pct_window']}% of a {WINDOW:,}-token window;"
        f" full is {result['full_pct_window']}%",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", choices=("proxy", "api"), default="proxy")
    ap.add_argument("--model", default="claude-sonnet-4-6", help="model for --backend api")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--max", type=int, default=0, help="exit 1 if core_total exceeds this (CI gate)")
    args = ap.parse_args()

    defs = live_tool_defs()
    result = measure_api(defs, args.model) if args.backend == "api" else measure_proxy(defs)

    print(json.dumps(result, indent=2) if args.json else render(result))

    if args.max and result["core_total"] > args.max:
        print(f"\nFAIL: core_total {result['core_total']} > --max {args.max}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
