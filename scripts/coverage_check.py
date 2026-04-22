#!/usr/bin/env python3
"""
selvedge coverage check

Cross-references git commit history against Selvedge's event log to measure
how often AI agents actually call log_change after making changes.

A "covered" commit is one where at least one Selvedge event was logged within
a configurable time window (default: 10 minutes) before or after the commit.

Usage:
    python scripts/coverage_check.py
    python scripts/coverage_check.py --since 30d
    python scripts/coverage_check.py --window 20 --limit 50
    python scripts/coverage_check.py --json
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add parent dir to path so we can import selvedge
sys.path.insert(0, str(Path(__file__).parent.parent))

from selvedge.config import get_db_path
from selvedge.storage import SelvedgeStorage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_since(s: str) -> datetime:
    """Parse relative time string like '30d', '7d', '1y' into a datetime."""
    import re
    m = re.fullmatch(r"(\d+)([dhmy])", s.strip())
    if not m:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    n, unit = int(m.group(1)), m.group(2)
    delta = {"h": timedelta(hours=n), "d": timedelta(days=n),
             "m": timedelta(days=n * 30), "y": timedelta(days=n * 365)}[unit]
    return datetime.now(timezone.utc) - delta


def get_git_commits(since_dt: datetime | None, limit: int) -> list[dict]:
    """Return git commits as list of dicts: hash, timestamp, subject, author."""
    fmt = "%H\t%aI\t%s\t%an"
    cmd = ["git", "log", f"--pretty=format:{fmt}", f"-{limit}"]
    if since_dt:
        cmd.append(f"--after={since_dt.isoformat()}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"error: git log failed — are you in a git repo?\n{e.stderr}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("error: git not found in PATH", file=sys.stderr)
        sys.exit(1)

    commits = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t", 3)
        if len(parts) < 4:
            continue
        commits.append({
            "hash": parts[0][:12],
            "full_hash": parts[0],
            "timestamp": parts[1],
            "subject": parts[2],
            "author": parts[3],
        })
    return commits


def get_selvedge_events(storage: SelvedgeStorage, since_dt: datetime | None) -> list[dict]:
    """Return all Selvedge events, optionally filtered by date."""
    since_str = since_dt.isoformat() if since_dt else ""
    return storage.get_history(since=since_str, limit=10_000)


def events_near_commit(
    commit_ts: str,
    events: list[dict],
    window_minutes: int,
) -> list[dict]:
    """Return Selvedge events logged within ±window_minutes of a commit."""
    try:
        commit_dt = datetime.fromisoformat(commit_ts).astimezone(timezone.utc)
    except ValueError:
        return []
    window = timedelta(minutes=window_minutes)
    lo = (commit_dt - window).isoformat()
    hi = (commit_dt + window).isoformat()
    return [e for e in events if lo <= e["timestamp"] <= hi]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Check what fraction of git commits have Selvedge coverage."
    )
    parser.add_argument("--since", default="30d",
                        help="How far back to look (e.g. 30d, 7d, 1y). Default: 30d")
    parser.add_argument("--window", type=int, default=10,
                        help="Match window in minutes around each commit. Default: 10")
    parser.add_argument("--limit", type=int, default=100,
                        help="Max commits to analyse. Default: 100")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="Output raw JSON")
    args = parser.parse_args()

    since_dt = parse_since(args.since) if args.since else None

    db_path = get_db_path()
    if not Path(db_path).exists():
        print(f"error: no Selvedge database found at {db_path}", file=sys.stderr)
        print("run 'selvedge init' in your project root first", file=sys.stderr)
        sys.exit(1)

    storage = SelvedgeStorage(db_path)
    commits = get_git_commits(since_dt, args.limit)
    events = get_selvedge_events(storage, since_dt)

    if not commits:
        print("no git commits found in the specified window", file=sys.stderr)
        sys.exit(0)

    results = []
    covered = 0
    for commit in commits:
        nearby = events_near_commit(commit["timestamp"], events, args.window)
        is_covered = len(nearby) > 0
        if is_covered:
            covered += 1
        results.append({
            "hash": commit["hash"],
            "timestamp": commit["timestamp"][:16],
            "subject": commit["subject"][:60],
            "author": commit["author"],
            "selvedge_events": len(nearby),
            "covered": is_covered,
            "entities": [e["entity_path"] for e in nearby[:5]],
        })

    total = len(commits)
    ratio = covered / total if total else 0.0

    if args.as_json:
        print(json.dumps({
            "summary": {
                "commits_analysed": total,
                "covered": covered,
                "uncovered": total - covered,
                "coverage_ratio": round(ratio, 3),
                "window_minutes": args.window,
                "since": args.since,
            },
            "commits": results,
        }, indent=2))
        return

    # --- human-readable output ---
    print(f"\nSelvedge coverage report  (window: ±{args.window}min, since: {args.since})\n")

    bar_width = 40
    filled = int(ratio * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    print(f"  [{bar}]  {ratio:.0%}  ({covered}/{total} commits covered)\n")

    uncovered = [r for r in results if not r["covered"]]
    if uncovered:
        print(f"  Uncovered commits ({len(uncovered)}):")
        for c in uncovered[:20]:
            print(f"    {c['hash']}  {c['timestamp']}  {c['subject']}")
        if len(uncovered) > 20:
            print(f"    ... and {len(uncovered) - 20} more")
    else:
        print("  All commits covered ✓")

    print()

    if ratio < 0.5:
        print("  Tip: coverage is low. Common causes:")
        print("    - Agent CLAUDE.md / system prompt doesn't instruct log_change")
        print("    - Commits include non-AI changes (human edits, merges, formatting)")
        print("    - DB path mismatch — check SELVEDGE_DB env var")
        print()


if __name__ == "__main__":
    main()
