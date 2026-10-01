"""PR comments from trusted base history; PR file names are data only.

Run by actions/review-context with Python isolated mode. No installation,
checkout of head, shell interpolation, third-party dependency, or model call.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

# Load only this trusted Action revision, never code from the caller's checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from selvedge.ledger import read_ledger, render_ledger  # noqa: E402

MARKER = "<!-- selvedge-review-context:v1 -->"


class GitHub:
    """Small GitHub.com client with bounded pagination and no shell calls."""

    def __init__(self, repo: str, token: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("Invalid repository")
        self.base = "https://api.github.com/repos/" + repo
        self.token = token

    def request(self, path: str, data: dict | None = None, method: str = "GET") -> Any:
        """Read or mutate one fixed-repository REST resource."""
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(data).encode() if data is not None else None,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)

    def pages(self, path: str, max_pages: int) -> list[dict]:
        """Fetch bounded complete collections, failing rather than silently truncating."""
        rows = []
        for page in range(1, max_pages + 1):
            batch = self.request(f"{path}?per_page=100&page={page}")
            rows.extend(batch)
            if len(batch) < 100:
                return rows
        # The API's file ceiling can end on a full page. Caller checks changed_files.
        return rows


def touched_paths(files: list[dict]) -> list[str]:
    """Include both sides of renames and deleted files; preserve names literally."""
    return sorted({p for f in files for p in (f["filename"], f.get("previous_filename", "")) if p})


def compose(report: dict, base: str, head: str) -> str:
    """Bind the report to the exact PR revisions and disclose base-only scope."""
    return (
        MARKER
        + "\n"
        + f"Base `{base}` · PR head `{head}`\n\n"
        + "History is from the trusted base commit. Decisions added only in this PR are not included. Matching uses exact file paths and dotted subentities; schema/logical entities need a separate local lookup.\n\n"
        + render_ledger(report)
    )


def upsert(api: GitHub, number: int, body: str) -> str:
    """Update this bot's marked comment, preserving human comments and other bots."""
    comments = api.pages(f"/issues/{number}/comments", 100)
    if len(comments) == 10000:
        raise ValueError("Comment pagination limit reached; no comment was sent")
    owned = [
        c
        for c in comments
        if c.get("user", {}).get("login") == "github-actions[bot]"
        and c.get("user", {}).get("type") == "Bot"
        and c.get("body", "").startswith(MARKER + "\n")
    ]
    if owned:
        comment = owned[-1]
        if comment["body"] == body:
            return "unchanged"
        api.request(f"/issues/comments/{int(comment['id'])}", {"body": body}, "PATCH")
        return "updated"
    api.request(f"/issues/{number}/comments", {"body": body}, "POST")
    return "created"


def main() -> None:
    """Validate the trusted base and render once before any optional comment write."""
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request_target":
        raise ValueError("Use pull_request_target with an explicit base-only checkout")
    if os.environ.get("GITHUB_API_URL", "https://api.github.com") != "https://api.github.com":
        raise ValueError("This initial Action supports GitHub.com only")
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    number = int(event["number"])
    api = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["SELVEDGE_REVIEW_TOKEN"])
    pr = api.request(f"/pulls/{number}")
    if pr["state"] != "open":
        raise ValueError("PR is no longer open")
    base, head = pr["base"]["sha"], pr["head"]["sha"]
    if not all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (base, head)):
        raise ValueError("Unexpected revision format")
    root = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
    actual = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual != base:
        raise ValueError("Checkout must equal the current PR base SHA; rerun after base changes")
    db = (root / os.environ.get("SELVEDGE_REVIEW_DB", ".selvedge/selvedge.db")).resolve()
    relative = db.relative_to(root).as_posix()
    # Byte comparison prevents an ignored, substituted, or modified local DB
    # from becoming an accidental source of public internal-history disclosure.
    tracked = subprocess.check_output(["git", "-C", str(root), "show", f"{base}:{relative}"])
    if tracked != db.read_bytes() or any(
        Path(str(db) + suffix).exists() for suffix in ("-wal", "-shm")
    ):
        raise ValueError("Database must be the clean base-commit file with no WAL/SHM sidecars")
    files = api.pages(f"/pulls/{number}/files", 30)
    if len(files) != pr["changed_files"]:
        raise ValueError(
            "Incomplete changed-file list (GitHub supports at most 3000); no comment sent"
        )
    paths = touched_paths(files)
    if not paths:
        raise ValueError("No touched paths; refusing an unfiltered ledger disclosure")
    try:
        limit = int(os.environ.get("SELVEDGE_REVIEW_LIMIT", "100"))
        if not 1 <= limit <= 1000:
            raise ValueError
    except ValueError as exc:
        raise ValueError("SELVEDGE_REVIEW_LIMIT must be an integer between 1 and 1000") from exc
    report = read_ledger(db, paths, limit)
    body = compose(report, base, head)
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
        stream.write(body)
    dry_run = os.environ.get("SELVEDGE_REVIEW_DRY_RUN", "false")
    if dry_run not in ("true", "false"):
        raise ValueError("dry-run must be true or false")
    latest = api.request(f"/pulls/{number}")
    if latest["base"]["sha"] != base or latest["head"]["sha"] != head or latest["state"] != "open":
        raise ValueError("PR changed while reporting; no stale comment sent")
    print("dry-run" if dry_run == "true" else upsert(api, number, body))
    if not report["chain"]["intact"]:
        raise SystemExit("Recorded chain verification failed; inspect the report")


if __name__ == "__main__":
    main()
