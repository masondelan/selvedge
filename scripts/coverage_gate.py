#!/usr/bin/env python3
"""
Gate + reporter for the Selvedge Coverage Check GitHub Action.

Reads the JSON produced by ``coverage_check.py --json``, exposes the headline
numbers as GitHub Actions step outputs, writes a job-summary block, and
optionally fails the job when coverage falls below a threshold.

Usage:
    python scripts/coverage_gate.py <coverage.json> [fail_under]

``fail_under`` is a float in [0, 1]; an empty string disables the gate. Outside
GitHub Actions (no ``GITHUB_OUTPUT`` / ``GITHUB_STEP_SUMMARY``) the script just
prints to stdout, so it is safe to run locally.
"""

import json
import os
import sys
from pathlib import Path


def _append(env_var: str, text: str) -> None:
    """Append ``text`` to the file named by ``env_var`` if that env var is set."""
    target = os.environ.get(env_var)
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(text)


def main() -> int:
    """Parse the coverage JSON, emit outputs/summary, enforce the threshold."""
    if len(sys.argv) < 2:
        print("usage: coverage_gate.py <coverage.json> [fail_under]", file=sys.stderr)
        return 2

    json_path = Path(sys.argv[1])
    fail_under_raw = sys.argv[2].strip() if len(sys.argv) > 2 else ""

    raw = json_path.read_text(encoding="utf-8").strip() if json_path.exists() else ""
    if not raw:
        # coverage_check.py exits early (no JSON) when there are no commits in
        # the window — report zero coverage rather than crashing.
        summary = {"coverage_ratio": 0.0, "covered": 0, "commits_analysed": 0,
                   "window_minutes": 0, "since": ""}
    else:
        summary = json.loads(raw)["summary"]

    ratio = float(summary["coverage_ratio"])
    covered = int(summary["covered"])
    total = int(summary["commits_analysed"])

    _append("GITHUB_OUTPUT", f"coverage-ratio={ratio}\ncovered={covered}\ntotal={total}\n")

    summary_md = (
        "### Selvedge coverage\n\n"
        f"**{ratio:.0%}** of commits covered "
        f"({covered}/{total}, window ±{summary['window_minutes']}min, "
        f"since {summary['since'] or 'all history'}).\n"
    )
    _append("GITHUB_STEP_SUMMARY", summary_md)
    print(f"Selvedge coverage: {ratio:.0%} ({covered}/{total})")

    if fail_under_raw:
        threshold = float(fail_under_raw)
        if ratio < threshold:
            print(
                f"::error::Selvedge coverage {ratio:.0%} is below the "
                f"required {threshold:.0%}",
                file=sys.stderr,
            )
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
