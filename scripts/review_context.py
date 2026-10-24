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
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# Load only this trusted Action revision, never code from the caller's checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from selvedge.ledger import read_ledger, render_ledger, render_review_context  # noqa: E402

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


def compose(
    report: dict,
    base: str,
    head: str,
    *,
    repo: str = "",
    db_path: str = "",
    budget: int = 6000,
) -> str:
    """Bind the compact report to the exact PR revisions and disclose base-only scope.

    Links are built only from validated values: the repository name, a
    40-hex base SHA and a percent-encoded tracked DB path. No per-run value
    (run ID or run URL) enters the body, so an unchanged report on a re-run
    yields an identical body and ``upsert`` makes no write. The whole comment,
    marker included, stays within ``budget`` characters.
    """
    if repo and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid repository")
    source = ""
    if repo and db_path:
        quoted = urllib.parse.quote(db_path, safe="/")
        source = (
            f"[tracked store at the base commit](https://github.com/{repo}/blob/{base}/{quoted}),"
            f" matched against {len(report['entities'])} touched path(s)"
        )
    # Stable pointer: the per-run link lives only in the job summary itself.
    details = "the longer report in the job summary of the latest Selvedge review run"
    preamble = (
        f"{MARKER}\nBase `{base}` · PR head `{head}`\n\n"
        "History is from the trusted base commit. Decisions added only in this PR are not included. "
        "Matching uses exact file paths and dotted subentities; schema/logical entities need a separate local lookup.\n\n"
    )
    return preamble + render_review_context(
        report, budget=budget, source=source, details=details, prefix_chars=len(preamble)
    )


def bounded_int(name: str, default: str, low: int, high: int) -> int:
    """Read one integer Action input, rejecting values outside ``low..high``."""
    try:
        value = int(os.environ.get(name, default))
        if not low <= value <= high:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer between {low} and {high}") from exc
    return value


def upsert(api: GitHub, number: int, body: str, *, create: bool = True) -> str:
    """Refresh this bot's comment, optionally avoiding a new empty-history comment."""
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
    if not create:
        return "no-matching-history"
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
    limit = bounded_int("SELVEDGE_REVIEW_LIMIT", "100", 1, 1000)
    # GitHub rejects comment bodies over 65536 characters.
    budget = bounded_int("SELVEDGE_REVIEW_BUDGET", "6000", 2000, 60000)
    report = read_ledger(db, paths, limit)
    body = compose(
        report,
        base,
        head,
        repo=os.environ["GITHUB_REPOSITORY"],
        db_path=relative,
        budget=budget,
    )
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
        # The summary keeps the compact view plus the longer per-event report
        # that the comment's omission line points to.
        stream.write(body + "\n---\n\n" + render_ledger(report))
    dry_run = os.environ.get("SELVEDGE_REVIEW_DRY_RUN", "false")
    if dry_run not in ("true", "false"):
        raise ValueError("dry-run must be true or false")
    latest = api.request(f"/pulls/{number}")
    if latest["base"]["sha"] != base or latest["head"]["sha"] != head or latest["state"] != "open":
        raise ValueError("PR changed while reporting; no stale comment sent")
    # Keep healthy no-match runs quiet on the PR. Refresh an existing comment
    # so a previous revision's context cannot masquerade as the current result.
    # A broken chain remains visible even when none of its events match.
    create = bool(report["total_matching"]) or not report["chain"]["intact"]
    print("dry-run" if dry_run == "true" else upsert(api, number, body, create=create))
    if not report["chain"]["intact"]:
        raise SystemExit("Recorded chain verification failed; inspect the report")


if __name__ == "__main__":
    main()
